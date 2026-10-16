"""The capability probe (SPEC §4.6): every check against mock servers that pass and fail it.

The mock servers (tests/unit/fixtures/probe/mock_servers.py) are written by hand from the
format references; switches on them turn single capabilities on or off.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from stepswitch import net, registry
from stepswitch.compat import probe
from stepswitch.compat.calibrate import Sample
from stepswitch.compat.ir import Usage
from stepswitch.config import EndpointConfig
from tests.conformance.harness import MockServer, Recorded, json_response
from tests.unit.fixtures.probe.mock_servers import (
    BASE_URL,
    DIGEST,
    KEY,
    Brain,
    ChatServer,
    OllamaServer,
    chat_config,
    ollama_config,
)

# --------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------


def run_probe(
    server: ChatServer | OllamaServer,
    config: EndpointConfig | None = None,
    *,
    quick: bool = False,
    fmt: str | None = None,
    model: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    lines: list[str] = []
    profile = probe.run(
        config or chat_config(),
        model=model,
        fmt=fmt,
        quick=quick,
        egress=net.Egress(),
        echo=lines.append,
        transport=server.transport(),
        timeout=30.0,
    )
    return profile, lines


def ok(profile: dict[str, Any], name: str) -> Any:
    return profile["checks"][name]["ok"]


# --------------------------------------------------------------------------------------------
# The full battery
# --------------------------------------------------------------------------------------------


def test_every_check_passes_against_a_capable_server() -> None:
    server = ChatServer(Brain(reasoning=True))
    profile, lines = run_probe(server)
    assert list(profile["checks"]) == list(probe.CHECKS)
    failing = {name: check for name, check in profile["checks"].items() if check["ok"] is not True}
    assert not failing, failing
    caps = profile["capabilities"]
    assert caps == {
        "native_tools": True,
        "parallel_tools": True,
        "streamed_tool_args": True,
        "tool_call_ids": "server",
        "forced_tool_choice": True,
        "assistant_prefill": True,
        "json_schema": True,
        "fallback_tools": True,
        "streaming": True,
        "usage": True,
        "usage_includes_cached": True,
        "reasoning_field": "reasoning_content",
        "reasoning_retention": True,
        "context_ok_tokens": caps["context_ok_tokens"],
        "prefix_cache": True,
    }
    assert caps["context_ok_tokens"] > 12_000
    assert profile["checks"]["reasoning_retention"]["data"]["policy"] == "keep-for-tool-turns"
    assert profile["checks"]["fallback_tool_protocol"]["data"]["honoured"] is True
    assert profile["checks"]["streamed_tool_args"]["data"]["incremental"] is True
    throughput = profile["throughput"]
    assert throughput["prefill_tps"] > 0 and throughput["decode_tps"] > 0
    assert throughput["sources"] == {"prefill_tps": "client", "decode_tps": "client"}
    assert profile["schema"] == 1 and profile["quick"] is False
    assert profile["format"] == "chat-completions" and profile["formats_served"] == [
        "chat-completions"
    ]
    assert profile["endpoint"] == "mock" and profile["model"] == "mock-model"
    assert profile["engine"] is None and profile["local"] is True and profile["scheme"] == "http"
    assert json.loads(json.dumps(profile)) == profile
    assert lines[0].startswith("Probing mock / mock-model") and lines[-1].startswith("Done in")
    assert len([line for line in lines if line.startswith("  ") and "s  " in line]) == 20


def test_every_capability_fails_against_a_bare_server() -> None:
    brain = Brain(tools=False, forced=False, prefill=False, schema=False, cache=False)
    server = ChatServer(brain, ids=False, stream_usage=False)
    profile, _lines = run_probe(server)
    caps = profile["capabilities"]
    for name in (
        "native_tools",
        "parallel_tools",
        "streamed_tool_args",
        "forced_tool_choice",
        "assistant_prefill",
        "json_schema",
        "fallback_tools",
        "usage",
        "prefix_cache",
    ):
        assert caps[name] is False, name
    assert caps["streaming"] is True
    assert caps["tool_call_ids"] is None  # no call to inspect
    assert caps["usage_includes_cached"] is None
    assert caps["reasoning_field"] is None
    assert caps["reasoning_retention"] is None
    checks = profile["checks"]
    assert checks["usage"]["data"] == {
        "non_streaming": True,
        "streaming": False,
        "example": checks["usage"]["data"]["example"],
    }
    assert ok(profile, "reasoning_field") is False
    assert ok(profile, "structured_output_under_reasoning") is None
    assert checks["fallback_tool_protocol"]["data"]["valid_first_try"] is False
    assert checks["throughput"]["ok"] is False
    assert "started a new reply" in checks["assistant_prefill"]["detail"]


def test_fallback_protocol_records_a_repair_and_unenforced_schemas() -> None:
    server = ChatServer(Brain(schema=False, repair=True))
    profile, _lines = run_probe(server, quick=True)
    data = profile["checks"]["fallback_tool_protocol"]["data"]
    assert ok(profile, "fallback_tool_protocol") is True
    assert data["constrained"] is True and data["honoured"] is False and data["repaired"] is True
    assert ok(profile, "json_schema") is False


def test_forced_tool_choice_ignored_and_synthetic_ids() -> None:
    server = ChatServer(Brain(forced=False, parallel=False), ids=False)
    profile, _lines = run_probe(server, quick=True)
    assert (
        "tool_choice ignored: called get_weather"
        in profile["checks"]["forced_tool_choice"]["detail"]
    )
    assert ok(profile, "parallel_tool_calls") is False
    assert "one call per reply" in profile["checks"]["parallel_tool_calls"]["detail"]
    assert profile["capabilities"]["tool_call_ids"] == "synthetic"
    assert ok(profile, "tool_call_ids") is False


@pytest.mark.parametrize("echo", [False, True])
def test_assistant_prefill_with_and_without_an_echoed_prefix(echo: bool) -> None:
    profile, _lines = run_probe(ChatServer(Brain(prefill_echo=echo)), quick=True)
    check = profile["checks"]["assistant_prefill"]
    assert check["ok"] is True and check["data"]["echoes_prefix"] is echo
    assert ("repeats it" in check["detail"]) is echo


def test_a_reply_cut_short_by_unexpected_reasoning_is_retried_with_more_room() -> None:
    # Like the llama.cpp server, which lets a model write reasoning before constrained JSON.
    server = ChatServer(Brain(preamble="Okay, the user asks about Paris. " * 21))
    profile, _lines = run_probe(server, quick=True)
    assert ok(profile, "json_schema") is True
    budgets = [
        r.body["max_tokens"]
        for r in server.chats
        if (r.body.get("response_format") or {}).get("json_schema", {}).get("name") == "city_facts"
    ]
    assert budgets[:2] == [192, 192 + probe.REASONING_ALLOWANCE]
    assert profile["capabilities"]["reasoning_field"] == "reasoning_content"
    assert ok(profile, "structured_output_under_reasoning") is True


def test_quick_mode_skips_the_slow_checks() -> None:
    server = ChatServer()
    profile, _lines = run_probe(server, quick=True)
    for name in probe.SLOW_CHECKS:
        assert profile["checks"][name] == {
            "ok": None,
            "seconds": 0.0,
            "detail": "skipped in quick mode",
        }
    assert profile["throughput"] == {} and profile["quick"] is True
    assert profile["capabilities"]["context_ok_tokens"] is None
    assert profile["capabilities"]["prefix_cache"] is None
    sent = json.dumps([r.body for r in server.chats])
    assert "passphrase" not in sent and "lighthouse keeper" not in sent
    assert ok(profile, "usage_semantics") is True  # cheap; runs in quick mode too


def test_a_broken_check_is_recorded_and_the_battery_continues() -> None:
    server = ChatServer(fail=frozenset({"schema"}))
    profile, lines = run_probe(server, quick=True)
    check = profile["checks"]["json_schema"]
    assert check["ok"] is False and check["detail"] == "request failed"
    assert check["error"].startswith("server (HTTP 500)")
    assert ok(profile, "fallback_tool_protocol") is True
    assert ok(profile, "assistant_prefill") is True
    assert ok(profile, "usage_semantics") is True
    assert any("json_schema" in line and "FAIL" in line for line in lines)


def test_an_unreachable_endpoint_fails_fast_without_raising() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    lines: list[str] = []
    profile = probe.run(
        chat_config(),
        quick=True,
        egress=net.Egress(),
        echo=lines.append,
        transport=httpx.MockTransport(refuse),
    )
    checks = profile["checks"]
    assert checks["basic_chat"]["error"].startswith("connection")
    assert checks["system_prompt"]["error"].startswith("connection")
    assert checks["native_tool_call"]["error"].startswith("not run")
    assert all(check["ok"] is not True for check in checks.values())
    assert profile["formats_served"] == []
    assert ok(profile, "formats_served") is False


def test_no_model_and_unknown_format_raise_value_error() -> None:
    def missing(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    config = EndpointConfig(name="empty", url=BASE_URL)
    with pytest.raises(ValueError, match="no model to probe"):
        probe.run(
            config,
            egress=net.Egress(),
            echo=lambda _l: None,
            transport=httpx.MockTransport(missing),
        )
    with pytest.raises(ValueError, match="unknown API format"):
        probe.run(chat_config(), fmt="smoke-signals", egress=net.Egress(), echo=lambda _l: None)


def test_the_model_defaults_to_the_first_one_the_server_lists() -> None:
    server = ChatServer(model_id="listed-model")
    profile, _lines = run_probe(server, chat_config(models=[]), quick=True)
    assert profile["model"] == "listed-model"
    assert {r.body["model"] for r in server.chats} == {"listed-model"}


# --------------------------------------------------------------------------------------------
# Context length
# --------------------------------------------------------------------------------------------


def test_needle_test_stops_where_the_server_loses_the_start_of_the_prompt() -> None:
    server = ChatServer(Brain(context=5_000))
    profile, _lines = run_probe(server)
    check = profile["checks"]["context_length"]
    assert check["ok"] is False
    results = check["data"]["results"]
    assert [entry["found"] for entry in results] == [True, True, False]
    assert 3_000 < profile["capabilities"]["context_ok_tokens"] < 5_000
    assert "failed at about 8192" in check["detail"]


def test_needle_lengths_stay_under_the_server_context() -> None:
    server = ChatServer(meta={"n_ctx_train": 6_000})
    profile, _lines = run_probe(server)
    data = profile["checks"]["context_length"]["data"]
    assert data["limit"] == 6_000 and data["limit_source"] == "model context length"
    assert [entry["target"] for entry in data["results"]] == [2048, 4096, 6000 - probe.ANSWER_ROOM]
    assert profile["checks"]["context_length"]["ok"] is True


# --------------------------------------------------------------------------------------------
# Reasoning
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("keep", "policy"),
    [("tool", "keep-for-tool-turns"), ("all", "keep-all"), ("none", "drop")],
)
def test_reasoning_retention_policy_from_prompt_sizes(keep: str, policy: str) -> None:
    server = ChatServer(Brain(reasoning=True, keep_reasoning=keep), reasoning_field="reasoning")
    profile, _lines = run_probe(server, quick=True)
    check = profile["checks"]["reasoning_retention"]
    assert check["ok"] is True and check["data"]["policy"] == policy
    assert profile["capabilities"]["reasoning_field"] == "reasoning"
    sent_back = [
        message
        for r in server.chats
        for message in r.body["messages"]
        if message["role"] == "assistant" and message.get("reasoning")
    ]
    assert sent_back, "the retention check sends the model's own reasoning back"
    assert ok(profile, "structured_output_under_reasoning") is True


def test_reasoning_elicited_by_effort_is_recorded() -> None:
    class EffortServer(ChatServer):
        def chat(self, recorded: Recorded) -> httpx.Response:
            self.brain.reasoning = "reasoning_effort" in recorded.body
            return super().chat(recorded)

    profile, _lines = run_probe(EffortServer(), quick=True)
    check = profile["checks"]["reasoning_field"]
    assert check["ok"] is True and check["data"]["needs_effort"] is True
    assert "when an effort is set" in check["detail"]


def test_reasoning_inside_the_text_is_reported() -> None:
    class InlineServer(ChatServer):
        def chat(self, recorded: Recorded) -> httpx.Response:
            response = super().chat(recorded)
            if recorded.body.get("stream"):
                return response
            body = response.json()
            message = body["choices"][0]["message"]
            if isinstance(message.get("content"), str) and message["content"]:
                message["content"] = "<think>hmm</think>" + message["content"]
            return json_response(body)

    profile, _lines = run_probe(InlineServer(), quick=True)
    check = profile["checks"]["reasoning_field"]
    assert check["ok"] is False and check["data"]["inline"] == "think tags"


# --------------------------------------------------------------------------------------------
# Ollama native
# --------------------------------------------------------------------------------------------


def test_ollama_native_discovery_hash_and_num_ctx() -> None:
    server = OllamaServer()
    profile, _lines = run_probe(server, ollama_config(), quick=True)
    assert profile["format"] == "ollama-native"
    assert profile["model"] == "mock:latest"  # the remote model is never the default
    assert profile["engine"] == "ollama" and profile["engine_version"] == "0.34.4"
    assert profile["formats_served"] == [
        "ollama-native",
        "chat-completions",
        "text-completion",
        "responses",
        "messages",
    ]
    assert profile["model_hash"] == f"sha256:{DIGEST}"
    assert profile["model_hash_source"] == "ollama-digest"
    # The loaded context wins over the Modelfile's, so probing never forces a reload.
    assert profile["settings"] == {"num_ctx": 8192}
    assert {r.body["options"]["num_ctx"] for r in server.chats} == {8192}
    assert all(isinstance(r.body["stream"], bool) for r in server.chats)
    info = profile["model_info"]
    assert info["context_length"] == 32768 and info["num_ctx"] == 4096
    assert set(info) <= {
        "context_length",
        "loaded_context",
        "server_context",
        "capabilities",
        "think_values",
        "num_ctx",
        "family",
        "parameter_size",
        "quantization",
        "template_sha256",
    }
    for name in ("basic_chat", "native_tool_call", "parallel_tool_calls", "json_schema"):
        assert ok(profile, name) is True, name
    assert ok(profile, "forced_tool_choice") is False  # the format has no tool_choice
    assert "cannot carry" in profile["checks"]["forced_tool_choice"]["detail"]
    assert ok(profile, "usage_semantics") is True
    assert profile["capabilities"]["usage_includes_cached"] is True
    assert profile["capabilities"]["reasoning_field"] is None
    # The thinking capability is absent, so no effort request was sent.
    assert not any("think" in r.body for r in server.chats)
    assert str(Path.home()) not in json.dumps(profile)


def test_ollama_num_ctx_falls_back_to_the_modelfile_then_a_default() -> None:
    discovery = probe.Discovery()
    config = ollama_config()
    assert probe.ollama_num_ctx(config, discovery, {"num_ctx": 4096}, "m", quick=True) == 4096
    assert probe.ollama_num_ctx(config, discovery, {}, "m", quick=True) == probe.QUICK_NUM_CTX
    capped = {"context_length": 8192}
    assert probe.ollama_num_ctx(config, discovery, capped, "m", quick=False) == 8192
    assert probe.ollama_num_ctx(config, discovery, {}, "m", quick=False) == probe.CONTEXT_CAP
    discovery.loaded_contexts["m:latest"] = 2048
    assert probe.ollama_num_ctx(config, discovery, {"num_ctx": 4096}, "m", quick=True) == 2048
    pinned = ollama_config(num_ctx=12_288)
    assert probe.ollama_num_ctx(pinned, discovery, {}, "m", quick=True) == 12_288


def test_ollama_full_probe_measures_server_timings() -> None:
    server = OllamaServer()
    profile, _lines = run_probe(server, ollama_config())
    throughput = profile["throughput"]
    assert throughput["sources"] == {"prefill_tps": "server", "decode_tps": "server"}
    assert throughput["prefill_tps"] == pytest.approx(1000.0)
    assert throughput["decode_tps"] == pytest.approx(50.0)
    data = profile["checks"]["context_length"]["data"]
    assert data["limit"] == 8192 and data["limit_source"] == "num_ctx sent with every request"
    assert ok(profile, "context_length") is True and ok(profile, "prefix_cache") is True


# --------------------------------------------------------------------------------------------
# Privacy: no secrets in profiles or exports
# --------------------------------------------------------------------------------------------


def test_profile_and_export_hold_no_key_url_or_home_path(tmp_path: Path) -> None:
    server = ChatServer(fail=frozenset({"schema"}), key=KEY)
    config = EndpointConfig(name=BASE_URL, url=BASE_URL, auth="bearer", api_key=KEY, models=["m"])
    profile, lines = run_probe(server, config, quick=True)
    assert all(r.headers.get("authorization") == f"Bearer {KEY}" for r in server.chats)
    assert ok(profile, "basic_chat") is True
    error = profile["checks"]["json_schema"]["error"]
    assert "<url>" in error and "<redacted>" in error
    hardware = {
        "os": {"system": "Linux", "machine": "x86_64", "name": "a distro", "release": "6.1"},
        "cpu": {"model": "Some CPU", "cores_physical": 8, "cores_logical": 16, "features": []},
        "ram_gb": 31.2,
        "gpus": [{"name": "Some GPU", "vram_gb": 24.0, "backend": "cuda", "driver": "1.2"}],
        "python": {"executable": str(Path.home() / "venv" / "python")},
        "disk": {"models_dir": {"path": str(Path.home() / "models")}},
    }
    written = probe.export(profile, tmp_path / "out" / "result.json", hardware=hardware)
    exported = json.loads(written.read_text(encoding="utf-8"))
    profile_text = json.dumps(profile)
    export_text = written.read_text(encoding="utf-8")
    for text in (profile_text, export_text, "\n".join(lines)):
        assert KEY not in text
        assert BASE_URL not in text and "127.0.0.1:65530" not in text
        assert str(Path.home()) not in text and Path.home().as_posix() not in text
    assert profile["endpoint"] == "http-local-endpoint"
    assert exported["endpoint"] == {"scheme": "http", "local": True}
    assert exported["hardware"] == {
        "os": {"system": "Linux", "machine": "x86_64"},
        "cpu": {"cores_physical": 8, "cores_logical": 16},
        "ram_gb": 31.2,
        "gpus": [{"vram_gb": 24.0, "backend": "cuda"}],
    }
    assert "Some CPU" not in export_text and "Some GPU" not in export_text


def test_model_paths_are_cut_to_the_file_name() -> None:
    path = str(Path.home() / "models" / "tiny-model-q4.gguf")
    server = ChatServer(model_id=path)
    profile, lines = run_probe(server, chat_config(models=[]), quick=True)
    assert profile["model"] == "tiny-model-q4.gguf"
    assert {r.body["model"] for r in server.chats} == {path}  # the server still gets its id
    assert str(Path.home()) not in json.dumps(profile) + "\n".join(lines)


@pytest.mark.parametrize(
    ("name", "safe"),
    [
        ("qwen3:4b-instruct-2507-q4_K_M", "qwen3:4b-instruct-2507-q4_K_M"),
        ("Qwen/Qwen3-4B-Instruct-2507", "Qwen/Qwen3-4B-Instruct-2507"),
        ("hf.co/org/repo:Q4_K_M", "hf.co/org/repo:Q4_K_M"),
        ("/srv/models/tiny.gguf", "tiny.gguf"),
        ("C:\\models\\tiny.gguf", "tiny.gguf"),
        ("models/tiny.gguf", "tiny.gguf"),
        ("~/models/tiny", "tiny"),
    ],
)
def test_safe_model_name(name: str, safe: str) -> None:
    assert probe.safe_model_name(name) == safe


def test_scrubber_replaces_urls_and_secrets() -> None:
    scrubber = probe.Scrubber(["topsecret-value"])
    tree = {"a": ["see https://example.org/x?y=1 now", {"b": "TOPSECRET-VALUE here"}], "n": 3}
    assert scrubber.tree(tree) == {"a": ["see <url> now", {"b": "<redacted> here"}], "n": 3}


# --------------------------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------------------------


def test_export_content(tmp_path: Path) -> None:
    profile, _lines = run_probe(OllamaServer(), ollama_config(), quick=True)
    written = probe.export(profile, tmp_path / "probe.json", hardware={"ram_gb": 16})
    exported = json.loads(written.read_text(encoding="utf-8"))
    assert written == tmp_path / "probe.json"
    assert exported["schema"] == 1 and exported["kind"] == "stepswitch-probe-result"
    assert exported["date"] == profile["created"][:10]
    assert exported["model"] == "mock:latest"
    assert exported["model_hash"] == f"sha256:{DIGEST}"
    assert exported["engine"] == "ollama" and exported["engine_version"] == "0.34.4"
    assert exported["format"] == "ollama-native"
    assert exported["checks"] == profile["checks"]
    assert exported["capabilities"] == profile["capabilities"]
    assert exported["throughput"] == {}
    assert exported["hardware"]["ram_gb"] == 16.0 and exported["hardware"]["gpus"] == []
    assert "endpoint" in exported and set(exported["endpoint"]) == {"scheme", "local"}
    assert "settings" not in exported and "model_info" not in exported


def test_registry_hash_when_the_name_matches_an_entry() -> None:
    entry = registry.find("qwen3-4b-instruct-2507")
    if entry is None or not entry.sha256:
        pytest.skip("the packaged registry has no such entry")
    server = ChatServer(model_id=entry.id)
    profile, _lines = run_probe(server, chat_config(models=[entry.id]), quick=True)
    assert profile["model_hash"] == f"sha256:{entry.sha256}"
    assert profile["model_hash_source"] == "registry"


# --------------------------------------------------------------------------------------------
# Pure judgements
# --------------------------------------------------------------------------------------------


def test_usage_semantics_judgement() -> None:
    def usage(raw: dict[str, Any], total: int, cached: int | None) -> Usage:
        return Usage(input_tokens=total, output_tokens=1, cached_input_tokens=cached, raw=raw)

    included = probe.judge_usage_semantics(
        usage({"prompt_tokens": 300}, 300, 0), usage({"prompt_tokens": 300}, 300, 290)
    )
    assert included.ok is True and included.data["includes_cached"] is True
    # A messages-style server reports only the uncached part; the codec adds the cache reads.
    excluded = probe.judge_usage_semantics(
        usage({"input_tokens": 300}, 300, 0), usage({"input_tokens": 10}, 300, 290)
    )
    assert excluded.ok is True and excluded.data["includes_cached"] is False
    # Normalization that double-counts is flagged.
    inconsistent = probe.judge_usage_semantics(
        usage({"input_tokens": 300}, 300, 0), usage({"input_tokens": 10}, 10, 290)
    )
    assert inconsistent.ok is False
    unknown = probe.judge_usage_semantics(
        usage({"prompt_tokens": 300}, 300, None), usage({"prompt_tokens": 300}, 300, None)
    )
    assert unknown.ok is None and unknown.data["includes_cached"] is None


def test_prefix_cache_judgement() -> None:
    cold = Sample("prefill", input_tokens=1500, cached_tokens=0, ttft_s=2.0, total_s=2.1)
    hit = probe.judge_prefix_cache(cold, Sample("prefill", 1500, 1480, ttft_s=0.2, total_s=0.3))
    assert hit.ok is True and hit.data["source"] == "usage"
    miss = probe.judge_prefix_cache(cold, Sample("prefill", 1500, 0, ttft_s=2.0, total_s=2.1))
    assert miss.ok is False
    timed = probe.judge_prefix_cache(cold, Sample("prefill", 1500, None, ttft_s=0.5, total_s=0.6))
    assert timed.ok is True and timed.data["source"] == "timing"
    slow = probe.judge_prefix_cache(cold, Sample("prefill", 1500, None, ttft_s=1.9, total_s=2.0))
    assert slow.ok is False
    fast = Sample("prefill", 10, None, ttft_s=0.01, total_s=0.02)
    assert probe.judge_prefix_cache(fast, fast).ok is None


def test_retention_policy() -> None:
    assert probe.retention_policy(None, 40) == "keep-all"
    assert probe.retention_policy(12, 0) == "keep-for-tool-turns"
    assert probe.retention_policy(0, 0) == "drop"
    assert probe.retention_policy(None, 0) is None


def test_json_reply_parsing() -> None:
    assert probe.parse_json_reply('{"a": 1}') == ({"a": 1}, True)
    assert probe.parse_json_reply('```json\n{"a": 1}\n```') == ({"a": 1}, False)
    assert probe.parse_json_reply('Sure: {"a": 1} done') == ({"a": 1}, False)
    with pytest.raises(ValueError):
        probe.parse_json_reply("no json here")
    schema = probe.action_schema(["get_weather"])
    action, problem = probe.parse_action('{"tool": "get_weather", "arguments": {}}', schema)
    assert action == {"tool": "get_weather", "arguments": {}} and problem is None
    action, problem = probe.parse_action('{"tool": "other", "arguments": {}}', schema)
    assert action is None and problem


def test_discovery_reads_llamacpp_props_without_keeping_paths() -> None:
    mock = MockServer()
    props = {
        "default_generation_settings": {"n_ctx": 4096},
        "total_slots": 1,
        "model_path": str(Path.home() / "models" / "x.gguf"),
        "build_info": "b11669-0123abcd",
    }
    mock.route("GET", r"/props", lambda _r: json_response(props))
    mock.route(
        "GET",
        r"/v1/models",
        lambda _r: json_response({"data": [{"id": "x.gguf", "meta": {"n_ctx_train": 32768}}]}),
    )
    mock.route("GET", r"/health", lambda _r: json_response({"status": "ok"}))
    found = probe.discover(BASE_URL, egress=net.Egress(), transport=mock.transport())
    assert found.engine == "llamacpp" and found.engine_version == "b11669-0123abcd"
    assert found.server_context == 4096 and found.health == 200
    assert found.formats == [
        "chat-completions",
        "llamacpp-native",
        "text-completion",
        "responses",
        "messages",
    ]
    assert found.model_contexts == {"x.gguf": 32768}
    assert str(Path.home()) not in repr(found)


def test_discovery_uses_the_egress_layer_with_purpose_probe() -> None:
    egress = net.Egress()
    server = ChatServer()
    probe.discover(BASE_URL, egress=egress, transport=server.transport())
    rows = list(egress.memory)
    assert rows and {row.purpose for row in rows} == {"probe"}
    assert {row.method for row in rows} == {"GET"}


# --------------------------------------------------------------------------------------------
# Live (skipped unless a local Ollama with a suitable model is running)
# --------------------------------------------------------------------------------------------

LIVE_URL = os.environ.get("STEPSWITCH_LIVE_OLLAMA_URL", "http://127.0.0.1:11434")
SMALL_MODEL_BYTES = 6 * 1024**3


def live_model() -> str | None:
    """STEPSWITCH_LIVE_OLLAMA_MODEL if installed, else the smallest local model up to 6 GiB."""
    try:
        with net.default().client("test", timeout=3.0) as http:
            tags = http.get(f"{LIVE_URL.rstrip('/')}/api/tags").json()
    except (httpx.HTTPError, ValueError):
        return None
    models = [
        m
        for m in (tags.get("models") or [] if isinstance(tags, dict) else [])
        if isinstance(m, dict) and isinstance(m.get("name"), str) and not m.get("remote_host")
    ]
    wanted = os.environ.get("STEPSWITCH_LIVE_OLLAMA_MODEL")
    if wanted:
        return wanted if any(m["name"] == wanted for m in models) else None
    small = [m for m in models if isinstance(m.get("size"), int) and m["size"] <= SMALL_MODEL_BYTES]
    return min(small, key=lambda m: m["size"])["name"] if small else None


@pytest.mark.live
def test_live_quick_probe_against_local_ollama() -> None:
    model = live_model()
    if model is None:
        pytest.skip("no local Ollama server with a suitable model")
    config = EndpointConfig(name="live-ollama", url=LIVE_URL, engine="ollama")
    profile = probe.run(
        config, model=model, fmt="ollama-native", quick=True, echo=lambda _l: None, timeout=300.0
    )
    assert profile["schema"] == 1 and list(profile["checks"]) == list(probe.CHECKS)
    assert profile["engine"] == "ollama" and profile["engine_version"]
    assert profile["checks"]["basic_chat"]["ok"] is True
    assert "ollama-native" in profile["formats_served"]
    assert profile["settings"]["num_ctx"] > 0
    assert all(profile["checks"][name]["ok"] is None for name in probe.SLOW_CHECKS)
    assert json.loads(json.dumps(profile)) == profile
