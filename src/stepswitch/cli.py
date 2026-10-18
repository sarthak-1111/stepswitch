"""The `ssw` command line (SPEC §19).

Commands import their implementation lazily, so `ssw --help` stays fast and a problem in one
area only affects the commands that need it. Commands that later phases deliver are listed with
their options already; until then they exit with status 2 and name the phase (SPEC §23).
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, NoReturn

import typer

from stepswitch import __version__

if TYPE_CHECKING:
    from stepswitch.config import Config
    from stepswitch.net import Egress

app = typer.Typer(
    name="ssw",
    help="Stepswitch picks a model and a harness for each task, and for each step inside it.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
models_app = typer.Typer(help="List, add, pull, serve and stop models.", no_args_is_help=True)
bench_app = typer.Typer(
    help="Run benchmark suites and report the leaderboard.", no_args_is_help=True
)
policy_app = typer.Typer(help="Show or edit the project policy.", no_args_is_help=True)
app.add_typer(models_app, name="models")
app.add_typer(bench_app, name="bench")
app.add_typer(policy_app, name="policy")

DEBUG_ENV = "STEPSWITCH_DEBUG"


# --------------------------------------------------------------------------------------------
# Shared state and helpers
# --------------------------------------------------------------------------------------------


@dataclass
class AppState:
    root: Path
    _config: Config | None = field(default=None, repr=False)
    _egress_ready: bool = field(default=False, repr=False)

    @property
    def config(self) -> Config:
        if self._config is None:
            from stepswitch import config as config_module

            self._config = config_module.load(self.root)
        return self._config

    def egress(self) -> Egress:
        """The process-wide egress layer, configured from the project (offline mode, audit)."""
        from stepswitch import net, paths, state

        if not self._egress_ready:
            net.configure(offline=self.config.offline)
            if paths.state_dir(self.root).is_dir():
                db = state.open_project_state(self.root)
                net.configure(sink=lambda row: db.record_audit(row.as_dict()))
            self._egress_ready = True
        return net.default()


def _state(ctx: typer.Context) -> AppState:
    if not isinstance(ctx.obj, AppState):
        from stepswitch import paths

        ctx.obj = AppState(root=paths.find_project_root())
    return ctx.obj


def _print_json(data: Any) -> None:
    typer.echo(json.dumps(data, indent=2, default=str))


def _fail(message: str, code: int = 1) -> NoReturn:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(code)


def _not_yet(command: str, phase: int) -> NoReturn:
    typer.echo(
        f"`ssw {command}` arrives in Phase {phase} (SPEC §23) and is not implemented yet.",
        err=True,
    )
    raise typer.Exit(2)


@contextmanager
def _user_errors() -> Iterator[None]:
    """Show expected failures as one line; set STEPSWITCH_DEBUG=1 for the traceback."""
    try:
        yield
    except (typer.Exit, typer.Abort, KeyboardInterrupt):
        raise
    except Exception as exc:  # noqa: BLE001 - the CLI boundary
        if os.environ.get(DEBUG_ENV):
            raise
        _fail(f"{exc}" or type(exc).__name__)


def _show_version(value: bool) -> None:
    if value:
        typer.echo(f"stepswitch {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    ctx: typer.Context,
    project: Annotated[
        Path | None,
        typer.Option(
            "--project",
            "-C",
            help="Project directory (default: nearest stepswitch.toml, .stepswitch/ or git root).",
            file_okay=False,
        ),
    ] = None,
    version: Annotated[
        bool,
        typer.Option(
            "--version", help="Show the version and exit.", is_eager=True, callback=_show_version
        ),
    ] = False,
) -> None:
    from stepswitch import paths

    root = project.resolve() if project is not None else paths.find_project_root()
    ctx.obj = AppState(root=root)


# --------------------------------------------------------------------------------------------
# Phase 1: setup, hardware, models, endpoints, claims
# --------------------------------------------------------------------------------------------


@app.command("init")
def cmd_init(
    ctx: typer.Context,
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an existing stepswitch.toml.")
    ] = False,
    detect: Annotated[
        bool,
        typer.Option("--detect/--no-detect", help="Detect hardware and record a profile."),
    ] = True,
    json_out: Annotated[bool, typer.Option("--json", help="Print the result as JSON.")] = False,
) -> None:
    """Create stepswitch.toml and .stepswitch/, detect hardware and pick a profile."""
    from stepswitch import project

    state = _state(ctx)
    with _user_errors():
        result = project.init(state.root, force=force, detect=detect)
    if json_out:
        _print_json(result)
        return
    for line in result["messages"]:
        typer.echo(line)


@app.command("doctor")
def cmd_doctor(
    ctx: typer.Context,
    json_out: Annotated[bool, typer.Option("--json", help="Print the report as JSON.")] = False,
    write: Annotated[
        bool,
        typer.Option(
            "--write/--no-write",
            help="Record the profile in .stepswitch/local.toml (initialized projects only).",
        ),
    ] = True,
) -> None:
    """Report OS, CPU, RAM, GPUs, disk, engines and a recommended profile.

    Exits with status 1 when the report lists problems (for example a SQLite build with the
    WAL-reset bug, ADR 0002).
    """
    import tomllib

    from stepswitch import doctor, paths

    state = _state(ctx)
    with _user_errors():
        report = doctor.collect(
            models_dir=state.config.models_dir(), project_root=state.root, egress=state.egress()
        )
        local_config = paths.local_config_path(state.root)
        if write and paths.state_dir(state.root).is_dir():
            try:
                doctor.write_profile(local_config, report)
            except tomllib.TOMLDecodeError as exc:
                message = f"warning: profile not recorded ({local_config} unchanged): {exc}"
                typer.echo(message, err=True)
    if json_out:
        _print_json(report)
    else:
        typer.echo(doctor.format_report(report))
    if report.get("problems"):
        raise typer.Exit(1)


@models_app.command("list")
def models_list(
    ctx: typer.Context,
    tier: Annotated[
        str | None, typer.Option("--tier", help="Only this hardware tier: cpu, t1, t2 or t3.")
    ] = None,
    installed: Annotated[bool, typer.Option("--installed", help="Only installed models.")] = False,
    json_out: Annotated[bool, typer.Option("--json", help="Print the list as JSON.")] = False,
) -> None:
    """List registry models, their roles, sizes and whether they are installed."""
    from stepswitch import registry

    state = _state(ctx)
    with _user_errors():
        models_dir = state.config.models_dir()
        entries = registry.for_tier(tier) if tier else list(registry.load().values())
        rows = [entry.listing(models_dir) for entry in entries]
    if installed:
        rows = [row for row in rows if row["installed"]]
    if json_out:
        _print_json({"models": rows, "models_dir": str(models_dir), "tier": tier})
        return
    if not rows:
        typer.echo("No models match.")
        return
    header = ("id", "quant", "disk GB", "RAM GB", "context", "tools", "roles", "installed")
    table = [
        (
            row["id"],
            row["quant"],
            f"{row['file_size_gb']:.2f}",
            f"{row['ram_gb']:.2f}",
            str(row["context"]),
            row["tool_calls"],
            ",".join(row["roles"]),
            "yes" if row["installed"] else "no",
        )
        for row in rows
    ]
    widths = [max(len(str(r[i])) for r in [header, *table]) for i in range(len(header))]
    for line in [header, *table]:
        typer.echo(
            "  ".join(
                str(cell).ljust(width) for cell, width in zip(line, widths, strict=True)
            ).rstrip()
        )
    typer.echo(f"\nModels directory: {models_dir}")


@models_app.command("add")
def models_add(
    ctx: typer.Context,
    target: Annotated[
        str,
        typer.Argument(help="A model hub ID (org/name), a local GGUF file or an endpoint URL."),
    ],
    name: Annotated[str | None, typer.Option("--name", help="Name for the entry.")] = None,
    quant: Annotated[
        str | None, typer.Option("--quant", help="Quantization to pick from a hub repo.")
    ] = None,
    file: Annotated[
        str | None, typer.Option("--file", help="Exact file to pick from a hub repo.")
    ] = None,
    engine: Annotated[str | None, typer.Option("--engine", help="Engine to serve it.")] = None,
    api_key_env: Annotated[
        str | None,
        typer.Option(
            "--api-key-env",
            help="Environment variable holding the endpoint's key (keys are not written).",
        ),
    ] = None,
    probe: Annotated[
        bool, typer.Option("--probe/--no-probe", help="Probe a new endpoint after adding it.")
    ] = True,
    json_out: Annotated[bool, typer.Option("--json", help="Print the result as JSON.")] = False,
) -> None:
    """Onboard a model or endpoint: read metadata, pick engine and parser, write the registry."""
    from stepswitch.compat import onboarding

    state = _state(ctx)
    with _user_errors():
        result = onboarding.add(
            target,
            root=state.root,
            config=state.config,
            name=name,
            quant=quant,
            file=file,
            engine=engine,
            api_key_env=api_key_env,
            egress=state.egress(),
            echo=(lambda _msg: None) if json_out else typer.echo,
        )
        profile = None
        if probe and result.endpoint_name:
            state._config = None  # the endpoint was just added to local.toml
            profile = _probe_endpoint(state, result.endpoint_name, None, None, quick=True)
    if json_out:
        _print_json(
            {
                "kind": result.kind,
                "id": result.id,
                "registry_path": str(result.registry_path) if result.registry_path else None,
                "endpoint": result.endpoint_name,
                "details": result.details,
                "probe": profile,
            }
        )


@models_app.command("pull")
def models_pull(
    ctx: typer.Context,
    names: Annotated[
        list[str] | None, typer.Argument(help="Registry ids or names (default: the tier's set).")
    ] = None,
    tier: Annotated[
        str | None, typer.Option("--tier", help="Install a tier's default set: cpu, t1, t2, t3.")
    ] = None,
    max_gb: Annotated[
        float | None,
        typer.Option("--max-gb", help="Disk cap for the set in GB (default: storage.max_gb, 8)."),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes", "-y", help="Download without asking (flagged licenses still need accepting)."
        ),
    ] = False,
    accept_license: Annotated[
        list[str] | None,
        typer.Option("--accept-license", help="Accept the flagged license of this model id."),
    ] = None,
    optional: Annotated[
        bool, typer.Option("--optional/--no-optional", help="Include the tier's optional models.")
    ] = True,
    engine: Annotated[
        str,
        typer.Option("--engine", help="llamacpp (download GGUF files) or ollama (pull there)."),
    ] = "llamacpp",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show the plan only.")] = False,
    tokenizers: Annotated[
        bool,
        typer.Option(
            "--tokenizers",
            help="Download tokenizer files instead (names: tokenizer names; default: all), for"
            " exact HANDOFF token counts.",
        ),
    ] = False,
    json_out: Annotated[bool, typer.Option("--json", help="Print the result as JSON.")] = False,
) -> None:
    """Download models from their original source, verified by size and sha256."""
    state = _state(ctx)
    from stepswitch import modelstore

    if tokenizers:
        with _user_errors():
            pulled = modelstore.pull_tokenizers(
                names or [],
                models_dir=state.config.models_dir(),
                egress=None if dry_run else state.egress(),
                echo=(lambda _msg: None) if json_out else typer.echo,
                dry_run=dry_run,
            )
        if json_out:
            _print_json(
                {
                    "installed": pulled.installed,
                    "downloaded": pulled.downloaded,
                    "skipped": pulled.skipped,
                    "failed": pulled.failed,
                }
            )
        if pulled.failed:
            raise typer.Exit(1)
        return
    if not names and not tier:
        _fail("give model names or --tier (for example: ssw models pull --tier cpu)")

    with _user_errors():
        entries = modelstore.select(tier, names or [])
        plan = modelstore.plan(
            entries,
            models_dir=state.config.models_dir(),
            max_gb=max_gb if max_gb is not None else state.config.storage.max_gb,
            include_optional=optional,
        )
        if not json_out:
            typer.echo(modelstore.describe(plan))
        result = modelstore.execute(
            plan,
            yes=yes,
            accept_licenses=accept_license or [],
            confirm=typer.confirm,
            echo=(lambda _msg: None) if json_out else typer.echo,
            engine=engine,
            egress=None if dry_run else state.egress(),
            dry_run=dry_run,
        )
    if json_out:
        _print_json(
            {
                "installed": result.installed,
                "verified": result.verified,
                "downloaded": result.downloaded,
                "skipped": result.skipped,
                "failed": result.failed,
            }
        )
    if result.failed:
        raise typer.Exit(1)


@models_app.command("serve")
def models_serve(
    ctx: typer.Context,
    model: Annotated[str, typer.Argument(help="Registry id or name of an installed model.")],
    engine: Annotated[
        str | None,
        typer.Option(
            "--engine",
            help="llamacpp, ollama, vllm, sglang, mlx or transformers"
            " (default: chosen by what is installed).",
        ),
    ] = None,
    port: Annotated[int | None, typer.Option("--port", help="Port (default: a free one).")] = None,
    ctx_size: Annotated[
        int | None, typer.Option("--ctx-size", help="Context size (default: the recipe's).")
    ] = None,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Install a missing engine without asking.")
    ] = False,
    json_out: Annotated[bool, typer.Option("--json", help="Print the server as JSON.")] = False,
) -> None:
    """Start a local server for a model (llama.cpp, vLLM, SGLang, MLX-LM or transformers) or
    load it into a running engine."""
    from stepswitch import registry
    from stepswitch.serve import install, llamacpp
    from stepswitch.serve.manager import ServerManager

    state = _state(ctx)
    with _user_errors():
        entry = registry.find(model)
        # Offer to install llama.cpp only when it is the engine that would serve the model.
        wants = engine == "llamacpp" or (
            engine is None and entry is not None and "llamacpp" in entry.engines
        )
        if wants and llamacpp.LlamaCppEngine().find_binary() is None:
            install.install_llamacpp(
                confirm=None if yes else typer.confirm, echo=typer.echo, egress=state.egress()
            )
        manager = ServerManager(state.root, state.config)
        info = manager.start(model, engine=engine, port=port, ctx_size=ctx_size)
    if json_out:
        _print_json(
            {
                "model": info.model,
                "engine": info.engine,
                "base_url": info.base_url,
                "port": info.port,
                "pid": info.pid,
            }
        )
    else:
        typer.echo(f"{info.model} is served by {info.engine} at {info.base_url} (pid {info.pid})")


@models_app.command("stop")
def models_stop(
    ctx: typer.Context,
    model: Annotated[str | None, typer.Argument(help="Model to stop.")] = None,
    all_servers: Annotated[
        bool, typer.Option("--all", help="Stop every server this project started.")
    ] = False,
) -> None:
    """Stop servers started by `ssw models serve` (or unload a model from an engine)."""
    from stepswitch.serve.manager import ServerManager

    state = _state(ctx)
    if not model and not all_servers:
        _fail("give a model or --all")
    with _user_errors():
        manager = ServerManager(state.root, state.config)
        if all_servers:
            typer.echo(f"Stopped {manager.stop_all()} server(s).")
        elif model is not None:
            stopped = manager.stop(model)
            typer.echo(f"Stopped {model}." if stopped else f"{model} was not running.")


def _resolve_endpoint(state: AppState, endpoint: str) -> Any:
    """An endpoint from the config by name, or an ad hoc one for a URL."""
    from stepswitch.config import EndpointConfig

    found = state.config.endpoint(endpoint)
    if found is not None:
        return found
    if endpoint.startswith(("http://", "https://")):
        return EndpointConfig(name=endpoint, url=endpoint)
    names = ", ".join(e.name for e in state.config.endpoints) or "none configured"
    raise ValueError(f"unknown endpoint {endpoint!r} (configured: {names})")


def _probe_endpoint(
    state: AppState,
    endpoint: str,
    model: str | None,
    fmt: str | None,
    *,
    quick: bool,
    export: Path | None = None,
    echo: Any = None,
    tool_parser: str | None = None,
) -> dict[str, Any]:
    from stepswitch import paths
    from stepswitch import state as state_module
    from stepswitch.compat import probe
    from stepswitch.compat.toolcall import parsers

    config = _resolve_endpoint(state, endpoint)
    if tool_parser is not None:
        if tool_parser not in parsers.PARSERS:
            known = ", ".join(parsers.PARSERS)
            raise ValueError(f"unknown tool parser {tool_parser!r}; known: {known}")
        configured = config.tool_parser
        config = config.model_copy(update={"tool_parser": tool_parser})
    say = echo or typer.echo
    profile = probe.run(config, model=model, fmt=fmt, quick=quick, egress=state.egress(), echo=say)
    if tool_parser is not None and tool_parser != configured:
        say(
            f'Note: workers parse calls this way only with tool_parser = "{tool_parser}" on'
            f" endpoint {config.name} or in its model's [models] table (ADR 0022)."
        )
    if paths.state_dir(state.root).is_dir():
        db = state_module.open_project_state(state.root)
        db.save_probe(config.name, profile["model"], profile["format"], profile)
    if export is not None:
        from stepswitch import doctor

        hardware = doctor.collect(models_dir=state.config.models_dir(), egress=state.egress())
        probe.export(profile, export, hardware=hardware)
    return profile


@app.command("probe")
def cmd_probe(
    ctx: typer.Context,
    endpoint: Annotated[
        str | None, typer.Argument(help="Endpoint name from .stepswitch/local.toml, or a URL.")
    ] = None,
    model: Annotated[str | None, typer.Option("--model", "-m", help="Model to probe.")] = None,
    fmt: Annotated[
        str | None, typer.Option("--format", help="API format (default: the endpoint's).")
    ] = None,
    quick: Annotated[
        bool, typer.Option("--quick", help="Skip slow checks (long context, throughput).")
    ] = False,
    export: Annotated[
        Path | None,
        typer.Option(
            "--export", help="Write a shareable result (model hash, engine, hardware, date)."
        ),
    ] = None,
    json_out: Annotated[bool, typer.Option("--json", help="Print the profile as JSON.")] = False,
    tool_parser: Annotated[
        str | None,
        typer.Option(
            "--tool-parser",
            help="Parse tool calls from the reply text with this catalog syntax (for servers"
            " that do not parse the model's calls), as `tool_parser` in the config does.",
        ),
    ] = None,
    ci_set: Annotated[
        bool,
        typer.Option(
            "--ci-set",
            help="Probe every model of the CI model set (probes/ci-set.yaml) on managed"
            " servers and write their results to probes/.",
        ),
    ] = False,
    render_matrix: Annotated[
        bool,
        typer.Option(
            "--render-matrix",
            help="Write docs/COMPATIBILITY.md from the probe results in probes/.",
        ),
    ] = False,
    check: Annotated[
        bool,
        typer.Option(
            "--check", help="With --render-matrix: fail if the file is out of date, change nothing."
        ),
    ] = False,
) -> None:
    """Probe an endpoint's capabilities and store its profile; or probe the CI model set and
    render the compatibility matrix (in a Stepswitch source checkout)."""
    state = _state(ctx)
    if ci_set or render_matrix:
        _probe_matrix(state, ci_set=ci_set, render=render_matrix, check=check)
        return
    if check:
        _fail("--check goes with --render-matrix")
    if endpoint is None:
        _fail("give an endpoint name or URL")
    with _user_errors():
        profile = _probe_endpoint(
            state,
            endpoint,
            model,
            fmt,
            quick=quick,
            export=export,
            echo=(lambda _msg: None) if json_out else typer.echo,
            tool_parser=tool_parser,
        )
    if json_out:
        _print_json(profile)


def _probe_matrix(state: AppState, *, ci_set: bool, render: bool, check: bool) -> None:
    from stepswitch.compat import matrix

    with _user_errors():
        root = _find_source_root(state.root)
        if ci_set:
            written = matrix.probe_ci_set(
                root, config=state.config, egress=state.egress(), echo=typer.echo
            )
            for path in written:
                typer.echo(f"Wrote {path.relative_to(root).as_posix()}")
        if check:
            if not matrix.is_current(root):
                _fail(f"{matrix.MATRIX_FILE} is out of date; run `ssw probe --render-matrix`")
            typer.echo(f"{matrix.MATRIX_FILE} matches the probe results.")
        elif render or ci_set:
            path = matrix.write(root)
            typer.echo(f"Wrote {path.relative_to(root).as_posix()}")


@app.command("calibrate")
def cmd_calibrate(
    ctx: typer.Context,
    endpoint: Annotated[
        str | None, typer.Argument(help="Endpoint to measure (default: every local one).")
    ] = None,
    model: Annotated[str | None, typer.Option("--model", "-m", help="Model to measure.")] = None,
    cache: Annotated[
        bool,
        typer.Option("--cache", help="Also measure the prefix-cache hit ratio (SPEC §7.2)."),
    ] = False,
    json_out: Annotated[bool, typer.Option("--json", help="Print results as JSON.")] = False,
) -> None:
    """Measure prefill and decode throughput and time to first token per endpoint."""
    from stepswitch import paths
    from stepswitch import state as state_module
    from stepswitch.compat import calibrate

    state = _state(ctx)
    with _user_errors():
        if endpoint is not None:
            targets = [_resolve_endpoint(state, endpoint)]
        else:
            targets = [e for e in state.config.endpoints if e.is_local()]
        if not targets:
            raise ValueError("no local endpoints configured; give an endpoint name or URL")
        results = []
        for target in targets:
            result = calibrate.run(
                target,
                model=model,
                egress=state.egress(),
                echo=(lambda _msg: None) if json_out else typer.echo,
                cache=cache,
            )
            results.append(result)
            if paths.state_dir(state.root).is_dir():
                db = state_module.open_project_state(state.root)
                db.save_calibration(
                    target.name,
                    result["model"],
                    result.get("prefill_tps"),
                    result.get("decode_tps"),
                    result.get("ttft_ms"),
                    result,
                )
    if json_out:
        _print_json(results)


def _find_source_root(start: Path) -> Path:
    for directory in [start, *start.parents]:
        if (directory / "claims" / "claims.yaml").is_file() and (
            directory / "tests" / "claims"
        ).is_dir():
            return directory
    raise ValueError(
        "this command runs in a Stepswitch source checkout (claims/claims.yaml and tests/claims)"
    )


@app.command("prove")
def cmd_prove(
    ctx: typer.Context,
    quick: Annotated[
        bool,
        typer.Option(
            "--quick", help="CPU-only claim tests; bench claims check committed evidence (default)."
        ),
    ] = False,
    full: Annotated[
        bool,
        typer.Option(
            "--full", help="Also re-run bench and probe-matrix claims and their evidence."
        ),
    ] = False,
    phase: Annotated[
        int | None, typer.Option("--phase", help="Only claims due by this phase (SPEC §23).")
    ] = None,
    claim: Annotated[
        list[str] | None, typer.Option("--claim", help="Only this claim, e.g. C-004 (repeatable).")
    ] = None,
    skip_claim: Annotated[
        list[str] | None, typer.Option("--skip-claim", help="Leave this claim out (repeatable).")
    ] = None,
    render_readme: Annotated[
        bool,
        typer.Option("--render-readme", help="Regenerate the README claims section; run no tests."),
    ] = False,
    check: Annotated[
        bool,
        typer.Option("--check", help="With --render-readme: fail if the section is out of date."),
    ] = False,
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Exit non-zero unless every selected claim is passing."),
    ] = False,
    json_out: Annotated[bool, typer.Option("--json", help="Print the report as JSON.")] = False,
) -> None:
    """Run the claim tests, record evidence and statuses, and write the prove report."""
    from stepswitch.prove import render_readme as readme
    from stepswitch.prove import runner

    state = _state(ctx)
    if quick and full:
        _fail("use --quick or --full, not both")
    with _user_errors():
        root = _find_source_root(state.root)
        if render_readme:
            changed = readme.update_readme(root, check=check)
            if check and changed:
                _fail("the README claims section is out of date; run `ssw prove --render-readme`")
            typer.echo("README claims section is up to date." if not changed else "README updated.")
            return
        report = runner.run(
            root,
            mode="full" if full else "quick",
            phase=phase,
            claims=claim or [],
            skip=skip_claim or [],
            echo=(lambda _msg: None) if json_out else typer.echo,
        )
    if json_out:
        _print_json(report)
    statuses = [entry["status"] for entry in report["claims"].values()]
    if "failing" in statuses or (strict and any(s != "passing" for s in statuses)):
        raise typer.Exit(1)


@policy_app.command("show")
def policy_show(
    ctx: typer.Context,
    json_out: Annotated[bool, typer.Option("--json", help="Print as JSON.")] = False,
) -> None:
    """Show the merged configuration (keys redacted)."""
    from stepswitch import tomlw

    state = _state(ctx)
    with _user_errors():
        data = state.config.model_dump(mode="json", by_alias=True, exclude_none=True)
    for endpoint in data.get("endpoints", []):
        if endpoint.get("api_key"):
            endpoint["api_key"] = "<redacted>"
    if json_out:
        _print_json(data)
    else:
        typer.echo(f"# project: {state.root}")
        typer.echo(tomlw.dumps(data).rstrip())


@policy_app.command("edit")
def policy_edit(ctx: typer.Context) -> None:
    """Open stepswitch.toml in your editor and validate it afterwards."""
    import click

    from stepswitch import config, paths

    state = _state(ctx)
    path = paths.project_config_path(state.root)
    if not path.is_file():
        _fail(f"{path} does not exist; run `ssw init` first")
    click.edit(filename=str(path))
    with _user_errors():
        config.load(state.root)
    typer.echo(f"{path} is valid.")


@app.command("audit")
def cmd_audit(
    ctx: typer.Context,
    limit: Annotated[int, typer.Option("--limit", help="Most recent N rows.")] = 50,
    blocked: Annotated[bool, typer.Option("--blocked", help="Only refused connections.")] = False,
    json_out: Annotated[bool, typer.Option("--json", help="Print rows as JSON.")] = False,
) -> None:
    """Show the egress audit log: every connection Stepswitch made or refused."""
    from stepswitch import paths, state

    root = _state(ctx).root
    rows: list[dict[str, Any]] = []
    if paths.state_db_path(root).is_file():
        db = state.open_project_state(root)
        where = "WHERE allowed = 0 " if blocked else ""
        rows = db.query(f"SELECT * FROM egress_audit {where}ORDER BY id DESC LIMIT ?", (limit,))
    if json_out:
        _print_json({"rows": rows})
        return
    if not rows:
        typer.echo(
            "No connections recorded." if paths.state_dir(root).is_dir() else "Run `ssw init`."
        )
        return
    for row in reversed(rows):
        verdict = "allowed" if row["allowed"] else "BLOCKED"
        where = "local" if row["local"] else "remote"
        target = f"{row['scheme']}://{row['host']}:{row['port'] or ''}{row['path']}"
        typer.echo(f"{row['ts']:.0f}  {verdict:7}  {where:6}  {row['purpose']:14}  {target}")


# --------------------------------------------------------------------------------------------
# Phase 2: tasks with step-level routing
# --------------------------------------------------------------------------------------------

EXIT_STOPPED = 3


def _run_one(state: AppState, spec: Any, *, json_out: bool) -> Any:
    from stepswitch.core.orchestrator import run_task

    echo = (lambda _msg: None) if json_out else typer.echo
    with _user_errors():
        return run_task(
            spec, root=state.root, config=state.config, egress=state.egress(), echo=echo
        )


def _show_result(result: Any, *, json_out: bool) -> None:
    if json_out:
        data = result.model_dump(mode="json", exclude={"diff"})
        data["diff_lines"] = len(result.diff.splitlines())
        _print_json(data)
        return
    status = result.status + (f" ({result.stop_reason})" if result.stop_reason else "")
    typer.echo(f"Status: {status}")
    if result.summary:
        typer.echo(f"Summary: {result.summary}")
    budget = result.budget
    typer.echo(
        f"Steps: {result.steps}, switches: {result.switches}, escalations: {result.escalations},"
        f" tokens: {int(budget.get('tokens', 0))}, time: {budget.get('wall_seconds', 0):.1f} s"
    )
    if result.best_of > 1:
        passed = sum(1 for c in result.candidates if c.get("status") == "done")
        winner = next((c for c in result.candidates if c.get("index") == result.winner), None)
        chosen = f"; kept candidate {result.winner} ({winner.get('family')})" if winner else ""
        typer.echo(f"Candidates: {len(result.candidates)}, {passed} passed the gates{chosen}")
        report = result.disagreement or {}
        if report.get("detected"):
            settled = report.get("resolution") or "not settled: the task is uncertain"
            typer.echo(f"Disagreement: passing candidates behave differently ({settled})")
    if result.changed_files:
        typer.echo("Changed: " + ", ".join(result.changed_files))
    if result.branch:
        typer.echo(f"Branch: {result.branch}")
    typer.echo(f"Trajectory: {result.run_dir}")


DISAGREEMENT_POLICIES = ("flag", "tests", "escalate", "human")


def _best_of(value: str) -> int:
    """`--best-of`: a count from 1 to 5, or auto (0: by the classifier's difficulty)."""
    text = value.strip().lower()
    if text == "auto":
        return 0
    if not text.isdigit() or not 1 <= int(text) <= 5:
        _fail("--best-of must be a number from 1 to 5, or auto")
    return int(text)


def _exit_code(status: str) -> int:
    if status == "done":
        return 0
    return EXIT_STOPPED if status == "stopped" else 1


@app.command("run")
def cmd_run(
    ctx: typer.Context,
    task: Annotated[str, typer.Argument(help="What to do, in plain language.")],
    track: Annotated[str, typer.Option("--track", help="general or science.")] = "general",
    best_of: Annotated[
        str,
        typer.Option(
            "--best-of",
            help="Candidates across model families, 1 to 5, or auto (by difficulty).",
        ),
    ] = "1",
    on_disagreement: Annotated[
        str,
        typer.Option(
            "--on-disagreement",
            help="When passing candidates disagree: flag, tests, escalate or human.",
        ),
    ] = "flag",
    probe_tests: Annotated[
        bool,
        typer.Option(
            "--probe-tests/--no-probe-tests",
            help="With two passing candidates, also ask a model for tests of the task.",
        ),
    ] = True,
    offline: Annotated[
        bool, typer.Option("--offline", help="Local endpoints only; refuse other connections.")
    ] = False,
    budget_tokens: Annotated[
        int | None, typer.Option("--budget-tokens", help="Stop after this many tokens.")
    ] = None,
    budget_seconds: Annotated[
        float | None, typer.Option("--budget-seconds", help="Stop after this much wall time.")
    ] = None,
    budget_gpu_seconds: Annotated[
        float | None,
        typer.Option("--budget-gpu-seconds", help="Stop after this much GPU time."),
    ] = None,
    budget_escalations: Annotated[
        int | None, typer.Option("--budget-escalations", help="Escalate at most this often.")
    ] = None,
    worker: Annotated[
        str | None,
        typer.Option("--worker", help="Run every step on this worker (<endpoint>/<model>)."),
    ] = None,
    allow_protected: Annotated[
        list[str] | None,
        typer.Option("--allow-protected", help="Allow changing this protected value (repeat)."),
    ] = None,
    acceptance: Annotated[
        list[str] | None,
        typer.Option("--acceptance", help="Extra command that must pass (repeat)."),
    ] = None,
    apply: Annotated[
        bool, typer.Option("--apply", help="Apply the result to the working tree when done.")
    ] = False,
    allow_remote: Annotated[
        bool,
        typer.Option("--allow-remote", help="Let the ladder escalate to remote endpoints."),
    ] = False,
    review: Annotated[
        bool, typer.Option("--review/--no-review", help="Review the change before finishing.")
    ] = True,
    max_steps: Annotated[
        int, typer.Option("--max-steps", help="Stop after this many model turns.")
    ] = 40,
    seed: Annotated[int, typer.Option("--seed", help="Seed for routing and sampling.")] = 0,
    include_uncommitted: Annotated[
        bool,
        typer.Option("--include-uncommitted", help="Start from the working tree, not HEAD."),
    ] = False,
    optimize: Annotated[
        bool | None,
        typer.Option(
            "--optimize/--no-optimize",
            help="Token and cache optimizations (default: [context] optimize, on).",
        ),
    ] = None,
    json_out: Annotated[bool, typer.Option("--json", help="Print the result as JSON.")] = False,
) -> None:
    """Run a task with step-level model routing."""
    from stepswitch.core.task import Budgets, TaskSpec

    state = _state(ctx)
    if track not in ("general", "science"):
        _fail("--track must be general or science")
    candidates = _best_of(best_of)
    if on_disagreement not in DISAGREEMENT_POLICIES:
        _fail("--on-disagreement must be one of " + ", ".join(DISAGREEMENT_POLICIES))
    with _user_errors():
        spec = TaskSpec(
            instruction=task,
            repo=state.root,
            track=track,  # type: ignore[arg-type]
            acceptance=acceptance or [],
            budgets=Budgets(
                tokens=budget_tokens,
                wall_seconds=budget_seconds,
                gpu_seconds=budget_gpu_seconds,
                escalations=budget_escalations,
            ),
            best_of=candidates,
            on_disagreement=on_disagreement,  # type: ignore[arg-type]
            probe_tests=probe_tests,
            offline=offline,
            worker=worker,
            seed=seed,
            max_steps=max_steps,
            allow_protected=allow_protected or [],
            review=review,
            apply=apply,
            include_uncommitted=include_uncommitted,
            allow_remote=allow_remote,
            optimize=optimize,
        )
    result = _run_one(state, spec, json_out=json_out)
    _show_result(result, json_out=json_out)
    code = _exit_code(result.status)
    if code:
        raise typer.Exit(code)


@app.command("agent")
def cmd_agent(
    ctx: typer.Context,
    track: Annotated[str, typer.Option("--track", help="general or science.")] = "general",
    offline: Annotated[
        bool, typer.Option("--offline", help="Local endpoints only; refuse other connections.")
    ] = False,
    worker: Annotated[
        str | None, typer.Option("--worker", help="Run every step on this worker.")
    ] = None,
    apply: Annotated[
        bool, typer.Option("--apply/--no-apply", help="Apply each finished task to the tree.")
    ] = True,
) -> None:
    """Interactive built-in agent with step-level routing: one task per line."""
    from stepswitch.core.task import TaskSpec

    state = _state(ctx)
    if track not in ("general", "science"):
        _fail("--track must be general or science")
    typer.echo("Stepswitch agent. Type a task and press Enter; an empty line or `exit` quits.")
    while True:
        try:
            line = input("task> ").strip()
        except (EOFError, KeyboardInterrupt):
            typer.echo("")
            return
        if not line or line in {"exit", "quit"}:
            return
        spec = TaskSpec(
            instruction=line,
            repo=state.root,
            track=track,  # type: ignore[arg-type]
            offline=offline,
            worker=worker,
            apply=apply,
            include_uncommitted=True,
        )
        result = _run_one(state, spec, json_out=False)
        _show_result(result, json_out=False)


# --------------------------------------------------------------------------------------------
# Later phases (options are fixed now; bodies arrive with their phase)
# --------------------------------------------------------------------------------------------


@app.command("proxy")
def cmd_proxy(
    port: Annotated[int, typer.Option("--port", help="Port to listen on.")] = 7778,
    host: Annotated[str, typer.Option("--host", help="Address to bind.")] = "127.0.0.1",
) -> None:
    """All-format proxy for external harnesses."""
    _not_yet("proxy", 8)


@bench_app.command("run")
def bench_run(
    ctx: typer.Context,
    suite: Annotated[
        str, typer.Option("--suite", help="Suite name (e.g. mini-general) or a suite YAML file.")
    ],
    workers: Annotated[
        str | None,
        typer.Option(
            "--workers",
            help="Comma-separated contestants: `router`, `best-of-n`, worker ids, or"
            " `fake:oracle`/`fake:noop`.",
        ),
    ] = None,
    seeds: Annotated[int, typer.Option("--seeds", min=1, help="Repetitions per task.")] = 1,
    tasks: Annotated[
        str | None, typer.Option("--tasks", help="Comma-separated task ids (default: all).")
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Results directory (default: bench/results/<suite>-<time>)."),
    ] = None,
    review: Annotated[
        bool, typer.Option("--review/--no-review", help="Run the review step of each task.")
    ] = True,
    max_steps: Annotated[
        int | None, typer.Option("--max-steps", min=1, help="Step limit per task.")
    ] = None,
    budget_tokens: Annotated[
        int | None, typer.Option("--budget-tokens", help="Token budget per task.")
    ] = None,
    budget_seconds: Annotated[
        float | None, typer.Option("--budget-seconds", help="Wall-time budget per task.")
    ] = None,
    price_in: Annotated[
        float | None,
        typer.Option("--price-in", help="Reference price per million input tokens."),
    ] = None,
    price_out: Annotated[
        float | None,
        typer.Option("--price-out", help="Reference price per million output tokens."),
    ] = None,
    keep: Annotated[
        bool, typer.Option("--keep", help="Keep the task workspaces under .stepswitch/bench.")
    ] = False,
    router_pool: Annotated[
        str | None,
        typer.Option(
            "--router-pool",
            help="Comma-separated workers the `router` contestant may use (default: all).",
        ),
    ] = None,
    baseline: Annotated[
        bool,
        typer.Option("--baseline", help="Run the Phase 2 baseline: every token optimization off."),
    ] = False,
    relay: Annotated[
        str | None,
        typer.Option(
            "--relay",
            help="FIRST,SECOND: the `relay` contestant starts on FIRST and hands every task to"
            " SECOND with a HANDOFF brief.",
        ),
    ] = None,
    relay_step: Annotated[
        int, typer.Option("--relay-step", min=2, help="The work step of the relay's handoff.")
    ] = 6,
    best_of: Annotated[
        int,
        typer.Option(
            "--best-of",
            min=2,
            max=5,
            help="Candidates of the `best-of-n` contestant, from the pool's model families.",
        ),
    ] = 2,
) -> None:
    """Run a benchmark suite: every contestant on every task, once per seed."""
    import shlex
    from datetime import UTC, datetime

    from stepswitch.bench import runner
    from stepswitch.bench.suites import load_suite
    from stepswitch.core.task import Budgets

    state = _state(ctx)
    with _user_errors():
        loaded = load_suite(suite, state.root)
        selected = loaded.select([t.strip() for t in tasks.split(",")] if tasks else None)
        relay_pair = [name.strip() for name in relay.split(",") if name.strip()] if relay else []
        if relay and len(relay_pair) != 2:
            raise ValueError("--relay takes two workers: FIRST,SECOND")
        contestants = runner.parse_contestants(workers or (runner.RELAY if relay else None))
        if any(c.name == runner.RELAY for c in contestants) != bool(relay_pair):
            raise ValueError("the `relay` contestant and --relay FIRST,SECOND go together")
        if relay_pair and baseline:
            raise ValueError("a relay hands over with a HANDOFF brief: it cannot run --baseline")
        pool = (
            [name.strip() for name in router_pool.split(",") if name.strip()]
            if router_pool
            else None
        )
        _check_contestants(state, contestants, [*(pool or []), *relay_pair])
        if (price_in is None) != (price_out is None):
            raise ValueError("give both --price-in and --price-out, or neither")
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        out_dir = out or state.root / "bench" / "results" / f"{selected.name}-{stamp}"
        options = runner.BenchOptions(
            seeds=seeds,
            review=review,
            max_steps=max_steps,
            budgets=Budgets(tokens=budget_tokens, wall_seconds=budget_seconds),
            keep=keep,
            router_pool=pool,
            optimize=not baseline,
            relay=relay_pair or None,
            relay_step=relay_step,
            best_of=best_of,
            prices=(
                {"input_per_million": price_in, "output_per_million": price_out}
                if price_in is not None and price_out is not None
                else None
            ),
        )
        # The command that reproduces the results, without local paths (it is published).
        args = ["--suite", Path(suite).name if Path(suite).suffix else suite]
        args += ["--workers", ",".join(c.name for c in contestants), "--seeds", str(seeds)]
        args += ["--tasks", tasks] if tasks else []
        args += [] if review else ["--no-review"]
        args += ["--router-pool", ",".join(pool)] if pool else []
        args += ["--baseline"] if baseline else []
        args += (
            ["--relay", ",".join(relay_pair), "--relay-step", str(relay_step)] if relay_pair else []
        )
        if any(c.name == runner.BEST_OF_N for c in contestants):
            args += ["--best-of", str(best_of)]
        args += ["--max-steps", str(max_steps)] if max_steps else []
        args += ["--budget-tokens", str(budget_tokens)] if budget_tokens else []
        args += ["--budget-seconds", str(budget_seconds)] if budget_seconds else []
        if options.prices:
            args += ["--price-in", str(price_in), "--price-out", str(price_out)]
        command = "ssw bench run " + " ".join(shlex.quote(arg) for arg in args)
        result_dir = runner.run_bench(
            selected,
            contestants,
            root=state.root,
            out_dir=out_dir,
            options=options,
            config=state.config,
            egress=state.egress(),
            command=command,
            echo=typer.echo,
        )
    typer.echo(f"Results: {result_dir}")
    typer.echo((result_dir / "leaderboard.md").read_text(encoding="utf-8"))


def _check_contestants(
    state: AppState, contestants: Sequence[Any], pool: Sequence[str] | None = None
) -> None:
    """Fail before the first run when a named worker is not configured."""
    named = [c.name for c in contestants if c.worker is not None and c.fake is None]
    named += list(pool or [])
    if not named:
        return
    from stepswitch.workers.catalog import Catalog

    catalog = Catalog.from_config(state.config, state.root, egress=state.egress())
    unknown = [name for name in named if catalog.find(name) is None]
    if unknown:
        known = ", ".join(sorted(catalog.workers)) or "none configured"
        raise ValueError(f"unknown worker(s) {', '.join(unknown)} (workers: {known})")


@bench_app.command("calibrate")
def bench_calibrate(
    ctx: typer.Context,
    results: Annotated[
        list[Path] | None,
        typer.Argument(help="Results directories (default: every run under bench/results)."),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the values without storing them.")
    ] = False,
    json_out: Annotated[bool, typer.Option("--json", help="Print the result as JSON.")] = False,
) -> None:
    """Learn the router's base rates and handoff recovery from bench results (SPEC §7.2)."""
    from stepswitch import state as state_module
    from stepswitch.bench.calibrate import calibrate
    from stepswitch.router import calibration

    state = _state(ctx)
    with _user_errors():
        folder = state.root / "bench" / "results"
        dirs = results or sorted(path.parent for path in folder.glob("*/run.json"))
        if not dirs:
            raise ValueError("no bench results found; run `ssw bench run` first")
        found = calibrate(dirs)
        if not dry_run:
            db = state_module.open_project_state(state.root)
            calibration.save_param(db, calibration.PRIORS_KEY, found.priors.as_dict())
            calibration.save_param(db, calibration.HANDOFF_KEY, found.recovery.as_dict())
            db.close()
    summary = {**found.summary(), "stored": not dry_run}
    if json_out:
        _print_json(summary)
        return
    typer.echo(f"Runs: {len(found.runs)} ({len(found.skipped)} merged runs skipped)")
    for key, value in summary["priors"].items():
        typer.echo(f"  {key}: {value['passes']}/{value['runs']} passed")
    recovery = summary["handoff_recovery"]
    typer.echo(
        f"Handoff recovery: {recovery['mean']:.2f} from {recovery['observations']} handoff(s)"
    )
    typer.echo("Stored in the project state." if not dry_run else "Not stored (--dry-run).")


@bench_app.command("report")
def bench_report(
    results: Annotated[
        list[Path], typer.Argument(help="Results directories written by `ssw bench run`.")
    ],
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Merge the runs into this directory (needed for several)."),
    ] = None,
    rescore: Annotated[
        bool,
        typer.Option(
            "--rescore",
            help="First recompute each run's verdicts under the current scoring rule.",
        ),
    ] = False,
    json_out: Annotated[
        bool, typer.Option("--json", help="Print the leaderboard as JSON.")
    ] = False,
) -> None:
    """Build the leaderboard (JSON, Markdown, chart) from recorded results; with several
    directories, one leaderboard over all of them."""
    from stepswitch.bench import runner
    from stepswitch.bench.report import LEADERBOARD_JSON, LEADERBOARD_MD, merge_runs, write_report

    with _user_errors():
        for directory in results:
            if not directory.is_dir():
                raise ValueError(f"{directory} is not a results directory")
        if len(results) > 1 and out is None:
            raise ValueError("give --out to merge several results directories")
        if rescore:
            for directory in results:
                changed = runner.rescore(directory)
                typer.echo(f"Rescored {directory.name}: {changed} verdict(s) changed", err=True)
        if out is not None:
            names = " ".join(directory.name for directory in results)
            target = merge_runs(results, out, command=f"ssw bench report {names} --out {out.name}")
        else:
            target = results[0]
            write_report(target)
    name = LEADERBOARD_JSON if json_out else LEADERBOARD_MD
    typer.echo((target / name).read_text(encoding="utf-8"))


