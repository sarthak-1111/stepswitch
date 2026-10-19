"""Record the network and process activity of one `ssw` command (claims C-006 and C-007).

Usage: `python recorder.py <ssw arguments>` with SSW_RECORD=<file.jsonl>,
SSW_RECORDER=audit|patch, and SSW_GUARD=block to refuse non-local traffic. The recorder is
installed before any Stepswitch code is imported, then the CLI runs in this process. Each event
becomes one JSON line:

- connect / send: socket connects and datagram sends, with the address;
- bind: sockets bound in this process (a connect to one of them never leaves the process);
- lookup: host name resolution (a lookup of a non-local name is itself outbound traffic);
- process: spawned processes, with the executable and the first arguments.

With SSW_GUARD=block, a connect or send to a non-local address and a lookup of a non-local name
are recorded with `"blocked": true` and then refused: a socket-level guard (C-006).

`audit` mode uses interpreter audit hooks, which also see sockets used from C. `patch` mode
wraps the socket, name-lookup and process functions instead; it is the fallback for hosts
where security software breaks audit hooks, and the evidence records which mode ran. In both
modes the Windows event loop's connect path is wrapped too, since it bypasses `socket.connect`.
Neither mode sees traffic of child processes; the evidence lists those processes.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

_OUT = Path(os.environ["SSW_RECORD"]).open("a", encoding="utf-8")  # noqa: SIM115
_GUARD = os.environ.get("SSW_GUARD") == "block"
_LOCAL_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}


class Blocked(PermissionError):
    """A non-local connection, send or lookup refused by the guard."""


def _local(host: Any) -> bool:
    text = str(host).strip("[]").split("%", 1)[0].lower()
    if text in _LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def _refused(address: Any) -> bool:
    """Whether the guard refuses this destination (unix sockets and loopback are local)."""
    if not _GUARD or not isinstance(address, tuple) or not address:
        return False
    return not _local(address[0])


def _refuse(event: str) -> Blocked:
    return Blocked(13, f"the socket guard refuses {event} to a non-local destination")


def _emit(row: dict[str, Any]) -> None:
    try:
        _OUT.write(json.dumps(row, default=str) + "\n")
        _OUT.flush()
    except Exception:  # noqa: BLE001 - recording must never break the command
        pass


def _net(kind: str, event: str, sock: Any, address: Any) -> bool:
    """Record one socket event; True when the guard refuses it."""
    blocked = kind != "bind" and _refused(address)
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
    if blocked:
        row["blocked"] = True
    _emit(row)
    return blocked


def _lookup(event: str, host: Any) -> bool:
    """Record one name lookup; True when the guard refuses it."""
    blocked = _GUARD and host is not None and not _local(host)
    row: dict[str, Any] = {"kind": "lookup", "event": event, "host": str(host)}
    if blocked:
        row["blocked"] = True
    _emit(row)
    return blocked


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

_NET_EVENTS = {
    "socket.connect": "connect",
    "socket.sendto": "send",
    "socket.sendmsg": "send",
    "socket.bind": "bind",
}
_LOOKUP_EVENTS = {
    "socket.getaddrinfo",
    "socket.gethostbyname",
    "socket.gethostbyname_ex",
    "socket.gethostbyaddr",
}
_PROCESS_EVENTS = {"os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.startfile"}


def _hook(event: str, args: tuple[Any, ...]) -> None:
    blocked = False
    try:
        if event in _NET_EVENTS:
            blocked = _net(_NET_EVENTS[event], event, args[0], args[1])
        elif event in _LOOKUP_EVENTS:
            blocked = _lookup(event, args[0])
        elif event == "subprocess.Popen":
            _process(event, args[0], args[1])
        elif event in _PROCESS_EVENTS:
            _process(event, None, list(args)[:3])
    except Exception:  # noqa: BLE001 - an exception here would fail the audited operation
        pass
    if blocked:  # raising from an audit hook aborts the operation
        raise _refuse(event)


# --------------------------------------------------------------------------------------------
# patch mode
# --------------------------------------------------------------------------------------------


def _patch_sockets() -> None:
    cls = socket.socket
    connect, connect_ex, sendto, bind = cls.connect, cls.connect_ex, cls.sendto, cls.bind

    def patched_connect(self: Any, address: Any) -> Any:
        if _net("connect", "socket.connect", self, address):
            raise _refuse("socket.connect")
        return connect(self, address)

    def patched_connect_ex(self: Any, address: Any) -> Any:
        if _net("connect", "socket.connect", self, address):
            raise _refuse("socket.connect")
        return connect_ex(self, address)

    def patched_sendto(self: Any, data: Any, *rest: Any) -> Any:
        if _net("send", "socket.sendto", self, rest[-1] if rest else None):
            raise _refuse("socket.sendto")
        return sendto(self, data, *rest)

    def patched_bind(self: Any, address: Any) -> Any:
        _net("bind", "socket.bind", self, address)
        return bind(self, address)

    cls.connect = patched_connect  # type: ignore[method-assign]
    cls.connect_ex = patched_connect_ex  # type: ignore[method-assign]
    cls.sendto = patched_sendto  # type: ignore[method-assign, assignment]
    cls.bind = patched_bind  # type: ignore[method-assign]
    if hasattr(cls, "sendmsg"):
        sendmsg = cls.sendmsg

        def patched_sendmsg(self: Any, buffers: Any, *rest: Any) -> Any:
            refused = len(rest) >= 3 and rest[2] is not None
            if refused and _net("send", "socket.sendmsg", self, rest[2]):
                raise _refuse("socket.sendmsg")
            return sendmsg(self, buffers, *rest)

        cls.sendmsg = patched_sendmsg  # type: ignore[method-assign]

    for name in sorted(e.split(".", 1)[1] for e in _LOOKUP_EVENTS):
        original = getattr(socket, name)

        def lookup(host: Any, *args: Any, _original: Any = original, _name: str = name) -> Any:
            if _lookup(f"socket.{_name}", host):
                raise socket.gaierror(socket.EAI_NONAME, "the socket guard refuses this lookup")
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
        if _net("connect", "proactor.connect", conn, address):
            raise _refuse("proactor.connect")
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
