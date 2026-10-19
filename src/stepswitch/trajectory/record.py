"""Trajectory recording (SPEC §11, ARCHITECTURE §10).

Every run gets a directory `.stepswitch/runs/<task id>/` with `events.jsonl`: one JSON object
per line, each with `seq`, `ts`, `type` and the event's fields. Events are also stored in the
`events` table of the project state database, which the dashboard reads. Large payloads (raw
tool outputs, diffs) go to the content-addressed blob store `.stepswitch/blobs/<aa>/<sha256>`
and events refer to them by hash.

Event types written by the built-in agent: task_start, catalog, classify, learning, ladder,
decision, switch, step, aux, tool, compress, context, handoff, gates, escalation, review,
model_error, budget, task_end (and best_of_n, candidate_start, candidate_end and selection for
best-of-N). A slow-lane review job appends slow_review and review_comment events to a finished
task (`core.jobs`). Model answers (`response_blob`), long tool outputs (`output_blob`,
`raw_blob`) and compressions (`output_blob`) are kept whole as blobs, so `trajectory.replay` can
replay a run.
"""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from stepswitch import paths
from stepswitch.state import StateDB

EVENTS_FILE = "events.jsonl"


def runs_dir(root: Path) -> Path:
    return paths.state_dir(root) / "runs"


def blobs_dir(root: Path) -> Path:
    return paths.state_dir(root) / "blobs"


def put_blob(root: Path, data: bytes | str) -> str:
    """Store `data` by its sha256 and return the hash."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    digest = hashlib.sha256(raw).hexdigest()
    target = blobs_dir(root) / digest[:2] / digest
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        partial.write_bytes(raw)
        partial.replace(target)
    return digest


def get_blob(root: Path, digest: str) -> bytes:
    return (blobs_dir(root) / digest[:2] / digest).read_bytes()


class Trajectory:
    """Appends events for one task to its run directory and the state database."""

    def __init__(
        self,
        root: Path,
        task_id: str,
        *,
        db: StateDB | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = root
        self.task_id = task_id
        self.db = db
        self.clock = clock
        self.run_dir = runs_dir(root) / task_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / EVENTS_FILE
        self.seq = 0
        self._lock = threading.Lock()
        self.listeners: list[Callable[[dict[str, Any]], None]] = []
        # Fields added to every event of a view (`tagged`); the owner keeps the sequence.
        self.fields: dict[str, Any] = {}
        self._owner: Trajectory | None = None

    @classmethod
    def resume(cls, root: Path, task_id: str, *, db: StateDB | None = None) -> Trajectory:
        """The trajectory of an earlier task, to append to (a slow-lane review's comments):
        its sequence continues from the last recorded event."""
        trajectory = cls(root, task_id, db=db)
        for event in iter_events(trajectory.run_dir):
            trajectory.seq = max(trajectory.seq, int(event.get("seq") or 0))
        return trajectory

    def tagged(self, **fields: Any) -> Trajectory:
        """A view whose events also carry `fields` (a best-of-N candidate's index, say). It
        writes to the same files and shares this trajectory's sequence numbers."""
        view = copy.copy(self)
        view.fields = {**self.fields, **fields}
        view._owner = self._owner or self
        return view

    def emit(self, type_: str, *, step: int | None = None, **data: Any) -> dict[str, Any]:
        owner = self._owner or self
        with owner._lock:
            owner.seq += 1
            seq = owner.seq
            event = {"seq": seq, "ts": round(self.clock(), 3), "type": type_}
            if step is not None:
                event["step"] = step
            event.update(self.fields)
            event.update(data)
            line = json.dumps(event, sort_keys=False, default=str, ensure_ascii=False)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
            if self.db is not None:
                self.db.execute(
                    "INSERT INTO events (task_id, ts, seq, type, step, data)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (self.task_id, event["ts"], seq, type_, step, line),
                )
        for listener in list(owner.listeners):
            listener(event)
        return event

    def blob(self, data: bytes | str) -> str:
        return put_blob(self.root, data)

    def write_file(self, name: str, text: str) -> Path:
        path = self.run_dir / name
        path.write_text(text, encoding="utf-8", newline="\n")
        return path


def iter_events(run_dir: Path) -> Iterator[dict[str, Any]]:
    path = Path(run_dir) / EVENTS_FILE
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def read_events(run_dir: Path) -> list[dict[str, Any]]:
    """All events of a run, in order."""
    return list(iter_events(run_dir))
