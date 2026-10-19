"""The secret scanner and redactor (SPEC §15, `stepswitch.redact`)."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator

import httpx
import pytest

from stepswitch import net, redact
from stepswitch.compat.client import Endpoint, ModelClient
from stepswitch.compat.ir import Message, Request, TextPart

# Test values only: built so no real credential format is complete in this file's text.
PRIVATE_KEY = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmU=\n"
    "-----END OPENSSH PRIVATE KEY-----"
)
API_KEY = "sk-" + "proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
PAYMENT_KEY = "sk_" + "live_" + "Tq7Y2mVx9Lr4Kp1Zs8Nw3Hc6"
REPO_TOKEN = "ghp_" + "a" * 18 + "B1" * 9
REPO_TOKEN_2 = "glpat-" + "x1Y2z3W4v5U6t7S8r9Q0"
PACKAGE_TOKEN = "npm_" + "a1b2c3d4e5" * 3 + "f6g7h8"
REGISTRY_TOKEN = (
    "pypi-" + "AgEIcHlwaS5vcmc" + "CJGZmZjM0NmY4LTk3MjgtNDc3MS05NGQ2LWFlMzZhZDQ3NTgwAAIqWzMsIjc2"
)
CLOUD_ID = "AKIA" + "ABCDEFGH23456723"
JWT = "eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0" + ".c2lnbmF0dXJlLWhlcmUtMTIz"
SSW = "ssw-" + "Zx9" * 15
BASIC = "Basic " + "YWxhZGRpbjpvcGVuc2VzYW1l"  # user:password, base64


@pytest.fixture(autouse=True)
def default_mode() -> Iterator[None]:
    yield
    redact.configure(mode="remote", known=[])


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (PRIVATE_KEY, "private-key"),
        (f"key={API_KEY}", "api-key"),
        (f"charge with {PAYMENT_KEY}", "payment-key"),
        (f"token {REPO_TOKEN}", "repo-token"),
        (f"token {REPO_TOKEN_2}", "repo-token"),
        (f"//registry/:_authToken={PACKAGE_TOKEN}", "package-token"),
        (f"password: {REGISTRY_TOKEN}", "package-token"),
        (f"id {CLOUD_ID} here", "cloud-key-id"),
        (f"session {JWT}", "jwt"),
        (f"proxy token {SSW}", "stepswitch-token"),
        ("Authorization: Bearer abcdefghijklmnopqrstuvwx12345", "bearer"),
        (f"Authorization: {BASIC}", "basic"),
        ("postgres://app:s3cr3t-pw@db.internal/app", "url-password"),
        ('API_KEY = "Q7f2Lw9xR4t8Zp1m"', "assignment"),
        ('token: "Zq81Lx0pWv3Kt7Rb"', "assignment"),
    ],
)
def test_high_signal_secrets_are_found(text: str, kind: str) -> None:
    counts: Counter[str] = Counter()
    out = redact.Redactor().text(text, counts)
    assert counts == Counter({kind: 1}), out
    assert f"[REDACTED:{kind}]" in out


def test_only_the_secret_part_of_a_match_is_replaced() -> None:
    out = redact.Redactor().text("Authorization: Bearer abcdefghijklmnopqrstuvwx12345")
    assert out == "Authorization: Bearer [REDACTED:bearer]"
    out = redact.Redactor().text("postgres://app:s3cr3t-pw@db.internal/app")
    assert out == "postgres://app:[REDACTED:url-password]@db.internal/app"


@pytest.mark.parametrize(
    "text",
    [
        "password = read_password()",
        "def get_api_key(self): return self.api_key",
        "risk-assessment-framework-v2 and sk-learn",
        "pip install sk-learn-compatible-estimators-v2 or sk-video-processing-toolkit",
        "a basic understanding of basic configuration and basic abcdefgh",
        "the token budget is 20000",
        "token = next_token(stream)",
        "https://example.org/docs/page",
        "commit 3f2a1b9c and a uuid 123e4567-e89b-12d3-a456-426614174000",
    ],
)
def test_ordinary_code_and_text_are_left_alone(text: str) -> None:
    assert redact.Redactor().text(text) == text


def test_known_values_are_replaced_wherever_they_appear() -> None:
    redactor = redact.Redactor(["hunter2-but-longer", "short"])
    counts: Counter[str] = Counter()
    out = redactor.text("a hunter2-but-longer b hunter2-but-longer short", counts)
    assert out == "a [REDACTED:known] b [REDACTED:known] short"  # short values are not used
    assert counts["known"] == 2


def test_values_are_redacted_recursively_without_touching_other_types() -> None:
    value = {"messages": [{"content": f"use {API_KEY}"}], "n": 1, "ok": True, "x": None}
    counts: Counter[str] = Counter()
    out = redact.Redactor().value(value, counts)
    assert out["messages"][0]["content"] == "use [REDACTED:api-key]"
    assert out["n"] == 1 and out["ok"] is True and out["x"] is None
    assert value["messages"][0]["content"] == f"use {API_KEY}"  # a copy, not in place


def test_command_lines_mask_secret_flags_and_values() -> None:
    argv = ["llama-server", "--api-key", "abc", "--token=xyz", "-m", "model.gguf", f"--x={JWT}"]
    shown = redact.redact_argv(argv, redact.Redactor())
    assert shown == [
        "llama-server",
        "--api-key",
        "[REDACTED:flag]",
        "--token=[REDACTED:flag]",
        "-m",
        "model.gguf",
        "--x=[REDACTED:jwt]",
    ]


def test_environment_credentials_are_known_secrets() -> None:
    env = {"MY_SERVICE_TOKEN": "tok-1234567890", "PATH": "/usr/bin", "API_KEY": "short"}
    assert redact.secret_environment(env) == ["tok-1234567890"]


def test_an_unknown_mode_is_refused() -> None:
    with pytest.raises(ValueError, match="privacy.redact"):
        redact.configure(mode="sometimes")


def _client(url: str, seen: list[dict[str, object]]) -> ModelClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    endpoint = Endpoint(name="e", base_url=url, model="m", local=net.is_local_url(url))
    return ModelClient(endpoint, egress=net.Egress(), transport=httpx.MockTransport(handler))


def _request() -> Request:
    text = f"The deploy key is {API_KEY}; keep it."
    return Request(model="m", messages=[Message(role="user", content=[TextPart(text=text)])])


def test_requests_to_remote_endpoints_are_redacted_and_reported() -> None:
    seen: list[dict[str, object]] = []
    completion = _client("https://models.example.org/v1", seen).complete(_request())
    body = json.dumps(seen[0])
    assert API_KEY not in body and "[REDACTED:api-key]" in body
    assert any(loss.field == "secrets" for loss in completion.report.losses)


def test_local_requests_are_sent_as_they_are_unless_the_mode_says_all() -> None:
    seen: list[dict[str, object]] = []
    _client("http://127.0.0.1:8080/v1", seen).complete(_request())
    assert API_KEY in json.dumps(seen[0])
    redact.configure(mode="all")
    _client("http://127.0.0.1:8080/v1", seen).complete(_request())
    assert API_KEY not in json.dumps(seen[1])
    redact.configure(mode="off")
    _client("https://models.example.org/v1", seen).complete(_request())
    assert API_KEY in json.dumps(seen[2])
