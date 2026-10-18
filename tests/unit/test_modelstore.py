"""`ssw models pull` planning and execution (stepswitch.modelstore), against a mock hub."""

from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path
from typing import Any

import httpx
import pytest

from stepswitch import hub, modelstore, net, registry, tomlw
from stepswitch.registry import ModelEntry

HUB = "https://hub.example.test"
REV = "1" * 40
GB = 1_000_000_000


@pytest.fixture(autouse=True)
def _hub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(hub.HUB_URL_ENV, HUB)
    monkeypatch.delenv(hub.TOKEN_ENV, raising=False)


def blob(name: str, size: int) -> bytes:
    seed = hashlib.sha256(name.encode()).digest()
    return (seed * (size // len(seed) + 1))[:size]


class Store:
    """A registry directory, a models directory and a mock hub serving the entries' files."""

    def __init__(self, root: Path) -> None:
        self.registry_dir = root / "registry"
        self.models_dir = root / "models"
        self.registry_dir.mkdir()
        self.files: dict[str, bytes] = {}
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._serve)
        self.egress = net.Egress()

    def add(
        self,
        entry_id: str,
        size: int,
        *,
        optional: bool = False,
        commercial_use: str = "permitted",
        tiers: tuple[str, ...] = ("cpu",),
        ollama: str | None = "tiny:latest",
        ram_gb: float = 0.5,
        **overrides: Any,
    ) -> ModelEntry:
        content = blob(entry_id, size)
        model: dict[str, Any] = {
            "id": entry_id,
            "name": entry_id.upper(),
            "hub_id": f"example-org/{entry_id}-GGUF",
            "revision": REV,
            "file": f"{entry_id}-Q8_0.gguf",
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": size,
            "quant": "Q8_0",
            "file_size_gb": round(size / GB, 2),
            "ram_gb": ram_gb,
            "context": 4096,
            "license": "apache-2.0" if commercial_use == "permitted" else "community-license",
            "commercial_use": commercial_use,
            "tiers": list(tiers),
            "optional": optional,
            **overrides,
        }
        engines: dict[str, Any] = {"llamacpp": {"args": ["--ctx-size", "4096"]}}
        if ollama:
            engines["ollama"] = {"name": ollama}
        path = self.registry_dir / f"{entry_id}.toml"
        path.write_text(tomlw.dumps({"schema": 1, "model": model, "engines": engines}), "utf-8")
        entry = registry.read_entry(path)
        if entry.file and not Path(entry.file).is_absolute():
            self.files[f"/{entry.hub_id}/resolve/{REV}/{entry.file}"] = content
        return entry

    def content(self, entry: ModelEntry) -> bytes:
        return self.files[f"/{entry.hub_id}/resolve/{REV}/{entry.file}"]

    def place(self, entry: ModelEntry, data: bytes | None = None, *, stamp: bool = False) -> Path:
        path = entry.local_path(self.models_dir)
        assert path is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.content(entry) if data is None else data)
        if stamp:
            registry.write_stamp(entry, self.models_dir, registry.sha256_file(path))
        return path

    def _serve(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        content = self.files.get(request.url.path)
        if content is None:
            return httpx.Response(404)
        header = request.headers.get("range")
        if header:
            start = int(header.removeprefix("bytes=").rstrip("-"))
            headers = {"content-range": f"bytes {start}-{len(content) - 1}/{len(content)}"}
            return httpx.Response(206, content=content[start:], headers=headers)
        return httpx.Response(200, content=content)

    def plan(self, entries: list[ModelEntry], **kwargs: Any) -> modelstore.PullPlan:
        return modelstore.plan(entries, models_dir=self.models_dir, **kwargs)

    def execute(self, plan: modelstore.PullPlan, **kwargs: Any) -> modelstore.PullResult:
        kwargs.setdefault("egress", self.egress)
        kwargs.setdefault("echo", lambda message: None)
        return modelstore.execute(plan, transport=self.transport, **kwargs)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path)


