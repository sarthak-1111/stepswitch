"""The worker catalog: every (endpoint, model) a task may route to (SPEC §12, ADR 0015).

Workers come from two places:

* configured endpoints: one worker per (endpoint, model), id `<endpoint>/<model>`;
* installed registry models that Stepswitch serves itself: one worker per model, id
  `managed/<registry id>`, whose server the server manager starts on first use.

What the router needs to know about each worker is collected here, in this order of
precedence: the project's `[models."<name>"]` table, the latest probe profile and calibration
in the state database, the registry entry, and finally the model name (a size such as "32b"
in the name). The tier follows from the parameter count (<= 4.5B cpu, <= 16B t1, <= 130B t2,
else t3); an endpoint that is not local is the remote rung. Workers whose probe says native
tool calls failed, or whose registry entry has no native tool calling, use the fallback tool
protocol (working rule 2).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stepswitch.workers.base import StepModel, ToolProtocol, Worker, WorkerSpec

if TYPE_CHECKING:
    from stepswitch.config import Config, EndpointConfig
    from stepswitch.net import Egress
    from stepswitch.registry import ModelEntry
    from stepswitch.serve.manager import ServerManager
    from stepswitch.state import StateDB

# Upper parameter bounds (billions) per tier; above the last one is t3.
TIER_BOUNDS: tuple[tuple[float, str], ...] = ((4.5, "cpu"), (16.0, "t1"), (130.0, "t2"))
DEFAULT_TIER = "t1"
# Registry roles of models that cannot chat at all, and of completion-only models that are
# used only when pinned.
NO_CHAT_ROLES = frozenset({"embeddings", "search_ranking"})
PIN_ONLY_ROLES = frozenset({"fim"})
CLASSIFIER_ROLES = ("classifier", "router")
_SIZE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*[bB](?![a-zA-Z])")


def tier_for_params(params_b: float) -> str:
    for bound, tier in TIER_BOUNDS:
        if params_b <= bound:
            return tier
    return "t3"


def params_from_name(name: str) -> float | None:
    """A parameter count written in a model name ("qwen2.5:32b", "Coder-1.5B")."""
    sizes = [float(m.group(1)) for m in _SIZE.finditer(name.replace("_", "-"))]
    return max(sizes) if sizes else None


def _number(value: Any) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def _protocol_word(value: Any) -> ToolProtocol | None:
    """Free-form config values such as "native; parsed server-side" or "none"."""
    if not isinstance(value, str):
        return None
    word = value.strip().lower()
    if word.startswith("native"):
        return "native"
    if word.startswith(("fallback", "none")):
        return "fallback"
    return None


@dataclass
class ModelFacts:
    tier: str
    family: str | None = None
    roles: tuple[str, ...] = ()
    tool_protocol: ToolProtocol = "native"
    context: int | None = None
    params_b: float | None = None
    prefill_tps: float | None = None
    decode_tps: float | None = None
    registry_id: str | None = None
    notes: tuple[str, ...] = ()


def model_facts(
    name: str,
    *,
    local: bool,
    table: Mapping[str, Any] | None = None,
    entry: ModelEntry | None = None,
    profile: Mapping[str, Any] | None = None,
    calibration: Mapping[str, Any] | None = None,
) -> ModelFacts:
    table = table or {}
    notes: list[str] = []
    params = (
        _number(table.get("params_b"))
        or _number(table.get("params_total_b"))
        or (entry.params_b if entry is not None else None)
        or params_from_name(name)
    )
    if isinstance(table.get("tier"), str):
        tier = str(table["tier"]).lower().replace("t0", "cpu")
    elif not local:
        tier = "remote"
    elif params is not None:
        tier = tier_for_params(params)
    else:
        tier = DEFAULT_TIER
        notes.append(f"size unknown: treated as {DEFAULT_TIER}")
    roles = table.get("roles")
    role_list = (
        tuple(str(r) for r in roles)
        if isinstance(roles, list)
        else tuple(entry.roles)
        if entry is not None
        else ()
    )
    capabilities = (profile or {}).get("capabilities") or {}
    protocol = _protocol_word(table.get("tool_protocol"))
    if protocol is not None:
        notes.append(f"tool protocol {protocol} (configured)")
    elif capabilities.get("native_tools") is True:
        protocol = "native"
    elif capabilities.get("native_tools") is False:
        protocol = "fallback"
        notes.append("probe: native tool calls failed, using the fallback protocol")
    else:
        protocol = _protocol_word(table.get("tool_calls"))
        if protocol is None and entry is not None:
            protocol = "native" if entry.tool_calls == "native" else "fallback"
        protocol = protocol or "native"
    context = _number(table.get("context")) or _number(table.get("num_ctx"))
    if context is None and entry is not None:
        context = float(entry.context)
    probed = _number(capabilities.get("context_ok_tokens"))
    if probed is not None and (context is None or probed < context):
        context = probed
    calibration = calibration or {}
    family = table.get("family") if isinstance(table.get("family"), str) else None
    return ModelFacts(
        tier=tier,
        family=family or (entry.family if entry is not None else None),
        roles=role_list,
        tool_protocol=protocol,
        context=int(context) if context else None,
        params_b=params,
        prefill_tps=_number(calibration.get("prefill_tps")),
        decode_tps=_number(calibration.get("decode_tps")),
        registry_id=entry.id if entry is not None else None,
        notes=tuple(notes),
    )


def _table_tool_parser(table: Mapping[str, Any]) -> tuple[str | None, tuple[str, ...]]:
    """A `[models."<name>"] tool_parser` override, when it names a catalog syntax."""
    from stepswitch.compat.toolcall import parsers

    value = table.get("tool_parser")
    if value is None:
        return None, ()
    if isinstance(value, str) and value in parsers.PARSERS:
        return value, (f"tool calls parsed from text ({value}, configured)",)
    return None, (f"unknown tool parser {value!r} ignored",)


def _host_has_gpu(config: Config) -> bool:
    host = config.host or {}
    detected = host.get("detected") or {}
    if isinstance(detected, Mapping) and detected.get("gpus"):
        return True
    gpu = host.get("gpu")
    return isinstance(gpu, Mapping) and bool(gpu.get("count"))


def _ctx_from_args(args: Sequence[str]) -> int | None:
    for flag in ("--ctx-size", "-c"):
        if flag in args:
            index = list(args).index(flag)
            if index + 1 < len(args) and str(args[index + 1]).isdigit():
                return int(args[index + 1])
    return None


class Catalog:
    """The workers of one task, by id."""

    def __init__(self, workers: Sequence[Worker] = (), *, notes: Sequence[str] = ()) -> None:
        self.workers: dict[str, Worker] = {}
        for worker in workers:
            if worker.spec.id in self.workers:
                raise ValueError(f"duplicate worker id {worker.spec.id}")
            self.workers[worker.spec.id] = worker
        self.notes = list(notes)

    def __iter__(self) -> Iterator[Worker]:
        return iter(self.workers.values())

    def __len__(self) -> int:
        return len(self.workers)

    def __contains__(self, worker_id: object) -> bool:
        return worker_id in self.workers

    def specs(self) -> list[WorkerSpec]:
        return [worker.spec for worker in self.workers.values()]

    def get(self, worker_id: str) -> Worker:
        return self.workers[worker_id]

    def find(self, name: str) -> Worker | None:
        """A worker by id, `<endpoint>/<model>`, model name or registry id."""
        if name in self.workers:
            return self.workers[name]
        matches = [
            w
            for w in self.workers.values()
            if name in {w.spec.model, w.spec.registry_id, w.spec.endpoint}
        ]
        return matches[0] if len(matches) == 1 else None

    def subset(self, names: Sequence[str]) -> Catalog:
        """A catalog of the named workers only (each found as `find` finds it)."""
        chosen: list[Worker] = []
        for name in names:
            worker = self.find(name)
            if worker is None:
                known = ", ".join(sorted(self.workers)) or "none configured"
                raise ValueError(f"unknown worker {name!r} (workers: {known})")
            if all(worker is not other for other in chosen):
                chosen.append(worker)
        return Catalog(chosen, notes=list(self.notes))

    def classifier(self) -> Worker | None:
        """The T0 worker that classifies tasks: a local level-0 model with a router role."""
        candidates = [
            w
            for w in self.workers.values()
            if w.spec.level == 0
            and w.spec.local
            and not w.spec.pin_only
            and set(CLASSIFIER_ROLES) & set(w.spec.roles)
        ]
        return min(candidates, key=lambda w: w.spec.id) if candidates else None

    def levels(self, *, offline: bool = False, allow_remote: bool = False) -> list[int]:
        """Rungs of the escalation ladder that have a usable worker."""
        remote_ok = allow_remote and not offline
        return sorted({s.level for s in self.specs() if (s.local or remote_ok) and not s.pin_only})

    # ------------------------------------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        config: Config,
        root: Path,
        *,
        egress: Egress | None = None,
        db: StateDB | None = None,
        manager: ServerManager | None = None,
        task_id: str | None = None,
        include_managed: bool = True,
        timeout: float = 600.0,
    ) -> Catalog:
        from stepswitch import registry

        entries = registry.load()
        by_name: dict[str, ModelEntry] = {}
        for entry in entries.values():
            for name in (entry.id, entry.hub_id, entry.name, entry.source_model):
                if name:
                    by_name.setdefault(name.lower(), entry)
            for recipe in entry.engines.values():
                if recipe.name:
                    by_name.setdefault(recipe.name.lower(), entry)
        gpu_host = _host_has_gpu(config)
        workers: list[Worker] = []
        notes: list[str] = []
        for endpoint in config.endpoints:
            fmt = endpoint.primary_format()
            for model in endpoint.models:
                table = config.models.get(model) or {}
                facts = model_facts(
                    model,
                    local=endpoint.is_local(),
                    table=table,
                    entry=by_name.get(model.lower()),
                    profile=_latest_profile(db, endpoint.name, model),
                    calibration=_latest_calibration(db, endpoint.name, model),
                )
                parser, parser_notes = _table_tool_parser(table)
                spec = WorkerSpec(
                    id=f"{endpoint.name}/{model}",
                    endpoint=endpoint.name,
                    model=model,
                    format=fmt,
                    tier=facts.tier,
                    family=facts.family,
                    local=endpoint.is_local(),
                    roles=facts.roles,
                    tool_protocol=facts.tool_protocol,
                    tool_parser=parser or endpoint.tool_parser,
                    context=facts.context,
                    prefill_tps=facts.prefill_tps,
                    decode_tps=facts.decode_tps,
                    gpu=gpu_host and endpoint.is_local() and facts.tier != "cpu",
                    registry_id=facts.registry_id,
                    notes=facts.notes + parser_notes,
                )
                factory = _endpoint_factory(endpoint, spec, egress, task_id, timeout)
                workers.append(Worker(spec, factory=factory))
        if include_managed:
            models_dir = config.models_dir()
            served = {w.spec.registry_id for w in workers if w.spec.registry_id}
            for entry in sorted(entries.values(), key=lambda e: e.id):
                if entry.id in served or not entry.installed(models_dir):
                    continue
                if entry.roles and set(entry.roles) <= NO_CHAT_ROLES:
                    continue
                pin_only = bool(entry.roles) and set(entry.roles) <= NO_CHAT_ROLES | PIN_ONLY_ROLES
                llama = entry.engines.get("llamacpp")
                ctx = _ctx_from_args(llama.args) if llama is not None else None
                table = config.models.get(entry.id) or {}
                facts = model_facts(
                    entry.id,
                    local=True,
                    table=table,
                    entry=entry,
                    profile=_latest_profile(db, "managed", entry.id),
                    calibration=_latest_calibration(db, "managed", entry.id),
                )
                context = (
                    min(c for c in (facts.context, ctx) if c) if facts.context or ctx else None
                )
                parser, parser_notes = _table_tool_parser(table)
                spec = WorkerSpec(
                    id=f"managed/{entry.id}",
                    endpoint="managed",
                    model=entry.id,
                    format="chat-completions",
                    tier=facts.tier,
                    family=facts.family,
                    local=True,
                    roles=facts.roles,
                    tool_protocol=facts.tool_protocol,
                    # llama.cpp serves installed registry models (the manager prefers it).
                    tool_parser=parser or entry.text_tool_parser("llamacpp"),
                    context=context,
                    prefill_tps=facts.prefill_tps,
                    decode_tps=facts.decode_tps,
                    gpu=False,
                    registry_id=entry.id,
                    notes=facts.notes + parser_notes,
                    pin_only=pin_only,
                )
                factory = _managed_factory(
                    config,
                    root,
                    spec,
                    manager,
                    egress,
                    task_id,
                    timeout,
                    entry=entry,
                    pinned=parser,
                )
                workers.append(Worker(spec, factory=factory))
        if not workers:
            notes.append(
                "no workers: configure an endpoint in .stepswitch/local.toml or install models"
                " with `ssw models pull --tier cpu`"
            )
        return cls(workers, notes=notes)


def _latest_profile(db: StateDB | None, endpoint: str, model: str) -> dict[str, Any] | None:
    if db is None:
        return None
    profile = db.latest_probe(endpoint, model)
    if profile is None and ("/" in model or "\\" in model):
        profile = db.latest_probe(endpoint, Path(model).name)
    return profile


def _latest_calibration(db: StateDB | None, endpoint: str, model: str) -> dict[str, Any] | None:
    if db is None:
        return None
    rows = db.query(
        "SELECT prefill_tps, decode_tps, ttft_ms FROM calibrations WHERE endpoint = ? AND model = ?"
        " ORDER BY created DESC LIMIT 1",
        (endpoint, model),
    )
    return rows[0] if rows else None


def wrap_protocol(client: StepModel, spec: WorkerSpec) -> StepModel:
    """The worker's model as the agent sees it: native tool calls, or the fallback protocol."""
    if spec.tool_protocol != "fallback":
        return client
    from stepswitch.compat.toolcall.fallback import FallbackModel

    return FallbackModel(client)  # type: ignore[arg-type]


