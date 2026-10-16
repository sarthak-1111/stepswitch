"""transformers adapter and wrapper server: command lines, availability, request handling, and
a real process.

The wrapper's request handling runs in process against a fake generator, so no torch or
transformers is needed. The process tests start the real wrapper file through the adapter with a
stand-in backend (fixtures/serve/fake_transformers_backend.py) that injects a fake generator.
"""

from __future__ import annotations

import ast
import contextlib
import json
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest

from stepswitch import net
from stepswitch.registry import EngineRecipe, ModelEntry
from stepswitch.serve import transformers_server as ts
from stepswitch.serve.base import EngineError, ServerInfo, pid_alive
from stepswitch.serve.transformers import SERVER_SCRIPT, TransformersEngine

FIXTURES = Path(__file__).parent / "fixtures" / "serve"
BACKEND = FIXTURES / "fake_transformers_backend.py"
KEY = "test-key-0123456789"
HEADERS = {"Authorization": f"Bearer {KEY}"}


def entry(recipe: EngineRecipe | None = None) -> ModelEntry:
    return ModelEntry(
        id="tiny-chat",
        name="Tiny Chat",
        hub_id="test/tiny-chat",
        quant="bf16",
        format="safetensors",
        file_size_gb=0.1,
        ram_gb=1.0,
        context=4096,
        license="apache-2.0",
        engines={"transformers": recipe or EngineRecipe()},
    )


def fake_environment(root: Path, dists: Sequence[str]) -> Path:
    """A virtual-environment layout: `bin/python` and `.dist-info` folders, nothing runnable."""
    (root / "bin").mkdir(parents=True)
    python = root / "bin" / "python"
    python.write_bytes(b"")
    site = root / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    for dist in dists:
        (site / f"{dist}.dist-info").mkdir()
    return python


# -- the wrapper, in process --------------------------------------------------------------------


class FakeGenerator:
    """Streams fixed pieces (one token each); records calls and the highest concurrency."""

    def __init__(
        self,
        pieces: Sequence[str] = ("Hello", ", wor", "ld!"),
        *,
        delay: float = 0.0,
        ctx_size: int | None = None,
        fail: bool = False,
    ) -> None:
        self.pieces = list(pieces)
        self.delay = delay
        self.ctx_size = ctx_size
        self.fail = fail
        self.calls: list[tuple[list[dict[str, Any]], ts.GenerateOptions]] = []
        self.sent: list[int] = []
        self.active = 0
        self.peak = 0
        self.lock = threading.Lock()

    def prepare(self, messages: list[dict[str, Any]], options: ts.GenerateOptions) -> ts.Prepared:
        self.calls.append((messages, options))
        if self.ctx_size is not None:
            raise ts.RequestError(
                f"the prompt has 7 tokens; the context size is {self.ctx_size}",
                kind="exceed_context_size_error",
            )
        return ts.Prepared(7, options.max_tokens or 16)

    def run(self, prepared: ts.Prepared, on_text: Callable[[str], bool]) -> ts.Generated:
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if self.fail:
                raise RuntimeError("device lost")
            sent = 0
            for piece in self.pieces[: prepared.max_tokens]:
                time.sleep(self.delay)
                sent += 1
                if not on_text(piece):
                    break
            self.sent.append(sent)
            return ts.Generated(
                sent, "length" if prepared.max_tokens < len(self.pieces) else "stop"
            )
        finally:
            with self.lock:
                self.active -= 1


Serve = Callable[..., tuple[str, ts.ChatServer]]


@pytest.fixture
def serve() -> Iterator[Serve]:
    servers: list[ts.ChatServer] = []

    def start(
        generator: Any,
        *,
        api_key: str | None = KEY,
        template_kwargs: dict[str, Any] | None = None,
    ) -> tuple[str, ts.ChatServer]:
        server = ts.ChatServer(
            ("127.0.0.1", 0),
            served_model="tiny-chat",
            api_key=api_key,
            template_kwargs=template_kwargs,
        )
        server.generator = generator
        thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
        thread.start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_port}", server

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def chat(url: str, body: dict[str, Any], headers: dict[str, str] = HEADERS) -> httpx.Response:
    return httpx.post(f"{url}/v1/chat/completions", json=body, headers=headers, timeout=10)


