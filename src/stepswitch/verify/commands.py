"""Project commands for the gates: interpreter and tool detection, bounded subprocesses.

Gates check the project, so every tool runs from the project's environment, not Stepswitch's.
The interpreter comes from configuration (`[tracks.<track>].python`, then `[science].python`
for the science track), else the repository's `.venv`, else the interpreter running Stepswitch.

A task's worktree (`<checkout>/.stepswitch/wt/<id>`) holds no `.venv`, because environments are
never copied into it. For a worktree, the checkout it was made from is searched next: for the
`.venv`, and for interpreter paths configured relative to the repository. When the
interpreter's environment lives outside the working tree and the tree has a `src/` directory,
that directory goes first on PYTHONPATH. Otherwise an editable install of the original
checkout would import the checkout's code instead of the task's.

`run` never raises: a program that cannot start gives exit code 127 and a timeout gives 124
after the whole process tree is killed. Commands get no stdin and, apart from acceptance
commands written by the user, no shell. Nothing here touches the network.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from stepswitch.paths import STATE_DIR_NAME

NOT_STARTED = 127
TIMED_OUT = 124
STREAM_LIMIT = 2_000_000  # characters kept per stream; the middle of longer output is dropped
PROBE_TIMEOUT = 60.0
PROBE_MODULES = ("ruff", "mypy", "pytest", "testmon", "jupytext")
KEY_PACKAGES = ("numpy", "scipy", "pandas")
_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# Directories that hold no project source: environments, caches, build output, VCS data.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        ".tox",
        ".nox",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".hypothesis",
        ".ipynb_checkpoints",
        ".stepswitch",
        "build",
        "dist",
        "site-packages",
    }
)


@dataclass
class CommandResult:
    """What one command did. `output` is stdout followed by stderr."""

    argv: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    seconds: float = 0.0
    timed_out: bool = False
    start_error: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.start_error

    @property
    def started(self) -> bool:
        return not self.start_error

    @property
    def output(self) -> str:
        parts = [part for part in (self.stdout, self.stderr) if part]
        return strip_ansi("\n".join(parts))

    def display(self) -> str:
        return display(self.argv)


def display(argv: Sequence[str] | str) -> str:
    """A command as one line of text, quoted for reading (not for pasting into a shell)."""
    if isinstance(argv, str):
        return argv
    return " ".join(shlex.quote(arg) if arg else "''" for arg in argv)


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _decode(data: bytes | None) -> str:
    text = (data or b"").decode("utf-8", "replace").replace("\r\n", "\n")
    if len(text) > STREAM_LIMIT:
        head, tail = STREAM_LIMIT // 4, STREAM_LIMIT - STREAM_LIMIT // 4
        dropped = len(text) - head - tail
        text = f"{text[:head]}\n... ({dropped} characters dropped) ...\n{text[-tail:]}"
    return text


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    """Kill the process and everything it started (pytest workers, shells, servers)."""
    if sys.platform == "win32":
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
                creationflags=_NO_WINDOW,
            )
    else:
        import signal

        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)  # the child leads its own session
    with contextlib.suppress(OSError):
        proc.kill()


def run(
    argv: Sequence[str] | str,
    *,
    cwd: Path,
    timeout: float,
    env: Mapping[str, str] | None = None,
    shell: bool = False,
) -> CommandResult:
    """Run one command to completion or timeout; never raises for a failing command.

    `shell=True` takes a command line (a string) and runs it through the platform shell; it is
    used only for acceptance commands, which the user writes.
    """
    args = [argv] if isinstance(argv, str) else [str(arg) for arg in argv]
    if not args or not args[0]:
        return CommandResult(args, NOT_STARTED, start_error="empty command")
    kwargs: dict[str, Any] = {
        "cwd": str(cwd),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "env": dict(env) if env is not None else None,
        "shell": shell,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = _NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    start = time.monotonic()
    try:
        proc = subprocess.Popen(args[0] if shell else args, **kwargs)
    except (OSError, ValueError) as exc:
        message = f"cannot run {display(args)}: {exc}"
        return CommandResult(args, NOT_STARTED, stderr=message, start_error=message)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=5.0)
        except subprocess.TimeoutExpired:  # a grandchild outside the tree still holds the pipes
            out, err = b"", b""
        stderr = _decode(err)
        note = f"timed out after {timeout:g} s; the process tree was killed"
        return CommandResult(
            args,
            TIMED_OUT,
            _decode(out),
            f"{stderr}\n{note}" if stderr else note,
            time.monotonic() - start,
            timed_out=True,
        )
    except BaseException:
        _kill_tree(proc)
        raise
    return CommandResult(
        args, int(proc.returncode), _decode(out), _decode(err), time.monotonic() - start
    )


def expand(argv: Sequence[str], values: Mapping[str, str]) -> list[str]:
    """Replace `{name}` placeholders inside each argument; unknown braces are left alone."""
    expanded: list[str] = []
    for arg in argv:
        for name, value in values.items():
            arg = arg.replace("{" + name + "}", value)
        expanded.append(arg)
    return expanded


def shell_quote(value: str) -> str:
    """Quote one argument for the platform shell used by `run(..., shell=True)`."""
    if sys.platform == "win32":
        return subprocess.list2cmdline([value])
    return shlex.quote(value)


def distill(text: str, keep: re.Pattern[str], *, limit: int = 1400, tail: int = 12) -> str:
    """The decisive lines of tool output: lines matching `keep`, else the last `tail` lines.

    Longer selections keep their beginning and their last three lines (usually the summary).
    """
    lines = [line.rstrip() for line in strip_ansi(text).splitlines()]
    picked = [line for line in lines if keep.search(line)]
    if not picked:
        picked = [line for line in lines if line.strip()][-tail:]
    deduped = [line for i, line in enumerate(picked) if i == 0 or line != picked[i - 1]]
    out = "\n".join(deduped)
    if len(out) <= limit:
        return out
    last = "\n".join(deduped[-3:])[-(limit // 3) :]
    head = out[: max(limit - len(last) - 8, 0)]
    if "\n" in head:
        head = head[: head.rfind("\n")]
    return f"{head}\n...\n{last}"


# --------------------------------------------------------------------------------------------
# Git (read-only queries; optional locks off so concurrent runs never contend for the index)
# --------------------------------------------------------------------------------------------


def git(repo: Path, *args: str, timeout: float = 60.0) -> subprocess.CompletedProcess[bytes] | None:
    """Run git in `repo`; None when git is missing or cannot run."""
    program = shutil.which("git")
    if program is None:
        return None
    try:
        return subprocess.run(
            [program, *args],
            cwd=repo,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def resolve_commit(repo: Path, revision: str) -> str | None:
    """The commit id `revision` names, or None (also when `repo` is not in a git checkout)."""
    done = git(
        repo, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{revision}^{{commit}}"
    )
    if done is None or done.returncode != 0:
        return None
    return done.stdout.decode("utf-8", "replace").strip() or None


def file_at(repo: Path, commit: str, rel: str) -> bytes | None:
    """Committed content of `rel` (relative to `repo`) at `commit`; None if absent there."""
    done = git(repo, "cat-file", "blob", f"{commit}:./{rel}")
    return done.stdout if done is not None and done.returncode == 0 else None


def blob_at(repo: Path, commit: str, rel: str) -> str | None:
    """Blob id of `rel` at `commit`, or None."""
    done = git(repo, "rev-parse", "--verify", "--quiet", f"{commit}:./{rel}")
    return done.stdout.decode().strip() if done is not None and done.returncode == 0 else None


def blob_in_worktree(repo: Path, rel: str) -> str | None:
    """Blob id the working-tree file would get if committed (clean filters applied), or None."""
    if not (repo / rel).is_file():
        return None
    done = git(repo, "hash-object", "--", rel)
    return done.stdout.decode().strip() if done is not None and done.returncode == 0 else None


def changed_files(repo: Path, base: str | None) -> list[str] | None:
    """Files that differ from `base` (HEAD when None) in the working tree, plus untracked ones.

    Relative POSIX paths inside `repo`, Stepswitch's own `.stepswitch/` left out, and so are
    untracked generated files (bytecode, tool caches: `worktree.GENERATED_PATTERNS`), which a
    diff leaves out too. None when git cannot tell (no git, not a checkout, unknown revision,
    no commits yet).
    """
    from stepswitch.worktree import EXCLUDE_GENERATED

    commit = resolve_commit(repo, base or "HEAD")
    if commit is None:
        return None
    diff = git(repo, "diff", "--name-only", "-z", "--no-renames", "--relative", commit, "--")
    untracked = git(repo, "ls-files", "--others", "--exclude-standard", *EXCLUDE_GENERATED, "-z")
    if diff is None or untracked is None or diff.returncode or untracked.returncode:
        return None
    names = (diff.stdout + untracked.stdout).decode("utf-8", "replace").split("\0")
    return sorted({n for n in names if n and n.split("/", 1)[0] != ".stepswitch"})


def purge_stale_bytecode(repo: Path) -> int:
    """Delete cached bytecode that could hide a recent source edit; returns how many went.

    CPython reuses `__pycache__/<module>.<tag>.pyc` while the source's size and modification
    time in whole seconds match the values recorded in it, so a same-size edit within the same
    second as the cached version would run the old code. A cache file is only trusted when it
    was written in a later second than the source's last modification; the others are removed
    and Python recompiles them. Environments and build directories are left alone.
    """
    removed = 0
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        cache = Path(root) / "__pycache__"
        try:
            cached = [entry.name for entry in cache.iterdir() if entry.name.endswith(".pyc")]
        except OSError:
            continue
        for name in files:
            if not name.endswith(".py"):
                continue
            stem = name[: -len(".py")]
            try:
                trusted_from = math.floor((Path(root) / name).stat().st_mtime) + 1
            except OSError:
                continue
            for entry in cached:
                # <stem>.<tag>.pyc or <stem>.<tag>.opt-N.pyc, never another module's cache
                if not entry.startswith(stem + "."):
                    continue
                if not 2 <= len(entry[len(stem) + 1 :].split(".")) <= 3:
                    continue
                with contextlib.suppress(OSError):
                    if (cache / entry).stat().st_mtime < trusted_from:
                        (cache / entry).unlink()
                        removed += 1
    return removed


# --------------------------------------------------------------------------------------------
# Configuration access (Config keeps unknown sections as extra fields)
# --------------------------------------------------------------------------------------------


def config_section(config: Any, name: str) -> dict[str, Any]:
    """A top-level table of the merged configuration as a plain dict ({} when absent)."""
    if config is None:
        return {}
    value = getattr(config, name, None)
    if value is None and isinstance(config, BaseModel) and config.model_extra:
        value = config.model_extra.get(name)
    if isinstance(value, BaseModel):
        value = value.model_dump()
    return dict(value) if isinstance(value, Mapping) else {}


def track_settings(config: Any, track: str) -> dict[str, Any]:
    """`[tracks.<track>]` from the configuration ({} when absent)."""
    value = config_section(config, "tracks").get(track)
    if isinstance(value, BaseModel):
        value = value.model_dump()
    return dict(value) if isinstance(value, Mapping) else {}


# --------------------------------------------------------------------------------------------
# The project interpreter and its environment
# --------------------------------------------------------------------------------------------


def venv_python(repo: Path) -> Path | None:
    """The interpreter of the repository's `.venv`, if there is one."""
    if sys.platform == "win32":
        candidate = repo / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = repo / ".venv" / "bin" / "python"
    return candidate if candidate.is_file() else None


