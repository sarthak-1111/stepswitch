"""Token counts with the receiving model's tokenizer (SPEC §9 resolution order)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from stepswitch import net
from stepswitch.context import tokens
from stepswitch.context.tokens import (
    RESOLUTION_ORDER,
    EstimateCounter,
    FileCounter,
    Receiver,
    ServerCounter,
    TokenizerError,
    TokenizerSpec,
)

pytest.importorskip("tokenizers")


def tiny_tokenizer(path: Path) -> Path:
    from tokenizers import Tokenizer, models, pre_tokenizers

    vocab = {"hello": 0, "world": 1, "[UNK]": 2}
    tokenizer = Tokenizer(models.WordLevel(vocab, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    path.parent.mkdir(parents=True, exist_ok=True)
    tokenizer.save(str(path))
    return path


def spec(name: str, *patterns: str) -> TokenizerSpec:
    return TokenizerSpec(
        name=name,
        hub_id=f"org/{name}",
        revision="0" * 40,
        file="tokenizer.json",
        sha256="0" * 64,
        match=patterns,
    )


def test_order_is_the_spec_order() -> None:
    assert RESOLUTION_ORDER == ("tokenizer.json", "server", "mapped", "estimate")


def test_bundled_specs_map_the_bench_models() -> None:
    specs = tokens.load_specs()
    assert {"qwen3", "qwen2.5", "gpt-oss"} <= set(specs)
    for name in specs.values():
        assert len(name.sha256) == 64 and len(name.revision) == 40
    assert tokens.mapped_spec("gpt-oss:120b", specs).name == "gpt-oss"  # type: ignore[union-attr]
    assert tokens.mapped_spec("qwen2.5:32b", specs).name == "qwen2.5"  # type: ignore[union-attr]
    assert tokens.mapped_spec("ws-local/qwen2.5-custom:latest", specs).name == "qwen2.5"  # type: ignore[union-attr]
    assert tokens.mapped_spec("Qwen3-4B-Instruct-2507", specs).name == "qwen3"  # type: ignore[union-attr]
    assert tokens.mapped_spec("phi-3.5-mini-instruct", specs) is None


def test_estimate_is_flagged() -> None:
    counter = EstimateCounter()
    assert counter.count("abcdefgh") == 2 and counter.count("abcdefghi") == 3
    measured = tokens.count("abcdefgh", counter)
    assert measured.exact is False and measured.tokenizer == "estimate:chars/4"


def test_resolution_prefers_the_registry_file_then_the_server_then_the_mapping(
    tmp_path: Path,
) -> None:
    models_dir = tmp_path / "models"
    specs = {"own": spec("own"), "family": spec("family", "fam-*")}
    receiver = Receiver(model="fam-7b", tokenizer="own", engine="llamacpp", base_url="http://x")
    # Nothing on disk: the llama.cpp server answers.
    counter = tokens.resolve(receiver, models_dir=models_dir, specs=specs)
    assert isinstance(counter, ServerCounter)
    # The mapped file alone does not beat the server.
    tiny_tokenizer(specs["family"].local_path(models_dir))
    assert isinstance(tokens.resolve(receiver, models_dir=models_dir, specs=specs), ServerCounter)
    # The registry's own file wins over everything.
    tiny_tokenizer(specs["own"].local_path(models_dir))
    counter = tokens.resolve(receiver, models_dir=models_dir, specs=specs)
    assert isinstance(counter, FileCounter) and counter.name == "tokenizer.json:own"
    assert counter.count("hello world hello") == 3
    # A model on another engine falls through to the mapped file, then to the estimate.
    other = Receiver(model="fam-7b", engine="ollama", base_url="http://x")
    counter = tokens.resolve(other, models_dir=models_dir, specs=specs)
    assert isinstance(counter, FileCounter) and counter.name == "mapped:family"
    unknown = Receiver(model="other", engine="ollama")
    assert isinstance(tokens.resolve(unknown, models_dir=models_dir, specs=specs), EstimateCounter)


def test_resolve_local_never_uses_a_server(tmp_path: Path) -> None:
    specs = {"family": spec("family", "fam-*")}
    assert tokens.resolve_local({"model": "fam-1"}, models_dir=tmp_path, specs=specs) is None
    tiny_tokenizer(specs["family"].local_path(tmp_path))
    counter = tokens.resolve_local({"model": "fam-1"}, models_dir=tmp_path, specs=specs)
    assert counter is not None and counter.exact and counter.count("hello") == 1
    assert tokens.resolve_local({}, models_dir=tmp_path, specs=specs) is None


def _server(
    answer: object, status: int = 200
) -> tuple[httpx.MockTransport, list[dict[str, object]]]:
    seen: list[dict[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append({"url": str(request.url), "body": json.loads(request.content)})
        return httpx.Response(status, json=answer)

    return httpx.MockTransport(handle), seen


def test_server_counter_posts_to_tokenize_without_special_tokens() -> None:
    transport, seen = _server({"tokens": [1, 2, 3, 4]})
    counter = ServerCounter("http://127.0.0.1:9/v1", transport=transport, egress=net.Egress())
    assert counter.count("some text") == 4
    assert seen == [
        {
            "url": "http://127.0.0.1:9/tokenize",
            "body": {"content": "some text", "add_special": False, "parse_special": False},
        }
    ]


@pytest.mark.parametrize(
    ("answer", "status"), [({"tokens": "x"}, 200), ({"nope": 1}, 200), ({"tokens": []}, 500)]
)
def test_server_counter_rejects_bad_answers(answer: object, status: int) -> None:
    transport, _ = _server(answer, status)
    counter = ServerCounter("http://127.0.0.1:9", transport=transport, egress=net.Egress())
    with pytest.raises(TokenizerError):
        counter.count("text")
    measured = tokens.count("text", counter)
    assert not measured.exact and measured.tokenizer.startswith("estimate:chars/4 (llama.cpp")


def test_describe_lists_presence(tmp_path: Path) -> None:
    specs = {"family": spec("family", "fam-*")}
    assert tokens.describe(tmp_path, specs)[0]["present"] is False
    tiny_tokenizer(specs["family"].local_path(tmp_path))
    assert tokens.describe(tmp_path, specs)[0]["present"] is True
