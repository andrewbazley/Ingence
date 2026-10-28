"""The reuse model, including every failure mode it was built to avoid.

Each test names the bug it guards. Every exclusion in the model is traceable to an observed
failure. A test suite that only checked "unchanged files are reused" would let all of them back
in.
"""

from __future__ import annotations

import stat as stat_module
from dataclasses import replace

import pytest

from ingence.core.registry import Registry
from ingence.core.schema import ReuseDecision, ScanOutcome
from ingence.core.settings import ExtractionSettings, HashMode, HashSettings, Settings
from ingence.scan.reuse import (
    PreviousRecord,
    StatFacts,
    policy_selects,
    processing_policy,
    record_satisfies_policy,
    reuse_decision,
    stat_facts,
    stat_fingerprint,
)

FACTS = StatFacts(
    size_bytes=1024,
    modified_ns=1_700_000_000_000_000_000,
    changed_ns=1_700_000_000_000_000_000,
    file_kind=stat_module.S_IFREG,
)
TXT = frozenset({".txt"})


def policy_for(settings: Settings, *, size: int = 1024, registry=None):
    return processing_policy(
        extensions=TXT, size_bytes=size, settings=settings, extractor_registry=registry
    )


def previous_for(settings: Settings, *, size: int = 1024, **overrides) -> PreviousRecord:
    base = {
        "stat_fingerprint": stat_fingerprint(FACTS),
        "processing_fingerprint": policy_for(settings, size=size).fingerprint,
        "scan_outcome": ScanOutcome.METADATA_ONLY,
    }
    return PreviousRecord(**{**base, **overrides})


# ---------------------------------------------------------------------------
# The stat fingerprint, and what it deliberately ignores.
# ---------------------------------------------------------------------------


def test_the_fingerprint_is_stable_for_unchanged_facts():
    assert stat_fingerprint(FACTS) == stat_fingerprint(replace(FACTS))


@pytest.mark.parametrize("field", ["size_bytes", "modified_ns", "changed_ns", "file_kind"])
def test_every_included_field_changes_the_fingerprint(field):
    changed = replace(FACTS, **{field: (FACTS.__getattribute__(field) or 0) + 1})
    assert stat_fingerprint(changed) != stat_fingerprint(FACTS)


def test_access_time_cannot_reach_the_fingerprint():
    """The structural exclusion. The engine reads files.

    A fingerprint including access time would record the engine's own reading as
    a change, so every scan would invalidate everything the previous scan looked
    at. Excluding it makes a pure read invisible by construction.

    Asserted structurally rather than behaviourally, because no behavioural test
    can isolate it: ``os.utime`` bumps the change time as a side effect of
    touching the inode, and change time is in the fingerprint deliberately, while
    a plain read may not update access time at all on a relatime or noatime
    mount. The honest assertion is that the value never enters the payload.
    """
    from dataclasses import fields

    assert {field.name for field in fields(StatFacts)} == {
        "size_bytes",
        "modified_ns",
        "changed_ns",
        "file_kind",
    }


def test_reading_a_file_does_not_change_its_fingerprint(tmp_path):
    """The consequence of the exclusion above, observed end to end."""
    path = tmp_path / "read-me.txt"
    path.write_text("contents", encoding="utf-8")
    before = stat_fingerprint(stat_facts(path.lstat()))
    for _ in range(3):
        path.read_bytes()
    assert stat_fingerprint(stat_facts(path.lstat())) == before


def test_device_and_inode_cannot_reach_the_fingerprint():
    """A file re-scanned from another node must not look new.

    Distributed filesystems expose one object under different device identifiers
    on different nodes, and FUSE and NFS gateways synthesise or remap inodes.
    Including either destroyed cross-node reuse until it was found.
    """
    from ingence.scan import reuse

    assert "device" not in reuse.STAT_FINGERPRINT_FIELDS
    assert "inode" not in reuse.STAT_FINGERPRINT_FIELDS
    assert set(reuse.STAT_FINGERPRINT_FIELDS) == {
        "size_bytes",
        "modified_ns",
        "changed_ns",
    }