def statuses(plan: modelstore.PullPlan) -> dict[str, str]:
    return {item.id: item.status for item in plan.items}


# --------------------------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------------------------


def test_select_by_tier_and_names(store: Store) -> None:
    store.add("alpha", 1000)
    store.add("beta", 1000, optional=True)
    store.add("gamma", 1000, tiers=("t1",))
    dirs = [store.registry_dir]
    assert [e.id for e in modelstore.select("cpu", [], dirs=dirs)] == ["alpha", "beta"]
    assert [e.id for e in modelstore.select("t0", [], dirs=dirs)] == ["alpha", "beta"]
    assert [e.id for e in modelstore.select(None, ["GAMMA"], dirs=dirs)] == ["gamma"]
    combined = modelstore.select("cpu", ["gamma", "alpha"], dirs=dirs)
    assert [e.id for e in combined] == ["alpha", "beta", "gamma"]
    hub_named = modelstore.select(None, ["example-org/alpha-GGUF"], dirs=dirs)
    assert [e.id for e in hub_named] == ["alpha"]
    with pytest.raises(ValueError, match="unknown model"):
        modelstore.select(None, ["nope"], dirs=dirs)
    with pytest.raises(ValueError, match="tier"):
        modelstore.select(None, [], dirs=dirs)
    with pytest.raises(ValueError, match="unknown tier"):
        modelstore.select("t9", [], dirs=dirs)


# --------------------------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------------------------


def test_plan_everything_to_download(store: Store) -> None:
    entries = [store.add("alpha", 4000, ram_gb=3.1), store.add("beta", 2000, ram_gb=0.87)]
    plan = store.plan(entries)
    assert statuses(plan) == {"alpha": "download", "beta": "download"}
    assert plan.total_disk_bytes == 6000 and plan.download_bytes == 6000
    assert plan.ram_gb == pytest.approx(3.97)
    text = modelstore.describe(plan)
    assert "alpha" in text and "beta" in text and "download" in text
    assert "cap 8 GB" in text and "RAM: about 3.97 GB" in text
    assert str(store.models_dir) in text


def test_cap_leaves_out_optional_models_first(store: Store) -> None:
    entries = [
        store.add("alpha", 4000),
        store.add("beta", 3000, optional=True),
        store.add("gamma", 2000),
    ]
    plan = store.plan(entries, max_gb=6000 / GB)
    assert statuses(plan) == {"alpha": "download", "beta": "over-cap", "gamma": "download"}
    beta = next(item for item in plan.items if item.id == "beta")
    assert "optional" in beta.reason and "--max-gb" in beta.reason
    assert beta.download_bytes == 0
    assert plan.total_disk_bytes == 6000
    assert "skipped: optional model, left out first" in modelstore.describe(plan)


def test_cap_then_leaves_out_the_largest(store: Store) -> None:
    entries = [
        store.add("alpha", 4000),
        store.add("beta", 3000, optional=True),
        store.add("gamma", 2000),
    ]
    plan = store.plan(entries, max_gb=5000 / GB)
    # The optional model goes first, then the largest. The optional one would fit again, but
    # it never replaces a required model.
    assert statuses(plan) == {"alpha": "over-cap", "beta": "over-cap", "gamma": "download"}
    alpha = next(item for item in plan.items if item.id == "alpha")
    assert alpha.reason.startswith("left out:") and "0.00 GB" in alpha.reason
    assert plan.total_disk_bytes == 2000 and plan.download_bytes == 2000


def test_cap_counts_installed_models_but_never_drops_them(store: Store) -> None:
    alpha = store.add("alpha", 4000)
    beta = store.add("beta", 2000)
    store.place(alpha, stamp=True)
    plan = store.plan([alpha, beta], max_gb=5000 / GB)
    assert statuses(plan) == {"alpha": "installed", "beta": "over-cap"}
    assert plan.total_disk_bytes == 4000 and plan.download_bytes == 0


