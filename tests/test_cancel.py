"""Cancellation is one-way, keeps the first reason, and stops work at a boundary."""

from __future__ import annotations

import threading

import pytest

from ingence.scan.cancel import CancellationToken, CancelledError


def test_a_fresh_token_is_not_cancelled():
    assert not CancellationToken().cancelled


def test_the_first_reason_is_the_one_kept():
    """The first is the cause; everything after it is a consequence."""
    token = CancellationToken()
    token.cancel("interrupted by SIGINT")
    token.cancel("worker pool collapsed")
    assert token.reason == "interrupted by SIGINT"


def test_a_checkpoint_raises_after_cancellation():
    token = CancellationToken()
    token.raise_if_cancelled()
    token.cancel("stop")
    with pytest.raises(CancelledError, match="stop"):
        token.raise_if_cancelled()


def test_waiting_returns_immediately_once_cancelled():
    """A background thread must stop within milliseconds, not at its interval."""
    token = CancellationToken()
    token.cancel("stop")
    assert token.wait(timeout=5.0) is True


def test_waiting_times_out_while_work_continues():
    assert CancellationToken().wait(timeout=0.01) is False


def test_cancellation_crosses_threads():
    token = CancellationToken()
    observed: list[bool] = []

    def worker() -> None:
        token.wait(timeout=2.0)
        observed.append(token.cancelled)

    # Daemon on the same principle: a test thread that can outlive its test
    # turns a failure into a hang, and the wait below is the real assertion.
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    token.cancel("from the main thread")
    thread.join(timeout=2.0)
    assert observed == [True]