def user(text: str = "Hi") -> dict[str, Any]:
    return {"messages": [{"role": "user", "content": text}]}


def sse_events(response: httpx.Response) -> list[Any]:
    events: list[Any] = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            data = line[len("data: ") :]
            events.append(data if data == "[DONE]" else json.loads(data))
    return events


def test_health_is_public_and_answers_503_while_loading(serve: Serve) -> None:
    url, server = serve(None)
    response = httpx.get(f"{url}/health")
    assert response.status_code == 503
    assert response.json()["error"]["type"] == "unavailable_error"
    assert chat(url, user()).status_code == 503
    server.generator = FakeGenerator()
    assert httpx.get(f"{url}/health").status_code == 200  # no key needed
    assert httpx.get(f"{url}/v1/models").status_code == 401
    models = httpx.get(f"{url}/v1/models", headers=HEADERS)
    assert models.status_code == 200 and models.json()["data"][0]["id"] == "tiny-chat"
    assert httpx.get(f"{url}/v1/other", headers=HEADERS).status_code == 404
    assert chat(url, user(), headers={}).status_code == 401
    assert chat(url, user(), headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert chat(url, user(), headers={"X-Api-Key": KEY}).status_code == 200


def test_chat_completion_with_usage(serve: Serve) -> None:
    generator = FakeGenerator()
    url, _ = serve(generator, template_kwargs={"enable_thinking": True, "style": "plain"})
    body = {
        "model": "anything",
        "messages": [
            {"role": "system", "content": "Be brief."},
            {
                "role": "user",
                "content": [{"type": "text", "text": "Hi"}, {"type": "text", "text": "there"}],
            },
            {"role": "assistant", "content": None, "name": "helper"},
        ],
        "max_tokens": 16,
        "temperature": 0.5,
        "top_p": 0.9,
        "seed": 3,
        "frequency_penalty": 0.1,  # unknown sampling fields are ignored
        "chat_template_kwargs": {"enable_thinking": False},
    }
    response = chat(url, body)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["object"] == "chat.completion" and data["model"] == "tiny-chat"
    assert data["id"].startswith("chatcmpl-")
    assert data["choices"] == [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Hello, world!"},
            "finish_reason": "stop",
        }
    ]
    assert data["usage"] == {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
    [(messages, options)] = generator.calls
    assert messages == [
        {"role": "system", "content": "Be brief."},
        {"role": "user", "content": "Hi\nthere"},
        {"role": "assistant", "content": "", "name": "helper"},
    ]
    assert options == ts.GenerateOptions(
        max_tokens=16,
        temperature=0.5,
        top_p=0.9,
        seed=3,
        stop=[],
        template_kwargs={"enable_thinking": False, "style": "plain"},
    )


def test_token_limit_ends_with_length(serve: Serve) -> None:
    url, _ = serve(FakeGenerator())
    data = chat(url, {**user(), "max_completion_tokens": 2}).json()
    assert data["choices"][0]["message"]["content"] == "Hello, wor"
    assert data["choices"][0]["finish_reason"] == "length"
    assert data["usage"]["completion_tokens"] == 2


def test_stop_strings_cut_the_text_and_end_generation(serve: Serve) -> None:
    generator = FakeGenerator()
    url, _ = serve(generator)
    data = chat(url, {**user(), "stop": ["wor", "never"]}).json()
    assert data["choices"][0]["message"]["content"] == "Hello, "
    assert data["choices"][0]["finish_reason"] == "stop"
    assert generator.sent == [2]  # stopped as soon as the stop string appeared
    across = FakeGenerator(["Hel", "lo wo", "rld"])
    url, _ = serve(across)
    data = chat(url, {**user(), "stop": "o wor"}).json()
    assert data["choices"][0]["message"]["content"] == "Hell"


def test_streaming_sends_chunks_usage_and_done(serve: Serve) -> None:
    url, _ = serve(FakeGenerator())
    body = {**user(), "stream": True, "stream_options": {"include_usage": True}}
    response = chat(url, body)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = sse_events(response)
    assert events[-1] == "[DONE]"
    chunks = events[:-1]
    assert {chunk["id"] for chunk in chunks} == {chunks[0]["id"]}
    assert all(chunk["object"] == "chat.completion.chunk" for chunk in chunks)
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""}
    content = [c["choices"][0]["delta"].get("content", "") for c in chunks[1:] if c["choices"]]
    assert "".join(content) == "Hello, world!"
    assert chunks[-2]["choices"][0] == {"index": 0, "delta": {}, "finish_reason": "stop"}
    assert chunks[-1]["choices"] == []
    assert chunks[-1]["usage"] == {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}