def source_checkout(repo: Path) -> Path | None:
    """The checkout a task worktree was made from, or None when `repo` is not a worktree:
    worktrees live at `<checkout>/.stepswitch/wt/<id>` (stepswitch.worktree)."""
    parent = Path(repo).absolute().parent
    if parent.name == "wt" and parent.parent.name == STATE_DIR_NAME:
        return parent.parent.parent
    return None


def resolve_interpreter(repo: Path, value: str, *, source: Path | None = None) -> str:
    """A configured interpreter: a path (relative to the repository) or a name on PATH.

    A relative path missing from `repo` resolves against `source` (the checkout a worktree
    was made from) when it exists there.
    """
    text = os.path.expandvars(value.strip())
    path = Path(text).expanduser()
    if path.is_absolute():
        return str(path)
    if len(path.parts) > 1 or text.startswith("."):
        candidate = (repo / path).resolve()
        if not candidate.exists() and source is not None and (source / path).exists():
            candidate = (source / path).resolve()
        return str(candidate)
    return shutil.which(text) or text


def project_python(
    repo: Path,
    config: Any = None,
    track: str = "general",
    *,
    science_python: str | None = None,
) -> str:
    """The interpreter that runs the project's tools and tests (see the module docstring).

    `science_python` is the `[science].python` value of science.toml, used for the science
    track after the configuration's own settings.
    """
    candidates: list[Any] = [track_settings(config, track).get("python")]
    if track == "science":
        candidates += [config_section(config, "science").get("python"), science_python]
    source = source_checkout(repo)
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return resolve_interpreter(repo, value, source=source)
    venv = venv_python(repo)
    if venv is None and source is not None:
        venv = venv_python(source)
    return str(venv) if venv is not None else sys.executable