def test_no_optional_excludes_with_a_reason(store: Store) -> None:
    entries = [store.add("alpha", 1000), store.add("beta", 1000, optional=True)]
    plan = store.plan(entries, include_optional=False)
    assert statuses(plan) == {"alpha": "download"}
    assert plan.excluded == [("beta", "optional model, left out on request (--no-optional)")]
    assert "beta" in modelstore.describe(plan)


def test_partial_download_is_planned_as_a_resume(store: Store) -> None:
    alpha = store.add("alpha", 4000)
    path = alpha.local_path(store.models_dir)
    assert path is not None
    path.parent.mkdir(parents=True)
    hub.part_path(path).write_bytes(store.content(alpha)[:1500])
    plan = store.plan([alpha])
    assert plan.items[0].download_bytes == 2500
    assert "resuming" in modelstore.describe(plan)


def test_unsafe_and_fileless_entries_are_refused(store: Store) -> None:
    escape = store.add("escape", 100, file="../../outside.gguf")
    bad_hub = store.add("badhub", 100, hub_id="example-org/../x")
    no_file = store.add("nofile", 100, file=None)
    no_sha = store.add("nosha", 100, sha256=None)
    no_size = store.add("nosize", 100, size_bytes=None)
    plan = store.plan([escape, bad_hub, no_file, no_sha, no_size])
    assert plan.items == []
    reasons = dict(plan.excluded)
    assert "refused" in reasons["escape"] and "refused" in reasons["badhub"]
    assert "no model file" in reasons["nofile"]
    assert "size and sha256" in reasons["nosha"] and "size and sha256" in reasons["nosize"]
    result = store.execute(plan, yes=True)
    assert store.requests == [] and result.downloaded == []
    assert sorted(result.skipped) == sorted(plan.excluded)


def test_invalid_cap(store: Store) -> None:
    with pytest.raises(ValueError, match="cap"):
        store.plan([store.add("alpha", 10)], max_gb=0)


# --------------------------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------------------------


def test_download_with_yes_installs_and_stamps(store: Store) -> None:
    entries = [store.add("alpha", 4000), store.add("beta", 2000)]
    lines: list[str] = []
    result = store.execute(store.plan(entries), yes=True, echo=lines.append)
    assert result.downloaded == ["alpha", "beta"] and result.failed == []
    for entry in entries:
        assert entry.installed(store.models_dir)
        path = entry.local_path(store.models_dir)
        assert path is not None and path.read_bytes() == store.content(entry)
    rows = list(store.egress.memory)
    assert {row.purpose for row in rows} == {hub.DOWNLOAD_PURPOSE}
    assert all(row.host == "hub.example.test" for row in rows)
    assert any("100%" in line for line in lines)  # default text progress
    # A second run finds everything installed and makes no request.
    store.requests.clear()
    again = store.execute(store.plan(entries), yes=True)
    assert again.installed == ["alpha", "beta"] and store.requests == []


def test_confirmation_is_asked_once(store: Store) -> None:
    entries = [store.add("alpha", 4000), store.add("beta", 2000)]
    questions: list[str] = []

    def confirm(question: str) -> bool:
        questions.append(question)
        return True

    result = store.execute(store.plan(entries), confirm=confirm)
    assert result.downloaded == ["alpha", "beta"]
    assert len(questions) == 1
    assert "2 model file(s)" in questions[0] and str(store.models_dir) in questions[0]


def test_declined_confirmation_downloads_nothing(store: Store) -> None:
    entries = [store.add("alpha", 4000)]
    lines: list[str] = []
    result = store.execute(store.plan(entries), confirm=lambda q: False, echo=lines.append)
    assert result.downloaded == [] and result.skipped == [("alpha", "declined")]
    assert store.requests == []
    assert "Nothing downloaded." in lines


