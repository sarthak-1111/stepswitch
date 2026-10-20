"""`[context]` settings of the agent (SPEC §13, ADR 0024)."""

from __future__ import annotations

from types import SimpleNamespace

from stepswitch.context.settings import AUTO_WINDOW_MAX, WINDOW_TOKENS, ContextSettings


def config(section: object) -> SimpleNamespace:
    return SimpleNamespace(model_extra={"context": section})


def test_defaults_follow_the_spec() -> None:
    settings = ContextSettings.from_config(SimpleNamespace(model_extra={}))
    assert settings.optimize and settings.window_tokens is None
    assert settings.compact_at == 0.6 and settings.handoff_threshold == 24_000
    assert settings.prune_min_tokens == 800 and settings.distill_tokens == 2000
    assert settings.compression.enabled is False
    assert settings.window_for(8192) == 8192 and settings.window_for(None) == WINDOW_TOKENS
    assert settings.window_for(32768) == WINDOW_TOKENS
    assert settings.window_for(65536) == WINDOW_TOKENS


def test_a_large_context_gets_half_of_it_up_to_a_cap() -> None:
    """ADR 0042: only workers with more than 65,536 tokens of context get a larger window."""
    settings = ContextSettings()
    assert settings.window_for(100_000) == 50_000
    assert settings.window_for(131_072) == AUTO_WINDOW_MAX
    assert settings.window_for(1_048_576) == AUTO_WINDOW_MAX
    fixed = ContextSettings(window_tokens=WINDOW_TOKENS)
    assert fixed.window_for(131_072) == WINDOW_TOKENS and fixed.window_for(8192) == 8192


def test_values_from_the_config_and_bad_ones_ignored() -> None:
    settings = ContextSettings.from_config(
        config(
            {
                "optimize": False,
                "window_tokens": 16384,
                "compact_at": 0.5,
                "handoff_threshold_tokens": 8000,
                "keep_recent": 3,
                "compression": {"enabled": True, "endpoint": "http://127.0.0.1:8095"},
            }
        )
    )
    assert not settings.optimize and settings.window_tokens == 16384
    assert settings.compact_at == 0.5 and settings.handoff_threshold == 8000
    assert settings.keep_recent == 3 and settings.compression.enabled
    bad = ContextSettings.from_config(
        config({"window_tokens": 100, "compact_at": 2, "optimize": "yes", "keep_recent": True})
    )
    assert bad == ContextSettings()
    assert ContextSettings.from_config(config("not a table")) == ContextSettings()
