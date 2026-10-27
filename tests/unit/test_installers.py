"""The installers (`install.sh`, `install.ps1`): pinned versions, the plan they print, and the
install flow with a stand-in uv. The real install runs in the C-017 container
(tests/claims/test_c017_quickstart.py); nothing here downloads anything."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = ROOT / "install.sh"
INSTALL_PS1 = ROOT / "install.ps1"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
# Any real download fails: curl and wget send it to a closed port.
NO_DOWNLOADS = dict.fromkeys(
    ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"), "http://127.0.0.1:9"
)


def posix_sh() -> str | None:
    """`sh`. On Windows, Git's bin\\sh.exe is a launcher that puts Git's own directories, with a
    real curl in them, before the PATH it is given, so take the shell it launches instead."""
    found = shutil.which("sh")
    if found and sys.platform == "win32":
        inner = Path(found).parent.parent / "usr" / "bin" / "sh.exe"
        if Path(found).parent.name.lower() == "bin" and inner.is_file():
            return str(inner)
    return found


SH = posix_sh()


def pinned(pattern: str, path: Path) -> str:
    match = re.search(pattern, path.read_text(encoding="utf-8"), re.M)
    assert match, (pattern, path.name)
    return match.group(1)


def test_both_installers_pin_the_same_uv_and_the_package_version() -> None:
    uv_sh = pinned(r'^UV_VERSION="([^"]+)"', INSTALL_SH)
    uv_ps1 = pinned(r"^\$UvVersion = '([^']+)'", INSTALL_PS1)
    assert uv_sh == uv_ps1
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    base = re.match(r"\d+(?:\.\d+)*", version)
    assert base is not None
    assert pinned(r'^STEPSWITCH_VERSION="([^"]+)"', INSTALL_SH) == base.group(0)
    assert pinned(r"^\$StepswitchVersion = '([^']+)'", INSTALL_PS1) == base.group(0)
    hashes = re.findall(r"[0-9a-f]{64}", INSTALL_PS1.read_text(encoding="utf-8"))
    assert len(hashes) == 2  # one uv archive per Windows CPU
    sh_pin = re.search(
        r'UV_INSTALLER_SHA256="[0-9a-f]{64}"', INSTALL_SH.read_text(encoding="utf-8")
    )
    assert sh_pin is not None


def test_the_installers_use_lf_line_endings() -> None:
    assert b"\r\n" not in INSTALL_SH.read_bytes()


needs_sh = pytest.mark.skipif(SH is None, reason="no POSIX sh on this machine")


def system_dirs() -> list[str]:
    """`sh`'s directory and the POSIX system directories, none with a uv in it. On macOS `sh`
    is in /bin, but `mktemp`, `awk` and `shasum` are in /usr/bin."""
    assert SH is not None
    dirs = [str(Path(SH).parent)]
    for name in ("/usr/bin", "/bin"):
        path = Path(name)
        if sys.platform != "win32" and path.is_dir() and not (path / "uv").exists():
            dirs.append(name)
    return list(dict.fromkeys(dirs))


def sh_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    """A small environment: `sh` and the system tools on PATH, no uv, HOME in `tmp_path`, and
    no way to download anything."""
    env = {
        "PATH": os.pathsep.join([str(tmp_path / "fakebin"), *system_dirs()]),
        "HOME": str(tmp_path / "home"),
        **NO_DOWNLOADS,
    }
    if sys.platform == "win32":
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", r"C:\Windows")
    (tmp_path / "home").mkdir(exist_ok=True)
    (tmp_path / "fakebin").mkdir(exist_ok=True)
    env.update(extra)
    return env


def run_sh(tmp_path: Path, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
    assert SH is not None
    return subprocess.run(
        [SH, str(INSTALL_SH), *args],
        env=sh_env(tmp_path, **extra),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("#!/bin/sh\n" + body).encode("utf-8"))
    path.chmod(0o755)
    return path


@needs_sh
def test_install_sh_parses() -> None:
    assert SH is not None
    done = subprocess.run([SH, "-n", str(INSTALL_SH)], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr


@needs_sh
def test_install_sh_dry_run_plans_the_pinned_uv(tmp_path: Path) -> None:
    done = run_sh(tmp_path, "--dry-run")
    assert done.returncode == 0, done.stderr
    assert "uv: 0.13.1 from https://astral.sh/uv/0.13.1/install.sh (UV_NO_MODIFY_PATH=1)" in (
        done.stdout
    )
    assert "Stepswitch: uv tool install stepswitch==" in done.stdout
    assert "then: ssw prefetch" in done.stdout and "dry run: nothing was changed" in done.stdout
    custom = run_sh(
        tmp_path, "--dry-run", STEPSWITCH_PACKAGE="/dist/x.whl", STEPSWITCH_NO_PREFETCH="1"
    )
    assert "uv tool install /dist/x.whl" in custom.stdout and "prefetch" not in custom.stdout


@needs_sh
def test_install_sh_options(tmp_path: Path) -> None:
    assert "Usage: sh install.sh" in run_sh(tmp_path, "--help").stdout
    bad = run_sh(tmp_path, "--bogus")
    assert bad.returncode == 1 and "unknown option: --bogus" in bad.stderr


@needs_sh
def test_install_sh_installs_with_the_uv_it_finds(tmp_path: Path) -> None:
    log, bin_dir = tmp_path / "calls.log", tmp_path / "tools-bin"
    executable(
        tmp_path / "home" / ".local" / "bin" / "uv",
        f'echo "uv $*" >> "{log.as_posix()}"\n'
        'case "$1 $2" in\n'
        f'  "tool install") mkdir -p "{bin_dir.as_posix()}"\n'
        f'    printf \'#!/bin/sh\\necho "ssw $*" >> "{log.as_posix()}"\\n\' > '
        f'"{bin_dir.as_posix()}/ssw"\n'
        f'    chmod +x "{bin_dir.as_posix()}/ssw" ;;\n'
        f'  "tool dir") echo "{bin_dir.as_posix()}" ;;\n'
        "esac\n",
    )
    done = run_sh(tmp_path, STEPSWITCH_PACKAGE="/dist/stepswitch.whl")
    assert done.returncode == 0, (done.stdout, done.stderr)
    calls = log.read_text(encoding="utf-8").splitlines()
    assert calls == [
        "uv tool install /dist/stepswitch.whl",
        "uv tool dir --bin",
        "ssw --version",
        "ssw prefetch",
    ]
    assert f"add {bin_dir.as_posix()} to your PATH" in done.stdout


@needs_sh
def test_install_sh_refuses_a_uv_installer_with_another_hash(tmp_path: Path) -> None:
    # A stand-in curl "downloads" something that is not the pinned installer.
    executable(
        tmp_path / "fakebin" / "curl",
        'while [ $# -gt 0 ]; do [ "$1" = -o ] && out="$2"; shift; done\n'
        'echo "echo not the uv installer" > "$out"\n',
    )
    done = run_sh(tmp_path, STEPSWITCH_NO_PREFETCH="1")
    assert done.returncode == 1
    assert "the uv installer's SHA-256 is" in done.stderr and "not the pinned" in done.stderr
    assert not (tmp_path / "home" / ".local" / "bin" / "uv").exists()


needs_powershell = pytest.mark.skipif(POWERSHELL is None, reason="no PowerShell on this machine")


def run_ps1(tmp_path: Path, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
    assert POWERSHELL is not None
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("UV_", "STEPSWITCH"))}
    # PATH without any uv: the PowerShell directory and the system directories only.
    system = [os.environ.get("SYSTEMROOT", r"C:\Windows")]
    env["PATH"] = os.pathsep.join(
        [str(Path(POWERSHELL).parent), *(str(Path(s) / "System32") for s in system)]
    )
    env["UV_INSTALL_DIR"] = str(tmp_path / "uv-bin")
    env.update(extra)
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(INSTALL_PS1), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@needs_powershell
def test_install_ps1_parses() -> None:
    assert POWERSHELL is not None
    script = (
        "$errors = $null; "
        f"[System.Management.Automation.Language.Parser]::ParseFile('{INSTALL_PS1}', "
        "[ref]$null, [ref]$errors) | Out-Null; "
        "if ($errors) { $errors | ForEach-Object { $_.Message }; exit 1 }"
    )
    done = subprocess.run(
        [POWERSHELL, "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr


@needs_powershell
def test_install_ps1_points_other_systems_to_install_sh(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("install.ps1 runs here; the dry run below covers it")
    done = run_ps1(tmp_path, "-DryRun")
    assert done.returncode == 1, done.stdout + done.stderr
    assert "this installer is for Windows; on Linux and macOS, use install.sh" in done.stdout
    assert not (tmp_path / "uv-bin").exists()


@needs_powershell
@pytest.mark.skipif(sys.platform != "win32", reason="install.ps1 is the Windows installer")
def test_install_ps1_dry_run_plans_the_pinned_uv_archive(tmp_path: Path) -> None:
    done = run_ps1(tmp_path, "-DryRun")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "https://github.com/astral-sh/uv/releases/download/0.13.1/uv-" in done.stdout
    assert "Stepswitch: uv tool install stepswitch==" in done.stdout
    assert "dry run: nothing was changed" in done.stdout
    custom = run_ps1(
        tmp_path, "-DryRun", STEPSWITCH_PACKAGE="C:\\dist\\x.whl", STEPSWITCH_NO_PREFETCH="1"
    )
    assert "uv tool install C:\\dist\\x.whl" in custom.stdout and "prefetch" not in custom.stdout
    assert not (tmp_path / "uv-bin").exists()
