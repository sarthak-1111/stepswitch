"""Local endpoints that forward requests to a hosted service (SPEC §15).

A localhost URL does not prove that a request stays on this machine:

* A signed-in Ollama server serves cloud-backed models by forwarding them to its hosted
  service. Their names carry the tag `cloud` or a tag ending in `-cloud` (`name:cloud`,
  `name:120b-cloud`), and `/api/tags` marks them with `remote_host` and `remote_model`; the
  name alone is not proof, so offline runs also ask `/api/tags`.
* llama-swap forwards the models of its `peers`, and any model whose `proxy` URL points at
  another machine, to those servers. Only its config file says so: a llama-swap endpoint names
  that file in `engine_config`.

Two checks use these facts:

* `static_reason` when the catalog is built: a worker that forwards, or that cannot be shown
  not to, counts as remote. Offline routing never picks it, pinning it offline fails, and the
  secret redactor applies to its requests.
* `offline_refusal` before the first offline request a model client makes for a model: the
  same facts read again at run time (the config file or the server's model list may have
  changed since the catalog was built). The client refuses the request when it returns a
  reason.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx

from stepswitch import net

LLAMA_SWAP = "llama-swap"
# The port llama-swap gives `${PORT}` is a number; any other unresolved macro could be a host.
_PORT_MACRO = "${PORT}"
_MACRO = re.compile(r"\$\{([A-Za-z0-9_-]+)\}")


def cloud_backed_name(model: str) -> bool:
    """An Ollama model name whose tag says the hosted service runs it."""
    _, sep, tag = model.rpartition(":")
    return bool(sep) and "/" not in tag and (tag == "cloud" or tag.endswith("-cloud"))


def is_ollama(engine: str | None, fmt: str | None) -> bool:
    return engine == "ollama" or fmt == "ollama-native"


def ollama_remote(tags: Any, model: str) -> bool:
    """Whether an `/api/tags` listing marks `model` as forwarded to a hosted service."""
    items = tags.get("models") if isinstance(tags, dict) else None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        if model in (item.get("name"), item.get("model")):
            return bool(item.get("remote_host") or item.get("remote_model"))
    return False


def _resolve(url: str, macros: dict[str, str]) -> str:
    url = url.replace(_PORT_MACRO, "5800")
    return _MACRO.sub(lambda m: macros.get(m.group(1), m.group(0)), url)


def _local(url: str) -> bool:
    return "${" not in url and net.is_local_url(url)


def _host(url: str) -> str:
    try:
        return httpx.URL(url).host or url
    except Exception:  # noqa: BLE001 - shown as written when it does not parse
        return url


def _macros(table: Any) -> dict[str, str]:
    if not isinstance(table, dict):
        return {}
    return {str(k): str(v) for k, v in table.items() if isinstance(v, str | int | float)}


def llama_swap_forwarding(path: Path) -> dict[str, str]:
    """The models a llama-swap config sends off this machine, each with where it goes.

    A peer counts unless its `proxy` URL is local, and so does a model (with its aliases)
    whose `proxy` URL is not local. A URL with a macro that cannot be resolved is not local.
    Raises ValueError when the file cannot be read as a llama-swap config.
    """
    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError

    try:
        data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, YAMLError) as exc:
        raise ValueError(f"cannot read the llama-swap config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a llama-swap config (expected a mapping)")
    macros = _macros(data.get("macros"))
    forwards: dict[str, str] = {}
    peers = data.get("peers")
    for name, peer in peers.items() if isinstance(peers, dict) else ():
        if not isinstance(peer, dict):
            continue
        url = _resolve(str(peer.get("proxy") or ""), macros)
        if _local(url):
            continue
        for model in peer.get("models") or []:
            forwards[str(model)] = f"llama-swap peer {name} at {_host(url)}"
    models = data.get("models")
    for model_id, table in models.items() if isinstance(models, dict) else ():
        if not isinstance(table, dict) or not table.get("proxy"):
            continue
        url = _resolve(str(table["proxy"]), {**macros, **_macros(table.get("macros"))})
        if _local(url):
            continue
        for name in [model_id, *(table.get("aliases") or [])]:
            forwards[str(name)] = f"llama-swap proxies it to {_host(url)}"
    return forwards


def _llama_swap_reason(engine_config: str | None, model: str, root: Path | None) -> str | None:
    if not engine_config:
        return (
            "a llama-swap endpoint without `engine_config` (its config file) cannot be checked "
            "for peers that forward requests"
        )
    path = Path(engine_config).expanduser()
    if not path.is_absolute() and root is not None:
        path = root / path
    try:
        forwards = llama_swap_forwarding(path)
    except ValueError as exc:
        return str(exc)
    return forwards.get(model)


def static_reason(
    *,
    model: str,
    engine: str | None,
    fmt: str | None,
    engine_config: str | None = None,
    root: Path | None = None,
) -> str | None:
    """Why requests for `model` on a local endpoint may leave this machine (None: no reason
    known). The name check applies whatever the engine: an endpoint's engine is not always
    known, and no other engine uses these tags."""
    if cloud_backed_name(model):
        return f"{model} is an Ollama cloud-backed model name (forwarded to a hosted service)"
    if engine == LLAMA_SWAP:
        return _llama_swap_reason(engine_config, model, root)
    return None


def _ollama_root(base_url: str) -> str:
    root = base_url.rstrip("/")
    return root[: -len("/v1")] if root.endswith("/v1") else root


def offline_refusal(
    http: httpx.Client,
    *,
    base_url: str,
    model: str,
    engine: str | None,
    fmt: str | None,
    engine_config: str | None = None,
    root: Path | None = None,
) -> str | None:
    """Why an offline request for `model` must not be sent (None: it may be).

    Asks an Ollama server's `/api/tags` through `http`, the client's own (audited) egress
    client. A listing that cannot be read is no reason to refuse: the request itself then fails
    or succeeds against the same server, and a model the listing does not mention is not
    forwarded by it."""
    reason = static_reason(
        model=model, engine=engine, fmt=fmt, engine_config=engine_config, root=root
    )
    if reason is not None or not is_ollama(engine, fmt):
        return reason
    try:
        response = http.get(_ollama_root(base_url) + "/api/tags")
        tags = response.json() if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None
    if ollama_remote(tags, model):
        return f"the Ollama server forwards {model} to a hosted service (`/api/tags`)"
    return None
