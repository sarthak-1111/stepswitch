import pytest
from retry.policy import RetryPolicy, run_with_retries


def test_delays_grow_by_the_factor() -> None:
    assert RetryPolicy(attempts=4, base_delay=1.0).delays() == [1.0, 2.0, 4.0]
    assert RetryPolicy(attempts=1).delays() == []


def test_max_delay_caps_every_wait() -> None:
    policy = RetryPolicy(attempts=6, base_delay=1.0, max_delay=5.0)
    assert policy.delays() == [1.0, 2.0, 4.0, 5.0, 5.0]
    capped = RetryPolicy(attempts=3, base_delay=2.0, factor=3.0, max_delay=2.0)
    assert capped.delays() == [2.0, 2.0]
    assert RetryPolicy(attempts=4, base_delay=1.0).max_delay is None  # no cap by default


def test_a_max_delay_below_the_base_delay_is_refused() -> None:
    with pytest.raises(ValueError, match="max_delay"):
        RetryPolicy(base_delay=1.0, max_delay=0.5)


def test_run_with_retries_sleeps_the_capped_waits() -> None:
    calls: list[int] = []
    slept: list[float] = []

    def flaky() -> str:
        calls.append(1)
        if len(calls) < 4:
            raise OSError("try again")
        return "ok"

    policy = RetryPolicy(attempts=5, base_delay=1.0, max_delay=3.0)
    assert run_with_retries(flaky, policy, sleep=slept.append) == "ok"
    assert slept == [1.0, 2.0, 3.0]


def test_the_last_failure_is_raised() -> None:
    def broken() -> None:
        raise OSError("down")

    with pytest.raises(OSError, match="down"):
        run_with_retries(broken, RetryPolicy(attempts=2, base_delay=0.0), sleep=lambda _: None)
