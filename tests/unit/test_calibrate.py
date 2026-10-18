"""`ssw calibrate` (SPEC §14 RN11): throughput arithmetic with deterministic timings."""

from __future__ import annotations

import json
import statistics
from collections.abc import Iterable
from pathlib import Path

import httpx
import pytest

from stepswitch import net
from stepswitch.compat import calibrate
from stepswitch.compat.calibrate import Sample, server_timings
from stepswitch.compat.errors import ModelError
from stepswitch.compat.ir import Usage
from stepswitch.config import EndpointConfig
from tests.conformance.harness import Recorded, json_response
from tests.unit.fixtures.probe.mock_servers import (
    BASE_URL,
    KEY,
    ChatServer,
    OllamaServer,
    chat_config,
    ollama_config,
)


class FakeClock:
    """Returns the given instants in order; three per streamed request (sent, first, end)."""

    def __init__(self, values: Iterable[float]) -> None:
        self._values = iter(values)
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        return next(self._values)


def test_client_clock_rates_from_deterministic_timings() -> None:
    server = ChatServer()
    # Round 1: prefill sent 0.0, first token 0.5, done 0.6; decode sent 1.0, first 1.1, done 3.1.
    # Round 2: prefill first token after 0.25 s; decode as in round 1.
    clock = FakeClock([0.0, 0.5, 0.6, 1.0, 1.1, 3.1, 10.0, 10.25, 10.3, 11.0, 11.1, 13.1])
    lines: list[str] = []
    result = calibrate.run(
        chat_config(),
        egress=net.Egress(),
        echo=lines.append,
        transport=server.transport(),
        repeats=2,
        clock=clock,
    )
    assert clock.calls == 12  # the warm-up request is not timed
    prefill = [s for s in result["samples"] if s["kind"] == "prefill"]
    decode = [s for s in result["samples"] if s["kind"] == "decode"]
    assert [s["ttft_ms"] for s in prefill] == [500.0, 250.0]
    rates = [
        (s["input_tokens"] - s["cached_tokens"]) / seconds
        for s, seconds in zip(prefill, (0.5, 0.25), strict=True)
    ]
    assert result["prefill_tps"] == pytest.approx(statistics.median(rates), abs=0.05)
    assert all(s["input_tokens"] > 1_000 for s in prefill)  # about 1.5K-token prompts
    # 128 tokens: the first after 0.1 s, the other 127 over the next 2 s.
    assert [s["output_tokens"] for s in decode] == [128, 128]
    assert result["decode_tps"] == 63.5
    assert result["ttft_ms"] == 100.0
    assert result["sources"] == {
        "prefill_tps": "client",
        "decode_tps": "client",
        "ttft_ms": "client",
    }
    assert result["endpoint"] == "mock" and result["model"] == "mock-model"
    assert result["format"] == "chat-completions" and result["repeats"] == 2
    assert result["created"][:4].isdigit() and "errors" not in result
    assert json.loads(json.dumps(result)) == result
    # The warm-up plus two rounds of two streamed requests.
    assert len(server.chats) == 5
    assert all(r.body.get("stream") for r in server.chats[1:])
    assert lines[0] == "Calibrating mock / mock-model (chat-completions)"
    assert "decode 63.5 tok/s (client)" in lines[-1]


def test_server_timings_win_and_name_their_source() -> None:
    server = OllamaServer()
    result = calibrate.run(
        ollama_config(),
        egress=net.Egress(),
        echo=lambda _line: None,
        transport=server.transport(),
        repeats=1,
    )
    # The mock reports 1000 prompt tokens/s and 50 generated tokens/s in nanoseconds.
    assert result["prefill_tps"] == 1000.0 and result["decode_tps"] == 50.0
    assert result["sources"]["prefill_tps"] == "server"
    assert result["sources"]["decode_tps"] == "server"
    assert result["engine"] == "ollama" and result["engine_version"] == "0.34.4"
    assert result["format"] == "ollama-native" and result["model"] == "mock:latest"
    assert result["settings"] == {"num_ctx": 8192}
    assert {r.body["options"]["num_ctx"] for r in server.chats} == {8192}


def test_a_failed_sample_is_recorded_and_the_rest_still_count() -> None:
    class NoLongAnswers(ChatServer):
        def chat(self, recorded: Recorded) -> httpx.Response:
            if "lighthouse keeper" in json.dumps(recorded.body):
                error = {"message": "output too long", "type": "invalid_request_error"}
                return json_response({"error": error}, 400)
            return super().chat(recorded)

    lines: list[str] = []
    result = calibrate.run(
        chat_config(),
        egress=net.Egress(),
        echo=lines.append,
        transport=NoLongAnswers().transport(),
        repeats=2,
    )
    assert result["decode_tps"] is None and result["ttft_ms"] is None
    assert result["prefill_tps"] is not None and result["prefill_tps"] > 0
    assert len(result["errors"]) == 2
    assert result["errors"][0].startswith("decode 1: bad_request: output too long")
    assert any("failed" in line for line in lines)


def test_an_unreachable_endpoint_raises() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(ModelError) as raised:
        calibrate.run(
            chat_config(),
            egress=net.Egress(),
            echo=lambda _line: None,
            transport=httpx.MockTransport(refuse),
        )
    assert raised.value.kind == "connection"