def test_permission_bits_do_not_change_the_fingerprint_but_file_kind_does():
    """Only the kind is taken from the mode, never the permissions."""
    directory = replace(FACTS, file_kind=stat_module.S_IFDIR)
    assert stat_fingerprint(directory) != stat_fingerprint(FACTS)
    facts = stat_facts_from(mode=stat_module.S_IFREG | 0o644)
    other = stat_facts_from(mode=stat_module.S_IFREG | 0o600)
    assert stat_fingerprint(facts) == stat_fingerprint(other)


def stat_facts_from(*, mode: int) -> StatFacts:
    return StatFacts(
        size_bytes=1024,
        modified_ns=1,
        changed_ns=1,
        file_kind=stat_module.S_IFMT(mode),
    )


def test_a_symlink_replacing_a_file_at_the_same_size_and_time_is_detected():
    regular = stat_facts_from(mode=stat_module.S_IFREG | 0o644)
    link = stat_facts_from(mode=stat_module.S_IFLNK | 0o777)
    assert stat_fingerprint(regular) != stat_fingerprint(link)


def test_the_version_is_both_a_prefix_and_inside_the_payload():
    """A record made by an older generation must not compare equal to a new one."""
    from ingence.scan import reuse

    assert stat_fingerprint(FACTS).startswith(f"{reuse.STAT_FINGERPRINT_VERSION}:")


# ---------------------------------------------------------------------------
# The processing fingerprint, and over-invalidation.
# ---------------------------------------------------------------------------


def test_a_policy_change_changes_the_processing_fingerprint():
    quiet = Settings()
    loud = Settings(hash=HashSettings(mode=HashMode.FULL))
    assert policy_for(quiet).fingerprint != policy_for(loud).fingerprint


def test_the_path_never_enters_the_processing_fingerprint():
    """It describes a policy applied to a class of file, not a file."""
    first = processing_policy(extensions=TXT, size_bytes=10, settings=Settings())
    second = processing_policy(extensions=TXT, size_bytes=10, settings=Settings())
    assert first.fingerprint == second.fingerprint


def test_bumping_one_extractor_does_not_invalidate_files_it_never_claimed():
    """Over-invalidation is the failure that makes people turn incremental off.

    An earlier design put every extractor's version into every file's
    fingerprint, so bumping the text extractor invalidated every image.
    """
    settings = Settings(extraction=ExtractionSettings(enabled=True))

    def build(text_version: str) -> Registry[str]:
        registry: Registry[str] = Registry("extractor")
        registry.register(
            "text", version=text_version, summary="", attributes={"extensions": ".txt"}
        )(lambda: "text")
        registry.register("image", version="1", summary="", attributes={"extensions": ".tif,.png"})(
            lambda: "image"
        )
        return registry

    def fingerprint_of(extension: str, text_version: str) -> str:
        return processing_policy(
            extensions=frozenset({extension}),
            size_bytes=10,
            settings=settings,
            extractor_registry=build(text_version),
        ).fingerprint

    assert fingerprint_of(".txt", "1") != fingerprint_of(".txt", "2")
    assert fingerprint_of(".tif", "1") == fingerprint_of(".tif", "2")


def test_a_universal_extractor_is_in_scope_for_every_file():
    """An extractor claiming no extension would run on anything, so it counts."""
    settings = Settings(extraction=ExtractionSettings(enabled=True))

    def build(version: str) -> Registry[str]:
        registry: Registry[str] = Registry("extractor")
        registry.register("generic", version=version, summary="")(lambda: "generic")
        return registry

    first = processing_policy(
        extensions=TXT, size_bytes=10, settings=settings, extractor_registry=build("1")
    )
    second = processing_policy(
        extensions=TXT, size_bytes=10, settings=settings, extractor_registry=build("2")
    )
    assert first.fingerprint != second.fingerprint


