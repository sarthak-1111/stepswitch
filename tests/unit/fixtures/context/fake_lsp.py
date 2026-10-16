"""A tiny language server for the LSP client tests (stdio, Content-Length framed JSON-RPC).

Handles initialize, initialized, didOpen, didChange, definition, references, hover, shutdown
and exit. Positions are UTF-16 code units, the protocol default. It publishes a warning for
every line containing TODO, writes a few hundred kilobytes to stderr while initializing (the
client must drain it or the server blocks), asks the client for its configuration and for an
unknown method after `initialized`, never answers a hover on the word `sleepy`, and reports
what the client answered through a hover on the word `report`.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
docs: dict[str, str] = {}
replies: dict[str, Any] = {}


def read_message() -> dict[str, Any] | None:
    stream = sys.stdin.buffer
    length = None
    while True:
        line = stream.readline()
        if not line:
            return None
        text = line.decode("ascii").strip()
        if not text:
            if length is not None:
                break
            continue
        name, _, value = text.partition(":")
        if name.strip().lower() == "content-length":
            length = int(value)
    message: dict[str, Any] = json.loads(stream.read(length))
    return message


def send(message: dict[str, Any]) -> None:
    body = json.dumps(message).encode("utf-8")
    sys.stdout.buffer.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
    sys.stdout.buffer.flush()


def utf16(text: str, index: int) -> int:
    return len(text[:index].encode("utf-16-le")) // 2


def index_of(text: str, units: int) -> int:
    data = text.encode("utf-16-le")[: units * 2]
    return len(data.decode("utf-16-le", errors="ignore"))


def word_at(uri: str, position: dict[str, int]) -> str:
    rows = docs.get(uri, "").split("\n")
    row = rows[position["line"]] if position["line"] < len(rows) else ""
    index = index_of(row, position["character"])
    for match in WORD.finditer(row):
        if match.start() <= index <= match.end():
            return match.group()
    return ""


def span(uri: str, line: int, start: int, end: int) -> dict[str, Any]:
    row = docs[uri].split("\n")[line]
    return {
        "uri": uri,
        "range": {
            "start": {"line": line, "character": utf16(row, start)},
            "end": {"line": line, "character": utf16(row, end)},
        },
    }


def publish(uri: str) -> None:
    diagnostics = []
    for number, row in enumerate(docs[uri].split("\n")):
        index = row.find("TODO")
        if index >= 0:
            location = span(uri, number, index, index + 4)
            diagnostics.append(
                {
                    "range": location["range"],
                    "severity": 2,
                    "source": "fake",
                    "code": "T1",
                    "message": "todo left in code",
                }
            )
    params = {"uri": uri, "diagnostics": diagnostics}
    send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": params})


def main() -> int:
    shutdown = False
    while True:
        message = read_message()
        if message is None:
            return 1
        method = message.get("method")
        ident = message.get("id")
        params = message.get("params") or {}
        if method is None:  # a response to one of our requests
            replies[str(ident)] = message.get("result", message.get("error"))
            continue
        if method == "initialize":
            for _ in range(2000):
                sys.stderr.write("noise " * 30 + "\n")
            sys.stderr.flush()
            capabilities = {
                "textDocumentSync": 1,
                "definitionProvider": True,
                "referencesProvider": True,
                "hoverProvider": True,
                "positionEncoding": "utf-16",
            }
            send({"jsonrpc": "2.0", "id": ident, "result": {"capabilities": capabilities}})
        elif method == "initialized":
            items = {"items": [{"section": "fake.level"}, {"section": "missing"}]}
            send(
                {
                    "jsonrpc": "2.0",
                    "id": "cfg",
                    "method": "workspace/configuration",
                    "params": items,
                }
            )
            send({"jsonrpc": "2.0", "id": "odd", "method": "fake/unknown", "params": {}})
        elif method == "textDocument/didOpen":
            document = params["textDocument"]
            docs[document["uri"]] = document["text"]
            publish(document["uri"])
        elif method == "textDocument/didChange":
            uri = params["textDocument"]["uri"]
            docs[uri] = params["contentChanges"][-1]["text"]
            publish(uri)
        elif method == "textDocument/definition":
            uri = params["textDocument"]["uri"]
            word = word_at(uri, params["position"])
            result = None
            for number, row in enumerate(docs[uri].split("\n")):
                match = re.match(rf"\s*(?:def|class)\s+({re.escape(word)})\b", row)
                if word and match:
                    result = [span(uri, number, match.start(1), match.end(1))]
                    break
            send({"jsonrpc": "2.0", "id": ident, "result": result})
        elif method == "textDocument/references":
            uri = params["textDocument"]["uri"]
            word = word_at(uri, params["position"])
            found = []
            for number, row in enumerate(docs[uri].split("\n")):
                for match in re.finditer(rf"\b{re.escape(word)}\b", row) if word else []:
                    found.append(span(uri, number, match.start(), match.end()))
            send({"jsonrpc": "2.0", "id": ident, "result": found})
        elif method == "textDocument/hover":
            word = word_at(params["textDocument"]["uri"], params["position"])
            if word == "sleepy":
                continue
            value = json.dumps(replies, sort_keys=True) if word == "report" else f"**{word}**"
            result = {"contents": {"kind": "markdown", "value": value}}
            send({"jsonrpc": "2.0", "id": ident, "result": result})
        elif method == "shutdown":
            shutdown = True
            send({"jsonrpc": "2.0", "id": ident, "result": None})
        elif method == "exit":
            return 0 if shutdown else 1
        elif ident is not None:
            error = {"code": -32601, "message": f"unknown method {method}"}
            send({"jsonrpc": "2.0", "id": ident, "error": error})


if __name__ == "__main__":
    sys.exit(main())