def test_results_hold_no_key_or_url_and_use_the_egress_layer() -> None:
    egress = net.Egress()
    config = EndpointConfig(name=BASE_URL, url=BASE_URL, auth="bearer", api_key=KEY, models=["m"])
    server = ChatServer(key=KEY)
    lines: list[str] = []
    result = calibrate.run(
        config, egress=egress, echo=lines.append, transport=server.transport(), repeats=1
    )
    text = json.dumps(result) + "\n".join(lines)
    assert KEY not in text and BASE_URL not in text and str(Path.home()) not in text
    assert result["endpoint"] == "http-local-endpoint"
    assert {row.purpose for row in egress.memory} == {"probe", "calibrate"}
    assert all(r.headers.get("authorization") == f"Bearer {KEY}" for r in server.chats)


def test_server_timings_from_raw_usage() -> None:
    ollama = Usage(
        input_tokens=1500,
        output_tokens=100,
        cached_input_tokens=500,
        raw={
            "prompt_eval_count": 1500,
            "prompt_eval_cached_count": 500,
            "prompt_eval_duration": 2_000_000_000,
            "eval_count": 100,
            "eval_duration": 4_000_000_000,
        },
    )
    assert server_timings(ollama) == {
        "prompt_n": 1000.0,
        "prompt_s": 2.0,
        "predicted_n": 100.0,
        "predicted_s": 4.0,
    }
    llama = Usage(
        raw={
            "tokens_evaluated": 1000,
            "timings": {
                "cache_n": 100,
                "prompt_n": 900,
                "prompt_ms": 450.0,
                "predicted_n": 64,
                "predicted_ms": 1280.0,
            },
        }
    )
    assert server_timings(llama) == {
        "prompt_n": 900.0,
        "prompt_s": 0.45,
        "predicted_n": 64.0,
        "predicted_s": 1.28,
    }
    assert server_timings(Usage(raw={"prompt_tokens": 10})) == {}
    assert server_timings(None) == {}


def test_sample_rates_prefer_server_timings() -> None:
    prefill = Sample("prefill", input_tokens=1500, cached_tokens=500, ttft_s=0.5, total_s=0.7)
    assert prefill.prefill_rate() == (pytest.approx(2000.0), "client")
    prefill.server = {"prompt_n": 1000.0, "prompt_s": 0.25}
    assert prefill.prefill_rate() == (pytest.approx(4000.0), "server")
    decode = Sample("decode", input_tokens=20, output_tokens=129, ttft_s=0.2, total_s=4.2)
    assert decode.decode_rate() == (pytest.approx(32.0), "client")
    decode.server = {"predicted_n": 129.0, "predicted_s": 3.0}
    assert decode.decode_rate() == (pytest.approx(43.0), "server")
    assert Sample("decode", output_tokens=1, ttft_s=0.1, total_s=0.2).decode_rate() == (None, None)
    assert Sample("prefill").prefill_rate() == (None, None)
    row = decode.as_dict()
    assert row["decode_tps"] == 43.0 and row["decode_source"] == "server"
    assert row["ttft_ms"] == 200.0 and row["total_ms"] == 4200.0


def test_median_rate_and_filler() -> None:
    assert calibrate.median_rate([(10.0, "server"), (30.0, "server"), (None, None)]) == (
        20.0,
        "server",
    )
    assert calibrate.median_rate([(10.0, "server"), (20.0, "client")]) == (15.0, "mixed")
    assert calibrate.median_rate([]) == (None, None)
    text = calibrate.filler(500, seed=3)
    assert len(text) >= 500 and text == calibrate.filler(500, seed=3)
    assert text != calibrate.filler(500, seed=4)
    prompt = calibrate.prefill_prompt("nonce123")
    assert prompt.startswith("Notes nonce123:") and len(prompt) > 5_000


def test_cache_ratio_compares_cached_with_shared_tokens() -> None:
    first = Sample(kind="prefill", input_tokens=1524, cached_tokens=0)
    second = Sample(kind="prefill", input_tokens=1530, cached_tokens=1000)
    measured = calibrate.cache_ratio(first, second)
    assert measured["shared_tokens"] == 1500 and measured["ratio"] == round(1000 / 1500, 4)
    assert measured["source"] == "server"
    silent = calibrate.cache_ratio(first, Sample(kind="prefill", input_tokens=1530))
    assert silent["ratio"] is None and silent["source"] is None
    full = calibrate.cache_ratio(first, Sample(kind="prefill", input_tokens=9, cached_tokens=5000))
    assert full["ratio"] == 1.0


def test_calibrate_cache_measures_the_prefix_cache() -> None:
    from tests.unit.fixtures.probe.mock_servers import Brain

    lines: list[str] = []
    server = ChatServer(Brain(cache=True))
    result = calibrate.run(
        chat_config(),
        egress=net.Egress(),
        echo=lines.append,
        transport=server.transport(),
        repeats=1,
        cache=True,
    )
    cache = result["cache"]
    assert cache["source"] == "server" and 0.9 <= cache["ratio"] <= 1.0
    assert cache["shared_tokens"] > 1000
    assert any(line.startswith("  prefix cache: ") for line in lines)
    cold = calibrate.run(
        chat_config(),
        egress=net.Egress(),
        echo=lambda _line: None,
        transport=ChatServer(Brain(cache=False)).transport(),
        repeats=1,
        cache=True,
    )
    assert cold["cache"]["ratio"] == 0.0
    plain = calibrate.run(
        chat_config(),
        egress=net.Egress(),
        echo=lambda _line: None,
        transport=ChatServer().transport(),
        repeats=1,
    )
    assert "cache" not in plain
