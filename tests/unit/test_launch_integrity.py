"""Integrity fixes found by the Phase 11 runs (ADR 0037).

- An Ollama reply that is not complete (`done` false) is a model error, not an empty answer.
  Ollama 0.34.4 answered so for a model that had stopped working (RESEARCH, Phase 12).
- Ollama native requests carry the worker's context as `num_ctx` (SPEC §4.2, ADR 0008), so the
  server's default never decides it.
- A review that yields nothing usable is no review. It is never an approval.
- `ssw bench run` sends every local worker a canary request before the first task. A worker
  that answers anything but the canary word stops the bench, and the manifest records the
  check.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from stepswitch import net
from stepswitch.bench import preflight, runner
from stepswitch.bench.report import MANIFEST_FILE, RESULTS_FILE
from stepswitch.bench.suites import load_suite
from stepswitch.compat.codecs.base import CodecOptions
from stepswitch.compat.codecs.ollama import OllamaCodec
from stepswitch.compat.errors import ModelError
from stepswitch.compat.ir import ConversionReport, Message, Request
from stepswitch.config import Config
from stepswitch.workers.catalog import Catalog
from tests.conformance.harness import MockServer, Recorded, json_response
from tests.unit.bench_project import make_project
from tests.unit.test_builtin_agent import StubGates, events, fixer_script, make_loop, worker

GARBAGE = "@" * 40


class MockEgress(net.Egress):
    """Egress whose clients all talk to one mock server."""

    def __init__(self, transport: httpx.BaseTransport) -> None:
        super().__init__()
        self._mock = transport

    def client(self, purpose: str, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = kwargs.get("transport") or self._mock
        return super().client(purpose, **kwargs)


def ollama_reply(text: str, *, done: bool = True) -> dict[str, Any]:
    reply: dict[str, Any] = {
        "model": "m",
        "created_at": "2026-10-20T00:00:00Z",
        "message": {"role": "assistant", "content": text},
        "done": done,
    }
    if done:
        reply.update(done_reason="stop", prompt_eval_count=20, eval_count=3)
    return reply


def lab_config(tmp_path: Path, models: dict[str, dict[str, Any]]) -> Config:
    return Config.model_validate(
        {
            "endpoints": [
                {
                    "name": "lab",
                    "url": "http://127.0.0.1:9",
                    "engine": "ollama",
                    "formats": ["ollama-native"],
                    "models": list(models),
                }
            ],
            "models": models,
            "storage": {"models_dir": str(tmp_path / "models")},
        }
    )


def canary_server(answers: dict[str, str]) -> MockServer:
    """Answers each model's requests with its text; a model mapped to "" answers incompletely."""

    def respond(call: Recorded) -> httpx.Response:
        text = answers[call.body["model"]]
        return json_response(ollama_reply(text, done=bool(text)))

    return MockServer().route("POST", r"/api/chat", respond)


def test_an_incomplete_ollama_reply_is_a_model_error() -> None:
    codec = OllamaCodec()
    with pytest.raises(ModelError) as raised:
        codec.decode_response(ollama_reply("", done=False))
    assert raised.value.kind == "server" and "incomplete" in raised.value.message
    assert codec.decode_response(ollama_reply("hello")).message.text_content() == "hello"


def test_ollama_requests_carry_the_workers_context_as_num_ctx(tmp_path: Path) -> None:
    codec = OllamaCodec(CodecOptions(extra={"num_ctx": 16384}))
    plain = Request(model="m", messages=[Message.text("user", "hi")])
    body = codec.encode_request(plain, ConversionReport()).json or {}
    assert body["options"]["num_ctx"] == 16384
    asked = plain.model_copy(update={"extensions": {"num_ctx": 4096}})
    assert (codec.encode_request(asked, ConversionReport()).json or {})["options"][
        "num_ctx"
    ] == 4096
    # The catalog gives an Ollama native worker its context, and the request on the wire has it.
    server = canary_server({"qwen:32b": "READY"})
    config = lab_config(tmp_path, {"qwen:32b": {"context": 16384, "family": "qwen"}})
    catalog = Catalog.from_config(
        config, tmp_path, egress=MockEgress(server.transport()), include_managed=False
    )
    lab = catalog.get("lab/qwen:32b")
    assert (lab.spec.format, lab.spec.context) == ("ollama-native", 16384)
    lab.get_model().complete(plain.model_copy(update={"model": "qwen:32b"}))
    assert server.requests[0].body["options"]["num_ctx"] == 16384


