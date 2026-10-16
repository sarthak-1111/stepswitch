"""Deterministic fake contestants for the bench (SPEC §16), so the bench machinery runs in CI
without models. They drive the real agent loop, toolbox and gates through scripted turns:

* `fake:oracle` solves a task from its golden patch: it writes each changed file whole with
  `edit`, runs the tests and calls `finish`.
* `fake:noop` calls `finish` at once and changes nothing.

Every turn reports the same usage, so token totals are exact.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Callable
from pathlib import Path

from stepswitch.bench import workspace
from stepswitch.bench.suites import BenchTask
from stepswitch.compat.ir import Request, Usage
from stepswitch.workers.base import Worker, WorkerSpec
from stepswitch.workers.catalog import Catalog
from stepswitch.workers.fake import Action, ScriptedModel

PREFIX = "fake:"
KINDS = ("oracle", "noop")
TURN_USAGE = Usage(input_tokens=400, output_tokens=40)
_TARGET = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)
_SOURCE = re.compile(r"^--- a/(.+)$", re.MULTILINE)


def worker_id(kind: str) -> str:
    return f"fake/{kind}"


def golden_files(task: BenchTask) -> dict[str, str]:
    """Files the task's golden patch writes -> their content after the patch."""
    if task.golden is None:
        raise ValueError(f"task {task.id} has no golden patch for fake:oracle")
    patch = task.golden.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="ssw-golden-") as tmp:
        copy = workspace.copy_repo(task.repo, Path(tmp) / "repo")
        workspace.init_repo(copy)
        workspace.apply_diff(copy, patch)
        written = sorted(set(_TARGET.findall(patch)))
        deleted = set(_SOURCE.findall(patch)) - set(written)
        if deleted:
            raise ValueError(f"fake:oracle cannot delete files ({', '.join(sorted(deleted))})")
        return {
            path: (copy / path).read_text(encoding="utf-8").replace("\r\n", "\n")
            for path in written
        }


def _text_answer(request: Request) -> Action:
    step = request.system_text().split("Step: ", 1)[-1].split("\n", 1)[0].strip()
    return Action(text={"review": "APPROVE"}.get(step, "Done."))


def oracle_script(task: BenchTask) -> Callable[[Request, int], Action]:
    files = golden_files(task)
    actions = [
        Action(
            tool="edit",
            arguments={"path": path, "text": text}
            | ({} if (task.repo / path).exists() else {"create": True}),
        )
        for path, text in files.items()
    ]
    actions += [
        Action(tool="test", arguments={}),
        Action(tool="finish", arguments={"summary": "Applied the reference change."}),
    ]

    def script(request: Request, _index: int) -> Action:
        if not request.tools:
            return _text_answer(request)
        done = sum(1 for message in request.messages if message.role == "tool")
        return actions[min(done, len(actions) - 1)]

    return script


def noop_script(_task: BenchTask) -> Callable[[Request, int], Action]:
    def script(request: Request, _index: int) -> Action:
        if not request.tools:
            return _text_answer(request)
        return Action(tool="finish", arguments={"summary": "Nothing to change."})

    return script


SCRIPTS = {"oracle": oracle_script, "noop": noop_script}


def catalog_for(kind: str, task: BenchTask) -> Catalog:
    """A catalog with the one fake worker, scripted for `task`."""
    if kind not in SCRIPTS:
        raise ValueError(f"unknown fake contestant fake:{kind} (known: {', '.join(KINDS)})")
    spec = WorkerSpec(
        id=worker_id(kind),
        harness="fake",
        endpoint="fake",
        model=kind,
        tier="t2",
        family="fake",
        local=True,
        tool_protocol="native",
    )
    model = ScriptedModel(SCRIPTS[kind](task), usage=TURN_USAGE, model=kind)
    return Catalog([Worker(spec, model)])