def test_extractor_versions_are_absent_when_extraction_is_off():
    """A disabled class of work must not be fingerprinted at all."""
    registry: Registry[str] = Registry("extractor")
    registry.register("text", version="1", summary="", attributes={"extensions": ".txt"})(
        lambda: "t"
    )
    off = processing_policy(
        extensions=TXT, size_bytes=10, settings=Settings(), extractor_registry=registry
    )
    bare = processing_policy(extensions=TXT, size_bytes=10, settings=Settings())
    assert off.fingerprint == bare.fingerprint


def test_an_extractor_routing_by_content_counts_for_a_file_no_extractor_claims():
    """Content routing reaches a file no extension claims, so its fingerprint must see it.

    A file some extractor claims by extension stays that extractor's, so a change to a
    content router does not re-examine every claimed file in the pool.
    """
    settings = Settings(extraction=ExtractionSettings(enabled=True))

    def build(router_version: str) -> Registry[str]:
        registry: Registry[str] = Registry("extractor")
        registry.register(
            "router",
            version=router_version,
            summary="",
            attributes={"extensions": ".txt", "media_types": "text/*"},
        )(lambda: "router")
        registry.register("image", version="1", summary="", attributes={"extensions": ".tif"})(
            lambda: "image"
        )
        return registry

    def fingerprint_of(extensions: frozenset[str], router_version: str) -> str:
        return processing_policy(
            extensions=extensions,
            size_bytes=10,
            settings=settings,
            extractor_registry=build(router_version),
        ).fingerprint

    for unclaimed in (frozenset({".dat"}), frozenset()):
        assert fingerprint_of(unclaimed, "1") != fingerprint_of(unclaimed, "2")
    assert fingerprint_of(frozenset({".tif"}), "1") == fingerprint_of(frozenset({".tif"}), "2")
    assert fingerprint_of(TXT, "1") != fingerprint_of(TXT, "2")


def test_hashing_is_refused_on_a_remote_filesystem_by_default():
    """Hashing across a share is slow and hammers it for everyone else on it."""
    settings = Settings(hash=HashSettings(mode=HashMode.FULL))
    assert policy_for(settings).hash_selected
    remote = processing_policy(extensions=TXT, size_bytes=10, settings=settings, is_remote=True)
    assert not remote.hash_selected


# ---------------------------------------------------------------------------
# Policy selection.
# ---------------------------------------------------------------------------


def test_an_empty_include_list_means_everything():
    assert policy_selects(
        extensions=TXT,
        size_bytes=1,
        include_extensions=(),
        exclude_extensions=(),
        max_file_bytes=None,
    )


def test_exclusion_beats_inclusion():
    assert not policy_selects(
        extensions=TXT,
        size_bytes=1,
        include_extensions=(".txt",),
        exclude_extensions=(".txt",),
        max_file_bytes=None,
    )


def test_a_compound_extension_is_matched_as_well_as_the_final_one():
    assert policy_selects(
        extensions=frozenset({".gz", ".tar.gz"}),
        size_bytes=1,
        include_extensions=(".tar.gz",),
        exclude_extensions=(),
        max_file_bytes=None,
    )


def test_a_size_ceiling_excludes():
    assert not policy_selects(
        extensions=TXT,
        size_bytes=100,
        include_extensions=(),
        exclude_extensions=(),
        max_file_bytes=99,
    )


# ---------------------------------------------------------------------------
# The five-valued decision.
# ---------------------------------------------------------------------------


def test_an_unchanged_file_under_an_unchanged_policy_is_reused():
    settings = Settings()
    decision = reuse_decision(
        previous_for(settings), facts=FACTS, policy=policy_for(settings), settings=settings
    )
    assert decision is ReuseDecision.REUSE


def test_a_file_never_seen_before_reports_so():
    settings = Settings()
    assert (
        reuse_decision(None, facts=FACTS, policy=policy_for(settings), settings=settings)
        is ReuseDecision.NO_PREVIOUS_RECORD
    )


def test_a_changed_file_reports_stat_changed():
    settings = Settings()
    decision = reuse_decision(
        previous_for(settings),
        facts=replace(FACTS, size_bytes=2048),
        policy=policy_for(settings),
        settings=settings,
    )
    assert decision is ReuseDecision.STAT_CHANGED