def test_streaming_holds_back_a_partial_stop_string(serve: Serve) -> None:
    url, _ = serve(FakeGenerator(["Hello, wor", "ld!"]))
    events = sse_events(chat(url, {**user(), "stream": True, "stop": ["world"]}))
    deltas = [e["choices"][0]["delta"].get("content") for e in events[1:-1] if e["choices"]]
    pieces = [piece for piece in deltas if piece]
    assert pieces == ["Hello, "] and events[-2]["choices"][0]["finish_reason"] == "stop"
    assert events[-1] == "[DONE]" and "usage" not in events[-2]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"messages": []}, "non-empty list"),
        ({"messages": [{"content": "x"}]}, "role must be a non-empty string"),
        (
            {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {}}]}]},
            "only text content",
        ),
        ({**user(), "tools": [{"type": "function", "function": {"name": "f"}}]}, "tool calling"),
        ({**user(), "response_format": {"type": "json_schema"}}, "constrained decoding"),
        ({**user(), "n": 2}, "`n` must be 1"),
        ({**user(), "logprobs": True}, "logprobs"),
        ({**user(), "max_tokens": 0}, "positive integer"),
        ({**user(), "temperature": 3}, "between 0.0 and 2.0"),
        ({**user(), "seed": "7"}, "`seed`"),
        ({**user(), "stop": 5}, "`stop`"),
        ({**user(), "stream": "yes"}, "`stream`"),
        ({**user(), "chat_template_kwargs": []}, "chat_template_kwargs"),
    ],
)
def test_unsupported_requests_are_refused(serve: Serve, body: Any, message: str) -> None:
    generator = FakeGenerator()
    url, _ = serve(generator)
    response = chat(url, body)
    assert response.status_code == 400
    error = response.json()["error"]
    assert message in error["message"] and error["type"] == "invalid_request_error"
    assert generator.calls == []


def test_tools_with_tool_choice_none_and_text_format_are_accepted(serve: Serve) -> None:
    url, _ = serve(FakeGenerator())
    tools = [{"type": "function", "function": {"name": "f"}}]
    body = {**user(), "tools": tools, "tool_choice": "none", "response_format": {"type": "text"}}
    assert chat(url, body).status_code == 200


def test_malformed_bodies_and_routes(serve: Serve) -> None:
    url, _ = serve(FakeGenerator())
    raw = httpx.post(
        f"{url}/v1/chat/completions",
        content=b"{not json",
        headers={**HEADERS, "Content-Type": "application/json"},
    )
    assert raw.status_code == 400 and "not valid JSON" in raw.json()["error"]["message"]
    listed = httpx.post(f"{url}/v1/chat/completions", json=[1], headers=HEADERS)
    assert listed.status_code == 400 and "JSON object" in listed.json()["error"]["message"]
    assert httpx.post(f"{url}/v1/completions", json={}, headers=HEADERS).status_code == 404


