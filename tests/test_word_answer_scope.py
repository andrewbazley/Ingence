"""Where a meaning given to a word question applies: everywhere by default, or only where it stands.

Names are invented (``kelt`` is a word nothing binds; ``atp``, ``adp`` and ``tcep`` are reagents the
shipped Dictionary holds; ``cluster`` stands between two senses). The page offers "only where it
stands" only where the route that writes would keep a meaning to a folder, both reading one
function. Runs are stamped when they run.
"""

from __future__ import annotations

import datetime as dt
import itertools
import os
from typing import TYPE_CHECKING, Any

import pytest

from fixtures.revision_4 import write_revision
from ingence.api.routes import pages
from ingence.api.routes.decide import AnswerRefusedError
from ingence.core.ids import ScopeKind
from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.infer.run import run_inference
from ingence.record.log import read_events
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog
from ingence.textword import questions

if TYPE_CHECKING:
    from pathlib import Path

#: The shipped ambiguity flag, declared again by revision 4: its findings sit on files.
AMBIGUOUS = "binding_is_ambiguous_outside_programs_and_domains"

_TICKS = itertools.count(1)


def _stamp(tail: str) -> str:
    """A run identifier stamped now, a second apart each time, so no file is born after its run."""
    moment = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=next(_TICKS))
    return f"run_{moment:%Y%m%dT%H%M%S}Z_{tail * 12}"


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _template(folder: Path, words: tuple[str, ...] = ("atp", "adp", "tcep", "kelt")) -> None:
    for word in words:
        for replicate in (1, 2):
            _write(folder / f"SubX_{word}_r{replicate}.txt")


def _inferred(base: Path, roots: list[Path]) -> Project:
    created = Project.create(base / "project", created_at="t", roots=roots)
    write_revision(created.path)
    run_census(created, run_id=_stamp("1"), started_at="t")
    run_inference(created, run_id=_stamp("2"), started_at="t")
    return created


@pytest.fixture
def nested(tmp_path: Path) -> Project:
    """Two sibling folders under one parent stand alike, and a third far away does too."""
    pool = tmp_path / "pool"
    for folder in ("day1/first", "day1/second", "elsewhere"):
        _template(pool / folder)
    return _inferred(tmp_path, [pool])


def _decide(project: Project, target: str | None = None, **fields: str) -> Any:
    from ingence.api.routes.decide import Decision, DecisionContext, decide_target

    with open_catalog(project.path) as catalog:
        context = DecisionContext(catalog=catalog, project=project.path)
        decision = Decision(target=target or f"question:{questions.RULE_ID}:kelt", **fields)
        return decide_target(context, decision, actor="tester", occurred_at="2026-09-26T00:00:00Z")


def _plainly(message: str) -> None:
    """A refusal a person reads: no internal spelling, and none of the words that meant nothing."""
    for spelled in ("where_it_stands", "whole pool", "raised", "_"):
        assert spelled not in message.replace("'kelt'", "").replace("'vorn'", ""), message


def test_a_meaning_applies_everywhere_unless_the_person_says_where_it_stands(
    nested: Project,
) -> None:
    event = _decide(nested, answer="kelt_protein", theme_kind="reagent")
    assert (event.scope.kind.value, event.scope.value) == ("project", None)


def test_only_where_it_stands_is_refused_where_that_is_every_folder(nested: Project) -> None:
    """The word stands in three folders, one of them apart, so the deepest folder holding all of
    them is the top: nothing narrower, and a scope that scopes nothing is refused, plainly."""
    with pytest.raises(AnswerRefusedError, match="cannot be kept to where it stands") as refused:
        _decide(
            nested,
            answer="kelt_protein",
            theme_kind="reagent",
            where=questions.WHERE_IT_STANDS,
        )
    _plainly(str(refused.value))


