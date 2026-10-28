"""The rule that orders replicates, on invented classes.

``read_series`` puts the members of one class of replicates in order by their one counting part, and
the predicate pairs only neighbours. A class abstains whole, with the reason, when two parts vary,
when two members carry the same number, when a number does not parse, when the names are laid out
unlike each other, or when the run recorded no layout. A dose ladder is never offered a class (a
dose is a quantity, and a class of replicates holds its quantities fixed).

The members are stand-ins carrying only what the reading uses: the name's slots, its components, and
an entry identifier.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from ingence.core.tiers import ResolutionStatus
from ingence.rulebook.predicates import load_builtins
from ingence.rulebook.predicates.series import counting_kinds, read_series
from ingence.rulebook.registry import PREDICATES

if TYPE_CHECKING:
    from pathlib import Path

INDEXED = counting_kinds("indexed")
load_builtins()


def _component(identifier: str, kind: str, value: str, raw: str | None = None) -> Any:
    return SimpleNamespace(
        component_id=identifier, component_type=kind, normalized_value=value, raw_value=raw or value
    )


def _member(name: str, parts: list[tuple[str, str, str]], *, layout: bool = True) -> Any:
    """A file whose name has the given (kind, normalised value, as written) parts in order."""
    components = [
        _component(f"{name}:{place}", kind, value, raw)
        for place, (kind, value, raw) in enumerate(parts)
    ]
    return SimpleNamespace(
        entry=SimpleNamespace(entry_id=name),
        components=tuple(components),
        slots=tuple(component.component_id for component in components) if layout else (),
    )


def _counted(number: str, *, word: str = "dmso", kind: str = "number") -> Any:
    value = str(int(number)) if kind == "number" else number
    return _member(
        f"{word}-{number}",
        [
            ("word", "hela", "HeLa"),
            ("word", word, word),
            (kind, value, number),
            ("extension", "tif", "tif"),
        ],
    )


def test_a_class_is_put_in_order_by_its_one_counting_part() -> None:
    members = [_counted(n) for n in ("003", "001", "002", "010")]
    reading = read_series(members, INDEXED)
    assert reading.order == ("dmso-001", "dmso-002", "dmso-003", "dmso-010")
    # Numbers compare as numbers: 010 follows 003, and text order would put it first.
    assert dict(reading.values)["dmso-010"] == 10


def test_an_indexed_label_counts_too_and_only_with_one_label() -> None:
    members = [_counted(f"rep{n}", kind="indexed") for n in (2, 1, 3)]
    assert read_series(members, INDEXED).order == ("dmso-rep1", "dmso-rep2", "dmso-rep3")
    mixed = [_counted("rep1", kind="indexed"), _counted("run2", kind="indexed")]
    reading = read_series(mixed, INDEXED)
    assert not reading.orders
    assert reading.code == "labels"


def test_a_tie_abstains_for_the_whole_class() -> None:
    reading = read_series([_counted("1"), _counted("001"), _counted("002")], INDEXED)
    assert reading.order == ()
    assert reading.code == "tie"
    assert "same number" in reading.reason


def test_two_varying_parts_abstain_for_the_whole_class() -> None:
    def member(stage: str, number: str) -> Any:
        return _member(
            f"{stage}-{number}",
            [
                ("word", "hela", "HeLa"),
                ("word", stage, stage),
                ("number", str(int(number)), number),
                ("extension", "tif", "tif"),
            ],
        )

    reading = read_series([member("raw", "001"), member("processed", "002")], INDEXED)
    assert reading.order == ()
    assert "more than one part varies" in reading.reason


def test_a_stage_word_alone_is_no_counting_part() -> None:
    def member(stage: str) -> Any:
        return _member(
            stage, [("word", "hela", "HeLa"), ("word", stage, stage), ("number", "1", "001")]
        )

    reading = read_series([member("raw"), member("processed")], INDEXED)
    assert reading.order == ()
    assert "no counting part" in reading.reason


def test_a_value_that_does_not_parse_abstains_rather_than_ordering_as_text() -> None:
    odd = _member("odd", [("word", "hela", "HeLa"), ("number", "1a", "1a")])
    fine = _member("fine", [("word", "hela", "HeLa"), ("number", "2", "002")])
    reading = read_series([odd, fine], INDEXED)
    assert reading.order == ()
    assert reading.code == "parse"


def test_names_laid_out_unlike_each_other_or_not_recorded_abstain() -> None:
    short = _member("short", [("word", "hela", "HeLa"), ("number", "1", "001")])
    long = _member(
        "long", [("word", "hela", "HeLa"), ("word", "dmso", "dmso"), ("number", "2", "002")]
    )
    assert "laid out alike" in read_series([short, long], INDEXED).reason
    unrecorded = read_series([_member("a", [("number", "1", "001")], layout=False), short], INDEXED)
    assert unrecorded.unexamined is True
    assert "no layout" in unrecorded.reason
    assert not read_series([short], INDEXED).orders


def test_a_class_of_names_that_never_differ_abstains() -> None:
    twin = [_member(f"twin{n}", [("word", "hela", "HeLa"), ("number", "1", "001")]) for n in (1, 2)]
    reading = read_series(twin, INDEXED)
    assert reading.order == ()
    assert "nothing tells the members apart" in reading.reason


def _pair(members: list[Any], source: str, target: str) -> Any:
    by_id = {member.entry.entry_id: member for member in members}
    return SimpleNamespace(
        source=by_id[source], target=by_id[target], members=tuple(members), within="replicates"
    )


def test_the_predicate_pairs_neighbours_and_explains_each_abstention() -> None:
    predicate: Any = PREDICATES.get("succeeds_by_ordered_token")
    parameters = {"ordering_kind": "indexed"}
    members = [_counted(n) for n in ("001", "002", "003", "004")]
    assert predicate.pairs_within("key", tuple(members), parameters) == {
        "dmso-001": ("dmso-002",),
        "dmso-002": ("dmso-003",),
        "dmso-003": ("dmso-004",),
    }
    follows = predicate.evaluate(_pair(members, "dmso-002", "dmso-003"), parameters)
    assert follows and follows[0].family == "ordered_token"
    # The first and the third are no neighbours; the order is source then target, never reversed.
    assert predicate.evaluate(_pair(members, "dmso-001", "dmso-003"), parameters) is None
    assert predicate.evaluate(_pair(members, "dmso-003", "dmso-002"), parameters) is None
    why = predicate.explain_abstention(_pair(members, "dmso-001", "dmso-003"), parameters)
    assert "directly after" in why.reason

    tied = [_counted("1"), _counted("001")]
    assert predicate.pairs_within("key", tuple(tied), parameters) == {}
    assert predicate.evaluate(_pair(tied, "dmso-1", "dmso-001"), parameters) is None
    abstained = predicate.explain_abstention(_pair(tied, "dmso-1", "dmso-001"), parameters)
    assert abstained.status is ResolutionStatus.EXAMINED_NO_EVIDENCE
    assert "same number" in abstained.reason


def test_a_dose_ladder_is_not_a_series() -> None:
    """Doses differ in a quantity, which a class of replicates holds fixed; were a class to hold
    three doses anyway, its one varying part is a quantity and no counting part."""

    def dosed(dose: str) -> Any:
        return _member(
            f"dose-{dose}",
            [
                ("word", "hela", "HeLa"),
                ("quantity", f"{dose} molar", f"{dose}uM"),
                ("number", "1", "001"),
            ],
        )

    reading = read_series([dosed("1"), dosed("10"), dosed("100")], INDEXED)
    assert reading.order == ()
    assert "no counting part" in reading.reason


def test_the_rule_ships_off_and_a_project_turns_it_on_with_a_change_file(tmp_path: Path) -> None:
    from ingence.core.project import Project
    from ingence.rulebook import project_rulebook, shipped_rulebook

    assert PREDICATES.declaration("succeeds_by_ordered_token").version == "2"
    shipped = shipped_rulebook()
    assert "succeeds_by_series_index" not in {rule.rule_id for rule in shipped.rules}
    assert dict(shipped.inactive)["succeeds_by_series_index"] == "disabled"
    project = Project.create(tmp_path / "project", created_at="t", roots=[tmp_path])
    folder = project.path / "rulebook"
    folder.mkdir(exist_ok=True)
    (folder / "project-0001.yaml").write_text(
        "project_revision: 1\nsummary: Replicate numbers are an order.\n"
        "changes:\n  - enable: succeeds_by_series_index\n"
    )
    rule = next(
        r for r in project_rulebook(project.path).rules if r.rule_id == "succeeds_by_series_index"
    )
    assert rule.candidates_within == "replicates_of_one_condition"
    assert rule.version.startswith("2")


def _numbered(values: list[str], *, clocks: dict[str, int] | None = None) -> list[Any]:
    members = [_counted(value) for value in values]
    for member in members:
        member.entry.event_time_ns = (clocks or {}).get(member.entry.entry_id)
        member.entry.event_time_tier = "instrument" if clocks else "unavailable"
    return members


@pytest.mark.parametrize(
    "values",
    [
        ["488", "561", "640"],  # wavelengths
        ["1", "10", "100"],  # a dose written without its unit
        ["0", "30", "60"],  # minutes
        ["3", "8", "17", "42"],  # sparse
    ],
)
def test_numbers_that_are_conditions_are_never_put_in_order(values: list[str]) -> None:
    reading = read_series([_counted(value) for value in values], INDEXED)
    assert reading.order == ()
    assert reading.code == "not_counting"
    assert "may name a condition" in reading.reason


@pytest.mark.parametrize("values", [["1", "2", "3"], ["001", "002", "010"], ["1", "2", "4", "5"]])
def test_numbers_that_count_are_put_in_order(values: list[str]) -> None:
    assert read_series([_counted(value) for value in values], INDEXED).orders


def test_the_files_own_clocks_running_against_the_numbers_abstain() -> None:
    hour = 3600 * 1_000_000_000
    agree = _numbered(
        ["001", "002", "003"], clocks={"dmso-001": hour, "dmso-002": 2 * hour, "dmso-003": 3 * hour}
    )
    assert read_series(agree, INDEXED).orders
    against = _numbered(
        ["001", "002", "003"], clocks={"dmso-001": 3 * hour, "dmso-002": 2 * hour, "dmso-003": hour}
    )
    reading = read_series(against, INDEXED)
    assert (reading.order, reading.code) == ((), "clocks")
    # A file with no clock of its own says nothing, and a gap inside a minute is no disagreement.
    partly = _numbered(
        ["001", "002", "003"], clocks={"dmso-001": hour, "dmso-003": hour - 30 * 1_000_000_000}
    )
    assert read_series(partly, INDEXED).orders


def test_the_clock_tolerance_is_the_rules_parameter() -> None:
    hour = 3600 * 1_000_000_000
    close = _numbered(
        ["001", "002"], clocks={"dmso-001": hour, "dmso-002": hour - 30 * 1_000_000_000}
    )
    assert read_series(close, INDEXED).orders  # 30 seconds is inside the default minute
    assert read_series(close, INDEXED, tolerance_seconds=0).code == "clocks"
    predicate: Any = PREDICATES.get("succeeds_by_ordered_token")
    assert predicate.pairs_within("key", tuple(close), {"tolerance_seconds": 0}) == {}
    assert predicate.pairs_within("key", tuple(_numbered(["001", "002"], clocks={})), {}) != {}
    assert (
        "tolerance_seconds"
        in PREDICATES.declaration("succeeds_by_ordered_token").attributes["parameters"]
    )
