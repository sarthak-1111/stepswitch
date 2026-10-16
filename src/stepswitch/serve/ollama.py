"""The Ollama adapter (SPEC §4.2, §5.3; ADR 0008).

Ollama loads and unloads models on its own, so it is a self-managing engine: the server manager
never evicts for it and does not count its memory. "Starting" a model means preloading it with
an empty generate request that carries a keep-alive (Ollama unloads it after that idle time);
"stopping" sends keep-alive 0, and only for models the manager loaded itself. Models that were
already loaded by someone else are never unloaded or given a new keep-alive.

Endpoints and semantics were checked against the tag-pinned API reference of v0.34.4 and the
running v0.34.4 server (RESEARCH.md, "Ollama" and "Ollama native API").
"""

from __future__ import annotations

import ipaddress
import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx

from stepswitch import net
from stepswitch.registry import ModelEntry
from stepswitch.serve.base import Engine, EngineError, ServerInfo, error_text, http_client

DEFAULT_URL = "http://127.0.0.1:11434"
ENGINE = "ollama"
PURPOSE = "local-engine"
PULL_PURPOSE = "model-pull"
DEFAULT_KEEP_ALIVE = "30m"


def base_url(environ: Mapping[str, str] | None = None) -> str:
    """The Ollama URL from `OLLAMA_HOST`, by Ollama's own rules (envconfig, v0.34.4).

    No scheme means http and port 11434; an explicit http or https scheme without a port means
    80 or 443. An unspecified bind address (0.0.0.0, ::) becomes the matching loopback address,
    since it is valid for a server to bind but not for a client to connect to.
    """
    env = os.environ if environ is None else environ
    raw = env.get("OLLAMA_HOST", "").strip()
    if not raw:
        return DEFAULT_URL
    default_port = "11434"
    scheme, sep, hostport = raw.partition("://")
    if not sep:
        scheme, hostport = "http", raw
        if raw == "ollama.com":
            scheme, hostport = "https", "ollama.com:443"
    elif scheme == "http":
        default_port = "80"
    elif scheme == "https":
        default_port = "443"
    hostport = hostport.split("/", 1)[0]
    split = _split_host_port(hostport)
    if split is None:
        host, port = "127.0.0.1", default_port
        literal = _ip(hostport.strip("[]"))
        if literal is not None:
            host = str(literal)
        elif hostport:
            host = hostport
    else:
        host, port = split
    if not port.isdigit() or int(port) > 65535:
        port = default_port
    address = _ip(host)
    if address is not None and address.is_unspecified:
        host = "127.0.0.1" if address.version == 4 else "::1"
    host = host or "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    return f"{scheme}://{host}:{port}"