def test_a_scoped_meaning_reaches_the_common_folder_and_what_lies_beneath_it(
    tmp_path: Path,
) -> None:
    pool = tmp_path / "pool"
    for folder in ("day1/first", "day1/second"):
        _template(pool / folder)
    _write(pool / "elsewhere" / "kelt.txt")
    created = _inferred(tmp_path, [pool])
    event = _decide(
        created, answer="kelt_protein", theme_kind="reagent", where=questions.WHERE_IT_STANDS
    )
    assert (event.scope.kind.value, event.scope.value) == ("folder", "day1")
    with open_catalog(created.path) as catalog:
        page = questions.word_question_page(catalog, created.path)
    (row,) = page["questions"]
    assert row["in_force"]["scope"] == {"kind": "folder", "value": "day1"}
    assert row["in_force"]["answer"] == "kelt_protein"


def test_a_scope_is_refused_beside_anything_but_a_meaning(nested: Project) -> None:
    with pytest.raises(AnswerRefusedError, match="Only a meaning can be kept") as refused:
        _decide(nested, word="not_a_word", where=questions.WHERE_IT_STANDS)
    _plainly(str(refused.value))
    with pytest.raises(AnswerRefusedError, match="applies everywhere, or only where") as refused:
        _decide(nested, answer="kelt_protein", theme_kind="reagent", where="nearby")
    _plainly(str(refused.value))


def test_a_word_question_is_answered_by_a_meaning_spelled_as_the_term_sheet_spells_one(
    nested: Project,
) -> None:
    """The question offers no sense; a guess taken from the shaded layer says ``sense [axis]``."""
    from ingence.record.questions import answers_in_force

    event = _decide(nested, word="kelt_protein [reagent]")
    assert event.kind is RecordEventKind.DEFINE
    (answer,) = answers_in_force(nested.path)
    assert (answer.raised, answer.term, answer.theme_kind) == ("kelt", "kelt_protein", "reagent")
    assert answer.scope.kind is ScopeKind.PROJECT


def test_a_word_question_can_be_answered_as_not_a_word_with_what_it_is_to_a_computer(
    nested: Project,
) -> None:
    event = _decide(nested, word="not_a_word", reading="a_program_mark")
    assert event.kind is RecordEventKind.REJECT
    assert '"reading":"a_program_mark"' in event.payload


def test_the_word_question_offers_not_a_word_and_cannot_say_beside_a_meaning() -> None:
    from ingence.record.verdicts import question_words

    assert question_words("unbound_word_where_a_kind_stands") == ("not_a_word", "cannot_say")


# --- the re-check's findings --------------------------------------------------------------


@pytest.fixture
def between(tmp_path: Path) -> Project:
    """A word standing between two senses in the files of two folders under one, and an empty
    file left long ago, which raises a question about something other than a word."""
    pool = tmp_path / "pool"
    for folder in ("amb/kelt", "amb/quolt"):
        for index in range(3):
            _write(pool / folder / f"zib_cluster_raw_{index}.csv", "a,b\n1,2\n")
            _write(pool / folder / f"zib_cluster_processed_{index}.csv", "a,b\n1,2\n")
    stub = pool / "plates" / "stub.dat"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_bytes(b"")
    long_ago = 1_577_836_800  # 2020-01-01, old enough for the empty-file rule
    os.utime(stub, (long_ago, long_ago))
    return _inferred(tmp_path, [pool])


def test_a_word_between_senses_is_kept_to_the_folder_holding_its_files(between: Project) -> None:
    """A meaning for a word between senses is kept to the folder that holds its files.

    The findings of such a question sit on files. A file finding's folder is its parent, and the
    page offers the folder the route writes.
    """
    with open_catalog(between.path) as catalog:
        rows = pages.page(catalog, between.path, "questions")["rows"]
    (row,) = [item for item in rows if item["rule_id"] == AMBIGUOUS and item["raised"] == "cluster"]
    assert [choice["value"] for choice in row["choices"]] == [
        questions.EVERYWHERE,
        questions.WHERE_IT_STANDS,
    ]
    assert row["choices"][1]["label"] == "only in amb and below"
    event = _decide(
        between,
        target=f"question:{AMBIGUOUS}:cluster",
        answer="spindle_density",
        theme_kind="measurement",
        where=questions.WHERE_IT_STANDS,
    )
    assert (event.scope.kind.value, event.scope.value) == ("folder", "amb")