def _within(path: str | Path, root: Path) -> bool:
    """True when `path` lies under `root` (symlinks are not followed: a venv's python links
    to its base interpreter)."""
    if not Path(path).is_absolute():
        return False
    return Path(path).absolute().is_relative_to(Path(root).absolute())


def project_overrides(
    python: str, repo: Path | None = None, *, base: Mapping[str, str] | None = None
) -> dict[str, str]:
    """The variables project commands need on top of `base` (default: this process's
    environment): the interpreter's venv first on PATH (with VIRTUAL_ENV), and for a working
    tree `repo` with a `src/` directory and an interpreter outside it, that `src/` first on
    PYTHONPATH (see the module docstring)."""
    source = os.environ if base is None else base
    found: dict[str, str] = {}
    exe = Path(python)
    venv = exe.parent.parent
    if exe.is_absolute() and (venv / "pyvenv.cfg").is_file():
        found["VIRTUAL_ENV"] = str(venv)
        found["PATH"] = str(exe.parent) + os.pathsep + source.get("PATH", "")
    if repo is not None and (Path(repo) / "src").is_dir() and not _within(python, Path(repo)):
        paths = [str(Path(repo).absolute() / "src"), source.get("PYTHONPATH", "")]
        found["PYTHONPATH"] = os.pathsep.join(part for part in paths if part)
    return found


