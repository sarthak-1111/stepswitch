"""Secret scanner and redactor (SPEC §15): nothing that looks like a credential leaves the
machine in a request to a non-local endpoint, and audit logs never store one.

* **What counts as a secret.** First, the exact values Stepswitch knows are secret: the API keys
  of configured endpoints and the values of environment variables whose names say they hold
  credentials. Then text that matches high-signal patterns:
  - private key blocks;
  - token formats with a fixed prefix (several services' API and payment keys, repository,
    chat-app and package-registry tokens, cloud access key IDs, model-hub tokens, Stepswitch's
    own `ssw-` tokens). A prefix that ordinary words share (`sk-learn`) counts only when the
    rest of the token looks random;
  - JSON Web Tokens;
  - bearer and basic credentials in text;
  - passwords inside URLs;
  - assignments such as `api_key = "..."`, when the value looks random (letters and digits,
    enough entropy), so code like `password = read_password()` is left alone.
* **Replacement.** Each finding becomes `[REDACTED:<kind>]`; only the secret part of a match is
  replaced (`Authorization: Bearer [REDACTED:bearer]`).
* **Where it runs.** `ModelClient.prepare` redacts the encoded request body of every call to a
  non-local endpoint (`mode = "remote"`, the default) and adds a note to the conversion report;
  `privacy.redact = "all"` redacts local calls too, and `"off"` disables it. The process audit
  log redacts command lines (`redact_argv`).
"""

from __future__ import annotations

import base64
import binascii
import math
import os
import re
import threading
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

Mode = Literal["remote", "all", "off"]
MODES = ("remote", "all", "off")
REPLACEMENT = "[REDACTED:{kind}]"
# Known secret values shorter than this are not searched for (they would match ordinary text).
MIN_KNOWN = 8
# Names of environment variables whose values are secrets (as for `context.proc.minimal_env`).
SECRET_NAME_RE = re.compile(
    r"(?i)(key|token|secret|passw|credential|auth|cookie|session|private|signature)"
)
# Command-line flags whose value is a secret.
SECRET_FLAG_RE = re.compile(
    r"(?i)^--?(api[-_]?key|token|password|passwd|secret|auth|auth[-_]?token|key)$"
)

ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|apikey|secret(?:[_-]?key)?|client[_-]?secret|access[_-]?token|"
    r"auth[_-]?token|token|private[_-]?key|password|passwd)\b[\"']?\s*[:=]\s*[\"']?"
    r"([A-Za-z0-9_\-+/=.]{12,})"
)


def entropy(value: str) -> float:
    """Shannon entropy in bits per character."""
    if not value:
        return 0.0
    counts = Counter(value)
    return -sum(n / len(value) * math.log2(n / len(value)) for n in counts.values())


def looks_random(value: str) -> bool:
    """A value that is a credential rather than a word or an identifier."""
    has_digit = any(c.isdigit() for c in value)
    has_alpha = any(c.isalpha() for c in value)
    if has_digit and has_alpha and entropy(value) >= 3.0:
        return True
    return len(value) >= 32 and entropy(value) >= 3.5


def _random_key(secret: str) -> bool:
    """An `sk-` key, not a word that starts the same way: its longest run between separators
    is long and random."""
    longest = max(re.split(r"[-_]", secret[3:]), key=len)
    return len(longest) >= 16 and looks_random(longest)


def _basic_credentials(secret: str) -> bool:
    """`Basic <base64>` is a credential when it decodes to `user:password`."""
    try:
        decoded = base64.b64decode(secret, validate=True).decode("utf-8")
    except (binascii.Error, ValueError):  # a decoding error is a ValueError too
        return False
    user, sep, password = decoded.partition(":")
    return bool(sep and user and password) and decoded.isprintable()


# (kind, pattern, group, check): the group of the match that is the secret (0: the whole match),
# and a test the secret must also pass where ordinary text can match the pattern (None: none).
# The token formats follow the rules of an open-source secret scanner (docs/RESEARCH.md).
PATTERNS: tuple[tuple[str, re.Pattern[str], int, Callable[[str], bool] | None], ...] = (
    (
        "private-key",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----.*?"
            r"-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----",
            re.S,
        ),
        0,
        None,
    ),
    ("api-key", re.compile(r"\bsk-(?:[A-Za-z0-9]+-){0,3}[A-Za-z0-9_-]{20,}"), 0, _random_key),
    ("payment-key", re.compile(r"\b(?:sk|rk)_(?:test|live|prod)_[A-Za-z0-9]{10,99}\b"), 0, None),
    (
        "repo-token",
        re.compile(
            r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,}|glpat-[A-Za-z0-9_-]{20,})"
        ),
        0,
        None,
    ),
    ("chat-token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"), 0, None),
    (
        "package-token",
        re.compile(r"\b(?:pypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{50,}|npm_[A-Za-z0-9]{36}\b)"),
        0,
        None,
    ),
    (
        "cloud-key-id",
        re.compile(r"\b(?:A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA)[A-Z2-7]{16}\b"),
        0,
        None,
    ),
    ("cloud-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}"), 0, None),
    ("hub-token", re.compile(r"\bhf_[A-Za-z0-9]{30,}"), 0, None),
    ("stepswitch-token", re.compile(r"\bssw-[A-Za-z0-9_-]{40,}"), 0, None),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
        0,
        None,
    ),
    ("bearer", re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._~+/-]{20,}=*)"), 1, None),
    ("basic", re.compile(r"(?i)\bbasic\s+([A-Za-z0-9+/]{8,}={0,2})"), 1, _basic_credentials),
    (
        "url-password",
        re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s/:@]+:([^\s/@]{3,})@"),
        1,
        None,
    ),
)


