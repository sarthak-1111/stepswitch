"""Repository hygiene: working rules 4 and 5.

* No company names in committed text. The denylist is stored as SHA-256 hashes of lowercase
  names (single words and word pairs), so this file names no company itself. URLs and whole
  ``org/name`` identifiers (repository paths, model-hub IDs) are skipped: they are citations and
  IDs, not prose (ADR 0007). Interoperability identifiers that merely contain a company name
  are listed in ALLOWED_IDENTIFIERS.
* Nothing personal or machine-specific is committed: endpoints, keys and paths from
  ``.stepswitch/local.toml``, the current home directory, a distinctive user name, the host name.
* The local state directory stays gitignored and kickoff material stays untracked.
"""

from __future__ import annotations

import getpass
import hashlib
import ipaddress
import re
import shutil
import socket
import subprocess
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEXT_SUFFIXES = {
    ".md",
    ".py",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".jsonl",
    ".txt",
    ".ts",
    ".tsx",
    ".js",
    ".mjs",
    ".cjs",
    ".css",
    ".html",
    ".ps1",
    ".sh",
    ".cfg",
    ".ini",
    ".scm",
    ".jinja",
    ".j2",
}
SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    ".stepswitch",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "dist",
    "build",
}

# sha256(lowercase name) of company names that are not also product or model names.
COMPANY_NAME_HASHES = frozenset(
    {
        "7d3194f79e645c42e4396dda38be04766810ec6a00d00aced3ffc2a0a1f1a9ef",
        "c70eca6b0f88f44d81a41311647e50fda1ac454ec04ffd442b0eb4743a993131",
        "bbdefa2950f49882f295b1285d4fa9dec45fc4144bfb07ee6acc68762d12c2e3",
        "90642c3dd333bc0e602c9fa887ab0f0482886d0ed52ce8762a27a5f901528fcc",
        "9fbf261b62c1d7c00db73afb81dd97fdf20b3442e36e338cb9359b856a03bdc8",
        "8d4321d936320802386311d254c4af52951abb880b47fb077edf3d89c150b289",
        "047364b9be67f665eca384b8061d688b8642a8b47b3de35c22a012ed6fe69242",
        "9c8ae69b84f21a2e46df9edf0063a697afec050188ff2884ddc8ab32b5e58c43",
        "a89151ba4b5ac0f22e96b71b963db927791d3808f5175f06ae4a60de5891bf0f",
        "5388bc10736a0eff09be2ab9c8369a9c1cd14ef7916dafe4e177da6903aefdec",
        "6749675d80a1faa70859312365b6eab644244d8e5d01cc94f44644c13d090ee7",
        "e4be74b69f36e8090c3e287a9aff3363a0c43f2a3c18f21835834d48351c6118",
        "b5d641b432cca81e57b5bb5b73c53e64c50d44dce93595a85d1d8885f48548e8",
        "c608c4787821de1b383d87ca8b2711d3d171b5a6c017c45a8523e9ae904d495e",
        "b81c8201acfc7639be3bd3d7f875ba5ca76e422fa2163cec9dace50662b637a7",
        "2c348893efb2316e5f1753dd68e3da88bce6847ff8c7b6ea083fe1fd46d7061a",
        "72e8088a05b38ae3cffbc126c1c2095046c7700edab31ce35867ce8ee3ac8932",
        "916a98f0fa2a39af1a736b1a3744cb655dc8638a6953e2fb13e39934ab60d55a",
        "3a7bd3e2360a3d29eea436fcfb7e44c735d117c42d1c1835420b6b9942dd4f1b",
        "cbc62794911ff31b2864ecd3dbbbee7ebcb7ea41c5a42e2cba377f3cfdb42811",
        "96eebba49dbbf422d245f02290f9d4ed0eb02da9daa6bbceefb162800ff42481",
        "240389f4b730bad41f218a56c250ee7d82f42297ce7b9d0b6eb4f1cb3282c480",
        "f31639a23c7440153fe07af3ef6833aa900064c4df50fb23a1739797dde073ea",
        "968e2d5b08687bf42997461cbdef6c844eabbf04f440cee888c95b864c2a4bcc",
        "6c2949a5b9698d223e285bc41038564e8626724885349976073ced55302fa3df",
        "13ed070478ef62c3a7baa36c8d042a9d1cdc0fcbb2af93a795f2ad20ad6e9cb5",
        "6fcba5d96a4fadfdd63064cf21da843c31de9a702bbee7fe306d4754fc9dd262",
        "0b79cf2de0ee6b8ee227d9ebfe69b91aa92b1d2509641c4b743518447dbc4c68",
        "77b30b8a88051252a94c391bf8a60d228a53fe85371a95dd7fa13350fbe59ca1",
        "dfc0fdff7af68ed25e26ce99ffa7c2599d40fc25c806a6d232c8ff68c7202c86",
        "850ee2c84465fca966a17198370a91a3b6340102d87404ae507ae50ca494edaf",
        "b5738c169b693bee89e1b74ebd48e0dfa53a34e8571790b7727721a0bfadc470",
        "3ccbd9105a45d8fcd4a0101c6532c599f6f59cfa4d4ce378792f547a869a4bea",
        "2dc010636b9dce91b7328ecb3d287ef6245c96281f1ebf37071210a6ef829000",
        "d203a277f19a8f7d78fe8e9a621534b2cbd8ef1f0d809908ffc481e778858872",
        "db36e7bd45191ce44c266739076394b71c59b7e64981a05e88353a3bcd9adb48",
        "481b505e421ef8fd9b06bdbba760722a78173c92b652627ab8e3b912300edd03",
        "6e8bd5b0ddb673c6dafd19cfbae61366f503a67b987cd69785c428deb1d83ebc",
        "ce08cc50a48c275b920112409a64170f6c905b372a26776c83e476fd8c26cabb",
        "938973a5d41077ec2deb8f4320e4f0206000a785d30d63cfda66723c67ed245a",
        "e3ce9cc5efa9354c42b30704bed3e17cfad9ba9968592ffaa06b41dc7256cf4c",
        "ae31f30d39374a8b3194d89a1ad6aa56848547dc892bdd350605890d1d586fc1",
        "9d8ac7e23cd1e212c2af0277e0ba0f4dec4d7c801f78ca59b7a3d89bab16e262",
        "04c869555f58b7ea47677d7e08403dc7611f0526abb38e3479614780633885cf",
        "c04bd5ebd10ab69665f88a6785f81c08783dafdae6af6a09544ad1fa00699480",
        "88ab1e5d793701f9a4137d877a9231e6ee743ad761d9211b4761aa330e8420a0",
        "3feb1417824fbc3483e8f60c686a4baa24107d3731a264cb76acf3b7459f9a31",
        "8d137c58231dbf560413893c12aa443cab5c4e901bf401c11de4673417589881",
        "8dfe5e6cf73b1d6b761fd2a2fbd30b37759f0abb0a386815b1398e89ae804bca",
        "627c6eb0c774ea984a8a2b7ccc7fa87357609a7060947d17967064099227b720",
        "f3d9076356cd183c154b205a16c80999c576ee1efb469d0504680e586ec63067",
        "f93038d828ff49aaf58fc4455b58980d53d48c3e7642836fa8d19cfb7b6e2eaa",
        "3eee4f30151db45a6507b8fde7c56d0f345e141fa35d5aa0998dfebc6f2d66fb",
    }
)

