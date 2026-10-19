"""Running the proxy: from the project's configuration, in the foreground (`ssw proxy`) or on
a thread for the length of a harness run (`stepswitch.workers.harness`).

`[proxy]` in stepswitch.toml or `.stepswitch/local.toml`:

    [proxy]
    min_level = 1               # rung where heavy steps start (0 = T0 ... 3 = remote)
    heartbeat_seconds = 5.0     # keep-alive interval of quiet streams
    [proxy.aliases]             # caller model names -> slots or workers
    "my-harness-large" = "ssw:plan"
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path
from typing import Any, cast

from stepswitch.config import Config
from stepswitch.proxy.app import DEFAULT_PORT, HOST, create_proxy_app, load_or_create_token
from stepswitch.proxy.service import ProxyService, ProxySettings, TaskEgress
from stepswitch.router.rules import rules_from_config
from stepswitch.workers.catalog import Catalog


def proxy_settings(
    config: Config, *, offline: bool | None = None, allow_remote: bool = False
) -> ProxySettings:
    raw: dict[str, Any] = dict((config.model_extra or {}).get("proxy") or {})
    aliases = raw.get("aliases") or {}
    if not isinstance(aliases, dict) or not all(isinstance(v, str) for v in aliases.values()):
        raise ValueError("[proxy.aliases] maps model names to slot names or worker IDs")
    settings = ProxySettings(
        offline=config.offline if offline is None else offline,
        allow_remote=allow_remote,
        aliases={str(k): v for k, v in aliases.items()},
    )
    if "min_level" in raw:
        settings.min_level = int(raw["min_level"])
    if "heartbeat_seconds" in raw:
        settings.heartbeat_seconds = max(0.5, float(raw["heartbeat_seconds"]))
    return settings


def build_service(
    root: Path,
    config: Config,
    *,
    egress: Any = None,
    offline: bool | None = None,
    allow_remote: bool = False,
    catalog: Catalog | None = None,
    db: Any = None,
) -> ProxyService:
    from stepswitch import net
    from stepswitch.state import open_project_state

    db = db if db is not None else open_project_state(root)
    settings = proxy_settings(config, offline=offline, allow_remote=allow_remote)
    if catalog is None:
        # Offline mode refuses non-local connections at the transport too, not only in routing.
        base = (egress or net.default()).child(offline=settings.offline)
        # The wrapper offers the `client()` that model clients call (see TaskEgress).
        audited = cast(net.Egress, TaskEgress(base))
        catalog = Catalog.from_config(config, root, egress=audited, db=db)
    return ProxyService(root, catalog, db=db, settings=settings, rules=rules_from_config(config))


def free_port(host: str = HOST) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


class ProxyServer:
    """A uvicorn server for one `ProxyService`."""

    def __init__(
        self, service: ProxyService, *, token: str | None = None, port: int = DEFAULT_PORT
    ) -> None:
        import uvicorn

        self.service = service
        self.token = token or load_or_create_token(service.root)
        self.port = port or free_port()
        app = create_proxy_app(service, token=self.token, port=self.port)
        self.server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=HOST,
                port=self.port,
                log_level="warning",
                access_log=False,
                server_header=False,
                timeout_graceful_shutdown=3,
                lifespan="off",
            )
        )
        self._thread: threading.Thread | None = None
        self._sweeper = threading.Event()

    @property
    def base_url(self) -> str:
        return f"http://{HOST}:{self.port}"

    def _sweep(self) -> None:
        while not self._sweeper.wait(60.0):
            self.service.sweep()

    def start(self, timeout: float = 15.0) -> str:
        """Serve on a thread; returns the base URL once the server accepts connections."""
        self._thread = threading.Thread(target=self.server.run, daemon=True, name="ssw-proxy")
        self._thread.start()
        threading.Thread(target=self._sweep, daemon=True).start()
        deadline = time.monotonic() + timeout
        while not self.server.started:
            if not self._thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError(f"the proxy did not start on {self.base_url}")
            time.sleep(0.05)
        return self.base_url

    def stop(self, *, reason: str = "proxy stopped") -> list[str]:
        self.server.should_exit = True
        self._sweeper.set()
        if self._thread is not None:
            self._thread.join(timeout=10.0)
        return self.service.end_tasks(reason=reason)

    def run(self) -> None:
        """Serve in the foreground until interrupted."""
        threading.Thread(target=self._sweep, daemon=True).start()
        try:
            self.server.run()
        finally:
            self._sweeper.set()
