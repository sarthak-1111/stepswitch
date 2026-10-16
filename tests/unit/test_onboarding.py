"""`ssw models add` for local files, hub repositories and endpoints (compat.onboarding)."""

from __future__ import annotations

import hashlib
import struct
import tomllib
from pathlib import Path
from typing import Any

import httpx
import pytest

from stepswitch import config as config_module
from stepswitch import hub, modelstore, net, paths, registry, tomlw
from stepswitch.compat import onboarding
from stepswitch.config import Config
from stepswitch.gguf import STRING, UINT32

HUB = "https://hub.example.test"
REPO = "example-org/Tiny-Chat-GGUF"
SHA = "89abcdef0123456789abcdef0123456789abcdef"
TEMPLATE = (
    "{%- if tools %}<|im_start|>system\n# Tools\n<tools>{{ tools | tojson }}</tools>\n"
    '<tool_call>\n{"name": <function-name>, "arguments": <args-json-object>}\n</tool_call>'
    "<|im_end|>\n{%- endif %}{%- for m in messages %}<|im_start|>{{ m.role }}\n"
    "{{ m.content }}<|im_end|>\n{%- endfor %}"
)


@pytest.fixture(autouse=True)
def _hub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(hub.HUB_URL_ENV, HUB)
    monkeypatch.delenv(hub.TOKEN_ENV, raising=False)


class Recorder:
    def __init__(self, routes: dict[str, httpx.Response | Any]) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get(request.url.path)
        if route is None:
            return httpx.Response(404, json={"error": "not found"})
        if isinstance(route, httpx.Response):
            return route
        return httpx.Response(200, json=route)

    @property
    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]


# --------------------------------------------------------------------------------------------
# A small GGUF writer (strings and uint32 values, plus tensor infos)
# --------------------------------------------------------------------------------------------


def _gguf_string(text: str) -> bytes:
    data = text.encode("utf-8")
    return struct.pack("<Q", len(data)) + data


def write_gguf(path: Path, kvs: list[tuple[str, int, Any]], tensors: int = 2) -> Path:
    out = b"GGUF" + struct.pack("<IQQ", 3, tensors, len(kvs))
    for key, vtype, value in kvs:
        out += _gguf_string(key) + struct.pack("<I", vtype)
        out += _gguf_string(value) if vtype == STRING else struct.pack("<I", value)
    for index in range(tensors):
        out += _gguf_string(f"blk.{index}.weight") + struct.pack("<I2Q", 2, 16, 8)
        out += struct.pack("<IQ", 0, index * 128)
    out += b"\0" * ((-len(out)) % 32) + b"\0" * (128 * tensors)
    path.write_bytes(out)
    return path


def chat_gguf(path: Path) -> Path:
    return write_gguf(
        path,
        [
            ("general.architecture", STRING, "qwen3"),
            ("general.name", STRING, "Tiny Chat"),
            ("general.license", STRING, "apache-2.0"),
            ("general.file_type", UINT32, 15),
            ("qwen3.context_length", UINT32, 32768),
            ("tokenizer.chat_template", STRING, TEMPLATE),
        ],
    )


def add(target: str, tmp_path: Path, **kwargs: Any) -> onboarding.AddResult:
    kwargs.setdefault("config", Config())
    kwargs.setdefault("registry_dir", tmp_path / "user-registry")
    kwargs.setdefault("egress", net.Egress())
    kwargs.setdefault("echo", lambda message: None)
    return onboarding.add(target, root=tmp_path / "project", **kwargs)


# --------------------------------------------------------------------------------------------
# Target detection
# --------------------------------------------------------------------------------------------


def test_detect_target(tmp_path: Path) -> None:
    existing = tmp_path / "weights.bin"
    existing.write_bytes(b"x")
    assert onboarding.detect_target("https://models.example.test/v1") == "url"
    assert onboarding.detect_target("HTTP://127.0.0.1:8080") == "url"
    assert onboarding.detect_target(str(existing)) == "path"
    assert onboarding.detect_target("not-there/model-Q4_K_M.gguf") == "path"
    assert onboarding.detect_target(REPO) == "hub"
    for bad in ("just-a-name", "a/b/c", "org/name--x", "ftp://host/x"):
        with pytest.raises(ValueError, match="cannot tell"):
            onboarding.detect_target(bad)