def _port_free(host: str, port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _open_when_ready(server: Any, url: str) -> None:
    import threading
    import time
    import webbrowser

    def run() -> None:
        deadline = time.monotonic() + 15.0
        while not server.started and not server.should_exit and time.monotonic() < deadline:
            time.sleep(0.1)
        if server.started and not server.should_exit:
            webbrowser.open(url)

    threading.Thread(target=run, name="ssw-open-browser", daemon=True).start()


class _Terminated(Exception):
    """SIGTERM (or Ctrl+Break on Windows) arrived: leave without waiting for tasks."""


@contextmanager
def _terminate_on_signal() -> Iterator[None]:
    """Turn SIGTERM and SIGBREAK into _Terminated while the server runs. uvicorn shuts down on
    them and then raises the signal again, which would otherwise end the process at once."""
    import signal

    def handler(signum: int, frame: Any) -> None:
        raise _Terminated

    previous: dict[int, Any] = {}
    for name in ("SIGTERM", "SIGBREAK"):
        number = getattr(signal, name, None)
        if number is not None:
            previous[number] = signal.signal(number, handler)
    try:
        yield
    finally:
        for number, old_handler in previous.items():
            signal.signal(number, old_handler)


def _stop_tasks(services: Any) -> None:
    """Let the tasks the dashboard started stop at their next step; a second Ctrl+C records
    them as stopped and leaves at once."""
    pending = services.shutdown()
    if pending:
        typer.echo(
            f"Waiting for {len(pending)} task(s) to stop at their next step"
            " (press Ctrl+C again to leave now)."
        )
        try:
            while not services.wait(0.5):
                pass
        except KeyboardInterrupt:
            services.abandon()
            services.close()
            typer.echo("Left without waiting; the tasks are recorded as stopped.", err=True)
            os._exit(130)
    services.close()


@app.command("dash")
def cmd_dash(
    ctx: typer.Context,
    port: Annotated[
        int, typer.Option("--port", min=1, max=65535, help="Port to listen on.")
    ] = 7777,
    open_browser: Annotated[
        bool, typer.Option("--open/--no-open", help="Open the dashboard in a browser.")
    ] = True,
    max_tasks: Annotated[
        int, typer.Option("--max-tasks", min=1, max=16, help="Tasks to run at the same time.")
    ] = 1,
) -> None:
    """Start the task API and the dashboard on this computer (127.0.0.1 only)."""
    import secrets

    import uvicorn

    from stepswitch.api.app import HOST, create_app

    state = _state(ctx)
    if not _port_free(HOST, port):
        _fail(f"port {port} is in use; choose another with --port")
    token = secrets.token_urlsafe(32)
    holder: list[Any] = []
    with _user_errors():
        app_ = create_app(
            state.root,
            token=token,
            port=port,
            egress=state.egress(),
            max_tasks=max_tasks,
            stopping=lambda: bool(holder) and bool(holder[0].should_exit),
        )
    services = app_.state.services
    server = uvicorn.Server(
        uvicorn.Config(
            app_,
            host=HOST,
            port=port,
            log_level="warning",
            access_log=False,
            server_header=False,
            timeout_graceful_shutdown=3,
            lifespan="on",
        )
    )
    holder.append(server)
    url = f"http://{HOST}:{port}/?token={token}"
    typer.echo(f"Stepswitch dashboard for {state.root}")
    typer.echo(f"  {url}")
    typer.echo("The link carries this session's access token. Press Ctrl+C to stop.")
    if services.static_dir is None:
        typer.echo(
            "note: the dashboard is not built (cd dashboard && npm ci && npm run build);"
            " the task API works without it",
            err=True,
        )
    if open_browser:
        _open_when_ready(server, url)
    try:
        # uvicorn shuts down on Ctrl+C, then raises the interrupt again for the caller.
        with _terminate_on_signal(), suppress(KeyboardInterrupt):
            server.run()
    except _Terminated:
        services.shutdown()
        services.abandon()
        services.close()
        raise typer.Exit(143) from None
    _stop_tasks(services)


@app.command("replay")
def cmd_replay(
    run: Annotated[str, typer.Argument(help="Recorded run id.")],
    from_step: Annotated[int, typer.Option("--from-step", help="Step to fork from.")] = 0,
    worker: Annotated[
        str | None, typer.Option("--worker", help="Worker for the forked steps.")
    ] = None,
) -> None:
    """Replay a recorded run, optionally forking at a step with another worker."""
    _not_yet("replay", 7)


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        typer.echo("interrupted", err=True)
        sys.exit(130)