def test_context_size_errors_are_request_errors(serve: Serve) -> None:
    url, _ = serve(FakeGenerator(ctx_size=4))
    response = chat(url, user())
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "exceed_context_size_error"
    streamed = chat(url, {**user(), "stream": True})
    assert streamed.status_code == 400  # refused before the stream starts


def test_generator_failures_are_reported(serve: Serve) -> None:
    url, _ = serve(FakeGenerator(fail=True))
    response = chat(url, user())
    assert response.status_code == 500 and "device lost" in response.json()["error"]["message"]
    events = sse_events(chat(url, {**user(), "stream": True}))
    assert events[0]["choices"][0]["delta"]["role"] == "assistant"
    assert "device lost" in events[1]["error"]["message"] and events[-1] == "[DONE]"


def test_one_generation_at_a_time(serve: Serve) -> None:
    generator = FakeGenerator(delay=0.05)
    url, _ = serve(generator)
    statuses: list[int] = []

    def ask() -> None:
        statuses.append(chat(url, user()).status_code)

    threads = [threading.Thread(target=ask) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert statuses == [200, 200, 200]
    assert generator.peak == 1


def test_stop_filter() -> None:
    stops = ts.StopFilter(["END", "", "##"])
    assert stops.stops == ["END", "##"]
    assert stops.push("abc#") == "abc"  # "#" may start "##"
    assert stops.push("x E") == "#x "  # "E" may start "END"
    assert stops.push("N") == ""
    assert stops.push("Dtail") == "" and stops.stopped
    assert stops.push("more") == "" and stops.flush() == ""
    earliest = ts.StopFilter(["cd", "b"])
    assert earliest.push("abcd") == "a" and earliest.stopped
    open_tail = ts.StopFilter(["xyz"])
    assert open_tail.push("abx") == "ab" and open_tail.flush() == "x"
    assert ts.StopFilter([]).push("anything") == "anything"


def test_main_returns_1_when_the_model_does_not_load() -> None:
    def broken(args: Any) -> ts.Generator:
        raise RuntimeError(f"no weights in {args.model}")

    assert ts.main(["--model", "nowhere", "--port", "0"], factory=broken) == 1


def test_main_returns_1_when_the_port_is_taken() -> None:
    server = ts.ChatServer(("127.0.0.1", 0), served_model="m")
    try:
        assert ts.main(["--model", "m", "--port", str(server.server_port)]) == 1
    finally:
        server.server_close()


# -- the wrapper stays standalone ---------------------------------------------------------------


def test_the_wrapper_imports_only_the_standard_library() -> None:
    tree = ast.parse(SERVER_SCRIPT.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module.split(".")[0])
    assert modules - {"__future__"} <= sys.stdlib_module_names, modules
    dynamic = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "import_module"
        and isinstance(node.args[0], ast.Constant)
    }
    assert dynamic == {"torch", "transformers"}  # imported only when a model is loaded