def test_without_confirm_or_yes_nothing_is_downloaded(store: Store) -> None:
    lines: list[str] = []
    result = store.execute(store.plan([store.add("alpha", 4000)]), echo=lines.append)
    assert result.downloaded == [] and result.skipped == [("alpha", "not confirmed")]
    assert store.requests == []
    assert any("--yes" in line for line in lines)


def test_dry_run_makes_no_request_and_changes_nothing(store: Store) -> None:
    alpha = store.add("alpha", 4000)
    beta = store.add("beta", 2000)
    store.place(beta)  # complete but unverified
    lines: list[str] = []
    result = store.execute(
        store.plan([alpha, beta]), yes=True, dry_run=True, egress=None, echo=lines.append
    )
    assert store.requests == []
    assert result.downloaded == [] and result.verified == []
    assert dict(result.skipped) == {
        "alpha": "dry run: would download",
        "beta": "dry run: would verify",
    }
    stamp = beta.stamp_path(store.models_dir)
    assert stamp is not None and not stamp.exists()
    assert any("Dry run" in line for line in lines)


def test_existing_file_is_verified_and_stamped_without_network(store: Store) -> None:
    alpha = store.add("alpha", 4000)
    store.place(alpha)
    plan = store.plan([alpha])
    assert statuses(plan) == {"alpha": "verify"}
    result = store.execute(plan)  # no confirmation needed: nothing is downloaded
    assert result.verified == ["alpha"] and result.skipped == []
    assert store.requests == []
    assert alpha.installed(store.models_dir)


def test_corrupt_existing_file_is_downloaded_again(store: Store) -> None:
    alpha = store.add("alpha", 4000)
    store.place(alpha, b"\xff" * 4000)
    lines: list[str] = []
    result = store.execute(store.plan([alpha]), yes=True, echo=lines.append)
    assert result.downloaded == ["alpha"] and result.verified == []
    path = alpha.local_path(store.models_dir)
    assert path is not None and path.read_bytes() == store.content(alpha)
    assert any("does not match its sha256" in line for line in lines)


def test_flagged_license_needs_explicit_acceptance(store: Store) -> None:
    flagged = store.add("flagged", 3000, commercial_use="conditional")
    free = store.add("free", 1000)
    plan = store.plan([flagged, free])
    assert statuses(plan) == {"flagged": "license", "free": "download"}
    assert plan.download_bytes == 1000
    assert "--accept-license flagged" in modelstore.describe(plan)
    result = store.execute(plan, yes=True)  # --yes never accepts a license
    assert result.downloaded == ["free"]
    (skipped,) = result.skipped
    assert skipped[0] == "flagged" and "--accept-license flagged" in skipped[1]
    assert all("flagged" not in r.url.path for r in store.requests)
    accepted = store.execute(store.plan([flagged, free]), yes=True, accept_licenses=["flagged"])
    assert accepted.downloaded == ["flagged"] and accepted.installed == ["free"]
    assert flagged.installed(store.models_dir)


