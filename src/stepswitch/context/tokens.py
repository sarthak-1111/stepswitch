"""Token counts with the receiving model's tokenizer (SPEC §9, ADR 0024).

HANDOFF briefs must fit 1,500 tokens as the receiving model counts them, so a count names the
tokenizer that produced it and says whether it is exact. The resolution order for a model
(`RESOLUTION_ORDER`):

1. ``tokenizer.json``: the file its registry entry names (`tokenizer = "<name>"` in the entry),
   present under ``<models_dir>/tokenizers/<name>/``;
2. ``server``: the ``POST /tokenize`` route of the llama.cpp server that serves it (exact for that
   model file; Ollama has no such route);
3. ``mapped``: a tokenizer file mapped from the model's name (``registry/tokenizers/*.toml``,
   glob patterns such as ``gpt-oss*``), present under the same directory;
4. ``estimate``: characters / 4, flagged as not exact.

Tokenizer files come from ``ssw models pull`` (registry models) and ``ssw models pull
--tokenizers`` (mapped files); nothing here downloads anything. Reading ``tokenizer.json`` needs
the optional ``tokens`` extra (the ``tokenizers`` package); without it, levels 1 and 3 are
skipped. Calls to a server go through ``stepswitch.net``.
"""

from __future__ import annotations

import fnmatch
import functools
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

from stepswitch import net, paths

RESOLUTION_ORDER = ("tokenizer.json", "server", "mapped", "estimate")
CHARS_PER_TOKEN = 4.0
TOKENIZE_TIMEOUT = 30.0
ESTIMATE_NAME = "estimate:chars/4"


class TokenizerError(RuntimeError):
    pass


@dataclass(frozen=True)
class TokenCount:
    tokens: int
    tokenizer: str
    exact: bool

    def as_dict(self) -> dict[str, Any]:
        return {"tokens": self.tokens, "tokenizer": self.tokenizer, "exact": self.exact}


class Counter(Protocol):
    name: str
    exact: bool

    def count(self, text: str) -> int: ...


@dataclass(frozen=True)
class TokenizerSpec:
    """A tokenizer file on the model hub (registry/tokenizers/*.toml)."""

    name: str
    hub_id: str
    revision: str
    file: str
    sha256: str
    size_bytes: int | None = None
    match: tuple[str, ...] = ()
    license: str | None = None

    def local_path(self, models_dir: Path) -> Path:
        return tokenizer_dir(models_dir) / self.name / Path(self.file).name

    def matches(self, model: str) -> bool:
        return any(fnmatch.fnmatchcase(model_key(model), pattern) for pattern in self.match)


@dataclass
class Receiver:
    """What is known about the model that will read a text."""

    model: str
    registry_id: str | None = None
    tokenizer: str | None = None  # the registry entry's tokenizer name
    engine: str | None = None  # the engine serving it, when known ("llamacpp", "ollama", ...)
    base_url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """The parts worth recording (never the headers, which may hold a key)."""
        return {
            "model": self.model,
            "registry_id": self.registry_id,
            "tokenizer": self.tokenizer,
            "engine": self.engine,
        }


def model_key(model: str) -> str:
    """The lowercase model name without a path or `endpoint/` prefix."""
    return model.strip().replace("\\", "/").rsplit("/", 1)[-1].lower()


def tokenizer_dir(models_dir: Path) -> Path:
    return models_dir / "tokenizers"


def _spec_files(dirs: Iterable[Path] | None) -> list[Path]:
    found = []
    for directory in paths.registry_dirs() if dirs is None else dirs:
        found.extend(sorted((directory / "tokenizers").glob("*.toml")))
    return found


def load_specs(dirs: Iterable[Path] | None = None) -> dict[str, TokenizerSpec]:
    """Tokenizer specs by name; later registry directories override earlier ones."""
    specs: dict[str, TokenizerSpec] = {}
    for path in _spec_files(dirs):
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        for item in data.get("tokenizer", []):
            spec = TokenizerSpec(
                name=str(item["name"]),
                hub_id=str(item["hub_id"]),
                revision=str(item["revision"]),
                file=str(item.get("file", "tokenizer.json")),
                sha256=str(item["sha256"]).lower(),
                size_bytes=int(item["size_bytes"]) if item.get("size_bytes") else None,
                match=tuple(str(p).lower() for p in item.get("match", [])),
                license=item.get("license"),
            )
            specs[spec.name] = spec
    return specs


def mapped_spec(model: str, specs: Mapping[str, TokenizerSpec]) -> TokenizerSpec | None:
    """The first spec (by name) whose patterns match `model`."""
    for name in sorted(specs):
        if specs[name].matches(model):
            return specs[name]
    return None


# --------------------------------------------------------------------------------------------
# Counters
# --------------------------------------------------------------------------------------------


def tokenizers_available() -> bool:
    try:
        import tokenizers  # noqa: F401
    except ImportError:
        return False
    return True


@functools.lru_cache(maxsize=8)
def _load_tokenizer(path: str, mtime_ns: int) -> Any:
    from tokenizers import Tokenizer

    return Tokenizer.from_file(path)


class FileCounter:
    """Counts with a local tokenizer.json (the `tokenizers` package, no network)."""

    exact = True

    def __init__(self, path: Path, name: str) -> None:
        self.path = path
        self.name = name

    def count(self, text: str) -> int:
        tokenizer = _load_tokenizer(str(self.path), self.path.stat().st_mtime_ns)
        return len(tokenizer.encode(text, add_special_tokens=False).ids)


