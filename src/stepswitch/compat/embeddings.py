"""Embeddings for semantic search (SPEC §12 `search`, §13 TK9).

Only the compatibility layer knows wire formats (working rule 2), so the embedding requests
live here. Two shapes are spoken, chosen by the endpoint's format:

* the chat-completions family (`chat-completions`, `responses`, `llamacpp-native`,
  `text-completion` endpoints all serve it): `POST /v1/embeddings` with
  `{"model", "input": [...]}`; the response's `data[]` entries carry `index` and `embedding`
  and are put back in input order by `index`. A base64 `embedding` string (little-endian
  float32, what `encoding_format: "base64"` returns) is decoded as well.
* Ollama native (`ollama-native`): `POST /api/embed` with `{"model", "input": [...]}`; the
  response's `embeddings` list is in input order.

Facts and sources are in docs/RESEARCH.md ("embeddings endpoints", checked 2026-10-16). Every
request goes through a `stepswitch.net` client, so the egress policy and audit apply.
"""

from __future__ import annotations

import base64
import binascii
import json
import struct
import time
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from stepswitch import net
from stepswitch.compat.client import Endpoint, join_url
from stepswitch.compat.errors import ModelError, backoff_delay, kind_for_status, parse_retry_after

COMPAT_EMBED_PATH = "/v1/embeddings"
NATIVE_EMBED_PATH = "/api/embed"
EMBED_PATHS: dict[str, str] = {
    "chat-completions": COMPAT_EMBED_PATH,
    "responses": COMPAT_EMBED_PATH,
    "llamacpp-native": COMPAT_EMBED_PATH,
    "text-completion": COMPAT_EMBED_PATH,
    "ollama-native": NATIVE_EMBED_PATH,
}


class EmbeddingClient:
    """Turns texts into vectors through one configured endpoint."""

    def __init__(
        self,
        endpoint: Endpoint,
        *,
        egress: net.Egress | None = None,
        transport: httpx.BaseTransport | None = None,
        purpose: str = "embeddings",
        task_id: str | None = None,
        timeout: float = 120.0,
        batch_size: int = 16,
        max_retries: int = 2,
        query_prefix: str = "",
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if endpoint.format not in EMBED_PATHS:
            raise ModelError(
                "unsupported",
                f"endpoint {endpoint.name!r} speaks {endpoint.format!r}, which has no embeddings "
                f"request here; use an endpoint in one of: {', '.join(sorted(EMBED_PATHS))}",
            )
        self.endpoint = endpoint
        self._egress = egress or net.default()
        self._transport = transport
        self._purpose = purpose
        self._task_id = task_id
        self._timeout = timeout
        self._batch_size = max(1, batch_size)
        self._max_retries = max(0, max_retries)
        self._query_prefix = query_prefix
        self._sleep = sleep

    @property
    def native(self) -> bool:
        return EMBED_PATHS[self.endpoint.format] == NATIVE_EMBED_PATH

    @property
    def cache_key(self) -> str:
        """Identifies the vectors this client produces (endpoint name, format and model)."""
        return f"{self.endpoint.name}|{self.endpoint.format}|{self.endpoint.model}"

    @property
    def local(self) -> bool:
        return self.endpoint.local

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, in order. Raises `ModelError` on any failure."""
        if not self.endpoint.model:
            raise ModelError(
                "bad_request", f"no embedding model configured for endpoint {self.endpoint.name!r}"
            )
        vectors: list[list[float]] = []
        items = list(texts)
        with self._http() as http:
            for start in range(0, len(items), self._batch_size):
                batch = items[start : start + self._batch_size]
                vectors.extend(self._embed_batch(http, batch))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        """The vector for a search query (with the configured query prefix, if any)."""
        return self.embed([self._query_prefix + text])[0]

    # ----------------------------------------------------------------------------------------

    def _http(self) -> httpx.Client:
        return self._egress.client(
            self._purpose,
            headers={"user-agent": "stepswitch", **self.endpoint.headers},
            timeout=httpx.Timeout(self._timeout, connect=10.0),
            transport=self._transport,
            task_id=self._task_id,
        )

    def _embed_batch(self, http: httpx.Client, batch: list[str]) -> list[list[float]]:
        url = join_url(self.endpoint.base_url, EMBED_PATHS[self.endpoint.format])
        body = {"model": self.endpoint.model, "input": batch}
        attempt = 0
        while True:
            try:
                response = self._post(http, url, body)
                if response.status_code >= 400:
                    raise _error(response)
                return self._decode(response, len(batch))
            except ModelError as exc:
                if not exc.retryable or attempt >= self._max_retries:
                    raise
                self._sleep(backoff_delay(attempt, exc.retry_after))
                attempt += 1

    def _post(self, http: httpx.Client, url: str, body: dict[str, Any]) -> httpx.Response:
        name = self.endpoint.name
        try:
            return http.post(url, json=body)
        except net.EgressBlocked as exc:
            raise ModelError("blocked", str(exc)) from exc
        except httpx.TimeoutException as exc:
            raise ModelError("timeout", f"timeout talking to {name}: {exc}") from exc
        except httpx.TransportError as exc:
            raise ModelError("connection", f"cannot reach {name}: {exc}") from exc

    def _decode(self, response: httpx.Response, expected: int) -> list[list[float]]:
        try:
            payload = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ModelError("invalid_response", f"non-JSON embeddings body: {exc}") from exc
        if not isinstance(payload, dict):
            raise ModelError("invalid_response", "embeddings body is not a JSON object")
        if self.native:
            raw = payload.get("embeddings")
            if not isinstance(raw, list):
                raise ModelError("invalid_response", "embeddings body has no `embeddings` list")
            vectors = [_vector(item) for item in raw]
        else:
            data = payload.get("data")
            if not isinstance(data, list) or not all(isinstance(d, dict) for d in data):
                raise ModelError("invalid_response", "embeddings body has no `data` list")
            ordered = sorted(enumerate(data), key=lambda pair: (_index(pair[1], pair[0]), pair[0]))
            vectors = [_vector(entry.get("embedding")) for _, entry in ordered]
        if len(vectors) != expected:
            raise ModelError(
                "invalid_response", f"asked for {expected} embeddings, received {len(vectors)}"
            )
        return vectors


def _index(entry: dict[str, Any], fallback: int) -> int:
    value = entry.get("index", fallback)
    return value if isinstance(value, int) else fallback


def _vector(raw: Any) -> list[float]:
    if isinstance(raw, str):  # base64 of little-endian float32 values
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ModelError("invalid_response", f"bad base64 embedding: {exc}") from exc
        if len(data) % 4:
            raise ModelError("invalid_response", "base64 embedding is not a float32 array")
        return list(struct.unpack(f"<{len(data) // 4}f", data))
    if not isinstance(raw, list) or not raw:
        raise ModelError("invalid_response", "embedding is not a non-empty list of numbers")
    try:
        return [float(value) for value in raw]
    except (TypeError, ValueError) as exc:
        raise ModelError("invalid_response", f"embedding holds a non-number: {exc}") from exc


def _error(response: httpx.Response) -> ModelError:
    status = response.status_code
    try:
        body: Any = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        body = response.text
    message = ""
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or error)
        elif error is not None:
            message = str(error)
    if not message:
        message = str(body)[:300] if body else response.reason_phrase
    return ModelError(
        kind_for_status(status),
        f"embeddings request failed ({status}): {message}",
        status=status,
        retry_after=parse_retry_after(response.headers.get("retry-after")),
        raw=body,
    )
