"""The `ssw` command line (SPEC §19).

Commands import their implementation lazily, so `ssw --help` stays fast and a problem in one
area only affects the commands that need it. Commands that later phases deliver are listed with
their options already; until then they exit with status 2 and name the phase (SPEC §23).
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
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
    json_out: Annotated[bool, typer.Option("--json", help="Print the result as JSON.")] = False,
) -> None:
    """Download models from their original source, verified by size and sha256."""
    state = _state(ctx)
    if not names and not tier:
        _fail("give model names or --tier (for example: ssw models pull --tier cpu)")
    from stepswitch import modelstore

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
        str | None, typer.Option("--engine", help="llamacpp or ollama (default: by install).")
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
    """Start a local server for a model (llama.cpp) or load it into a running engine."""
    from stepswitch.serve import install, llamacpp
    from stepswitch.serve.manager import ServerManager

    state = _state(ctx)
    with _user_errors():
        if engine in (None, "llamacpp") and llamacpp.LlamaCppEngine().find_binary() is None:
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
) -> dict[str, Any]:
    from stepswitch import paths
    from stepswitch import state as state_module
    from stepswitch.compat import probe

    config = _resolve_endpoint(state, endpoint)
    profile = probe.run(
        config, model=model, fmt=fmt, quick=quick, egress=state.egress(), echo=echo or typer.echo
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
) -> None:
    """Probe an endpoint's capabilities and store its profile."""
    state = _state(ctx)
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
        )
    if json_out:
        _print_json(profile)


@app.command("calibrate")
def cmd_calibrate(
    ctx: typer.Context,
    endpoint: Annotated[
        str | None, typer.Argument(help="Endpoint to measure (default: every local one).")
    ] = None,
    model: Annotated[str | None, typer.Option("--model", "-m", help="Model to measure.")] = None,
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
        "`ssw prove` runs in a Stepswitch source checkout (claims/claims.yaml and tests/claims)"
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
    if result.changed_files:
        typer.echo("Changed: " + ", ".join(result.changed_files))
    if result.branch:
        typer.echo(f"Branch: {result.branch}")
    typer.echo(f"Trajectory: {result.run_dir}")


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
        int, typer.Option("--best-of", help="Candidates across model families; keep the best.")
    ] = 1,
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
    json_out: Annotated[bool, typer.Option("--json", help="Print the result as JSON.")] = False,
) -> None:
    """Run a task with step-level model routing."""
    from stepswitch.core.task import Budgets, TaskSpec

    state = _state(ctx)
    if track not in ("general", "science"):
        _fail("--track must be general or science")
    if best_of > 1 and not json_out:
        typer.echo("note: best-of-N arrives in Phase 6; running one candidate", err=True)
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
            best_of=best_of,
            offline=offline,
            worker=worker,
            seed=seed,
            max_steps=max_steps,
            allow_protected=allow_protected or [],
            review=review,
            apply=apply,
            include_uncommitted=include_uncommitted,
            allow_remote=allow_remote,
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
    suite: Annotated[str, typer.Option("--suite", help="Suite name, e.g. mini-general.")],
    workers: Annotated[
        str | None, typer.Option("--workers", help="Comma-separated workers to compare.")
    ] = None,
    seeds: Annotated[int, typer.Option("--seeds", help="Repetitions per task.")] = 1,
) -> None:
    """Run a benchmark suite."""
    _not_yet("bench run", 3)


@bench_app.command("report")
def bench_report() -> None:
    """Build the leaderboard from recorded results."""
    _not_yet("bench report", 3)


@app.command("dash")
def cmd_dash(
    port: Annotated[int, typer.Option("--port", help="Port to listen on.")] = 7777,
    open_browser: Annotated[
        bool, typer.Option("--open/--no-open", help="Open the dashboard in a browser.")
    ] = True,
) -> None:
    """Start the task API and the dashboard."""
    _not_yet("dash", 4)


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