def test_disk_space_is_checked_before_asking(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(modelstore, "_free_bytes", lambda directory: 100)
    asked: list[str] = []
    result = store.execute(
        store.plan([store.add("alpha", 4000)]), confirm=lambda q: bool(asked.append(q))
    )
    assert result.failed and "not enough free disk space" in result.failed[0][1]
    assert asked == [] and store.requests == []


def test_offline_mode_refuses_downloads(store: Store) -> None:
    offline = net.Egress(net.EgressPolicy(offline=True))
    result = store.execute(store.plan([store.add("alpha", 4000)]), yes=True, egress=offline)
    assert result.failed == [
        ("alpha", "offline mode (privacy.mode = local) blocks downloads from the model hub")
    ]
    assert store.requests == []


def test_one_failed_download_does_not_stop_the_others(store: Store) -> None:
    alpha = store.add("alpha", 4000)
    beta = store.add("beta", 2000)
    store.files.pop(f"/{alpha.hub_id}/resolve/{REV}/{alpha.file}")
    result = store.execute(store.plan([alpha, beta]), yes=True)
    assert result.downloaded == ["beta"]
    assert result.failed[0][0] == "alpha" and "not found" in result.failed[0][1]
    assert not alpha.installed(store.models_dir)


def test_local_entries_are_never_downloaded(store: Store, tmp_path: Path) -> None:
    own = tmp_path / "own" / "mine.gguf"
    own.parent.mkdir()
    own.write_bytes(b"local model bytes")
    digest = hashlib.sha256(own.read_bytes()).hexdigest()
    local = store.add(
        "mine",
        len(own.read_bytes()),
        hub_id="local/mine",
        file=own.as_posix(),
        sha256=digest,
        ollama=None,
    )
    plan = store.plan([local])
    assert statuses(plan) == {"mine": "verify"}
    result = store.execute(plan, yes=True)
    assert result.verified == ["mine"] and store.requests == []
    assert local.installed(store.models_dir)
    assert statuses(store.plan([local])) == {"mine": "installed"}
    own.unlink()
    assert "missing" in dict(store.plan([local]).excluded)["mine"]


# --------------------------------------------------------------------------------------------
# Ollama
# --------------------------------------------------------------------------------------------


@pytest.fixture
def fake_ollama(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    module = types.ModuleType("stepswitch.serve.ollama")

    def pull_model(
        base_url: str,
        name: str,
        *,
        egress: Any = None,
        transport: Any = None,
        progress: Any = None,
    ) -> None:
        # The signature of stepswitch.serve.ollama.pull_model: progress gets NDJSON events.
        if name == "broken:latest":
            raise RuntimeError("pull failed: manifest unknown")
        for event in (
            {"status": "pulling manifest"},
            {"status": "pulling 1234abcd", "digest": "sha256:1234abcd", "total": 10},
            {"status": "pulling 1234abcd", "completed": 5, "total": 10},
            {"status": "success"},
        ):
            progress(event)
        calls.append((base_url, name))

    module.pull_model = pull_model  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "stepswitch.serve.ollama", module)
    return calls


def test_ollama_engine_pulls_by_name(store: Store, fake_ollama: list[tuple[str, str]]) -> None:
    alpha = store.add("alpha", 4000, ollama="alpha:q8_0")
    beta = store.add("beta", 2000, ollama=None)
    broken = store.add("broken", 1000, ollama="broken:latest")
    store.place(alpha, stamp=True)  # a local GGUF does not stop an Ollama pull
    lines: list[str] = []
    seen: list[tuple[int, int | None]] = []
    result = store.execute(
        store.plan([alpha, beta, broken]),
        yes=True,
        engine="ollama",
        ollama_url="http://127.0.0.1:11434",
        echo=lines.append,
        progress=lambda done, total: seen.append((done, total)),
    )
    assert fake_ollama == [("http://127.0.0.1:11434", "alpha:q8_0")]
    assert result.downloaded == ["alpha"]
    assert result.skipped == [("beta", "its registry entry has no Ollama model name")]
    assert result.failed[0][0] == "broken" and "manifest unknown" in result.failed[0][1]
    assert store.requests == []  # nothing went to the model hub
    # Ollama's progress events become status lines (once each) and (completed, total) calls.
    assert [line for line in lines if line.startswith("  alpha:")] == [
        "  alpha: pulling manifest",
        "  alpha: pulling 1234abcd",
        "  alpha: success",
    ]
    assert seen == [(5, 10)]


def test_ollama_pull_needs_confirmation_and_is_blocked_offline(
    store: Store, fake_ollama: list[tuple[str, str]]
) -> None:
    plan = store.plan([store.add("alpha", 4000, ollama="alpha:q8_0")])
    declined = store.execute(plan, engine="ollama", confirm=lambda q: False)
    assert declined.skipped == [("alpha", "declined")] and fake_ollama == []
    offline = net.Egress(net.EgressPolicy(offline=True))
    blocked = store.execute(plan, engine="ollama", yes=True, egress=offline)
    assert blocked.failed and "offline" in blocked.failed[0][1] and fake_ollama == []


def test_ollama_engine_with_the_real_adapter(store: Store) -> None:
    """The real `serve.ollama.pull_model` against a mock Ollama server (POST /api/pull)."""
    pytest.importorskip("stepswitch.serve.ollama")
    events = [
        {"status": "pulling manifest"},
        {"status": "pulling 1234abcd", "digest": "sha256:1234abcd", "total": 10, "completed": 10},
        {"status": "verifying sha256 digest"},
        {"status": "success"},
    ]
    pulls: list[str] = []

    def ollama(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path == "/api/pull"
        pulls.append(json.loads(request.content)["model"])
        body = "\n".join(json.dumps(event) for event in events) + "\n"
        return httpx.Response(200, text=body, headers={"content-type": "application/x-ndjson"})

    plan = store.plan([store.add("alpha", 4000, ollama="alpha:q8_0")])
    result = modelstore.execute(
        plan,
        yes=True,
        engine="ollama",
        ollama_url="http://127.0.0.1:11434",
        egress=net.Egress(),
        transport=httpx.MockTransport(ollama),
        echo=lambda message: None,
    )
    assert result.downloaded == ["alpha"] and result.failed == []
    assert pulls == ["alpha:q8_0"]


def test_default_ollama_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert modelstore.default_ollama_url() == "http://127.0.0.1:11434"
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0:11500")
    assert modelstore.default_ollama_url() == "http://127.0.0.1:11500"
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost")
    assert modelstore.default_ollama_url() == "http://localhost:11434"


def test_unknown_engine(store: Store) -> None:
    with pytest.raises(ValueError, match="unknown engine"):
        store.execute(store.plan([store.add("alpha", 10)]), engine="vllm")


def test_tokenizer_files_are_pulled_verified_and_kept(tmp_path: Path) -> None:
    from stepswitch.context.tokens import TokenizerSpec

    body = b'{"model": {"type": "WordLevel"}}'
    spec = TokenizerSpec(
        name="tiny",
        hub_id="org/tiny",
        revision="0" * 40,
        file="tokenizer.json",
        sha256=hashlib.sha256(body).hexdigest(),
        size_bytes=len(body),
        match=("tiny*",),
        license="apache-2.0",
    )
    requests: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, content=body)

    lines: list[str] = []
    result = modelstore.pull_tokenizers(
        [],
        models_dir=tmp_path,
        egress=net.Egress(),
        transport=httpx.MockTransport(handle),
        echo=lines.append,
        specs={"tiny": spec},
    )
    assert result.downloaded == ["tiny"] and not result.failed
    assert (tmp_path / "tokenizers" / "tiny" / "tokenizer.json").read_bytes() == body
    assert requests[0].endswith("/org/tiny/resolve/" + "0" * 40 + "/tokenizer.json")
    again = modelstore.pull_tokenizers(
        ["tiny"],
        models_dir=tmp_path,
        egress=net.Egress(),
        echo=lines.append,
        specs={"tiny": spec},
    )
    assert again.installed == ["tiny"] and len(requests) == 1
    with pytest.raises(ValueError, match="unknown tokenizer"):
        modelstore.pull_tokenizers(["nope"], models_dir=tmp_path, specs={"tiny": spec})
    bad = TokenizerSpec(**{**spec.__dict__, "name": "bad", "sha256": "1" * 64})
    failed = modelstore.pull_tokenizers(
        [],
        models_dir=tmp_path / "other",
        egress=net.Egress(),
        transport=httpx.MockTransport(handle),
        echo=lines.append,
        specs={"bad": bad},
    )
    assert failed.failed and failed.failed[0][0] == "bad"
    assert not (tmp_path / "other" / "tokenizers" / "bad" / "tokenizer.json").exists()