# --------------------------------------------------------------------------------------------
# Local GGUF files
# --------------------------------------------------------------------------------------------


def test_add_local_gguf(tmp_path: Path) -> None:
    (tmp_path / "files").mkdir()
    model = chat_gguf(tmp_path / "files" / "Tiny-Chat-Q4_K_M.gguf").resolve()
    lines: list[str] = []
    result = add(str(model), tmp_path, echo=lines.append)
    assert result.kind == "path" and result.id == "tiny-chat-q4_k_m"
    assert result.endpoint_name is None and result.registry_path is not None
    assert result.registry_path == tmp_path / "user-registry" / "tiny-chat-q4_k_m.toml"

    entry = registry.read_entry(result.registry_path)
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    assert entry.sha256 == digest and entry.size_bytes == model.stat().st_size
    assert entry.architecture == "qwen3" and entry.context == 32768
    assert entry.quant == "Q4_K_M" and entry.format == "gguf"
    assert entry.tool_calls == "native" and entry.tool_syntax == "hermes"
    assert entry.template_sha256 == hashlib.sha256(TEMPLATE.encode()).hexdigest()
    assert entry.source == "path"
    assert entry.license == "apache-2.0" and entry.commercial_use == "permitted"
    assert entry.params_b == 0.0 and result.details["params"] == 256
    assert entry.ram_gb == round(model.stat().st_size / 1e9 * 1.15 + 0.5, 2)
    assert "estimate" in (entry.notes or "")
    assert "llamacpp" in entry.engines
    text = result.registry_path.read_text(encoding="utf-8")
    assert text.startswith("# Model registry entry written by `ssw models add`")

    # The entry points at the file in place and counts as installed (stamp next to the file).
    models_dir = tmp_path / "models"
    assert entry.local_path(models_dir) == model
    assert entry.installed(models_dir)
    assert (model.parent / (model.name + registry.STAMP_SUFFIX)).is_file()
    plan = modelstore.plan([entry], models_dir=models_dir)
    assert [item.status for item in plan.items] == ["installed"]
    assert any("Registered tiny-chat-q4_k_m" in line for line in lines)

    with pytest.raises(ValueError, match="already exists"):
        add(str(model), tmp_path)
    named = add(str(model), tmp_path, name="My Tiny Chat")
    assert named.id == "my-tiny-chat"


def test_add_local_gguf_without_template_or_license(tmp_path: Path) -> None:
    model = write_gguf(tmp_path / "base.gguf", [("general.architecture", STRING, "llama")])
    result = add(str(model), tmp_path)
    assert result.registry_path is not None
    entry = registry.read_entry(result.registry_path)
    assert entry.tool_calls == "fallback" and entry.tool_syntax is None
    assert entry.license == "unknown" and entry.commercial_use == "conditional"
    assert entry.context == onboarding.DEFAULT_CONTEXT
    assert "assumed" in (entry.notes or "") and "No license" in (entry.notes or "")
    assert entry.quant == "unknown"


def test_add_rejects_non_gguf_files_and_other_engines(tmp_path: Path) -> None:
    fake = tmp_path / "fake.gguf"
    fake.write_bytes(b"not a gguf at all")
    with pytest.raises(ValueError, match="not a GGUF file"):
        add(str(fake), tmp_path)
    with pytest.raises(ValueError, match="no such file"):
        add(str(tmp_path / "missing.gguf"), tmp_path)
    model = chat_gguf(tmp_path / "ok.gguf")
    with pytest.raises(ValueError, match="cannot serve"):
        add(str(model), tmp_path, engine="vllm")
    split = write_gguf(
        tmp_path / "big-00001-of-00003.gguf",
        [("general.architecture", STRING, "qwen3"), ("split.count", UINT32, 3)],
    )
    with pytest.raises(ValueError, match="3-part split GGUF"):
        add(str(split), tmp_path)
    assert not (tmp_path / "user-registry").exists()


# --------------------------------------------------------------------------------------------
# Model hub repositories
# --------------------------------------------------------------------------------------------