# Interoperability identifiers that contain a company name: tool, package, protocol header,
# environment variable, engine parser, configuration value and license names that other
# software requires verbatim (ADR 0007). Bare company names are never listed here; prose
# describes them instead. Plain lowercase literals (a trailing separator marks a prefix
# family), so this file passes its own scan.
ALLOWED_IDENTIFIERS = (
    # tools and packages
    "nvidia-smi",
    "nvidia_visible_devices",
    "nvidia-container-toolkit",
    "huggingface_hub",
    "huggingface-hub",
    "huggingface-cli",
    "openai-harmony",
    # protocol headers of the chat-completions and messages formats
    "openai-version",
    "openai-organization",
    "openai-project",
    "openai-processing-ms",
    "anthropic-version",
    "anthropic-beta",
    "anthropic-ratelimit-",
    "anthropic-priority-",
    # environment variables read by a supported harness
    "anthropic_base_url",
    "anthropic_auth_token",
    "anthropic_api_key",
    "anthropic_custom_headers",
    "anthropic_small_fast_model",
    "anthropic_model",
    "anthropic_default_",
    # engine parser names and flags
    "openai_gptoss",
    "cohere_command3",
    "cohere_command4",
    "cohere_melody",
    "tool-call-parser openai",
    # API-format values in harness configuration files
    "openai-completions",
    "openai-responses",
    "openai-codex-responses",
    "anthropic-messages",
    "google-generative-ai",
    "google-vertex",
    # model license identifiers
    "nvidia-nemotron-open-model-license",
)

# Keys in .stepswitch/local.toml whose values are machine-specific by definition.
SENSITIVE_KEYS = {"url", "api_key", "key", "token", "secret", "password", "model_dir", "host"}

URL_RE = re.compile(r"https?://\S+")
ORG_ID_RE = re.compile(r"\b[\w.-]+/[\w.-]+")
WORD_RE = re.compile(r"[a-z0-9]+")
HEX_SECRET_RE = re.compile(r"^[0-9a-fA-F]{32,}$")


