"""The per-server prefix registry (SPEC §7.2): hash chains, common prefixes, calibration."""

from __future__ import annotations

from stepswitch.compat.ir import Message, Request, TextPart, ToolSpec
from stepswitch.router.cache import MIN_PREDICTED, PrefixRegistry, chain_of


def request(*texts: str, system: str = "You are the agent.", tool: str = "read") -> Request:
    return Request(
        model="m",
        system=[TextPart(text=system)],
        tools=[ToolSpec(name=tool, description="Read a file.", parameters={"type": "object"})],
        messages=[
            Message.text("user" if i % 2 == 0 else "assistant", t) for i, t in enumerate(texts)
        ],
    )


def test_chain_grows_by_one_block_per_message_and_is_stable() -> None:
    a = chain_of(request("task", "reply"))
    b = chain_of(request("task", "reply"))
    assert a == b
    assert len(a.blocks) == 3
    assert a.prefix_tokens > 0 and a.tokens > a.prefix_tokens


def test_common_prefix_stops_at_the_first_changed_block() -> None:
    old = chain_of(request("task", "reply", "tool result"))
    grown = chain_of(request("task", "reply", "tool result", "next reply"))
    assert old.common(grown) == old.tokens
    pruned = chain_of(request("task", "[superseded]", "tool result", "next reply"))
    assert old.common(pruned) == old.blocks[1][1]
    other_prefix = chain_of(request("task", system="Another system prompt."))
    assert old.common(other_prefix) == 0
    other_tools = chain_of(request("task", tool="grep"))
    assert old.common(other_tools) == 0


def test_registry_tracks_the_last_prompt_per_server() -> None:
    registry = PrefixRegistry()
    first = chain_of(request("task", "reply"))
    later = chain_of(request("task", "reply", "result"))
    assert registry.cached_tokens("a", later) == 0
    registry.record("a", first)
    assert registry.cached_tokens("a", later) == first.tokens
    assert registry.cached_tokens("b", later) == 0
    # A different prompt on the same server replaces what it held.
    registry.record("a", chain_of(request("plan this", system="You plan.")))
    assert registry.cached_tokens("a", later) == 0
    registry.forget("a")
    assert registry.snapshot() == {}


def test_observations_calibrate_the_hit_ratio() -> None:
    registry = PrefixRegistry()
    chain = chain_of(request("x" * 4000))
    registry.record("s", chain)
    predicted = registry.predicted("s", chain)
    assert predicted >= MIN_PREDICTED
    registry.observe("s", predicted, 0)  # the server reports nothing cached
    assert registry.hit_ratio["s"] < 1.0
    assert registry.cached_tokens("s", chain) < predicted
    registry.observe("s", 10, 0)  # too few tokens to learn from
    assert registry.observations["s"] == 1
    registry.seed("s", 1.0)  # a measured ratio never overrides observations
    assert registry.hit_ratio["s"] < 1.0
    registry.seed("t", 0.5)
    assert registry.hit_ratio["t"] == 0.5