def _ip(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


def _split_host_port(hostport: str) -> tuple[str, str] | None:
    """Go's net.SplitHostPort: None when there is no port or the address is malformed."""
    if hostport.startswith("["):
        end = hostport.find("]")
        if end < 0 or hostport[end + 1 : end + 2] != ":":
            return None
        return hostport[1:end], hostport[end + 2 :]
    if hostport.count(":") != 1:
        return None
    host, port = hostport.split(":")
    return host, port


def _same_name(left: str | None, right: str | None) -> bool:
    """Ollama model names compare case-insensitively, with ":latest" as the default tag."""
    if not left or not right:
        return False

    def norm(name: str) -> str:
        name = name.strip().lower()
        return name if ":" in name.rsplit("/", 1)[-1] else f"{name}:latest"

    return norm(left) == norm(right)


def model_name(entry: ModelEntry) -> str:
    """The Ollama tag of a registry entry (its `[engines.ollama] name`, else its id)."""
    recipe = entry.engines.get(ENGINE)
    return recipe.name if recipe is not None and recipe.name else entry.id


def keep_alive_for(seconds: float) -> str:
    """A keep-alive duration in Ollama's duration syntax ("30m", "45s")."""
    whole = max(0, int(seconds))
    return f"{whole // 60}m" if whole and whole % 60 == 0 else f"{whole}s"


def pull_model(
    base_url: str,
    name: str,
    *,
    egress: net.Egress | None = None,
    transport: httpx.BaseTransport | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Pull a model into Ollama: POST /api/pull, streamed as NDJSON progress events.

    Raises EngineError on a non-200 answer, an `error` event, or a stream that ends without
    `"status": "success"`. Refused in offline mode: Ollama downloads from the network itself,
    behind a local URL the egress layer would otherwise allow (SPEC §15).
    """
    factory = egress or net.default()
    if factory.policy.offline:
        raise EngineError(
            f"offline mode: pulling {name} through Ollama would download it from the network"
        )
    url = f"{base_url.rstrip('/')}/api/pull"
    last: dict[str, Any] | None = None
    timeout = httpx.Timeout(30.0, read=600.0)
    try:
        with (
            http_client(PULL_PURPOSE, egress=factory, transport=transport, timeout=timeout) as c,
            c.stream("POST", url, json={"model": name, "stream": True}) as response,
        ):
            if response.status_code != 200:
                response.read()
                raise EngineError(
                    f"Ollama could not pull {name}: HTTP {response.status_code}: "
                    f"{error_text(response)}"
                )
            for line in response.iter_lines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    raise EngineError(
                        f"unreadable pull progress from Ollama: {line[:200]!r}"
                    ) from None
                if not isinstance(event, dict):
                    continue
                if event.get("error"):
                    raise EngineError(f"Ollama could not pull {name}: {event['error']}")
                last = event
                if progress is not None:
                    progress(event)
    except httpx.HTTPError as exc:
        raise EngineError(f"Ollama could not pull {name}: {exc}") from exc
    if last is None or last.get("status") != "success":
        raise EngineError(f"the pull of {name} ended without success")


class OllamaEngine(Engine):
    """A running Ollama server, reached over its native API."""

    name = ENGINE
    self_managed = True

    def __init__(
        self,
        url: str | None = None,
        *,
        egress: net.Egress | None = None,
        transport: httpx.BaseTransport | None = None,
        keep_alive: str = DEFAULT_KEEP_ALIVE,
        load_timeout: float = 600.0,
    ) -> None:
        self.url = (url or base_url()).rstrip("/")
        self.egress = egress
        self.transport = transport
        self.keep_alive = keep_alive
        self.load_timeout = load_timeout

    def _client(self, timeout: float | httpx.Timeout = 5.0) -> httpx.Client:
        return http_client(
            PURPOSE,
            egress=self.egress,
            transport=self.transport,
            base_url=self.url,
            timeout=timeout,
        )

    def _get(self, path: str) -> Any:
        try:
            with self._client() as client:
                response = client.get(path)
        except httpx.HTTPError as exc:
            raise EngineError(f"Ollama is not reachable at {self.url}: {exc}") from exc
        if response.status_code != 200:
            raise EngineError(f"Ollama {path}: HTTP {response.status_code}: {error_text(response)}")
        return response.json()

    def _offline(self) -> bool:
        return (self.egress or net.default()).policy.offline

    # -- read API -------------------------------------------------------------------------------

    def version(self) -> str | None:
        """GET /api/version, or None when Ollama is not reachable."""
        try:
            data = self._get("/api/version")
        except (EngineError, ValueError):
            return None
        value = data.get("version") if isinstance(data, dict) else None
        return str(value) if value else None

    def running(self) -> bool:
        return self.version() is not None

    def available(self) -> bool:
        return self.running()

    def list_models(self) -> list[dict[str, Any]]:
        """GET /api/tags: the models pulled into this Ollama."""
        return _models(self._get("/api/tags"))

    def loaded(self) -> list[dict[str, Any]]:
        """GET /api/ps: the models in memory, with `size`, `size_vram` and `expires_at`."""
        return _models(self._get("/api/ps"))

    def show(self, name: str) -> dict[str, Any]:
        """POST /api/show: capabilities, template, parameters, details and model info."""
        try:
            with self._client(30.0) as client:
                response = client.post("/api/show", json={"model": name})
        except httpx.HTTPError as exc:
            raise EngineError(f"Ollama is not reachable at {self.url}: {exc}") from exc
        if response.status_code != 200:
            raise EngineError(f"Ollama cannot show {name}: {error_text(response)}")
        data = response.json()
        return dict(data) if isinstance(data, dict) else {}

    def tag_info(self, name: str) -> dict[str, Any] | None:
        return next((m for m in self.list_models() if _same_name(m.get("name"), name)), None)

    def has_model(self, name: str) -> bool:
        return self.tag_info(name) is not None

    def loaded_info(self, name: str) -> dict[str, Any] | None:
        return next(
            (
                m
                for m in self.loaded()
                if _same_name(m.get("name"), name) or _same_name(m.get("model"), name)
            ),
            None,
        )

    # -- lifecycle ------------------------------------------------------------------------------

    def start(
        self,
        entry: ModelEntry,
        *,
        port: int | None = None,
        ctx_size: int | None = None,
        api_key: str | None = None,
        log_path: Path | None = None,
        extra_args: Sequence[str] = (),
        keep_alive: str | None = None,
    ) -> ServerInfo:
        """Preload the model: POST /api/generate with no prompt and a keep-alive.

        `port`, `api_key`, `log_path` and `extra_args` do not apply to a shared Ollama server.
        The context size (`num_ctx`) is always set, since Ollama's default varies (ADR 0008).
        """
        name = model_name(entry)
        if not self.running():
            raise EngineError(f"Ollama is not reachable at {self.url}")
        if self._offline():
            tag = self.tag_info(name) or {}
            if tag.get("remote_host") or tag.get("remote_model"):
                raise EngineError(f"offline mode: {name} is served by a remote host through Ollama")
        now = time.time()
        current = self.loaded_info(name)
        managed = current is None
        if current is None:
            body: dict[str, Any] = {
                "model": name,
                "keep_alive": keep_alive or self.keep_alive,
                "stream": False,
            }
            num_ctx = ctx_size or _recipe_ctx(entry)
            if num_ctx:
                body["options"] = {"num_ctx": int(num_ctx)}
            try:
                with self._client(httpx.Timeout(30.0, read=self.load_timeout)) as client:
                    response = client.post("/api/generate", json=body)
            except httpx.HTTPError as exc:
                raise EngineError(f"Ollama could not load {name}: {exc}") from exc
            if response.status_code != 200:
                raise EngineError(
                    f"Ollama could not load {name}: HTTP {response.status_code}: "
                    f"{error_text(response)}"
                )
            current = self.loaded_info(name)
        size = (current or {}).get("size")
        return ServerInfo(
            model=entry.id,
            engine=self.name,
            base_url=self.url,
            port=httpx.URL(self.url).port,
            pid=None,
            started=now,
            last_used=now,
            api_key=None,
            managed=managed,
            ram_gb=round(size / 1e9, 2) if isinstance(size, int) and size > 0 else entry.ram_gb,
            engine_model=name,
        )

    def health(self, info: ServerInfo) -> bool:
        return self.running()

    def alive(self, info: ServerInfo) -> bool:
        """The model is still loaded (Ollama drops it once its keep-alive runs out)."""
        try:
            return self.loaded_info(info.engine_model or info.model) is not None
        except EngineError:
            return False

    def stop(self, info: ServerInfo, timeout: float = 10.0) -> None:
        """Unload with keep-alive 0, only when the manager loaded the model itself."""
        if not info.managed:
            return
        name = info.engine_model or info.model
        body = {"model": name, "keep_alive": 0, "stream": False}
        try:
            with self._client(httpx.Timeout(10.0, read=max(timeout, 10.0))) as client:
                response = client.post("/api/generate", json=body)
        except httpx.ConnectError:
            return  # Ollama is not running, so nothing is loaded
        except httpx.HTTPError as exc:
            raise EngineError(f"Ollama could not unload {name}: {exc}") from exc
        if response.status_code not in (200, 404):
            raise EngineError(f"Ollama could not unload {name}: {error_text(response)}")


def _models(data: Any) -> list[dict[str, Any]]:
    models = data.get("models") if isinstance(data, dict) else None
    return [dict(m) for m in models or [] if isinstance(m, dict)]


def _recipe_ctx(entry: ModelEntry) -> int | None:
    """`num_ctx` from the Ollama recipe, else the context size the RAM figure was sized for."""
    recipe = entry.engines.get(ENGINE)
    extra = (recipe.model_extra or {}) if recipe is not None else {}
    value = extra.get("num_ctx") or entry.ram_ctx
    return int(value) if value else None