def _endpoint_factory(
    endpoint: EndpointConfig,
    spec: WorkerSpec,
    egress: Egress | None,
    task_id: str | None,
    timeout: float,
) -> Callable[[], StepModel]:
    def make() -> StepModel:
        from stepswitch.compat.client import Endpoint, ModelClient

        target = Endpoint.from_config(endpoint, model=spec.model, fmt=spec.format)
        target.options.tool_parser = spec.tool_parser
        client = ModelClient(
            target, egress=egress, purpose="model", task_id=task_id, timeout=timeout
        )
        return wrap_protocol(client, spec)

    return make


def _managed_factory(
    config: Config,
    root: Path,
    spec: WorkerSpec,
    manager: ServerManager | None,
    egress: Egress | None,
    task_id: str | None,
    timeout: float,
    *,
    entry: ModelEntry | None = None,
    pinned: str | None = None,
) -> Callable[[], StepModel]:
    """`pinned` is a tool parser set in the project's model table; otherwise the recipe of the
    engine that actually serves the model decides (ADR 0022)."""

    def make() -> StepModel:
        from stepswitch.compat.client import Endpoint, ModelClient
        from stepswitch.compat.codecs.base import CodecOptions
        from stepswitch.serve.manager import ServerManager

        servers = manager or ServerManager(root, config)
        info = servers.ensure(spec.model)
        parser = pinned or (entry.text_tool_parser(info.engine) if entry else spec.tool_parser)
        target = Endpoint(
            name="managed",
            base_url=info.base_url,
            format=spec.format,
            model=info.engine_model or spec.model,
            headers=info.headers(),
            local=True,
            options=CodecOptions(tool_parser=parser),
            engine=info.engine,
        )
        client = ModelClient(
            target, egress=egress, purpose="model", task_id=task_id, timeout=timeout
        )
        return wrap_protocol(client, spec)

    return make
