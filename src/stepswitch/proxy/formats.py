"""Inbound routes and the answers that are not model calls (ADR 0004 item 2, ADR 0029).

* Which API format each inbound route speaks.
* Model listings: `/v1/models` in one shape that chat-completions and messages clients both
  read, and the Ollama-native `/api/tags` and `/api/show`.
* Ollama-native `/api/generate`: a prompt request, served as a chat request with the prompt as
  the user turn, and answered in the generate shape (`response` instead of `message`).
* Token counts for `/v1/messages/count_tokens`: an estimate, and said to be one.
"""

from __future__ import annotations

import time
from typing import Any

from stepswitch.compat.errors import ModelError
from stepswitch.compat.ir import Request
from stepswitch.ledger import estimate_tokens
from stepswitch.proxy.service import request_chars
from stepswitch.proxy.slots import SLOTS, listed_models
from stepswitch.workers.catalog import Catalog

# Inbound route -> API format (SPEC §4.1 names).
ROUTE_FORMATS = {
    "/v1/chat/completions": "chat-completions",
    "/v1/completions": "text-completion",
    "/v1/responses": "responses",
    "/v1/messages": "messages",
    "/v1beta/models/{model}:generateContent": "generate-content",
    "/v1beta/models/{model}:streamGenerateContent": "generate-content",
    "/api/chat": "ollama-native",
    "/api/generate": "ollama-native",
}
ESTIMATE_HEADER = "x-stepswitch-estimate"
EPOCH = "1970-01-01T00:00:00Z"


def _iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts if ts is not None else 0))


def _description(name: str, catalog: Catalog) -> str:
    if name in SLOTS:
        step = SLOTS[name]
        return f"Stepswitch routes each request ({step or 'any'} steps)"
    worker = catalog.find(name)
    return f"pinned to {worker.spec.id}" if worker is not None else name


def _window(name: str, catalog: Catalog) -> dict[str, int]:
    """A worker's context window, for harnesses that size their transcript by it (the Pi
    extension); other clients ignore the field."""
    worker = catalog.find(name) if name not in SLOTS else None
    if worker is None or not worker.spec.context:
        return {}
    return {"context_window": worker.spec.context}


def models_list(catalog: Catalog) -> dict[str, Any]:
    names = listed_models(catalog)
    data = [
        {
            "id": name,
            "object": "model",
            "type": "model",
            "created": 0,
            "created_at": EPOCH,
            "owned_by": "stepswitch",
            "display_name": name,
            "description": _description(name, catalog),
            **_window(name, catalog),
        }
        for name in names
    ]
    return {
        "object": "list",
        "data": data,
        "has_more": False,
        "first_id": names[0] if names else None,
        "last_id": names[-1] if names else None,
    }


def ollama_tags(catalog: Catalog) -> dict[str, Any]:
    return {
        "models": [
            {
                "name": name,
                "model": name,
                "modified_at": EPOCH,
                "size": 0,
                "digest": "",
                "details": {"format": "proxy", "family": "stepswitch", "families": ["stepswitch"]},
            }
            for name in listed_models(catalog)
        ]
    }


def ollama_show(catalog: Catalog, body: Any) -> dict[str, Any]:
    name = ""
    if isinstance(body, dict):
        name = str(body.get("model") or body.get("name") or "")
    if name not in listed_models(catalog):
        raise ModelError("not_found", f"model {name!r} not found", status=404)
    worker = catalog.find(name)
    context = worker.spec.context if worker is not None else None
    return {
        "modelfile": "",
        "parameters": "",
        "template": "",
        "details": {"format": "proxy", "family": "stepswitch", "families": ["stepswitch"]},
        "model_info": {"stepswitch.context_length": context} if context else {},
        "capabilities": ["completion", "tools"],
        "modified_at": EPOCH,
    }


def generate_as_chat(body: Any) -> dict[str, Any]:
    """An `/api/generate` body as the `/api/chat` body that asks the same."""
    if not isinstance(body, dict):
        raise ModelError("bad_request", "the request body must be a JSON object", status=400)
    if body.get("raw"):
        raise ModelError(
            "unsupported",
            "raw prompts bypass the chat template; the proxy routes chat turns",
            status=400,
        )
    if body.get("suffix"):
        raise ModelError("unsupported", "fill-in-the-middle (`suffix`) is not routed", status=400)
    messages: list[dict[str, Any]] = []
    if isinstance(body.get("system"), str) and body["system"]:
        messages.append({"role": "system", "content": body["system"]})
    user: dict[str, Any] = {"role": "user", "content": str(body.get("prompt") or "")}
    if body.get("images"):
        user["images"] = body["images"]
    messages.append(user)
    chat = {k: v for k, v in body.items() if k not in {"prompt", "system", "images", "context"}}
    chat["messages"] = messages
    return chat


def chat_as_generate(obj: Any) -> Any:
    """One `/api/chat` answer (or stream line) in the `/api/generate` shape."""
    if not isinstance(obj, dict) or "message" not in obj:
        return obj
    message = obj.get("message") or {}
    out = {k: v for k, v in obj.items() if k != "message"}
    out["response"] = message.get("content", "") if isinstance(message, dict) else ""
    if isinstance(message, dict) and message.get("thinking"):
        out["thinking"] = message["thinking"]
    return out


def count_tokens(request: Request) -> dict[str, int]:
    return {"input_tokens": estimate_tokens(request_chars(request))}