def _lfs(path: str, size: int) -> dict[str, Any]:
    digest = hashlib.sha256(path.encode()).hexdigest()
    return {
        "type": "file",
        "oid": "0" * 40,
        "size": size,
        "path": path,
        "lfs": {"oid": digest, "size": size, "pointerSize": 134},
    }


TREE = [
    {"type": "file", "oid": "1" * 40, "size": 1519, "path": ".gitattributes"},
    _lfs("Tiny-Chat-Q4_K_M.gguf", 2_497_281_120),
    _lfs("Tiny-Chat-UD-Q4_K_XL.gguf", 2_546_340_960),
    _lfs("Tiny-Chat-Q8_0.gguf", 4_280_405_600),
    _lfs("Tiny-Chat-Q2_K.gguf", 1_669_500_000),
    _lfs("mmproj-F16.gguf", 600_000_000),
    {"type": "directory", "oid": "2" * 40, "size": 0, "path": "UD-Q2_K_XL"},
    _lfs("UD-Q2_K_XL/Tiny-Chat-UD-Q2_K_XL-00001-of-00002.gguf", 50_000_000_000),
    _lfs("UD-Q2_K_XL/Tiny-Chat-UD-Q2_K_XL-00002-of-00002.gguf", 40_000_000_000),
    {"type": "file", "oid": "3" * 40, "size": 2000, "path": "README.md"},
]


def hub_info(**overrides: Any) -> dict[str, Any]:
    info: dict[str, Any] = {
        "id": REPO,
        "sha": SHA,
        "gated": False,
        "tags": ["gguf", "license:apache-2.0"],
        "cardData": {"license": "apache-2.0", "base_model": ["example-org/Tiny-Chat"]},
        "gguf": {
            "total": 4_022_468_096,
            "architecture": "qwen3",
            "context_length": 262144,
            "chat_template": TEMPLATE,
        },
    }
    info.update(overrides)
    return info


def hub_routes(info: dict[str, Any] | None = None) -> Recorder:
    return Recorder(
        {
            f"/api/models/{REPO}": info or hub_info(),
            f"/api/models/{REPO}/tree/{SHA}": TREE,
        }
    )


def test_add_hub_repository(tmp_path: Path) -> None:
    recorder = hub_routes()
    lines: list[str] = []
    result = add(REPO, tmp_path, transport=recorder.transport, echo=lines.append)
    assert result.kind == "hub" and result.id == "tiny-chat-q4_k_m"
    assert recorder.paths == [f"/api/models/{REPO}", f"/api/models/{REPO}/tree/{SHA}"]
    assert all("/resolve/" not in path for path in recorder.paths)  # nothing downloaded
    assert result.registry_path is not None
    entry = registry.read_entry(result.registry_path)
    assert entry.hub_id == REPO and entry.revision == SHA
    assert entry.file == "Tiny-Chat-Q4_K_M.gguf" and entry.quant == "Q4_K_M"
    assert entry.size_bytes == 2_497_281_120
    assert entry.sha256 == hashlib.sha256(b"Tiny-Chat-Q4_K_M.gguf").hexdigest()
    assert entry.file_size_gb == 2.5 and entry.ram_gb == round(2.49728112 * 1.15 + 0.5, 2)
    assert entry.params_b == 4.02 and entry.context == 262144
    assert entry.architecture == "qwen3" and entry.source_model == "example-org/Tiny-Chat"
    assert entry.tool_calls == "native" and entry.tool_syntax == "hermes"
    assert entry.template_sha256 == hashlib.sha256(TEMPLATE.encode()).hexdigest()
    assert entry.license == "apache-2.0" and entry.commercial_use == "permitted"
    assert entry.engines["ollama"].name == f"hf.co/{REPO}:Tiny-Chat-Q4_K_M.gguf"
    assert result.details["choice"] == "Q4_K_M, the default quantization"
    assert any("ssw models pull tiny-chat-q4_k_m" in line for line in lines)
    # The new entry is pullable as planned: a download of the pinned file.
    plan = modelstore.plan([entry], models_dir=tmp_path / "models")
    assert [item.status for item in plan.items] == ["download"]
    assert hub.file_url(entry.hub_id, entry.revision or "", entry.file or "").endswith(
        f"/resolve/{SHA}/Tiny-Chat-Q4_K_M.gguf"
    )