def test_not_a_word_is_refused_on_a_question_about_something_else(between: Project) -> None:
    """'not a word' is refused on a question that is not about a word."""
    with open_catalog(between.path) as catalog:
        (raised,) = catalog.query(
            "SELECT raised[1] FROM findings WHERE rule_id = 'content_unreadable'"
        ).fetchone()
    with pytest.raises(AnswerRefusedError, match="answers a question about what a word is"):
        _decide(between, target=f"question:content_unreadable:{raised}", word="not_a_word")
    assert read_events(between.path) == ()


def test_the_route_sets_aside_a_program_s_folder_as_the_page_does(tmp_path: Path) -> None:
    """The page and the route read one function, so the page does not set a program's folder aside
    and offer day1 where the route counts it and refuses.
    """
    pool = tmp_path / "pool"
    for folder in ("day1/first", "day1/second", "Kit.app"):
        _template(pool / folder)
    created = _inferred(tmp_path, [pool])
    with open_catalog(created.path) as catalog:
        (row,) = questions.word_question_page(catalog, created.path)["questions"]
    assert row["choices"][1]["scope_folder"] == "day1"
    event = _decide(
        created, answer="kelt_protein", theme_kind="reagent", where=questions.WHERE_IT_STANDS
    )
    assert (event.scope.kind.value, event.scope.value) == ("folder", "day1")


def test_a_word_standing_only_at_the_top_is_answered_everywhere(tmp_path: Path) -> None:
    """The top of the folder a project reads is written '.'.

    With one such folder, a meaning kept to the top would reach every folder anyway. So everywhere
    is the one choice, and "only where it stands" is refused in plain words.
    """
    pool = tmp_path / "pool"
    _template(pool)
    created = _inferred(tmp_path, [pool])
    with open_catalog(created.path) as catalog:
        (row,) = questions.word_question_page(catalog, created.path)["questions"]
    assert [choice["value"] for choice in row["choices"]] == [questions.EVERYWHERE]
    assert row["folders"] == ["."]
    with pytest.raises(AnswerRefusedError, match="would reach every folder anyway") as refused:
        _decide(created, answer="kelt_protein", theme_kind="reagent", where="where_it_stands")
    _plainly(str(refused.value))


def test_a_meaning_is_never_kept_to_a_path_another_folder_the_project_reads_holds(
    tmp_path: Path,
) -> None:
    """A folder scope names a path within one of the folders a project reads.

    It would reach the same path in another, and a word standing in two of them could be kept to a
    path in each. Both are refused, plainly.
    """
    one = tmp_path / "one"
    two = tmp_path / "two"
    for folder in ("day1/first", "day1/second"):
        _template(one / folder)
    _write(two / "day1" / "other" / "kelt_notes.txt")
    _template(one / "a" / "vorn", ("atp", "adp", "tcep", "vorn"))
    _template(two / "b" / "vorn", ("atp", "adp", "tcep", "vorn"))
    created = _inferred(tmp_path, [one, two])
    with pytest.raises(AnswerRefusedError, match="also holds a folder day1") as refused:
        _decide(created, answer="kelt_protein", theme_kind="reagent", where="where_it_stands")
    _plainly(str(refused.value))
    with pytest.raises(AnswerRefusedError, match="more than one of the folders") as refused:
        _decide(
            created,
            target=f"question:{questions.RULE_ID}:vorn",
            answer="vorn_salt",
            theme_kind="reagent",
            where="where_it_stands",
        )
    _plainly(str(refused.value))
    with open_catalog(created.path) as catalog:
        offered = {
            row["word"]: [choice["value"] for choice in row["choices"]]
            for row in questions.word_question_page(catalog, created.path)["questions"]
        }
    assert offered == {"kelt": [questions.EVERYWHERE], "vorn": [questions.EVERYWHERE]}
