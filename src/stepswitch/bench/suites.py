"""Bench suites (SPEC §16): YAML files listing tasks on bundled or local repositories.

A suite is `bench/suites/<name>.yaml` in the project, or a built-in suite shipped inside the
package (`stepswitch/_bench/`), or any YAML file given by path. Task paths (`repo`, `golden`)
are relative to the directory that holds `bench/`, so a suite and its repositories move
together.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

PACKAGE_DIR = Path(__file__).resolve().parents[1]
# Installed: shipped inside the package; source checkout: the repository's bench/ directory.
BUNDLED_ROOTS = (PACKAGE_DIR / "_bench", PACKAGE_DIR.parents[1])
TRACKS = ("general", "science")
REQUIRED_KEYS = ("id", "repo", "instruction", "acceptance", "track", "tags")
# Paths an agent must not change to pass: they are restored before the acceptance commands.
DEFAULT_PROTECTED = ("tests/",)


class SuiteError(ValueError):
    pass


@dataclass(frozen=True)
class BenchTask:
    id: str
    repo: Path
    instruction: str
    acceptance: tuple[str, ...]
    track: str
    tags: tuple[str, ...]
    golden: Path | None = None
    max_steps: int | None = None
    budgets: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Suite:
    name: str
    description: str
    path: Path
    tasks: tuple[BenchTask, ...]
    protected: tuple[str, ...] = DEFAULT_PROTECTED

    def select(self, ids: list[str] | None) -> Suite:
        if not ids:
            return self
        known = {task.id: task for task in self.tasks}
        missing = [task_id for task_id in ids if task_id not in known]
        if missing:
            raise SuiteError(f"unknown task(s) in {self.name}: {', '.join(missing)}")
        return Suite(
            self.name,
            self.description,
            self.path,
            tuple(known[task_id] for task_id in ids),
            self.protected,
        )


def suite_path(name: str, root: Path) -> Path:
    """The YAML file of suite `name`: a path, the project's `bench/suites/`, or a built-in."""
    given = Path(name)
    if given.suffix in {".yaml", ".yml"} and given.is_file():
        return given.resolve()
    for base in (root, *BUNDLED_ROOTS):
        candidate = base / "bench" / "suites" / f"{name}.yaml"
        if base == PACKAGE_DIR / "_bench":
            candidate = base / "suites" / f"{name}.yaml"
        if candidate.is_file():
            return candidate.resolve()
    raise SuiteError(f"no suite named {name!r} (looked in bench/suites/ and the built-in suites)")


def _home(path: Path) -> Path:
    """The directory task paths are relative to: the parent of `bench/` (or of `suites/` for
    the packaged copy)."""
    suites_dir = path.parent
    if suites_dir.name == "suites":
        if suites_dir.parent.name == "bench":
            return suites_dir.parent.parent
        if suites_dir.parent.name == "_bench":
            return suites_dir.parent
    return suites_dir


def _resolve(home: Path, value: str, *, packaged: bool) -> Path:
    relative = Path(value)
    if relative.is_absolute():
        return relative
    if packaged and relative.parts[:1] == ("bench",):
        relative = Path(*relative.parts[1:])
    return (home / relative).resolve()


def load_suite(name: str, root: Path) -> Suite:
    path = suite_path(name, root)
    data = YAML(typ="safe", pure=True).load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SuiteError(f"{path.name}: expected a mapping at the top level")
    if data.get("schema", 1) != 1:
        raise SuiteError(f"{path.name}: unsupported schema {data.get('schema')!r}")
    home = _home(path)
    packaged = path.parent.parent.name == "_bench"
    tasks = []
    seen: set[str] = set()
    for index, raw in enumerate(data.get("tasks") or []):
        if not isinstance(raw, dict):
            raise SuiteError(f"{path.name}: task {index + 1} is not a mapping")
        missing = [key for key in REQUIRED_KEYS if key not in raw]
        if missing:
            raise SuiteError(f"{path.name}: task {raw.get('id', index + 1)} lacks {missing}")
        task_id = str(raw["id"])
        if task_id in seen:
            raise SuiteError(f"{path.name}: duplicate task id {task_id}")
        seen.add(task_id)
        if raw["track"] not in TRACKS:
            raise SuiteError(f"{path.name}: task {task_id} has unknown track {raw['track']!r}")
        acceptance = raw["acceptance"]
        if isinstance(acceptance, str):
            acceptance = [acceptance]
        tasks.append(
            BenchTask(
                id=task_id,
                repo=_resolve(home, str(raw["repo"]), packaged=packaged),
                instruction=str(raw["instruction"]).strip(),
                acceptance=tuple(str(command) for command in acceptance),
                track=str(raw["track"]),
                tags=tuple(str(tag) for tag in raw.get("tags") or ()),
                golden=_resolve(home, str(raw["golden"]), packaged=packaged)
                if raw.get("golden")
                else None,
                max_steps=int(raw["max_steps"]) if raw.get("max_steps") else None,
                budgets=dict(raw.get("budgets") or {}),
            )
        )
    if not tasks:
        raise SuiteError(f"{path.name}: no tasks")
    protected = data.get("protected")
    return Suite(
        name=str(data.get("name") or path.stem),
        description=str(data.get("description") or ""),
        path=path,
        tasks=tuple(tasks),
        protected=tuple(protected) if isinstance(protected, list) else DEFAULT_PROTECTED,
    )
