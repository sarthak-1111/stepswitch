"""Stable prefix layout (TK1): every work step sends the same system text and tools, byte for
byte, and the transcript only grows between the batched points where TK6 rewrites it."""

from __future__ import annotations

from pathlib import Path

from stepswitch.compat.ir import Request
from stepswitch.context.prefix import LAYOUT, StablePrefix, append_only, digest, volatile_parts
from stepswitch.workers.builtin_agent import WORK_PROMPT
from tests.unit.test_builtin_agent import StubGates, fixer_script, make_loop, worker


def work_requests(requests: list[Request]) -> list[Request]:
    return [request for request in requests if request.tools]


def test_layout_is_system_tools_repo_map_glossary() -> None:
    assert LAYOUT == ("system", "tools", "repo map", "glossary")
    prefix = StablePrefix("role", repo_map="map", glossary="terms")
    assert prefix.text() == "role\n\nmap\n\nterms"
    assert StablePrefix("role").text() == "role"


def test_work_steps_share_one_prefix_and_append_only_messages(tmp_path: Path) -> None:
    model = worker("a/m", "t2", fixer_script())
    loop = make_loop(tmp_path, [model], StubGates([True]))
    loop.overview = "Repository map:\ncalc.py: add"
    loop.track_notes = "Protected values: none"
    loop.glossary = "add: the sum of two numbers"
    assert loop.run().status == "done"
    requests = work_requests(model.model.requests)  # type: ignore[union-attr]
    assert len(requests) >= 4
    system = requests[0].system_text()
    assert system.index(WORK_PROMPT) < system.index("Protected values")
    assert system.index("Repository map") < system.index("Domain glossary:")
    assert {digest(request) for request in requests} == {digest(requests[0])}
    for before, after in zip(requests, requests[1:], strict=False):
        assert append_only(before, after)
    assert volatile_parts(system) == []


def test_the_baseline_keeps_the_phase_two_order(tmp_path: Path) -> None:
    model = worker("a/m", "t2", fixer_script())
    loop = make_loop(tmp_path, [model], StubGates([True]))
    loop.optimize = False
    loop.overview = "Repository map:\ncalc.py: add"
    loop.track_notes = "Protected values: none"
    loop.glossary = "add: the sum of two numbers"
    assert loop.run().status == "done"
    system = work_requests(model.model.requests)[0].system_text()  # type: ignore[union-attr]
    # Phase 2: role prompt, track notes with the glossary, then the repository overview.
    assert system.index("Domain glossary:") < system.index("Repository map")
