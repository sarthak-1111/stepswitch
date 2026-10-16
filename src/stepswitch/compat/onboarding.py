"""`ssw models add <hub-id | local path | endpoint URL>`: onboard a model or an endpoint.

SPEC §4.3. Three kinds of target:

- **A local GGUF file.** Its metadata is read (architecture, context length, chat template,
  quantization, parameter count), the file is hashed, and a registry entry that points at the
  file is written to the user's registry directory. The verification stamp is written next to
  the file, so the entry counts as installed.
- **A model hub repository** (`org/name`). Metadata comes from the hub API; nothing is
  downloaded. One GGUF file is chosen (`file`, else `quant`, else Q4_K_M, else the smallest
  single-file GGUF), the entry is pinned to the repository's current commit, and the file's
  size and sha256 come from the file listing. `ssw models pull <id>` downloads it later.
- **An endpoint URL.** The server type is detected with read-only GETs, and an `[[endpoints]]`
  table is appended to `.stepswitch/local.toml`, keeping the rest of the file and its comments.
  Keys are never written, only the name of the environment variable that holds one.

The tool-call syntax is chosen from chat-template markers (`parsers.detect`, confirmed later by
`ssw probe`), and the template's sha256 is recorded as `template_sha256` so a parser can be
pinned per model version (ADR 0003, item 5).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import httpx

from stepswitch import gguf, hub, net, paths, registry, tomlw
from stepswitch.compat.client import join_url
from stepswitch.compat.toolcall import parsers
from stepswitch.config import LOCAL_TEMPLATE_HEADER, Config

Kind = Literal["path", "hub", "url"]
Echo = Callable[[str], None]

GB = 1_000_000_000
DEFAULT_CONTEXT = 4096
PROBE_PURPOSE = "onboarding"
PROBE_TIMEOUT = httpx.Timeout(10.0)
PROBE_MAX_BYTES = 4 << 20
LOCAL_HUB_PREFIX = "local/"

# Licenses whose terms put no condition on commercial use (registry `commercial_use`).
PERMISSIVE_LICENSES = frozenset(
    {"apache-2.0", "mit", "bsd-2-clause", "bsd-3-clause", "cc-by-4.0", "isc", "unlicense"}
)

OLLAMA_FORMATS = ["ollama-native", "chat-completions"]
LLAMACPP_FORMATS = ["chat-completions", "llamacpp-native", "text-completion"]
GENERIC_FORMATS = ["chat-completions"]
ENGINE_FORMATS = {"ollama": OLLAMA_FORMATS, "llamacpp": LLAMACPP_FORMATS}

_SPLIT_RE = re.compile(r"-\d{5}-of-\d{5}\.gguf$", re.IGNORECASE)
_AUXILIARY_RE = re.compile(r"(?<![a-z0-9])(mmproj|imatrix|draft|mtp|dflash)(?![a-z0-9])", re.I)
_QUANT_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])((?:UD-)?(?:I?Q\d(?:_[A-Z0-9]+)*|BF16|F16|F32|MXFP4(?:_MOE)?|NVFP4|TQ\d_\d))"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_ENDPOINT_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_ENGINE_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,31}")
_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
_SECRET_PARAM_RE = re.compile(r"key|token|secret|sig|pass|auth", re.IGNORECASE)


@dataclass
class AddResult:
    kind: Kind
    id: str
    registry_path: Path | None
    endpoint_name: str | None
    details: dict[str, Any]


# --------------------------------------------------------------------------------------------
# Targets
# --------------------------------------------------------------------------------------------


def detect_target(target: str) -> Kind:
    """`url` for http(s) URLs, `path` for existing paths and *.gguf names, `hub` for org/name."""
    text = target.strip()
    if re.match(r"(?i)https?://", text):
        return "url"
    if text.lower().endswith(".gguf") or _exists(text):
        return "path"
    if hub.REPO_RE.fullmatch(text) and "--" not in text and ".." not in text:
        return "hub"
    raise ValueError(
        f"cannot tell what {target!r} is: give a local .gguf file, a model hub id such as "
        "org/name, or an endpoint URL starting with http:// or https://"
    )


def _exists(text: str) -> bool:
    try:
        return bool(text) and Path(text).expanduser().exists()
    except (OSError, ValueError, RuntimeError):  # invalid names, no home directory
        return False


def add(
    target: str,
    *,
    root: Path,
    config: Config,
    name: str | None = None,
    quant: str | None = None,
    file: str | None = None,
    engine: str | None = None,
    api_key_env: str | None = None,
    registry_dir: Path | None = None,
    egress: net.Egress | None = None,
    transport: httpx.BaseTransport | None = None,
    echo: Echo = print,
) -> AddResult:
    """Onboard `target` (see the module docstring) and say what was written."""
    kind = detect_target(target)
    directory = registry_dir or paths.user_registry_dir()
    if kind == "path":
        _model_engine(engine, allowed=("llamacpp",))
        return _add_path(target.strip(), config=config, name=name, directory=directory, echo=echo)
    if kind == "hub":
        _model_engine(engine, allowed=("llamacpp", "ollama"))
        return _add_hub(
            target.strip(),
            config=config,
            name=name,
            quant=quant,
            file=file,
            directory=directory,
            egress=egress,
            transport=transport,
            echo=echo,
        )
    return _add_url(
        target.strip(),
        root=root,
        config=config,
        name=name,
        engine=engine,
        api_key_env=api_key_env,
        egress=egress,
        transport=transport,
        echo=echo,
    )


def _model_engine(engine: str | None, *, allowed: tuple[str, ...]) -> None:
    if engine is not None and engine not in allowed:
        raise ValueError(
            f"engine {engine!r} cannot serve this target here; use {' or '.join(allowed)} "
            "(other engines are added as endpoints: ssw models add <url> --engine <name>)"
        )


# --------------------------------------------------------------------------------------------
# Shared helpers for registry entries
# --------------------------------------------------------------------------------------------


def slug(text: str) -> str:
    """A registry id: lowercase letters, digits, dots, dashes and underscores."""
    value = re.sub(r"[^a-z0-9._-]+", "-", text.strip().lower())
    value = re.sub(r"-{2,}", "-", re.sub(r"\.{2,}", ".", value)).strip("-._")
    value = value[:96].rstrip("-._")
    if not value:
        raise ValueError(f"cannot derive an id from {text!r}; pass a --name")
    return value


def tool_support(template: str | None, pooling: Any = None) -> tuple[str, str | None]:
    """(tool_calls, tool_syntax) from the chat template's markers; the probe confirms it.

    A GGUF with a pooling type (llama.cpp: 1 mean, 2 cls, 3 last, 4 rank) is an embedding or
    reranking model, which never calls tools, whatever its inherited template says.
    """
    if isinstance(pooling, int) and not isinstance(pooling, bool) and pooling > 0:
        return "none", None
    syntax = parsers.detect(template) if template else None
    return ("native", syntax) if syntax else ("fallback", None)


def template_sha256(template: str | None) -> str | None:
    return hashlib.sha256(template.encode("utf-8")).hexdigest() if template else None


def commercial_use(license_id: str) -> str:
    """`permitted` when every term of some alternative of the license expression is permissive."""
    text = license_id.lower().replace("(", " ").replace(")", " ")
    alternatives = [alt.split(" and ") for alt in text.split(" or ")]
    permitted = any(
        all(term.strip() in PERMISSIVE_LICENSES for term in alt) for alt in alternatives
    )
    return "permitted" if permitted else "conditional"


def ram_estimate_gb(size_bytes: int) -> float:
    return round(size_bytes / GB * 1.15 + 0.5, 2)


def quant_from_filename(filename: str) -> str | None:
    matches = _QUANT_TOKEN_RE.findall(PurePosixPath(filename).name)
    return matches[-1].upper() if matches else None


def _has_quant(filename: str, quant: str) -> bool:
    pattern = rf"(?<![A-Za-z0-9]){re.escape(quant)}(?![A-Za-z0-9_])"
    return re.search(pattern, PurePosixPath(filename).name, re.IGNORECASE) is not None


def _write_entry(
    directory: Path,
    model: dict[str, Any],
    engines: dict[str, dict[str, Any]],
    header: str,
    echo: Echo,
) -> Path:
    """Validate, write and read back one registry entry; never overwrites an existing file."""
    entry_id = model["id"]
    registry.ModelEntry.model_validate({**model, "engines": engines})
    path = directory / f"{entry_id}.toml"
    if path.exists():
        raise ValueError(
            f"a registry entry {entry_id!r} already exists at {path}; pass another --name "
            "or remove that file first"
        )
    if entry_id in _packaged_ids():
        echo(f"Note: this entry overrides the packaged registry entry {entry_id!r}.")
    text = tomlw.dumps({"schema": 1, "model": model, "engines": engines}, header=header)
    tomlw.atomic_write(path, text)
    try:
        registry.read_entry(path)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def _packaged_ids() -> set[str]:
    packaged = paths.packaged_registry_dir()
    try:
        return set(registry.load([packaged])) if packaged is not None else set()
    except Exception:  # noqa: BLE001 - only used for a note; never block an add on it
        return set()


def _header(source: str) -> str:
    return (
        f"# Model registry entry written by `ssw models add` on {date.today().isoformat()} "
        "(SPEC §4.3).\n"
        f"# Source: {source}. The RAM figure is an estimate; `ssw probe` confirms tool calling."
    )


def _context(value: Any, notes: list[str]) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    notes.append(f"Context length not found in the metadata; {DEFAULT_CONTEXT} assumed.")
    return DEFAULT_CONTEXT


# --------------------------------------------------------------------------------------------
# Local GGUF files
# --------------------------------------------------------------------------------------------


def _add_path(
    target: str, *, config: Config, name: str | None, directory: Path, echo: Echo
) -> AddResult:
    path = Path(target).expanduser()
    if not path.is_file():
        raise ValueError(f"no such file: {path}")
    path = path.resolve()
    info = gguf.read_metadata(path)
    splits = info.metadata.get("split.count")
    if isinstance(splits, int) and not isinstance(splits, bool) and splits > 1:
        raise ValueError(
            f"{path.name} is one part of a {splits}-part split GGUF, which is not supported yet; "
            "merge the parts into one file first (llama.cpp: llama-gguf-split --merge)"
        )
    size = path.stat().st_size
    echo(f"Hashing {path.name} ({size / GB:.2f} GB)...")
    digest = registry.sha256_file(path)
    template = gguf.chat_template(info)
    pooling = info.metadata.get(f"{gguf.architecture(info)}.pooling_type")
    tool_calls, tool_syntax = tool_support(template, pooling)
    quant = gguf.quant_name(info) or quant_from_filename(path.name) or "unknown"
    params = gguf.parameter_count(info)
    license_value = info.metadata.get("general.license")
    license_id = license_value.lower() if isinstance(license_value, str) and license_value else ""
    notes = [
        "Registered from a local file by `ssw models add`; `file` is its absolute path.",
        "RAM estimate: file size x 1.15 + 0.5 GB (not measured).",
    ]
    if not license_id:
        notes.append("No license in the GGUF metadata: check the model's terms before use.")
    entry_id = slug(name or path.stem)
    model: dict[str, Any] = {
        "id": entry_id,
        "name": name or path.stem,
        "hub_id": LOCAL_HUB_PREFIX + entry_id,
        "file": path.as_posix(),
        "sha256": digest,
        "size_bytes": size,
        "quant": quant,
        "format": "gguf",
        "architecture": gguf.architecture(info),
        "params_b": round(params / 1e9, 2) if params else None,
        "file_size_gb": round(size / GB, 2),
        "ram_gb": ram_estimate_gb(size),
        "context": _context(gguf.context_length(info), notes),
        "tool_calls": tool_calls,
        "tool_syntax": tool_syntax,
        "template_sha256": template_sha256(template),
        "license": license_id or "unknown",
        "commercial_use": commercial_use(license_id) if license_id else "conditional",
        "template_source": "gguf" if template else None,
        "verified": date.today(),
        "source": "path",
        "notes": " ".join(notes),
    }
    registry_path = _write_entry(
        directory, model, {"llamacpp": {}}, _header("a local GGUF file"), echo
    )
    entry = registry.read_entry(registry_path)
    stamp: Path | None = None
    try:
        registry.write_stamp(entry, config.models_dir(), digest)
        stamp = entry.stamp_path(config.models_dir())
    except OSError as exc:
        echo(f"Note: could not write the verification stamp next to the file ({exc}).")
    echo(f"Registered {entry_id} ({quant}, {tool_calls} tool calls) in {registry_path}")
    details = {
        "path": str(path),
        "sha256": digest,
        "size_bytes": size,
        "architecture": model["architecture"],
        "context": model["context"],
        "quant": quant,
        "params": params,
        "tool_calls": tool_calls,
        "tool_syntax": tool_syntax,
        "template_sha256": model["template_sha256"],
        "license": model["license"],
        "commercial_use": model["commercial_use"],
        "stamp": str(stamp) if stamp else None,
    }
    return AddResult("path", entry_id, registry_path, None, details)


# --------------------------------------------------------------------------------------------
# Model hub repositories
# --------------------------------------------------------------------------------------------


def choose_file(
    files: Iterable[hub.HubFile], *, file: str | None = None, quant: str | None = None
) -> tuple[hub.HubFile, str]:
    """Pick the GGUF file to register, with a short reason for the choice."""
    ggufs = [f for f in files if f.path.lower().endswith(".gguf")]
    if not ggufs:
        raise ValueError(
            "the repository has no GGUF files; serve it with an engine and add the endpoint URL"
        )
    if file:
        matches = [f for f in ggufs if f.path == file] or [
            f for f in ggufs if PurePosixPath(f.path).name == file
        ]
        if len(matches) != 1:
            listing = ", ".join(f.path for f in ggufs[:12])
            problem = "is ambiguous" if matches else "is not in the repository"
            raise ValueError(f"file {file!r} {problem}; GGUF files: {listing}")
        _single_file(matches[0])
        return matches[0], "the requested file"
    weights = [f for f in ggufs if not _AUXILIARY_RE.search(PurePosixPath(f.path).name)]
    if not weights:
        raise ValueError(
            "the repository has only auxiliary GGUF files (projectors, importance matrices or "
            "draft models); pick one explicitly with --file"
        )
    singles = [f for f in weights if not _SPLIT_RE.search(f.path)]
    if quant:
        matches = [f for f in weights if _has_quant(f.path, quant)]
        if not matches:
            available = sorted({q for f in weights if (q := quant_from_filename(f.path))})
            raise ValueError(
                f"no GGUF file for quantization {quant!r}; available: {', '.join(available)}"
            )
        single = [f for f in matches if not _SPLIT_RE.search(f.path)]
        if not single:
            _single_file(matches[0])
        best = min(single, key=lambda f: (len(PurePosixPath(f.path).name), f.size))
        return best, f"quantization {quant}"
    default = [f for f in singles if _has_quant(f.path, "Q4_K_M")]
    if default:
        best = min(default, key=lambda f: (len(PurePosixPath(f.path).name), f.size))
        return best, "Q4_K_M, the default quantization"
    if not singles:
        _single_file(weights[0])  # every weight file is a split part: say so
    return min(singles, key=lambda f: f.size), "the smallest single-file GGUF (no Q4_K_M)"


def _single_file(chosen: hub.HubFile) -> None:
    if _SPLIT_RE.search(chosen.path):
        raise ValueError(
            f"{chosen.path} is part of a split multi-part GGUF, which `ssw models add` does "
            "not support yet; pick a single-file quantization with --quant or --file"
        )


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _license(info: dict[str, Any]) -> str:
    """The license id from the model card, else from a `license:` tag."""
    card = _mapping(info.get("cardData"))
    value: Any = card.get("license")
    if isinstance(value, list):
        value = value[0] if value else None
    if value == "other" and isinstance(card.get("license_name"), str):
        value = card["license_name"]
    if not isinstance(value, str) or not value:
        tags = info.get("tags")
        for tag in tags if isinstance(tags, list) else []:
            if isinstance(tag, str) and tag.startswith("license:"):
                value = tag.split(":", 1)[1]
                break
    return value.lower() if isinstance(value, str) and value else "unknown"


def _base_model(info: dict[str, Any]) -> str | None:
    card = info.get("cardData")
    value = card.get("base_model") if isinstance(card, dict) else None
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, str) and hub.REPO_RE.fullmatch(value):
        return value
    return None


def _display_name(repo: str, quant: str) -> str:
    base = re.sub(r"[-_.]gguf$", "", repo.split("/", 1)[1], flags=re.IGNORECASE)
    return base if quant == "unknown" or _has_quant(base, quant) else f"{base}-{quant}"


def _add_hub(
    repo: str,
    *,
    config: Config,
    name: str | None,
    quant: str | None,
    file: str | None,
    directory: Path,
    egress: net.Egress | None,
    transport: httpx.BaseTransport | None,
    echo: Echo,
) -> AddResult:
    hub.validate_repo(repo)
    if config.offline and not net.is_local_url(hub.hub_url()):
        raise ValueError("offline mode (privacy.mode = local): the model hub cannot be reached")
    info = hub.model_info(repo, egress=egress, transport=transport)
    revision = info.get("sha")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError(f"{repo}: the model hub did not report a commit to pin")
    files = hub.list_files(repo, revision=revision, egress=egress, transport=transport)
    chosen, why = choose_file(files, file=file, quant=quant)
    summary = _mapping(info.get("gguf"))
    raw_template = summary.get("chat_template")
    template = raw_template if isinstance(raw_template, str) and raw_template else None
    tool_calls, tool_syntax = tool_support(template, summary.get("pooling_type"))
    quant_id = quant_from_filename(chosen.path) or (quant.upper() if quant else None) or "unknown"
    total = summary.get("total")
    params = total if isinstance(total, int) and not isinstance(total, bool) and total > 0 else None
    license_id = _license(info)
    notes = [
        "Registered from the model hub by `ssw models add`; run `ssw models pull` to download.",
        "RAM estimate: file size x 1.15 + 0.5 GB (not measured).",
    ]
    if chosen.sha256 is None:
        notes.append("The hub listing has no sha256 for this file: the download is size-checked.")
    gated = info.get("gated")
    if gated:
        notes.append(
            f"Gated repository: accept its terms on the model hub and set {hub.TOKEN_ENV}."
        )
    architecture = summary.get("architecture")
    entry_name = name or _display_name(repo, quant_id)
    entry_id = slug(entry_name)
    model: dict[str, Any] = {
        "id": entry_id,
        "name": entry_name,
        "hub_id": repo,
        "source_model": _base_model(info),
        "revision": revision,
        "file": chosen.path,
        "sha256": chosen.sha256,
        "size_bytes": chosen.size,
        "quant": quant_id,
        "format": "gguf",
        "architecture": architecture if isinstance(architecture, str) else None,
        "params_b": round(params / 1e9, 2) if params else None,
        "file_size_gb": round(chosen.size / GB, 2),
        "ram_gb": ram_estimate_gb(chosen.size),
        "context": _context(summary.get("context_length"), notes),
        "tool_calls": tool_calls,
        "tool_syntax": tool_syntax,
        "template_sha256": template_sha256(template),
        "license": license_id,
        "commercial_use": commercial_use(license_id),
        "template_source": "gguf" if template else None,
        "verified": date.today(),
        "source": "hub",
        "notes": " ".join(notes),
    }
    tag = PurePosixPath(chosen.path).name if "/" not in chosen.path else quant_id
    engines: dict[str, dict[str, Any]] = {
        "llamacpp": {},
        # Ollama pulls the same file from the original repository (`hf.co/<repo>:<file>`).
        "ollama": {"name": f"hf.co/{repo}:{tag}"},
    }
    registry_path = _write_entry(
        directory, model, engines, _header(f"{repo} at commit {revision}"), echo
    )
    echo(
        f"Registered {entry_id}: {chosen.path} ({chosen.size / GB:.2f} GB, {why}) from {repo} "
        f"at {revision[:12]}, {tool_calls} tool calls, license {license_id}."
    )
    if model["commercial_use"] != "permitted":
        echo(
            f"Note: license {license_id} is flagged; pulling it needs --accept-license {entry_id}."
        )
    echo(f"Nothing was downloaded. Run `ssw models pull {entry_id}` to download it.")
    details = {
        "hub_id": repo,
        "revision": revision,
        "file": chosen.path,
        "size_bytes": chosen.size,
        "sha256": chosen.sha256,
        "quant": quant_id,
        "choice": why,
        "architecture": model["architecture"],
        "context": model["context"],
        "params": params,
        "tool_calls": tool_calls,
        "tool_syntax": tool_syntax,
        "template_sha256": model["template_sha256"],
        "license": license_id,
        "commercial_use": model["commercial_use"],
        "gated": bool(gated),
    }
    return AddResult("hub", entry_id, registry_path, None, details)


# --------------------------------------------------------------------------------------------
# Endpoint URLs
# --------------------------------------------------------------------------------------------


def _clean_url(target: str) -> str:
    try:
        url = httpx.URL(target)
    except httpx.InvalidURL as exc:
        raise ValueError(f"not a valid URL: {target!r}") from exc
    if url.scheme not in ("http", "https") or not url.host:
        raise ValueError(f"not an http(s) endpoint URL: {target!r}")
    if url.userinfo:
        raise ValueError(
            "the URL carries credentials; put the key in an environment variable and pass "
            "--api-key-env instead"
        )
    if any(_SECRET_PARAM_RE.search(key) for key in url.params):
        raise ValueError(
            "the URL query looks like it carries a key or token; keys are never written to "
            "config files, so pass --api-key-env instead"
        )
    if url.query:
        # The client appends API paths (/v1/chat/completions, ...) to the endpoint URL, so a
        # query string would end up in the middle of every request URL.
        query = url.query.decode("ascii", "replace")
        raise ValueError(
            f"endpoint URLs cannot carry a query string ({query!r}); give the base URL only"
        )
    return str(url.copy_with(fragment=None)).rstrip("/")


def _check_key_env(name: str | None) -> str | None:
    if name is None:
        return None
    name = name.strip()
    looks_secret = len(name) >= 24 and "_" not in name
    if not _ENV_NAME_RE.fullmatch(name) or looks_secret:
        raise ValueError(
            "--api-key-env takes the name of an environment variable (for example "
            "MY_ENDPOINT_KEY), never the key itself"
        )
    return name


def endpoint_slug(url: str) -> str:
    """The default endpoint name: host and port, for example `127-0-0-1-8080`."""
    parsed = httpx.URL(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    host = f"ipv6-{parsed.host}" if ":" in parsed.host else parsed.host
    return slug(f"{host}-{port}".replace(".", "-").replace(":", "-"))


class _Prober:
    """Read-only GETs for server detection; remembers the status codes it saw."""

    def __init__(self, client: httpx.Client) -> None:
        self.client = client
        self.statuses: list[int] = []

    def get(self, url: str) -> tuple[int | None, Any]:
        """(status, parsed JSON or None); a network failure gives (None, None)."""
        try:
            with self.client.stream("GET", url) as response:
                status = response.status_code
                self.statuses.append(status)
                body = bytearray()
                for block in response.iter_bytes():
                    body.extend(block)
                    if len(body) > PROBE_MAX_BYTES:
                        return status, None
        except net.EgressBlocked:
            raise
        except httpx.HTTPError:
            return None, None
        try:
            return status, json.loads(bytes(body))
        except ValueError:
            return status, None


def _model_ids(data: Any) -> list[str]:
    """`data[].id` from a chat-completions model listing."""
    items = data.get("data") if isinstance(data, dict) else None
    ids = (
        [item.get("id") for item in items if isinstance(item, dict)]
        if isinstance(items, list)
        else []
    )
    return [i for i in ids if isinstance(i, str) and i][:256]


@dataclass
class _Detection:
    found: bool
    engine: str | None
    formats: list[str]
    models: list[str]
    facts: dict[str, Any]


def _is_llamacpp(probe: _Prober, root: str, facts: dict[str, Any]) -> bool:
    status, data = probe.get(root + "/props")
    props = _mapping(data)
    if status == 200 and {"default_generation_settings", "total_slots", "build_info"} & set(props):
        facts["detected_by"] = "/props"
        if isinstance(props.get("build_info"), str):
            facts["version"] = props["build_info"]
        return True
    status, data = probe.get(root + "/health")
    health = _mapping(data)
    loading = status == 503 and isinstance(health.get("error"), dict)
    if (status == 200 and health.get("status") == "ok") or loading:
        facts["detected_by"] = "/health"
        return True
    return False


def _detect_server(
    probe: _Prober, url: str, engine: str | None, offline: bool, echo: Echo
) -> _Detection:
    """Identify the server with read-only GETs, in the order Ollama, llama.cpp, generic."""
    root = re.sub(r"/v1(beta)?$", "", url)
    facts: dict[str, Any] = {}
    if engine in (None, "ollama"):
        status, data = probe.get(root + "/api/version")
        version = _mapping(data).get("version")
        if status == 200 and isinstance(version, str):
            facts.update(detected_by="/api/version", version=version)
            _, tags = probe.get(root + "/api/tags")
            models = _ollama_models(tags, offline, echo)
            return _Detection(True, "ollama", OLLAMA_FORMATS, models, facts)
    if engine in (None, "llamacpp") and _is_llamacpp(probe, root, facts):
        _, listing = probe.get(join_url(url, "/v1/models"))
        return _Detection(True, "llamacpp", LLAMACPP_FORMATS, _model_ids(listing), facts)
    formats = ENGINE_FORMATS.get(engine or "", GENERIC_FORMATS)
    status, listing = probe.get(join_url(url, "/v1/models"))
    if status == 200 and isinstance(_mapping(listing).get("data"), list):
        facts["detected_by"] = "/v1/models"
        return _Detection(True, engine, formats, _model_ids(listing), facts)
    return _Detection(False, engine, formats, [], facts)


def _ollama_models(tags: Any, offline: bool, echo: Echo) -> list[str]:
    """Model names from /api/tags; cloud-backed ones are left out in offline mode (SPEC §15)."""
    items = tags.get("models") if isinstance(tags, dict) else None
    names: list[str] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        model_name = item.get("name") or item.get("model")
        if not isinstance(model_name, str) or not model_name:
            continue
        if item.get("remote_host") or item.get("remote_model"):
            if offline:
                echo(f"Left out {model_name}: it forwards to a hosted service (offline mode).")
                continue
            echo(f"Note: {model_name} forwards to a hosted service; offline mode will refuse it.")
        names.append(model_name)
    return names[:256]


def _append_endpoint(path: Path, endpoint: dict[str, Any]) -> None:
    """Append one `[[endpoints]]` table, keeping the rest of the file byte for byte."""
    # Read without newline translation, so a file with CRLF line endings keeps them.
    current = path.read_bytes().decode("utf-8") if path.is_file() else LOCAL_TEMPLATE_HEADER
    try:
        existing = tomllib.loads(current)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{path} is not valid TOML ({exc}); fix it first") from exc
    tables = existing.get("endpoints", [])
    names = {item.get("name") for item in tables if isinstance(item, dict)}
    if endpoint["name"] in names:
        raise ValueError(
            f"an endpoint named {endpoint['name']!r} already exists in {path}; pass another --name"
        )
    newline = "\r\n" if "\r\n" in current else "\n"
    if current and not current.endswith("\n"):
        current += newline
    stamp = f"# Added by `ssw models add` on {date.today().isoformat()}.\n"
    block = (stamp + tomlw.dumps({"endpoints": [endpoint]})).replace("\n", newline)
    updated = current + (newline if current.strip() else "") + block
    try:
        tomllib.loads(updated)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(
            f"cannot append an [[endpoints]] table to {path} ({exc}); add the endpoint by hand"
        ) from exc
    tomlw.atomic_write(path, updated)


def _add_url(
    target: str,
    *,
    root: Path,
    config: Config,
    name: str | None,
    engine: str | None,
    api_key_env: str | None,
    egress: net.Egress | None,
    transport: httpx.BaseTransport | None,
    echo: Echo,
) -> AddResult:
    url = _clean_url(target)
    key_env = _check_key_env(api_key_env)
    if engine is not None and not _ENGINE_RE.fullmatch(engine):
        raise ValueError(f"not an engine name: {engine!r}")
    local = net.is_local_url(url)
    if config.offline and not local:
        raise ValueError("offline mode (privacy.mode = local): only local endpoints can be added")
    endpoint_name = name or endpoint_slug(url)
    if not _ENDPOINT_NAME_RE.fullmatch(endpoint_name):
        raise ValueError(f"not a usable endpoint name: {endpoint_name!r}")
    if config.endpoint(endpoint_name) is not None:
        raise ValueError(f"an endpoint named {endpoint_name!r} already exists; pass another --name")
    headers: dict[str, str] = {}
    secret = os.environ.get(key_env, "") if key_env else ""
    if key_env and not secret:
        echo(f"Note: {key_env} is not set in this environment; set it before using the endpoint.")
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    layer = egress or net.default()
    with layer.client(
        PROBE_PURPOSE, headers=headers, timeout=PROBE_TIMEOUT, transport=transport
    ) as client:
        probe = _Prober(client)
        found = _detect_server(probe, url, engine, config.offline, echo)
    if not found.found:
        if engine is None and {401, 403} & set(probe.statuses):
            raise ValueError(
                f"the server at {url} requires a key: put it in an environment variable "
                "and pass --api-key-env with the variable's name"
            )
        if engine is None:
            raise ValueError(
                f"no supported server answered at {url} (tried /api/version, /props, /health "
                "and /v1/models); start it, or pass --engine to register it anyway"
            )
        echo(f"Note: the server did not answer; registering it as {engine} anyway.")
    if not local and not url.startswith("https://"):
        echo("Note: this endpoint is not local and not https; traffic to it is unencrypted.")
    endpoint: dict[str, Any] = {
        "name": endpoint_name,
        "url": url,
        "engine": found.engine,
        "formats": found.formats,
        "models": found.models,
        "auth": "bearer" if key_env else "none",
        "api_key_env": key_env,
    }
    config_path = paths.local_config_path(root)
    _append_endpoint(config_path, endpoint)
    echo(
        f"Added endpoint {endpoint_name} ({found.engine or 'chat-completions server'}, "
        f"{len(found.models)} model(s)) to {config_path}"
    )
    details = {
        "url": url,
        "engine": found.engine,
        "formats": found.formats,
        "models": found.models,
        "auth": endpoint["auth"],
        "api_key_env": key_env,
        "config_path": str(config_path),
        **found.facts,
    }
    return AddResult("url", endpoint_name, None, endpoint_name, details)
