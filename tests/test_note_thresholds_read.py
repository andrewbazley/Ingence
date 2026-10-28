"""The two rules that leave a note out read the note's thresholds as their own parameters.

``content_unreadable`` and ``empty_among_data`` leave a note written as a file name out through
``notes_as_names``. They read what a note is from their own parameters. So tuning the fewest words
a note holds, or the extensions it is written with, changes the note rule and both of them
together. Otherwise a file could be claimed a note and still be raised as empty. Every name is
invented.
"""

from __future__ import annotations

from ingence.core.schema import Entry, EntryType
from ingence.infer.subjects import EntryAmongSiblings
from ingence.rulebook.predicates.data_points import NoDataPoint
from ingence.rulebook.predicates.flag import ContentUnreadable
from ingence.rulebook.registry import PREDICATES, parameters_of

THREE_WORDS = "kelt vorn zib.txt"
TABLE_NOTE = "ask brisa about the trell.csv"


def _entry(name: str) -> Entry:
    extension = name.rsplit(".", 1)[1] if "." in name else ""
    return Entry(
        entry_id="entry_" + "1" * 32,
        root_id="root_" + "2" * 32,
        relative_path=f"kelt run/{name}",
        name=name,
        extension=extension,
        entry_type=EntryType.FILE,
        size_bytes=0,
        depth=2,
        first_seen_run="run_20260101T000001Z_111111111111",
        last_seen_run="run_20260101T000001Z_111111111111",
    )


def _among_data(name: str) -> EntryAmongSiblings:
    # A sibling of the same name but for its numbers holds bytes, so an empty file here is an
    # empty output unless it is a note.
    from ingence.infer.subjects import sibling_key

    return EntryAmongSiblings(entry=_entry(name), beside=frozenset({sibling_key(name)}))


def _parameters(**tuned: object) -> dict[str, object]:
    return {"notes_as_names": True, "written_before": None, **tuned}


def test_both_rules_declare_the_note_thresholds() -> None:
    for predicate in ("content_unreadable", "empty_among_data"):
        declared = parameters_of(PREDICATES.declaration(predicate))
        assert {"minimum_words", "note_extensions"} <= declared


def test_fewer_words_make_a_note_for_both_rules_when_the_parameter_says_so() -> None:
    three = _among_data(THREE_WORDS)
    # Four words by default: three are a name, so the empty file is raised and no data point.
    assert ContentUnreadable().evaluate(three, _parameters()) is not None
    assert NoDataPoint().evaluate(three, _parameters()) is not None
    # Three words tuned: it is a note, and neither rule raises it.
    assert ContentUnreadable().evaluate(three, _parameters(minimum_words=3)) is None
    assert NoDataPoint().evaluate(three, _parameters(minimum_words=3)) is None


def test_an_extension_a_note_may_be_written_with_is_read_from_the_parameter() -> None:
    table = _among_data(TABLE_NOTE)
    assert ContentUnreadable().evaluate(table, _parameters()) is not None
    assert NoDataPoint().evaluate(table, _parameters()) is not None
    tuned = _parameters(note_extensions=["txt", "csv"])
    assert ContentUnreadable().evaluate(table, tuned) is None
    assert NoDataPoint().evaluate(table, tuned) is None


def test_the_three_rules_hold_one_default_and_say_they_are_tuned_together() -> None:
    """Three copies of one threshold hold the same defaults, and every copy's description names
    the three rules that read it, so a person tuning one sees the other two."""
    from fixtures.revision_4 import REVISION

    rules = {str(rule["id"]): rule for rule in REVISION["rules"]}  # type: ignore[union-attr]
    readers = (
        "note_written_as_a_file_name",
        "content_unreadable",
        "empty_replicate_is_no_data_point",
    )
    held = {
        rule_id: {
            str(parameter["name"]): parameter
            for parameter in rules[rule_id]["parameters"]
            if parameter["name"] in ("minimum_words", "note_extensions")
        }
        for rule_id in readers
    }
    assert len({repr(sorted(values.items())) for values in held.values()}) == 1
    for values in held.values():
        for parameter in values.values():
            assert "content_unreadable" in str(parameter["description"])
            assert "empty_replicate_is_no_data_point" in str(parameter["description"])