class ServerCounter:
    """Counts with a llama.cpp server's `POST /tokenize` (no special tokens added)."""

    exact = True

    def __init__(
        self,
        base_url: str,
        *,
        headers: Mapping[str, str] | None = None,
        egress: net.Egress | None = None,
        name: str = "llama.cpp:/tokenize",
        timeout: float = TOKENIZE_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
        task_id: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/").removesuffix("/v1")
        self.headers = dict(headers or {})
        self.egress = egress or net.default()
        self.name = name
        self.timeout = timeout
        self.transport = transport
        self.task_id = task_id

    def count(self, text: str) -> int:
        body = {"content": text, "add_special": False, "parse_special": False}
        try:
            with self.egress.client(
                "tokenize", timeout=self.timeout, transport=self.transport, task_id=self.task_id
            ) as client:
                response = client.post(f"{self.base_url}/tokenize", json=body, headers=self.headers)
        except Exception as exc:  # noqa: BLE001 - any transport failure means no count
            raise TokenizerError(f"tokenize request failed: {exc}") from exc
        if response.status_code != 200:
            raise TokenizerError(f"tokenize returned HTTP {response.status_code}")
        try:
            tokens = response.json()["tokens"]
        except (ValueError, KeyError, TypeError) as exc:
            raise TokenizerError(f"unexpected tokenize answer: {exc}") from exc
        if not isinstance(tokens, list):
            raise TokenizerError("unexpected tokenize answer: tokens is not a list")
        return len(tokens)


class EstimateCounter:
    """About four characters per token; never presented as exact."""

    exact = False
    name = ESTIMATE_NAME

    def count(self, text: str) -> int:
        return int(-(-len(text) // CHARS_PER_TOKEN))


# --------------------------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------------------------


def _file_level(
    receiver: Receiver, models_dir: Path, specs: Mapping[str, TokenizerSpec]
) -> Counter | None:
    if not receiver.tokenizer or not tokenizers_available():
        return None
    spec = specs.get(receiver.tokenizer)
    if spec is None:
        return None
    path = spec.local_path(models_dir)
    return FileCounter(path, f"tokenizer.json:{spec.name}") if path.is_file() else None


def _mapped_level(
    receiver: Receiver, models_dir: Path, specs: Mapping[str, TokenizerSpec]
) -> Counter | None:
    if not tokenizers_available():
        return None
    spec = mapped_spec(receiver.model, specs)
    if spec is None and receiver.registry_id:
        spec = mapped_spec(receiver.registry_id, specs)
    if spec is None:
        return None
    path = spec.local_path(models_dir)
    return FileCounter(path, f"mapped:{spec.name}") if path.is_file() else None


def resolve(
    receiver: Receiver,
    *,
    models_dir: Path | None,
    egress: net.Egress | None = None,
    specs: Mapping[str, TokenizerSpec] | None = None,
) -> Counter:
    """The first available counter in `RESOLUTION_ORDER` for `receiver` (without a models
    directory, the local file levels are skipped)."""
    specs = load_specs() if specs is None else specs
    counter = _file_level(receiver, models_dir, specs) if models_dir is not None else None
    if counter is not None:
        return counter
    if receiver.engine == "llamacpp" and receiver.base_url:
        return ServerCounter(receiver.base_url, headers=receiver.headers, egress=egress)
    counter = _mapped_level(receiver, models_dir, specs) if models_dir is not None else None
    if counter is not None:
        return counter
    return EstimateCounter()


def resolve_local(
    receiver: Mapping[str, Any] | Receiver,
    *,
    models_dir: Path | None = None,
    specs: Mapping[str, TokenizerSpec] | None = None,
) -> Counter | None:
    """An exact counter from local files only (levels 1 and 3), or None. For re-counting
    recorded texts, for example in claim tests, without any server."""
    if not isinstance(receiver, Receiver):
        if not receiver.get("model"):
            return None
        receiver = Receiver(
            model=str(receiver["model"]),
            registry_id=receiver.get("registry_id"),
            tokenizer=receiver.get("tokenizer"),
        )
    if models_dir is None:
        models_dir = _configured_models_dir()
    specs = load_specs() if specs is None else specs
    return _file_level(receiver, models_dir, specs) or _mapped_level(receiver, models_dir, specs)


def _configured_models_dir() -> Path:
    from stepswitch import config

    return config.load(Path.cwd()).models_dir()


def count(text: str, counter: Counter) -> TokenCount:
    """`text` counted by `counter`; a failing exact counter falls back to a flagged estimate."""
    try:
        return TokenCount(counter.count(text), counter.name, counter.exact)
    except (TokenizerError, OSError, ValueError) as exc:
        estimate = EstimateCounter()
        reason = str(exc).splitlines()[0][:120] if str(exc) else type(exc).__name__
        return TokenCount(
            estimate.count(text), f"{estimate.name} ({counter.name}: {reason})", False
        )


def describe(
    models_dir: Path, specs: Mapping[str, TokenizerSpec] | None = None
) -> list[dict[str, Any]]:
    """Which tokenizer files are present (for `ssw doctor` and the dashboard)."""
    specs = load_specs() if specs is None else specs
    return [
        {
            "name": spec.name,
            "hub_id": spec.hub_id,
            "present": spec.local_path(models_dir).is_file(),
            "path": str(spec.local_path(models_dir)),
            "match": list(spec.match),
        }
        for spec in specs.values()
    ]
