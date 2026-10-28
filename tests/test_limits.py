"""Resource limits, and the hostile inputs they exist to stop."""

from __future__ import annotations

import pytest

from ingence.core.settings import ExtractionSettings
from ingence.extractors.limits import (
    ExpansionBudget,
    LimitExceededError,
    Limits,
    UnsafeMemberPathError,
    safe_member_path,
)


@pytest.mark.parametrize(
    "name",
    [
        "../../etc/passwd",
        "/etc/passwd",
        "..",
        "a/../../b",
        r"..\..\windows\win.ini",
        "C:/Windows/System32/config",
        "",
    ],
)
def test_a_member_escaping_its_container_is_refused(name):
    """Path traversal in an archive is decades old and still shipped."""
    with pytest.raises(UnsafeMemberPathError):
        safe_member_path(name)


def test_an_ordinary_member_is_normalised_not_rejected():
    assert safe_member_path("a/b/../c.txt") == "a/c.txt"
    assert safe_member_path("./data/file.csv") == "data/file.csv"
    assert safe_member_path(r"data\file.csv") == "data/file.csv"


def test_refusing_beats_sanitising():
    """A silently rewritten name records an archive that never existed.

    An archive containing a traversal is a finding worth surfacing, not a mess to
    tidy away, so the boundary refuses rather than repairing.
    """
    with pytest.raises(UnsafeMemberPathError, match="outside its container"):
        safe_member_path("../escape.txt")


def test_a_compression_bomb_is_stopped_by_ratio():
    """A few hundred kilobytes of zeroes decompresses to gigabytes."""
    budget = ExpansionBudget(limits=Limits(max_expansion_ratio=10.0), container_bytes=1000)
    with pytest.raises(LimitExceededError, match="expansion_ratio"):
        for _ in range(100):
            budget.admit(1000)
    assert budget.stops


def test_an_archive_of_a_million_tiny_members_is_stopped_by_count():
    """Passes depth and ratio while exhausting memory through bookkeeping."""
    budget = ExpansionBudget(limits=Limits(max_expansion_members=5), container_bytes=1_000_000)
    with pytest.raises(LimitExceededError, match="expansion_members"):
        for _ in range(50):
            budget.admit(1)


def test_nesting_is_stopped_by_depth():
    budget = ExpansionBudget(limits=Limits(max_expansion_depth=2), container_bytes=10)
    deeper = budget.enter().enter()
    with pytest.raises(LimitExceededError, match="expansion_depth"):
        deeper.enter()


def test_a_limit_says_which_limit_and_by_how_much():
    """ "The extraction stopped" is not actionable; the number is."""
    budget = ExpansionBudget(limits=Limits(max_expansion_ratio=2.0), container_bytes=100)
    with pytest.raises(LimitExceededError) as raised:
        budget.admit(1000)
    assert raised.value.limit == "expansion_ratio"
    assert "to 1" in raised.value.detail


def test_limits_come_from_settings():
    limits = Limits.from_settings(
        ExtractionSettings(max_expansion_depth=7, subprocess_timeout_seconds=3.0)
    )
    assert limits.max_expansion_depth == 7
    assert limits.timeout_seconds == 3.0


def test_a_budget_is_single_use_per_container():
    """Sharing one would let a large archive starve a small one that followed."""
    limits = Limits(max_expansion_members=3)
    first = ExpansionBudget(limits=limits, container_bytes=100)
    first.admit(1)
    second = ExpansionBudget(limits=limits, container_bytes=100)
    assert second.members_seen == 0
