"""The per-server prefix registry (SPEC §7.2, ADR 0024).

A model server keeps the key-value cache of the prompt it processed last, so a request that
starts with that prompt only pays to prefill the rest. The registry remembers, per server, a hash
chain of the last prompt sent there: one cumulative hash per block, where block 0 is the stable
prefix (system text and tool specs, TK1) and every message is one more block. The cached part of
a new request is the longest common prefix of the two chains, in (estimated) tokens.

Servers may hold less than that: a single-slot server forgets a prompt when another arrives, and
some servers do not cache at all. The registry calibrates itself per server from what servers
report (`usage.cached_input_tokens`): `observe` keeps a moving ratio of reported to predicted
cached tokens, and `ssw calibrate --cache` seeds it.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

from stepswitch.compat.ir import Request

CHARS_PER_TOKEN = 4.0
# Weight of a new observation in the moving hit ratio.
OBSERVATION_WEIGHT = 0.3
# Predicted cached tokens below this are too few to learn from.
MIN_PREDICTED = 64


def _tokens(chars: int) -> int:
    return int(-(-max(0, chars) // CHARS_PER_TOKEN))


def _canonical(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


@dataclass(frozen=True)
class Chain:
    """Cumulative (hash, tokens) at each block boundary of one request."""

    blocks: tuple[tuple[str, int], ...]

    @property
    def tokens(self) -> int:
        return self.blocks[-1][1] if self.blocks else 0

    @property
    def prefix_tokens(self) -> int:
        """Tokens of block 0, the stable prefix."""
        return self.blocks[0][1] if self.blocks else 0

    def common(self, other: Chain) -> int:
        """Tokens of the longest common block prefix with `other`."""
        shared = 0
        for (mine, tokens), (theirs, _) in zip(self.blocks, other.blocks, strict=False):
            if mine != theirs:
                break
            shared = tokens
        return shared


def chain_of(request: Request) -> Chain:
    """The hash chain of `request`. Equal chains mean byte-identical prompts in the IR."""
    digest = hashlib.sha256()
    head = _canonical(
        {
            "system": [part.text for part in request.system],
            "tools": [tool.model_dump(exclude_none=True) for tool in request.tools],
        }
    )
    digest.update(head)
    total = _tokens(len(head))
    blocks = [(digest.hexdigest()[:24], total)]
    for message in request.messages:
        data = _canonical(message.model_dump(exclude_none=True))
        digest.update(b"\0" + data)
        total += _tokens(len(data))
        blocks.append((digest.copy().hexdigest()[:24], total))
    return Chain(tuple(blocks))


@dataclass
class _Held:
    chain: Chain
    at: float


@dataclass
class PrefixRegistry:
    """What each server holds, keyed by server (a worker id: `<endpoint>/<model>`)."""

    held: dict[str, _Held] = field(default_factory=dict)
    # Reported / predicted cached tokens per server (1.0 until observed or calibrated).
    hit_ratio: dict[str, float] = field(default_factory=dict)
    observations: dict[str, int] = field(default_factory=dict)

    def cached_tokens(self, server: str, chain: Chain) -> int:
        """Tokens of `chain` that `server` is expected to have cached."""
        held = self.held.get(server)
        if held is None:
            return 0
        shared = held.chain.common(chain)
        return int(shared * max(0.0, min(1.0, self.hit_ratio.get(server, 1.0))))

    def predicted(self, server: str, chain: Chain) -> int:
        """The common prefix before calibration (what `observe` compares with)."""
        held = self.held.get(server)
        return held.chain.common(chain) if held is not None else 0

    def record(self, server: str, chain: Chain, *, at: float | None = None) -> None:
        """`server` just processed a request with `chain`."""
        self.held[server] = _Held(chain, time.time() if at is None else at)

    def observe(self, server: str, predicted: int, reported: int | None) -> None:
        """Calibrate with the server's own count of cached prompt tokens for one request."""
        if reported is None or predicted < MIN_PREDICTED:
            return
        ratio = max(0.0, min(1.0, reported / predicted))
        old = self.hit_ratio.get(server, 1.0)
        self.hit_ratio[server] = round(
            (1 - OBSERVATION_WEIGHT) * old + OBSERVATION_WEIGHT * ratio, 4
        )
        self.observations[server] = self.observations.get(server, 0) + 1

    def seed(self, server: str, ratio: float | None) -> None:
        """A measured ratio (`ssw calibrate --cache`) before any observation in this task."""
        if ratio is not None and server not in self.observations:
            self.hit_ratio[server] = max(0.0, min(1.0, float(ratio)))

    def forget(self, server: str) -> None:
        self.held.pop(server, None)

    def snapshot(self) -> dict[str, Any]:
        return {
            server: {
                "tokens": held.chain.tokens,
                "blocks": len(held.chain.blocks),
                "hit_ratio": self.hit_ratio.get(server, 1.0),
            }
            for server, held in sorted(self.held.items())
        }