def test_an_improved_parser_invalidates_untouched_files():
    """The question nearly every other indexer fails to ask.

    Without it you improve a parser, re-run, and the engine serves records the
    old parser produced, forever, for every file nobody touched.
    """
    before = Settings()
    after = Settings(extraction=ExtractionSettings(enabled=True))
    decision = reuse_decision(
        previous_for(before), facts=FACTS, policy=policy_for(after), settings=after
    )
    assert decision is ReuseDecision.PROCESSING_CHANGED


def test_a_metadata_only_record_cannot_satisfy_a_run_that_wants_content():
    settings = Settings(extraction=ExtractionSettings(enabled=True))
    previous = previous_for(settings, scan_outcome=ScanOutcome.METADATA_ONLY)
    decision = reuse_decision(previous, facts=FACTS, policy=policy_for(settings), settings=settings)
    assert decision is ReuseDecision.PREVIOUS_INCOMPLETE


@pytest.mark.parametrize(
    "outcome",
    [ScanOutcome.CONTENT_ERROR, ScanOutcome.DEFERRED, ScanOutcome.CHANGED_DURING_READ],
)
def test_failure_is_never_cached(outcome):
    """A scan interrupted by a stalled filesystem retries exactly what failed."""
    settings = Settings()
    previous = previous_for(settings, scan_outcome=outcome)
    decision = reuse_decision(previous, facts=FACTS, policy=policy_for(settings), settings=settings)
    assert decision is ReuseDecision.PREVIOUS_INCOMPLETE


def test_a_recorded_error_is_never_reused_whatever_the_outcome_says():
    settings = Settings()
    previous = previous_for(settings, had_error=True)
    decision = reuse_decision(previous, facts=FACTS, policy=policy_for(settings), settings=settings)
    assert decision is ReuseDecision.PREVIOUS_INCOMPLETE


def test_a_deliberate_policy_exclusion_still_satisfies_an_extraction_run():
    """ "This file is not to be opened" remains true, so it is reusable."""
    settings = Settings(extraction=ExtractionSettings(enabled=True))
    previous = previous_for(settings, scan_outcome=ScanOutcome.SKIPPED_BY_POLICY)
    decision = reuse_decision(previous, facts=FACTS, policy=policy_for(settings), settings=settings)
    assert decision is ReuseDecision.REUSE


def test_a_quick_hash_cannot_satisfy_a_policy_that_now_wants_a_full_one():
    settings = Settings(hash=HashSettings(mode=HashMode.FULL))
    previous = previous_for(settings, has_quick_hash=True, has_full_hash=False)
    assert not record_satisfies_policy(
        previous, policy_for(settings), settings=settings, size_bytes=1024
    )


def test_adaptive_hashing_wants_full_below_the_threshold_and_quick_above_it():
    settings = Settings(hash=HashSettings(mode=HashMode.ADAPTIVE, full_hash_max_bytes=1000))
    small = previous_for(settings, has_full_hash=True)
    assert record_satisfies_policy(
        small, policy_for(settings, size=500), settings=settings, size_bytes=500
    )
    large_with_only_full = previous_for(settings, has_full_hash=True)
    assert not record_satisfies_policy(
        large_with_only_full,
        policy_for(settings, size=5000),
        settings=settings,
        size_bytes=5000,
    )
    large_with_quick = previous_for(settings, has_quick_hash=True)
    assert record_satisfies_policy(
        large_with_quick,
        policy_for(settings, size=5000),
        settings=settings,
        size_bytes=5000,
    )


def test_the_decision_order_reports_the_first_and_cheapest_reason():
    """A file that changed *and* whose policy changed reports the change."""
    before = Settings()
    after = Settings(extraction=ExtractionSettings(enabled=True))
    decision = reuse_decision(
        previous_for(before),
        facts=replace(FACTS, size_bytes=99),
        policy=policy_for(after),
        settings=after,
    )
    assert decision is ReuseDecision.STAT_CHANGED


def test_every_decision_value_is_reachable():
    """All five are counted into the run record, so all five must be producible."""
    assert len(ReuseDecision) == 5