def test_add_hub_with_quant_or_file(tmp_path: Path) -> None:
    by_quant = add(REPO, tmp_path, quant="q8_0", transport=hub_routes().transport)
    assert by_quant.id == "tiny-chat-q8_0" and by_quant.details["file"] == "Tiny-Chat-Q8_0.gguf"
    by_file = add(
        REPO, tmp_path, file="Tiny-Chat-UD-Q4_K_XL.gguf", transport=hub_routes().transport
    )
    assert by_file.details["quant"] == "UD-Q4_K_XL" and by_file.id == "tiny-chat-ud-q4_k_xl"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"quant": "UD-Q2_K_XL"}, "split multi-part GGUF"),
        ({"file": "UD-Q2_K_XL/Tiny-Chat-UD-Q2_K_XL-00001-of-00002.gguf"}, "split multi-part"),
        ({"quant": "Q3_K_S"}, "available: .*Q4_K_M"),
        ({"file": "missing.gguf"}, "not in the repository"),
    ],
)
def test_add_hub_file_choice_errors(tmp_path: Path, kwargs: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        add(REPO, tmp_path, transport=hub_routes().transport, **kwargs)
    assert not (tmp_path / "user-registry").exists()


def test_choose_file_rules() -> None:
    def files(*specs: tuple[str, int]) -> list[hub.HubFile]:
        return [hub.HubFile(path, size, None) for path, size in specs]

    smallest, why = onboarding.choose_file(
        files(("m-Q8_0.gguf", 900), ("m-Q6_K.gguf", 700), ("mmproj-m-F16.gguf", 10))
    )
    assert smallest.path == "m-Q6_K.gguf" and "smallest" in why
    standard, _ = onboarding.choose_file(
        files(("m-UD-Q4_K_M.gguf", 100), ("m-Q4_K_M.gguf", 120), ("m-Q2_K.gguf", 50))
    )
    assert standard.path == "m-Q4_K_M.gguf"
    with pytest.raises(ValueError, match="split"):
        onboarding.choose_file(files(("Q8_0/m-Q8_0-00001-of-00003.gguf", 9)))
    with pytest.raises(ValueError, match="auxiliary"):
        onboarding.choose_file(files(("mmproj-F16.gguf", 9), ("imatrix.gguf", 3)))
    with pytest.raises(ValueError, match="no GGUF files"):
        onboarding.choose_file(files(("model.safetensors", 9)))


def test_add_hub_flags_conditional_licenses_and_gating(tmp_path: Path) -> None:
    info = hub_info(
        gated="manual",
        cardData={"license": "other", "license_name": "tiny-community-1.0"},
    )
    lines: list[str] = []
    result = add(REPO, tmp_path, transport=hub_routes(info).transport, echo=lines.append)
    assert result.registry_path is not None
    entry = registry.read_entry(result.registry_path)
    assert entry.license == "tiny-community-1.0" and entry.commercial_use == "conditional"
    assert "Gated repository" in (entry.notes or "") and result.details["gated"] is True
    assert any("--accept-license tiny-chat-q4_k_m" in line for line in lines)
    plan = modelstore.plan([entry], models_dir=tmp_path / "models")
    assert [item.status for item in plan.items] == ["license"]


def test_add_hub_license_from_tags(tmp_path: Path) -> None:
    info = hub_info(cardData={}, tags=["gguf", "license:mit"])
    result = add(REPO, tmp_path, transport=hub_routes(info).transport)
    assert result.details["license"] == "mit" and result.details["commercial_use"] == "permitted"


def test_embedding_models_never_call_tools(tmp_path: Path) -> None:
    summary = {**hub_info()["gguf"], "pooling_type": 3}  # template still has tool markers
    hub_result = add(REPO, tmp_path, transport=hub_routes(hub_info(gguf=summary)).transport)
    assert hub_result.details["tool_calls"] == "none"
    assert hub_result.details["tool_syntax"] is None
    model = write_gguf(
        tmp_path / "embed-Q8_0.gguf",
        [
            ("general.architecture", STRING, "qwen3"),
            ("qwen3.pooling_type", UINT32, 3),
            ("tokenizer.chat_template", STRING, TEMPLATE),
        ],
    )
    path_result = add(str(model), tmp_path)
    assert path_result.details["tool_calls"] == "none"
    assert onboarding.tool_support(TEMPLATE, 0) == ("native", "hermes")


def test_add_hub_needs_a_pinned_commit(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="commit"):
        add(REPO, tmp_path, transport=hub_routes(hub_info(sha=None)).transport)


def test_add_hub_offline_makes_no_request(tmp_path: Path) -> None:
    recorder = hub_routes()
    offline = Config.model_validate({"privacy": {"mode": "local"}})
    with pytest.raises(ValueError, match="offline"):
        add(REPO, tmp_path, config=offline, transport=recorder.transport)
    assert recorder.requests == []


def test_license_expressions() -> None:
    assert onboarding.commercial_use("MIT") == "permitted"
    assert onboarding.commercial_use("MIT OR Apache-2.0") == "permitted"
    assert onboarding.commercial_use("(gpl-3.0 OR mit)") == "permitted"
    assert onboarding.commercial_use("mit AND cc-by-nc-4.0") == "conditional"
    assert onboarding.commercial_use("llama-community") == "conditional"


# --------------------------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------------------------

EXISTING_LOCAL = """\
# Machine-specific settings (hand-written comment that must survive).
schema = 1

[storage]
max_gb = 8.0  # inline comment

[[endpoints]]
name = "existing"
url = "http://127.0.0.1:9000"
"""


def local_toml(tmp_path: Path, text: str | None = EXISTING_LOCAL) -> Path:
    path = paths.local_config_path(tmp_path / "project")
    path.parent.mkdir(parents=True, exist_ok=True)
    if text is not None:
        path.write_text(text, encoding="utf-8")
        tomlw.write_block(path, "host", {"host": {"detected": {"os": "testos"}}})
    return path


OLLAMA_ROUTES: dict[str, Any] = {
    "/api/version": {"version": "0.34.4"},
    "/api/tags": {
        "models": [
            {"name": "qwen3:4b-instruct-2507-q4_K_M", "model": "qwen3:4b-instruct-2507-q4_K_M"},
            {"name": "big-model:cloud", "remote_host": "https://remote.example.test"},
        ]
    },
}


def test_add_ollama_endpoint_appends_and_keeps_the_file(tmp_path: Path) -> None:
    path = local_toml(tmp_path)
    before = path.read_text(encoding="utf-8")
    recorder = Recorder(dict(OLLAMA_ROUTES))
    lines: list[str] = []
    result = add(
        "http://127.0.0.1:11434/", tmp_path, transport=recorder.transport, echo=lines.append
    )
    assert result.kind == "url" and result.endpoint_name == "127-0-0-1-11434"
    assert result.registry_path is None
    assert result.details["engine"] == "ollama" and result.details["version"] == "0.34.4"
    after = path.read_text(encoding="utf-8")
    assert after.startswith(before)  # comments, tables and the managed block are untouched
    data = tomllib.loads(after)
    existing, added = data["endpoints"]
    assert existing["name"] == "existing"
    assert added == {
        "name": "127-0-0-1-11434",
        "url": "http://127.0.0.1:11434",
        "engine": "ollama",
        "formats": ["ollama-native", "chat-completions"],
        "models": ["qwen3:4b-instruct-2507-q4_K_M", "big-model:cloud"],
        "auth": "none",
    }
    assert any("hosted service" in line for line in lines)
    assert all(request.method == "GET" for request in recorder.requests)
    # The project configuration loads it.
    loaded = config_module.load(tmp_path / "project", environ={})
    endpoint = loaded.endpoint("127-0-0-1-11434")
    assert endpoint is not None and endpoint.primary_format() == "ollama-native"

    with pytest.raises(ValueError, match="already exists"):
        add("http://127.0.0.1:11434", tmp_path, config=loaded, transport=recorder.transport)
    with pytest.raises(ValueError, match="already exists"):
        add("http://127.0.0.1:11434", tmp_path, transport=recorder.transport)
    assert path.read_text(encoding="utf-8") == after


def test_offline_mode_leaves_out_cloud_backed_ollama_models(tmp_path: Path) -> None:
    local_toml(tmp_path, None)
    offline = Config.model_validate({"privacy": {"mode": "local"}})
    result = add(
        "http://localhost:11434",
        tmp_path,
        config=offline,
        transport=Recorder(OLLAMA_ROUTES).transport,
    )
    assert result.details["models"] == ["qwen3:4b-instruct-2507-q4_K_M"]
    path = paths.local_config_path(tmp_path / "project")
    assert path.read_text(encoding="utf-8").startswith(config_module.LOCAL_TEMPLATE_HEADER)


def test_add_llamacpp_endpoint_from_props(tmp_path: Path) -> None:
    local_toml(tmp_path, None)
    recorder = Recorder(
        {
            "/props": {"default_generation_settings": {}, "total_slots": 2, "build_info": "b11669"},
            "/v1/models": {"object": "list", "data": [{"id": "tiny-chat", "object": "model"}]},
        }
    )
    result = add("http://127.0.0.1:9931/v1", tmp_path, name="router", transport=recorder.transport)
    assert result.endpoint_name == "router" and result.id == "router"
    assert result.details["engine"] == "llamacpp" and result.details["models"] == ["tiny-chat"]
    assert result.details["formats"] == ["chat-completions", "llamacpp-native", "text-completion"]
    assert result.details["detected_by"] == "/props" and result.details["version"] == "b11669"
    assert "/api/version" in recorder.paths and "/v1/models" in recorder.paths
    endpoint = tomllib.loads(paths.local_config_path(tmp_path / "project").read_text("utf-8"))
    assert endpoint["endpoints"][0]["url"] == "http://127.0.0.1:9931/v1"


def test_add_llamacpp_endpoint_while_loading(tmp_path: Path) -> None:
    local_toml(tmp_path, None)
    loading = {"error": {"code": 503, "message": "Loading model", "type": "unavailable_error"}}
    recorder = Recorder(
        {"/props": httpx.Response(503, json=loading), "/health": httpx.Response(503, json=loading)}
    )
    result = add("http://127.0.0.1:9931", tmp_path, transport=recorder.transport)
    assert result.details["engine"] == "llamacpp" and result.details["detected_by"] == "/health"
    assert result.details["models"] == []


def test_add_generic_chat_completions_endpoint(tmp_path: Path) -> None:
    local_toml(tmp_path, None)
    recorder = Recorder({"/v1/models": {"data": [{"id": "model-a"}, {"id": "model-b"}]}})
    result = add("http://127.0.0.1:8000", tmp_path, transport=recorder.transport)
    assert result.details["engine"] is None and result.details["formats"] == ["chat-completions"]
    stored = tomllib.loads(paths.local_config_path(tmp_path / "project").read_text("utf-8"))
    (endpoint,) = stored["endpoints"]
    assert "engine" not in endpoint and endpoint["models"] == ["model-a", "model-b"]


def test_unreachable_endpoint_needs_an_engine(tmp_path: Path) -> None:
    local_toml(tmp_path, None)
    recorder = Recorder({})
    with pytest.raises(ValueError, match="no supported server answered"):
        add("http://127.0.0.1:8000", tmp_path, transport=recorder.transport)
    lines: list[str] = []
    result = add(
        "http://127.0.0.1:8000",
        tmp_path,
        engine="vllm",
        transport=recorder.transport,
        echo=lines.append,
    )
    assert result.details["engine"] == "vllm" and result.details["formats"] == ["chat-completions"]
    assert any("anyway" in line for line in lines)


def test_api_key_is_used_for_probes_but_never_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_toml(tmp_path, None)
    monkeypatch.setenv("TINY_ENDPOINT_KEY", "not-a-real-key-123")
    recorder = Recorder({"/v1/models": {"data": [{"id": "remote-model"}]}})
    result = add(
        "https://models.example.test/v1",
        tmp_path,
        name="remote",
        api_key_env="TINY_ENDPOINT_KEY",
        transport=recorder.transport,
    )
    assert all(
        request.headers["authorization"] == "Bearer not-a-real-key-123"
        for request in recorder.requests
    )
    text = paths.local_config_path(tmp_path / "project").read_text(encoding="utf-8")
    assert "not-a-real-key-123" not in text
    (endpoint,) = tomllib.loads(text)["endpoints"]
    assert endpoint["auth"] == "bearer" and endpoint["api_key_env"] == "TINY_ENDPOINT_KEY"
    assert result.details["api_key_env"] == "TINY_ENDPOINT_KEY"
    loaded = config_module.load(tmp_path / "project", environ={})
    configured = loaded.endpoint("remote")
    assert configured is not None and configured.resolved_api_key() == "not-a-real-key-123"


@pytest.mark.parametrize(
    ("url", "kwargs", "message"),
    [
        ("http://127.0.0.1:8000", {"api_key_env": "abcDEF0123456789abcDEF0123456789"}, "name of"),
        ("http://127.0.0.1:8000", {"api_key_env": "has-dash"}, "name of"),
        ("https://user:pw@models.example.test", {}, "credentials"),
        ("https://models.example.test/v1?api_key=abc", {}, "key or token"),
        ("https://models.example.test/v1?api-version=1", {}, "query string"),
        ("ftp://models.example.test", {}, "http"),
        ("http://127.0.0.1:8000", {"name": "bad name!"}, "endpoint name"),
        ("http://127.0.0.1:8000", {"engine": "Bad Engine"}, "engine name"),
    ],
)
def test_endpoint_input_checks(
    tmp_path: Path, url: str, kwargs: dict[str, str], message: str
) -> None:
    recorder = Recorder({"/v1/models": {"data": []}})
    with pytest.raises(ValueError, match=message):
        add(url, tmp_path, transport=recorder.transport, **kwargs)
    assert recorder.requests == []
    assert not paths.local_config_path(tmp_path / "project").exists()


def test_offline_mode_refuses_remote_endpoints(tmp_path: Path) -> None:
    recorder = Recorder({"/v1/models": {"data": []}})
    offline = Config.model_validate({"privacy": {"mode": "local"}})
    with pytest.raises(ValueError, match="offline"):
        add("https://models.example.test", tmp_path, config=offline, transport=recorder.transport)
    assert recorder.requests == []


def test_inline_endpoints_array_is_not_rewritten(tmp_path: Path) -> None:
    text = 'schema = 1\nendpoints = [{ name = "inline", url = "http://127.0.0.1:1" }]\n'
    path = local_toml(tmp_path, None)
    path.write_text(text, encoding="utf-8")
    recorder = Recorder({"/v1/models": {"data": []}})
    with pytest.raises(ValueError, match="by hand"):
        add("http://127.0.0.1:8000", tmp_path, transport=recorder.transport)
    assert path.read_text(encoding="utf-8") == text


def test_crlf_files_keep_their_line_endings(tmp_path: Path) -> None:
    original = b"# Windows-edited file\r\nschema = 1\r\n\r\n[storage]\r\nmax_gb = 4.0\r\n"
    path = local_toml(tmp_path, None)
    path.write_bytes(original)
    recorder = Recorder({"/v1/models": {"data": [{"id": "m"}]}})
    add("http://127.0.0.1:8000", tmp_path, transport=recorder.transport)
    after = path.read_bytes()
    assert after.startswith(original)
    assert b"\n" not in after.replace(b"\r\n", b"")  # no bare LF was mixed in
    assert tomllib.loads(after.decode("utf-8"))["endpoints"][0]["models"] == ["m"]


def test_server_that_wants_a_key_gets_a_hint(tmp_path: Path) -> None:
    denied = httpx.Response(401, json={"error": "unauthorized"})
    recorder = Recorder({"/v1/models": denied, "/api/version": denied})
    with pytest.raises(ValueError, match="--api-key-env"):
        add("https://models.example.test/v1", tmp_path, transport=recorder.transport)
    assert not paths.local_config_path(tmp_path / "project").exists()


def test_endpoint_slug() -> None:
    assert onboarding.endpoint_slug("http://127.0.0.1:11434") == "127-0-0-1-11434"
    assert onboarding.endpoint_slug("https://models.example.test/v1") == ("models-example-test-443")
    assert onboarding.endpoint_slug("http://[::1]:8080") == "ipv6-1-8080"
    assert onboarding.slug("  Qwen3 4B -- Instruct!! ") == "qwen3-4b-instruct"
    with pytest.raises(ValueError):
        onboarding.slug("!!!")