def project_env(
    python: str,
    *,
    repo: Path | None = None,
    extra: Mapping[str, str] | None = None,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """The environment for project commands: `base` (default: this process's environment)
    with `project_overrides` for the interpreter and the working tree `repo`.

    PYTHONIOENCODING is set to UTF-8 (unless already set) so captured output decodes the same
    way on every platform; nothing else about the process changes.
    """
    env = dict(os.environ if base is None else base)
    env.update(project_overrides(python, repo, base=env))
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if extra:
        env.update(extra)
    return env


# --------------------------------------------------------------------------------------------
# Tool probes
# --------------------------------------------------------------------------------------------

# Runs inside the project interpreter, which may be older than Stepswitch's: keep it 3.8+.
_PROBE = """\
import json, sys
out = {"modules": {}, "python": sys.version.split()[0]}
try:
    import importlib.util
    for name in sys.argv[1:]:
        try:
            out["modules"][name] = importlib.util.find_spec(name) is not None
        except Exception:
            out["modules"][name] = False
except Exception:
    pass
try:
    import platform
    out["implementation"] = platform.python_implementation()
except Exception:
    pass
try:
    import hashlib
    import importlib.metadata as md
    names = set()
    for dist in md.distributions():
        name = (dist.metadata["Name"] or "").lower()
        if name:
            names.add(name + "==" + dist.version)
    names = sorted(names)
    out["packages"] = len(names)
    out["packages_sha256"] = hashlib.sha256("\\n".join(names).encode()).hexdigest()
    versions = {}
    for name in KEY_PACKAGES:
        try:
            versions[name] = md.version(name)
        except Exception:
            pass
    out["versions"] = versions
except Exception:
    pass
try:
    import sysconfig
    paths = sysconfig.get_paths()
    out["site"] = sorted({paths.get("purelib", ""), paths.get("platlib", "")} - {""})
except Exception:
    pass
print(json.dumps(out))
""".replace("KEY_PACKAGES", repr(KEY_PACKAGES))

# (python, PYTHONPATH, cwd) -> (probe result, [(path, mtime_ns)] that invalidate it)
_PROBE_CACHE: dict[tuple[str, str, str], tuple[dict[str, Any], list[tuple[str, int]]]] = {}


def _mtime(path: str) -> int:
    try:
        return Path(path).stat().st_mtime_ns
    except OSError:
        return -1


def probe_interpreter(python: str, *, cwd: Path, env: Mapping[str, str]) -> dict[str, Any]:
    """Facts about the project interpreter: tool modules present, versions, package set.

    Cached per interpreter, PYTHONPATH and directory until the interpreter or its
    site-packages directories change. A failed probe returns {"error": ...}.
    """
    key = (python, env.get("PYTHONPATH", ""), str(cwd))
    cached = _PROBE_CACHE.get(key)
    if cached is not None and all(_mtime(path) == stamp for path, stamp in cached[1]):
        return cached[0]
    result = run([python, "-c", _PROBE, *PROBE_MODULES], cwd=cwd, timeout=PROBE_TIMEOUT, env=env)
    info: dict[str, Any] = {}
    if result.ok:
        lines = result.stdout.strip().splitlines()
        with contextlib.suppress(ValueError, IndexError):
            parsed = json.loads(lines[-1])
            if isinstance(parsed, dict):
                info = parsed
    if not info:
        reason = result.start_error or result.output.strip()[-400:] or "no output"
        return {"error": f"cannot run the project interpreter {python}: {reason}"}
    watched = [python, *[str(site) for site in info.get("site", [])]]
    _PROBE_CACHE[key] = (info, [(path, _mtime(path)) for path in watched])
    return info


@dataclass
class Tools:
    """Tool lookup in the project environment for one gate run (probes run lazily)."""

    python: str
    cwd: Path
    env: dict[str, str]
    _info: dict[str, Any] | None = field(default=None, repr=False)

    @property
    def info(self) -> dict[str, Any]:
        if self._info is None:
            self._info = probe_interpreter(self.python, cwd=self.cwd, env=self.env)
        return self._info

    @property
    def error(self) -> str | None:
        error = self.info.get("error")
        return str(error) if error else None

    def has(self, module: str) -> bool:
        """True when `module` is importable by the project interpreter."""
        modules = self.info.get("modules", {})
        if module in modules:
            return bool(modules[module])
        check = (
            f"import importlib.util, sys; sys.exit(importlib.util.find_spec({module!r}) is None)"
        )
        return run([self.python, "-c", check], cwd=self.cwd, timeout=PROBE_TIMEOUT, env=self.env).ok

    def which(self, program: str) -> str | None:
        return shutil.which(program, path=self.env.get("PATH"))

    def command(self, tool: str) -> list[str] | None:
        """argv prefix for a tool: `<python> -m <tool>`, else the program on PATH, else None.

        pytest only counts from the project interpreter: another environment's pytest would not
        see the project's dependencies. A mypy from PATH is pointed at the project interpreter.
        """
        if self.has(tool):
            return [self.python, "-m", tool]
        if tool == "pytest":
            return None
        program = self.which(tool)
        if program is None:
            return None
        if tool == "mypy":
            return [program, "--python-executable", self.python]
        return [program]
