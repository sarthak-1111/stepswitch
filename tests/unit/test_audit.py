"""The egress audit log (SPEC §15): bytes on the wire for every request (streams included), the
processes Stepswitch starts, and `ssw audit` over both."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
from typer.testing import CliRunner

from stepswitch import net
from stepswitch.cli import app
from stepswitch.state import open_project_state


def test_streamed_responses_are_counted_when_their_body_closes() -> None:
    def chunks() -> Iterator[bytes]:
        yield b"data: one\n\n"
        yield b"data: two\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=httpx.ByteStream(b"".join(chunks())))

    egress = net.Egress()
    with (
        egress.client("model", transport=httpx.MockTransport(handler)) as client,
        client.stream("POST", "http://127.0.0.1:9/v1/x", json={"a": 1}) as response,
    ):
        assert list(egress.memory) == []  # written when the body closes
        body = b"".join(response.iter_raw())
    (row,) = list(egress.memory)
    assert row.bytes_in == len(body) == 22
    assert row.bytes_out == len(b'{"a":1}')
    assert row.status == 200 and row.error is None


def test_streamed_request_bodies_are_counted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        request.read()
        return httpx.Response(204)

    def upload() -> Iterator[bytes]:
        yield b"x" * 1000
        yield b"y" * 24

    egress = net.Egress()
    with egress.client("model", transport=httpx.MockTransport(handler)) as client:
        client.post("http://127.0.0.1:9/upload", content=upload())
    (row,) = list(egress.memory)
    assert row.bytes_out == 1024 and row.bytes_in == 0


def test_requests_without_a_client_task_take_the_threads_task() -> None:
    egress = net.Egress()
    token = net.CURRENT_TASK.set("t-audit")
    try:
        transport = httpx.MockTransport(lambda _r: httpx.Response(200, json={}))
        with egress.client("model", transport=transport) as client:
            client.get("http://127.0.0.1:9/")
    finally:
        net.CURRENT_TASK.reset(token)
    assert [row.task_id for row in egress.memory] == ["t-audit"]


def test_started_processes_are_recorded_with_their_task_and_without_secrets() -> None:
    seen: list[net.ProcessRow] = []
    net.audit_processes(sink=seen.append)
    token = net.CURRENT_TASK.set("t-proc")
    try:
        subprocess.run(
            [sys.executable, "-c", "pass", "--api-key", "abc"], check=True, capture_output=True
        )
    finally:
        net.CURRENT_TASK.reset(token)
    row = next(r for r in seen if r.task_id == "t-proc")
    assert row.executable == Path(sys.executable).name
    assert row.argv[-2:] == ["--api-key", "[REDACTED:flag]"]
    assert row.pid and row.pid > 0
    assert row in net.processes()


def test_ssw_audit_filters_by_task_and_lists_processes(tmp_path: Path) -> None:
    (tmp_path / ".stepswitch").mkdir()
    db = open_project_state(tmp_path)
    base = {"method": "GET", "scheme": "http", "host": "127.0.0.1", "port": 1, "path": "/"}
    for task in ("t-a", "t-b"):
        db.record_audit(
            {
                **base,
                "ts": 1.0,
                "purpose": "model",
                "task_id": task,
                "allowed": 1,
                "local": 1,
                "status": 200,
                "bytes_out": 10,
                "bytes_in": 20,
            }
        )
    db.record_process(
        {
            "ts": 2.0,
            "task_id": "t-a",
            "executable": "git",
            "argv": ["git", "status"],
            "cwd": None,
            "pid": 7,
            "offline": True,
        }
    )
    db.close()
    runner = CliRunner()
    shown = runner.invoke(app, ["--project", str(tmp_path), "audit", "--task", "t-a", "--json"])
    assert shown.exit_code == 0, shown.output
    rows = json.loads(shown.output)["rows"]
    assert [r["task_id"] for r in rows] == ["t-a"] and rows[0]["bytes_in"] == 20
    text = runner.invoke(app, ["--project", str(tmp_path), "audit"])
    assert "10/20 B" in text.output and "t-b" in text.output
    procs = runner.invoke(app, ["--project", str(tmp_path), "audit", "--processes", "--json"])
    assert json.loads(procs.output)["rows"][0]["argv"] == ["git", "status"]