def repo_files(root: Path = ROOT) -> list[Path]:
    """Tracked files plus untracked files that are not ignored (what the next commit could hold)."""
    git = shutil.which("git")
    if git and (root / ".git").exists():
        out = subprocess.run(
            [git, "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8")
        paths = [root / rel for rel in out.split("\0") if rel]
    else:
        paths = [p for p in root.rglob("*") if not set(p.relative_to(root).parts) & SKIP_DIRS]
    return sorted(p for p in paths if p.is_file())


def repo_text_files(root: Path = ROOT) -> list[Path]:
    return [p for p in repo_files(root) if p.suffix.lower() in TEXT_SUFFIXES]


def company_name_hits(text: str, hashes: frozenset[str] = COMPANY_NAME_HASHES) -> list[str]:
    text = text.lower()
    for identifier in ALLOWED_IDENTIFIERS:
        text = text.replace(identifier, " ")
    text = ORG_ID_RE.sub(" ", URL_RE.sub(" ", text))
    words = WORD_RE.findall(text)
    hits: list[str] = []
    for index, word in enumerate(words):
        candidates = [word]
        if index + 1 < len(words):
            candidates.append(f"{word} {words[index + 1]}")
        hits.extend(c for c in candidates if hashlib.sha256(c.encode()).hexdigest() in hashes)
    return hits


def _is_loopback_url(value: str) -> bool:
    host = urlparse(value).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _sensitive_strings(node: Any, key: str = "") -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for child_key, child in node.items():
            found |= _sensitive_strings(child, str(child_key))
    elif isinstance(node, list):
        for child in node:
            found |= _sensitive_strings(child, key)
    elif isinstance(node, str) and len(node) >= 6:
        if "://" in node:
            if not _is_loopback_url(node):
                found.add(node)
        elif key.lower() in SENSITIVE_KEYS or HEX_SECRET_RE.match(node):
            found.add(node)
    return found


def machine_specific_values(root: Path = ROOT) -> set[str]:
    home = Path.home()
    values = {str(home), home.as_posix()}
    user = getpass.getuser()
    if not user.isalpha():  # plain alphabetic names (CI defaults) are too common to scan for
        values.add(user)
    host = socket.gethostname()
    if host and host.lower() != "localhost":
        values.add(host)
    local = root / ".stepswitch" / "local.toml"
    if local.is_file():
        values |= _sensitive_strings(tomllib.loads(local.read_text(encoding="utf-8")))
    return {value for value in values if len(value) >= 4}


def _redact(value: str) -> str:
    return value[:4] + "…" if len(value) > 4 else value


def test_no_company_names_in_repository_text() -> None:
    problems: dict[str, list[str]] = {}
    for path in repo_text_files():
        hits = company_name_hits(path.read_text(encoding="utf-8", errors="replace"))
        if hits:
            problems[path.relative_to(ROOT).as_posix()] = sorted(set(hits))
    assert not problems, f"company names found (working rule 4): {problems}"


def test_no_machine_specific_values_in_repository_text() -> None:
    values = {value.lower() for value in machine_specific_values()}
    problems: dict[str, list[str]] = {}
    for path in repo_text_files():
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        found = sorted(_redact(value) for value in values if value in text)
        if found:
            problems[path.relative_to(ROOT).as_posix()] = found
    assert not problems, f"machine-specific values found (working rule 5): {problems}"


def test_local_state_and_kickoff_material_stay_untracked() -> None:
    ignored = {
        line.strip() for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    }
    assert ".stepswitch/" in ignored
    for path in repo_files():
        rel = path.relative_to(ROOT).as_posix()
        assert not rel.startswith(".stepswitch/"), rel
        assert path.name != "KICKOFF.md" and path.suffix.lower() != ".zip", rel


# --------------------------------------------------------------------------------------------
# Self-tests with a fictional company, so the checks above are known to bite
# --------------------------------------------------------------------------------------------

FICTIONAL = frozenset(
    {hashlib.sha256(b"acmecorp").hexdigest(), hashlib.sha256(b"acme corp").hexdigest()}
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Released by AcmeCorp last year.", ["acmecorp"]),
        ("Released by Acme Corp last year.", ["acme corp"]),
        ("See https://acmecorp.example/models for details.", []),
        ("Pull `acmecorp/tiny-model-1b` from the model hub.", []),
        ("Pull `acmecorp/AcmeCorp-Tiny-1B` from the model hub.", []),
        ("The AcmeCorp-Tiny-1B model.", ["acmecorp"]),
        ("Check GPUs with nvidia-smi first.", []),
    ],
)
def test_company_name_check_on_samples(text: str, expected: list[str]) -> None:
    assert company_name_hits(text, FICTIONAL) == expected


def test_sensitive_values_from_local_config() -> None:
    config = {
        "endpoints": [
            {"url": "https://tunnel.example.net", "api_key": "abc123secret"},
            {"url": "http://127.0.0.1:11434"},
        ],
        "host": {"model_dir": "E:/models", "os": "linux"},
        "token": "0123456789abcdef0123456789abcdef",
    }
    assert _sensitive_strings(config) == {
        "https://tunnel.example.net",
        "abc123secret",
        "0123456789abcdef0123456789abcdef",
    } | {"E:/models"}
