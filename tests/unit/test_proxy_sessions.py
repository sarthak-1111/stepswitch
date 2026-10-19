"""Proxy sessions (src/stepswitch/proxy/sessions.py): conversation keys, user-turn boundaries,
the bounded session store and the stored responses of the responses format."""

from __future__ import annotations

from stepswitch.compat.ir import Message, Request, TextPart, ToolCallPart, ToolResultPart
from stepswitch.proxy import sessions as S
from stepswitch.workers.fake import FakeClock


def request(*messages: Message, system: str = "You are a coding agent.") -> Request:
    return Request(model="ssw:auto", system=[TextPart(text=system)], messages=list(messages))


def user(text: str) -> Message:
    return Message.text("user", text)


def call(name: str = "read", call_id: str = "c1") -> Message:
    return Message(role="assistant", content=[ToolCallPart(id=call_id, name=name, arguments="{}")])


def result(call_id: str = "c1", *, role: str = "tool") -> Message:
    return Message(
        role=role,  # type: ignore[arg-type]
        content=[ToolResultPart(call_id=call_id, content=[TextPart(text="file text")])],
    )


def test_a_conversation_hashes_the_same_on_every_request() -> None:
    first = request(user("Fix the bug in add()."))
    later = request(user("Fix the bug in add()."), call(), result(), user("Now run the tests."))
    other = request(user("Write a README."))
    assert S.session_key("messages", first, {}) == S.session_key("messages", later, {})
    assert S.session_key("messages", first, {}) != S.session_key("messages", other, {})
    # The same words in another format, or under another system prompt, are another session.
    assert S.session_key("messages", first, {}) != S.session_key("chat-completions", first, {})
    moved = request(user("Fix the bug in add()."), system="Another prompt.")
    assert S.session_key("messages", first, {}) != S.session_key("messages", moved, {})


def test_headers_name_the_task_and_each_agent_has_its_own_conversation() -> None:
    main = request(user("Fix the bug."))
    sub = request(user("Find where add() is defined."), system="You are a search subagent.")
    explicit = {"X-Stepswitch-Session": "t-123"}
    assert S.session_key("messages", main, explicit).task == "t-123"
    assert S.session_key("messages", sub, explicit).task == "t-123"
    assert (
        S.session_key("messages", main, explicit).conversation
        != S.session_key("messages", sub, explicit).conversation
    )
    harness = {"x-claude-code-session-id": "run-9", "x-claude-code-agent-id": "agent-main"}
    key = S.session_key("messages", main, harness)
    assert key == S.SessionKey(task="run-9", conversation="run-9/agent-main")
    # The explicit header wins over a harness's run header; blank values count as missing.
    both = {**harness, "x-stepswitch-session": "t-123"}
    assert S.session_key("messages", main, both).task == "t-123"
    blank = {"x-stepswitch-session": "  "}
    assert S.session_key("messages", main, blank) == S.session_key("messages", main, {})


def test_models_may_switch_only_at_a_new_user_turn() -> None:
    assert S.user_turn(request(user("Fix it.")))
    assert S.user_turn(request(user("Fix it."), call(), result(), user("Thanks, now commit.")))
    assert S.user_turn(request())
    loop = request(user("Fix it."), call(), result())
    assert not S.user_turn(loop) and S.open_tool_loop(loop)
    # The messages format sends tool results inside a user message.
    in_user = request(user("Fix it."), call(), result(role="user"))
    assert not S.user_turn(in_user) and S.open_tool_loop(in_user)
    # A continued assistant turn (prefill) is not a user turn either.
    prefill = request(user("Fix it."), Message.text("assistant", "Here is"))
    assert not S.user_turn(prefill) and not S.open_tool_loop(prefill)
    assert S.open_tool_loop(request(user("Fix it."), call()))


def test_the_store_keeps_conversations_and_ends_idle_tasks() -> None:
    clock = FakeClock(1000.0)
    ids = iter(f"p-{n}" for n in range(10))
    store = S.SessionStore(
        max_conversations=2, ttl_seconds=60.0, clock=clock, new_task_id=lambda: next(ids)
    )
    a1, new = store.conversation(S.SessionKey("A", "A/main"), instruction="Fix the bug.")
    assert new and a1.task.task_id == "p-0" and a1.task.instruction == "Fix the bug."
    a2, new = store.conversation(S.SessionKey("A", "A/sub"))
    assert not new and a2.task is a1.task  # a subagent's conversation joins its task
    assert a1.task.next_step() == 1 and a2.task.next_step() == 2
    again, new = store.conversation(S.SessionKey("A", "A/main"))
    assert again is a1 and not new
    # A third conversation pushes out the least recently used one; its task still has one.
    store.conversation(S.SessionKey("B", "B"))
    assert store.take_ended() == []
    assert {t.key for t in store.tasks()} == {"A", "B"}
    clock.advance(61.0)
    store.conversation(S.SessionKey("C", "C"))
    assert {t.key for t in store.take_ended()} == {"A", "B"}
    assert {t.key for t in store.tasks()} == {"C"}
    assert store.take_ended() == []


def test_stored_responses_continue_a_conversation_until_they_expire() -> None:
    clock = FakeClock(0.0)
    store = S.ResponseStore(max_items=2, ttl_seconds=10.0, clock=clock)
    history = [user("Hi"), Message.text("assistant", "Hello")]
    store.put("resp_1", history)
    found = store.get("resp_1")
    assert found == history and found is not history
    store.put("resp_2", history)
    store.put("resp_3", history)
    assert store.get("resp_1") is None  # the oldest went first
    clock.advance(11.0)
    assert store.get("resp_3") is None