@dataclass(frozen=True)
class Finding:
    kind: str
    start: int
    end: int


def scan(text: str, known: Sequence[str] = ()) -> list[Finding]:
    """The secrets in `text`, as non-overlapping spans (earliest and longest first)."""
    found: list[Finding] = []
    for value in known:
        start = text.find(value)
        while start >= 0:
            found.append(Finding("known", start, start + len(value)))
            start = text.find(value, start + len(value))
    for kind, pattern, group, check in PATTERNS:
        for match in pattern.finditer(text):
            if check is None or check(match.group(group)):
                found.append(Finding(kind, match.start(group), match.end(group)))
    for match in ASSIGNMENT.finditer(text):
        if looks_random(match.group(1)):
            found.append(Finding("assignment", match.start(1), match.end(1)))
    found.sort(key=lambda f: (f.start, -(f.end - f.start)))
    spans: list[Finding] = []
    for finding in found:
        if spans and finding.start < spans[-1].end:
            continue
        spans.append(finding)
    return spans


class Redactor:
    """Replaces secrets in text and in JSON-like values; counts what it replaced by kind."""

    def __init__(self, known: Iterable[str] = ()) -> None:
        values = {v for v in known if v and len(v) >= MIN_KNOWN}
        # Longest first, so a secret that contains another is replaced whole.
        self.known = tuple(sorted(values, key=len, reverse=True))

    def text(self, text: str, counts: Counter[str] | None = None) -> str:
        spans = scan(text, self.known)
        if not spans:
            return text
        parts: list[str] = []
        last = 0
        for span in spans:
            parts.append(text[last : span.start])
            parts.append(REPLACEMENT.format(kind=span.kind))
            last = span.end
            if counts is not None:
                counts[span.kind] += 1
        parts.append(text[last:])
        return "".join(parts)

    def value(self, value: Any, counts: Counter[str] | None = None) -> Any:
        """A copy of a JSON-like value (dicts, lists, strings) with every string redacted."""
        if isinstance(value, str):
            return self.text(value, counts)
        if isinstance(value, dict):
            return {k: self.value(v, counts) for k, v in value.items()}
        if isinstance(value, list):
            return [self.value(v, counts) for v in value]
        if isinstance(value, tuple):
            return tuple(self.value(v, counts) for v in value)
        return value


def redact_argv(argv: Sequence[str], redactor: Redactor | None = None) -> list[str]:
    """A command line with secret flag values masked and secrets in arguments redacted."""
    redactor = redactor or current()
    shown: list[str] = []
    hide_next = False
    for arg in argv:
        if hide_next:
            shown.append(REPLACEMENT.format(kind="flag"))
            hide_next = False
            continue
        flag, sep, _value = arg.partition("=")
        if SECRET_FLAG_RE.match(flag):
            if sep:
                shown.append(f"{flag}={REPLACEMENT.format(kind='flag')}")
            else:
                shown.append(arg)
                hide_next = True
            continue
        shown.append(redactor.text(arg))
    return shown


def secret_environment(env: dict[str, str] | None = None) -> list[str]:
    """The values of environment variables whose names say they are credentials."""
    source = os.environ if env is None else env
    return [v for k, v in source.items() if SECRET_NAME_RE.search(k) and len(v) >= MIN_KNOWN]


_lock = threading.Lock()
_mode: Mode = "remote"
_redactor = Redactor(secret_environment())


def configure(*, mode: str | None = None, known: Iterable[str] | None = None) -> None:
    """Set the process-wide mode and the known secret values (called from the loaded config)."""
    global _mode, _redactor
    with _lock:
        if mode is not None:
            if mode not in MODES:
                raise ValueError(f"privacy.redact is one of {', '.join(MODES)}, not {mode!r}")
            _mode = mode  # type: ignore[assignment]
        if known is not None:
            _redactor = Redactor([*known, *secret_environment()])


def current() -> Redactor:
    return _redactor


def mode() -> Mode:
    return _mode


def applies(local: bool) -> bool:
    """Whether a request to an endpoint (local or not) is redacted."""
    return _mode == "all" or (_mode == "remote" and not local)
