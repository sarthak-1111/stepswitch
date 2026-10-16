"""Record the network and process activity of one `ssw` command (claim C-007).

Usage: `python recorder.py <ssw arguments>` with SSW_RECORD=<file.jsonl> and
SSW_RECORDER=audit|patch. The recorder is installed before any Stepswitch code is imported,
then the CLI runs in this process. Each event becomes one JSON line:

- connect / send: socket connects and datagram sends, with the address;
- lookup: host name resolution (a lookup of a non-local name is itself outbound traffic);
- process: spawned processes, with the executable and the first arguments.

`audit` mode uses interpreter audit hooks, which also see sockets used from C. `patch` mode
wraps the socket, name-lookup and process functions instead; it is the fallback for hosts
where security software breaks audit hooks, and the evidence records which mode ran. In both
modes the Windows event loop's connect path is wrapped too, since it bypasses `socket.connect`.
Neither mode sees traffic of child processes; the evidence lists those processes.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

_OUT = Path(os.environ["SSW_RECORD"]).open("a", encoding="utf-8")  # noqa: SIM115


def _emit(row: dict[str, Any]) -> None:
    try:
        _OUT.write(json.dumps(row, default=str) + "\n")
        _OUT.flush()
    except Exception:  # noqa: BLE001 - recording must never break the command
        pass


def _net(kind: str, event: str, sock: Any, address: Any) -> None:
    row: dict[str, Any] = {
        "kind": kind,
        "event": event,
        "family": int(getattr(sock, "family", -1)),
        "address": repr(address),
    }
    if isinstance(address, tuple) and address:
        row["host"], row["port"] = str(address[0]), address[1]
    elif isinstance(address, str | bytes):
        row["unix"] = True
    _emit(row)


def _process(event: str, executable: Any, argv: Any) -> None:
    items = list(argv) if isinstance(argv, list | tuple) else [argv]
    _emit(
        {
            "kind": "process",
            "event": event,
            "executable": str(executable or (items[0] if items else "")),
            "argv": [str(item) for item in items][:6],
        }
    )


# --------------------------------------------------------------------------------------------
# audit mode
# --------------------------------------------------------------------------------------------

_NET_EVENTS = {"socket.connect": "connect", "socket.sendto": "send", "socket.sendmsg": "send"}
_LOOKUP_EVENTS = {
    "socket.getaddrinfo",
    "socket.gethostbyname",
    "socket.gethostbyname_ex",
    "socket.gethostbyaddr",
}
_PROCESS_EVENTS = {"os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.startfile"}


def _hook(event: str, args: tuple[Any, ...]) -> None:
    try:
        if event in _NET_EVENTS:
            _net(_NET_EVENTS[event], event, args[0], args[1])
        elif event in _LOOKUP_EVENTS:
            _emit({"kind": "lookup", "event": event, "host": str(args[0])})
        elif event == "subprocess.Popen":
            _process(event, args[0], args[1])
        elif event in _PROCESS_EVENTS:
            _process(event, None, list(args)[:3])
    except Exception:  # noqa: BLE001 - an exception here would fail the audited operation
        pass


# --------------------------------------------------------------------------------------------
# patch mode
# --------------------------------------------------------------------------------------------


def _patch_sockets() -> None:
    cls = socket.socket
    connect, connect_ex, sendto = cls.connect, cls.connect_ex, cls.sendto

    def patched_connect(self: Any, address: Any) -> Any:
        _net("connect", "socket.connect", self, address)
        return connect(self, address)

    def patched_connect_ex(self: Any, address: Any) -> Any:
        _net("connect", "socket.connect", self, address)
        return connect_ex(self, address)

    def patched_sendto(self: Any, data: Any, *rest: Any) -> Any:
        _net("send", "socket.sendto", self, rest[-1] if rest else None)
        return sendto(self, data, *rest)

    cls.connect = patched_connect  # type: ignore[method-assign]
    cls.connect_ex = patched_connect_ex  # type: ignore[method-assign]
    cls.sendto = patched_sendto  # type: ignore[method-assign, assignment]
    if hasattr(cls, "sendmsg"):
        sendmsg = cls.sendmsg

        def patched_sendmsg(self: Any, buffers: Any, *rest: Any) -> Any:
            if len(rest) >= 3 and rest[2] is not None:
                _net("send", "socket.sendmsg", self, rest[2])
            return sendmsg(self, buffers, *rest)

        cls.sendmsg = patched_sendmsg  # type: ignore[method-assign]

    for name in sorted(e.split(".", 1)[1] for e in _LOOKUP_EVENTS):
        original = getattr(socket, name)

        def lookup(host: Any, *args: Any, _original: Any = original, _name: str = name) -> Any:
            _emit({"kind": "lookup", "event": f"socket.{_name}", "host": str(host)})
            return _original(host, *args)

        setattr(socket, name, lookup)


def _patch_processes() -> None:
    popen_init = subprocess.Popen.__init__

    def patched_init(self: Any, args: Any, *rest: Any, **kwargs: Any) -> None:
        _process("subprocess.Popen", kwargs.get("executable"), args)
        popen_init(self, args, *rest, **kwargs)

    subprocess.Popen.__init__ = patched_init  # type: ignore[method-assign]
    for name in ("system", "startfile", "posix_spawn", "posix_spawnp", "execv", "execve"):
        original = getattr(os, name, None)
        if original is None:
            continue

        def wrapper(*args: Any, _original: Any = original, _name: str = name) -> Any:
            _process(f"os.{_name}", None, list(args)[:3])
            return _original(*args)

        setattr(os, name, wrapper)


def _patch_event_loop() -> None:
    if sys.platform != "win32":
        return
    from asyncio import windows_events

    proactor_connect = windows_events.IocpProactor.connect

    def patched(self: Any, conn: Any, address: Any) -> Any:
        _net("connect", "proactor.connect", conn, address)
        return proactor_connect(self, conn, address)

    windows_events.IocpProactor.connect = patched  # type: ignore[method-assign]


def main() -> None:
    mode = os.environ.get("SSW_RECORDER", "audit")
    if mode == "audit":
        sys.addaudithook(_hook)
    else:
        _patch_sockets()
        _patch_processes()
    _patch_event_loop()
    sys.argv = ["ssw", *sys.argv[1:]]
    from stepswitch.cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()