def test_the_wrapper_runs_by_path_in_isolated_mode() -> None:
    done = subprocess.run(
        [sys.executable, "-I", str(SERVER_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "--model" in done.stdout and "--served-model-name" in done.stdout


# -- the adapter --------------------------------------------------------------------------------


def test_command_line_from_the_recipe() -> None:
    engine = TransformersEngine(python=sys.executable)
    assert engine.launcher() == [str(Path(sys.executable)), "-I", str(SERVER_SCRIPT)]
    recipe = EngineRecipe(args=["--device", "cpu", "--dtype", "float32", "--max-tokens", "512"])
    cmd = engine.command(entry(recipe), "/models/tiny-chat", port=41234)
    assert cmd == [
        *engine.launcher(),
        "--model",
        "/models/tiny-chat",
        "--device",
        "cpu",
        "--dtype",
        "float32",
        "--max-tokens",
        "512",
        "--host",
        "127.0.0.1",
        "--port",
        "41234",
        "--served-model-name",
        "tiny-chat",
    ]
    sized = engine.command(
        entry(EngineRecipe(args=["--ctx-size", "2048"])), "m", port=1, ctx_size=8192
    )
    assert sized[sized.index("--ctx-size") + 1] == "8192" and sized.count("--ctx-size") == 1
    appended = engine.command(entry(), "m", port=1, ctx_size=1024)
    assert appended[appended.index("--ctx-size") + 1] == "1024"


@pytest.mark.parametrize(
    "bad",
    [["--host", "0.0.0.0"], ["--port=9"], ["--model", "other"], ["--served-model-name", "x"]],
)
def test_owned_flags_cannot_be_overridden(bad: list[str]) -> None:
    engine = TransformersEngine(python=sys.executable)
    with pytest.raises(EngineError, match="may not set"):
        engine.command(entry(EngineRecipe(args=bad)), "m", port=1)
    with pytest.raises(EngineError, match="may not set"):
        engine.command(entry(), "m", port=1, extra_args=bad)


def test_the_key_and_the_offline_settings_travel_in_the_environment() -> None:
    engine = TransformersEngine(python=sys.executable, env={"HF_HUB_OFFLINE": "0", "X": "1"})
    base = {"PATH": "p", ts.API_KEY_ENV: "inherited", "HF_HUB_DISABLE_TELEMETRY": "0"}
    env = engine.server_env("k3y", base=base)
    assert env[ts.API_KEY_ENV] == "k3y" and env["X"] == "1" and env["PATH"] == "p"
    assert env["HF_HUB_OFFLINE"] == "1" and env["HF_HUB_DISABLE_TELEMETRY"] == "1"
    assert env["DO_NOT_TRACK"] == "1"
    assert ts.API_KEY_ENV not in engine.server_env(None, base=base)


def test_availability_needs_an_interpreter_with_transformers_and_torch(tmp_path: Path) -> None:
    missing = TransformersEngine()
    assert not missing.available()
    assert "[engines.transformers] python" in str(missing.unavailable_reason())
    with pytest.raises(EngineError, match="engines.transformers"):
        missing.launcher()
    gone = TransformersEngine(python=tmp_path / "no" / "python")
    assert "was not found" in str(gone.unavailable_reason())
    bare = TransformersEngine(python=fake_environment(tmp_path / "bare", []))
    assert "transformers, torch not installed" in str(bare.unavailable_reason())
    ready = fake_environment(tmp_path / "ready", ["transformers-5.20.0", "torch-2.13.0"])
    engine = TransformersEngine(python=ready)
    assert engine.available() and engine.version() == "5.20.0"
    assert engine.launcher() == [str(ready), "-I", str(SERVER_SCRIPT)]


def test_model_sources(tmp_path: Path) -> None:
    prefix = [sys.executable, "-I", str(SERVER_SCRIPT)]
    engine = TransformersEngine(command_prefix=prefix, models_dir=tmp_path)
    assert engine.model_source(entry()) is None
    assert "config.json" in str(engine.serve_problem(entry()))
    folder = tmp_path / "test--tiny-chat"
    folder.mkdir()
    (folder / "config.json").write_text("{}", encoding="utf-8")
    assert engine.model_source(entry()) == str(folder)
    assert engine.model_source(entry(EngineRecipe(name="org/tiny-chat"))) == "org/tiny-chat"
    assert engine.model_source(entry(), model_path=tmp_path / "x") == str(tmp_path / "x")
    remote = entry(EngineRecipe(name="https://example.org/models/tiny-chat"))
    assert "remote" in str(engine.serve_problem(remote))
    with pytest.raises(EngineError, match="remote"):
        engine.start(remote)
    other = entry().model_copy(update={"engines": {}})
    assert "no transformers recipe" in str(engine.serve_problem(other))


def test_no_metrics() -> None:
    engine = TransformersEngine(python=sys.executable)
    info = ServerInfo("m", "transformers", "http://127.0.0.1:9", 9, None, 0.0, 0.0)
    assert engine.metrics(info) is None and engine.prefix_cache_stats(info) is None


# -- a real process (stand-in backend) ----------------------------------------------------------


@pytest.fixture
def started() -> Iterator[list[tuple[TransformersEngine, ServerInfo]]]:
    servers: list[tuple[TransformersEngine, ServerInfo]] = []
    yield servers
    for engine, info in servers:
        engine.stop(info, timeout=5.0)


def backend_pid(log: Path, timeout: float = 10.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        match = re.search(r"pid=(\d+) listening", log.read_text(errors="replace"))
        if match:
            return int(match.group(1))
        time.sleep(0.05)
    raise AssertionError(f"no pid line in {log}")


def test_stand_in_server_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    started: list[tuple[TransformersEngine, ServerInfo]],
) -> None:
    monkeypatch.setenv("FAKE_TRANSFORMERS_LOAD_SECONDS", "1.0")
    monkeypatch.setenv(ts.API_KEY_ENV, "inherited-must-not-leak")
    folder = tmp_path / "models" / "test--tiny-chat"
    folder.mkdir(parents=True)
    (folder / "config.json").write_text("{}", encoding="utf-8")
    engine = TransformersEngine(
        command_prefix=[sys.executable, "-I", str(BACKEND)],
        models_dir=tmp_path / "models",
        egress=net.Egress(),
    )
    log = tmp_path / "logs" / "tiny-chat.log"
    info = engine.start(entry(EngineRecipe(args=["--max-tokens", "8"])), api_key=KEY, log_path=log)
    started.append((engine, info))
    assert info.base_url == f"http://127.0.0.1:{info.port}" and info.engine_model == "tiny-chat"
    assert info.api_key == KEY and info.managed and info.engine == "transformers"

    statuses: list[int] = []
    deadline = time.monotonic() + 30
    while 200 not in statuses and time.monotonic() < deadline:
        with contextlib.suppress(httpx.TransportError):  # not listening yet
            statuses.append(httpx.get(f"{info.base_url}/health", timeout=1).status_code)
        assert engine.alive(info), log.read_text(errors="replace")
        time.sleep(0.05)
    assert statuses[0] == 503 and statuses[-1] == 200
    assert engine.health(info)

    response = httpx.post(
        f"{info.base_url}/v1/chat/completions",
        json={"model": "tiny-chat", "messages": [{"role": "user", "content": "hello there"}]},
        headers=info.headers(),
        timeout=10,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["choices"][0]["message"]["content"] == "echo: hello there"
    assert data["usage"] == {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}
    assert httpx.get(f"{info.base_url}/v1/models").status_code == 401
    assert engine.metrics(info) is None

    text = log.read_text(errors="replace")
    assert KEY not in text and "inherited-must-not-leak" not in text
    assert f"model={folder}" in text
    env = json.loads(re.search(r"env=(\{.*\})", text).group(1))  # type: ignore[union-attr]
    assert env == {
        "HF_HUB_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "DO_NOT_TRACK": "1",
        "key": "set",
    }
    pid = backend_pid(log)
    engine.stop(info, timeout=5.0)
    assert not engine.alive(info) and not engine.health(info)
    deadline = time.monotonic() + 10
    while pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not pid_alive(pid), "the server outlived stop()"
    engine.stop(info)  # stopping twice is harmless


def test_stand_in_server_that_cannot_load_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    started: list[tuple[TransformersEngine, ServerInfo]],
) -> None:
    monkeypatch.setenv("FAKE_TRANSFORMERS_FAIL", "safetensors header is corrupt")
    monkeypatch.setenv("FAKE_TRANSFORMERS_LOAD_SECONDS", "0")
    engine = TransformersEngine(command_prefix=[sys.executable, "-I", str(BACKEND)])
    log = tmp_path / "broken.log"
    info = engine.start(entry(), model_path=tmp_path, api_key=KEY, log_path=log)
    started.append((engine, info))
    deadline = time.monotonic() + 30
    while engine.alive(info) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not engine.alive(info)
    text = log.read_text(errors="replace")
    assert "failed to load" in text and "safetensors header is corrupt" in text