def test_an_empty_review_is_no_review_and_never_an_approval(tmp_path: Path) -> None:
    loop = make_loop(
        tmp_path, [worker("a/m", "t2", fixer_script({"review": ""}))], StubGates([True])
    )
    outcome = loop.run()
    assert outcome.status == "done"
    assert outcome.review is not None
    assert outcome.review["approved"] is None and outcome.review["failed"] is True
    (event,) = events(loop, "review")
    assert event["failed"] is True and event["reason"]
    # Best-of-N asks for the review again: still none, rather than an approval.
    assert loop.review_change() is None
    # A plain answer that is neither a verdict nor the schema's JSON is no review either.
    (tmp_path / "other").mkdir()
    other = make_loop(
        tmp_path / "other",
        [worker("a/m", "t2", fixer_script({"review": "Looks fine to me."}))],
        StubGates([True]),
    )
    review = other.run().review
    assert review is not None and review["failed"] is True


def test_the_canary_tells_a_working_worker_from_a_dead_one(tmp_path: Path) -> None:
    assert preflight.healthy("READY") and preflight.healthy("**Ready.**")
    assert not preflight.healthy(GARBAGE) and not preflight.healthy("")
    server = canary_server({"good:7b": "READY", "dead:32b": GARBAGE, "empty:32b": ""})
    config = lab_config(
        tmp_path, {name: {"family": name} for name in ("good:7b", "dead:32b", "empty:32b")}
    )
    catalog = Catalog.from_config(
        config, tmp_path, egress=MockEgress(server.transport()), include_managed=False
    )
    report = preflight.check_workers(catalog, ["lab/good:7b", "lab/dead:32b", "lab/empty:32b"])
    assert report["ok"] is False
    workers = report["workers"]
    assert workers["lab/good:7b"]["ok"] is True and workers["lab/good:7b"]["reply"] == "READY"
    assert workers["lab/dead:32b"]["ok"] is False and workers["lab/dead:32b"]["reply"].startswith(
        "@@"
    )
    assert (
        workers["lab/empty:32b"]["ok"] is False
        and "incomplete" in workers["lab/empty:32b"]["error"]
    )
    assert preflight.check_workers(catalog, ["lab/good:7b"])["ok"] is True


def test_the_bench_stops_before_the_first_task_when_a_worker_fails_the_canary(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path / "project")
    server = canary_server({"good:7b": "READY", "dead:32b": GARBAGE})
    config = lab_config(tmp_path, {"good:7b": {"family": "good"}, "dead:32b": {"family": "dead"}})
    out = project / "bench" / "results" / "dead"
    with pytest.raises(preflight.PreflightFailed) as raised:
        runner.run_bench(
            load_suite("tiny", project),
            runner.parse_contestants("router"),
            root=project,
            out_dir=out,
            options=runner.BenchOptions(router_pool=["lab/good:7b", "lab/dead:32b"]),
            config=config,
            egress=MockEgress(server.transport()),
        )
    assert "lab/dead:32b" in str(raised.value)
    manifest = json.loads((out / MANIFEST_FILE).read_text(encoding="utf-8"))
    assert manifest["preflight"]["ok"] is False
    assert manifest["preflight"]["workers"]["lab/good:7b"]["ok"] is True
    assert (out / RESULTS_FILE).read_text(encoding="utf-8") == ""
    # Skipping the check is recorded, and such a run is not one the claims accept.
    skipped = runner.BenchOptions(preflight=False)
    fake_out = project / "bench" / "results" / "fake"
    runner.run_bench(
        load_suite("tiny", project),
        runner.parse_contestants("fake:oracle"),
        root=project,
        out_dir=fake_out,
        options=skipped,
    )
    recorded = json.loads((fake_out / MANIFEST_FILE).read_text(encoding="utf-8"))["preflight"]
    assert recorded == {"ok": None, "skipped": True}
