"""A group question answered before the lattice's words changed is still answered.

Switching ``groups.words_as`` (or teaching the Dictionary a word) rewords the group questions.
An answer kept under the old wording must not make the question new. A person who confirmed a
proposed group while the lattice held spellings has still confirmed it when the next run holds
terms: the group still groups, and its question is not asked again. Every name is invented, and
so is the person answering.
"""

from __future__ import annotations

import json
import urllib.request
from typing import TYPE_CHECKING, Any, Literal

import pytest

from fixtures.revision_4 import each_deviation, write_revision
from ingence.api.routes.decide import Decision, DecisionContext, decide_target
from ingence.api.routes.pages import page
from ingence.api.server import DashboardServer
from ingence.core.ids import PROJECT_SCOPE
from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.core.settings import GroupSettings, Settings
from ingence.grouping.asked_again import AskedAgain
from ingence.grouping.lattice_words import WordMap
from ingence.infer.run import run_inference
from ingence.record.log import append_event
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

CENSUS = "run_20260101T000001Z_111111111111"
REBOUND = "run_20260101T000002Z_222222222222"
INFERRED = "run_20260101T000003Z_333333333333"
PROPOSAL_RULE = "experiments_may_belong_together"
DEVIATION_RULE = "group_resolution_deviation"
PERSON = "dana (an invented person)"
MOMENT = "2026-01-01T00:00:30Z"

#: ``kelt`` and ``brisa`` are taught as the reagents ``kelt_line`` and ``brisa_stain``.
TAUGHT = (("kelt", "kelt_line"), ("brisa", "brisa_stain"))


def _project(base: Path, words_as: Literal["terms", "spellings"]) -> Project:
    pool = base / "pool"
    pairs = [
        ("20240301", "kelt vorn brisa"),
        ("20240302", "kelt vorn quolt"),
        *[
            (f"202404{number:02d}", words)
            for number, words in enumerate(
                ("alpa bret", "cind dorn", "elk fenn", "gorb hask", "ilm jorv", "kasp lunt"),
                start=1,
            )
        ],
    ]
    for day, words in pairs:
        for number in (1, 2):
            path = pool / f"{day} {words}" / f"{words.replace(' ', '_')}_{number:02d}.nd2"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")
    created = Project.create(
        base / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(groups=GroupSettings(words_as=words_as)),
    )
    each_deviation(write_revision(created.path))
    run_census(created, run_id=CENSUS, started_at="2026-01-01T00:00:01Z")
    for word, term in TAUGHT:
        append_event(
            created.path,
            kind=RecordEventKind.DEFINE,
            actor=PERSON,
            occurred_at="2026-01-01T00:00:10Z",
            target=f"term:{word}",
            against_run_id=CENSUS,
            payload={"raised": word, "answer": term, "theme_kind": "reagent"},
            scope=PROJECT_SCOPE,
        )
    run_census(
        Project.open(created.path), run_id=REBOUND, started_at="2026-01-01T00:00:02Z", rebind=True
    )
    run_inference(Project.open(created.path), run_id=INFERRED, started_at="2026-01-01T00:00:03Z")
    return created


def _rerun(project: Path, words_as: Literal["terms", "spellings"], number: int) -> None:
    """The setting changed, and a run that reads it."""
    Project.open(project).save_settings(Settings(groups=GroupSettings(words_as=words_as)))
    run_inference(
        Project.open(project),
        run_id=f"run_20260101T00000{number}Z_{str(number) * 12}",
        started_at=f"2026-01-01T00:00:0{number}Z",
    )


def _asked(project: Path, rule_id: str) -> list[str]:
    from ingence.api.routes.review import review

    with open_catalog(project) as catalog:
        return sorted(
            str(question["raised"])
            for question in review(catalog, limit=10000)["questions"]
            if question["rule_id"] == rule_id
        )


def _claims(project: Path, rule_id: str) -> set[str]:
    """The entries a rule concluded on, by their relative paths."""
    with open_catalog(project) as catalog:
        rows = catalog.query(
            "SELECT DISTINCT e.relative_path FROM claims c JOIN entries e "
            "ON e.entry_id = c.subject WHERE c.rule_id = ? AND c.resolution_status = 'resolved'",
            [rule_id],
        ).fetchall()
    return {str(path) for (path,) in rows}


def _the_proposal(project: Path) -> str:
    (raised,) = [
        r for r in _asked(project, PROPOSAL_RULE) if r.startswith(("kelt vorn", "kelt_line"))
    ]
    return raised


def _on_the_page(project: Path, rule_id: str, raised: str, word: str) -> None:
    """A person's answer through the Dashboard's one write route (it lands on ``group:<core>``)."""
    with open_catalog(project) as catalog:
        decide_target(
            DecisionContext(catalog, project),
            Decision(target=f"question:{rule_id}:{raised}", word=word),
            actor=PERSON,
            occurred_at=MOMENT,
        )


def _on_the_question(project: Path, rule_id: str, raised: str) -> None:
    """A confirmation written on the question's own target, as a sheet writes it."""
    append_event(
        project,
        kind=RecordEventKind.CONFIRM,
        actor=PERSON,
        occurred_at=MOMENT,
        target=f"question:{rule_id}:{raised}",
        against_run_id=INFERRED,
        payload={},
        scope=PROJECT_SCOPE,
    )


# -- the finder --------------------------------------------------------------------------------


def _words() -> WordMap:
    # ``brisal`` is a second spelling of the term ``brisa_stain``, beside ``brisa``.
    return WordMap(
        [("kelt", "kelt_line", 3), ("brisa", "brisa_stain", 2), ("brisal", "brisa_stain", 1)],
        words_as="terms",
    )


def test_a_set_asked_in_spellings_is_found_by_the_terms_it_is_asked_in_now() -> None:
    stored = {"question:r:a group: kelt vorn zib + brisal": "yes"}
    again = AskedAgain(_words, stored)
    # The whole set is what a set asks; which part is the base is the lattice's business.
    assert again.find("question:r:a group: brisa_stain kelt_line vorn zib") == "yes"
    assert again.find("question:r:a group: kelt_line vorn zib + brisa_stain") == "yes"
    assert again.find("question:r:a group: kelt_line vorn zib") is None
    # A page answers a set on its group, and that answers the question about the set.
    assert (
        AskedAgain(_words, {"group:kelt vorn zib brisa": "yes"}).find(
            "question:r:a group: brisa_stain kelt_line vorn zib"
        )
        == "yes"
    )


def test_a_deviation_asked_in_spellings_is_found_by_its_word_and_core_as_terms() -> None:
    stored = {"question:r:brisal, added to the group kelt vorn": "yes"}
    again = AskedAgain(_words, stored)
    assert again.find("question:r:brisa_stain, added to the group kelt_line vorn") == "yes"
    # Added is not missing, and another word is another question.
    assert again.find("question:r:brisa_stain, missing from the group kelt_line vorn") is None
    assert again.find("question:r:quolt, added to the group kelt_line vorn") is None
    # Another rule's question is another question.
    assert again.find("question:other:brisa_stain, added to the group kelt_line vorn") is None


def _members(*paths: str) -> frozenset[str]:
    return frozenset(paths)


def test_a_proposal_reworded_is_found_by_its_exact_members_and_by_nothing_less() -> None:
    stored = "question:r:kelt vorn brisal [20240301 a and 2 more]"
    held = {stored: "yes"}
    then = _members("20240301 a", "20240302 b", "20240303 c")

    def again(**kwargs: Any) -> AskedAgain[str]:
        return AskedAgain(_words, held, members_then=lambda _target, _given: then, **kwargs)

    now = "question:r:brisa_stain kelt_line vorn [20240301 a and 2 more]"
    # The same members, reworded: the answer is kept.
    assert again().find(now, members=_members("20240301 a", "20240302 b", "20240303 c")) == "yes"
    # **Different members behind the same first folder name and the same count**: another group,
    # asked again, though the words it is worded in agree.
    assert again().find(now, members=_members("20240301 a", "20240304 d", "20240305 e")) is None
    # Members that are not known are not shown to be the same.
    assert again().find(now) is None
    assert again(members_now=lambda _target: None).find(now) is None
    assert again(members_now=lambda _target: then).find(now) == "yes"
    # The words are only a label: the same members are the same group, however many are shown.
    for worded in (
        "question:r:kelt_line [20240301 a and 2 more]",
        "question:r:brisa_stain kelt_line vorn zib [20240301 a and 2 more]",
    ):
        assert again().find(worded, members=then) == "yes"
    # Other files named are another group, whatever the members given (the count is part of it).
    assert again().find("question:r:kelt_line vorn [20240301 a and 1 more]", members=then) is None


def test_a_proposal_is_looked_for_in_other_words_only_where_its_words_read_differently() -> None:
    """Stored in terms already, the words read as they are through the map: found as written."""
    then = _members("20240301 a", "20240302 b")
    again = AskedAgain(
        _words,
        {"question:r:brisa_stain kelt_line [20240301 a and 1 more]": "yes"},
        members_then=lambda _target, _given: then,
    )
    assert (
        again.find("question:r:brisa_stain kelt_line [20240301 a and 1 more]", members=then)
        == "yes"
    )
    # The same members worded some other way are asked again: the words did not change by the map.
    assert again.find("question:r:kelt vorn [20240301 a and 1 more]", members=then) is None


def test_a_proposal_worded_as_it_was_answered_still_needs_its_members() -> None:
    """The text names at most three words, the first folder and a count: other members behind the
    very same text are another group. Where the run the answer was made against cannot be read,
    the text is all there is and the answer stands."""
    stored = "question:r:kelt vorn [20240301 a and 1 more]"
    said = _members("20240301 a", "20240302 b")

    def again(then: frozenset[str] | None) -> AskedAgain[str]:
        return AskedAgain(_words, {stored: "yes"}, members_then=lambda _target, _given: then)

    assert again(said).find(stored, members=said) == "yes"
    assert again(said).find(stored, members=_members("20240301 a", "20240303 c")) is None
    # Members that cannot be told now are not shown to be the same.
    assert again(said).find(stored) is None
    # The run is gone: the text is all there is.
    assert again(None).find(stored, members=_members("20240301 a", "20240303 c")) == "yes"
    assert again(None).find(stored) == "yes"
    # A caller that holds an answer found by its text asks whether it still holds.
    given = "yes"
    assert (
        AskedAgain(_words, {stored: given}, members_then=lambda _target, _given: said).holds(
            stored, given
        )
        is False
    )
    assert again(said).holds("question:r:another [20240301 a and 1 more]", given) is True


def test_two_answers_reading_as_one_question_give_the_latest_by_sequence() -> None:
    """An approval of ``brisa kelt`` and a decline of ``brisal kelt`` are one question once the two
    spellings are one term: the later answer is the one in force, whichever was stored first."""
    approved, declined = (5, "approved"), (9, "declined")
    for held in (
        {"group:brisa kelt": approved, "group:brisal kelt": declined},
        {"group:brisal kelt": declined, "group:brisa kelt": approved},
    ):
        again = AskedAgain(_words, held, rank=lambda found: found[0])
        assert again.find("question:r:a group: brisa_stain kelt_line") == declined
    # Without a rank the one stored first stands (a caller with no sequence has no later).
    first = AskedAgain(_words, {"group:brisa kelt": approved, "group:brisal kelt": declined})
    assert first.find("question:r:a group: brisa_stain kelt_line") == approved
    # And where one of them is worded as the question is, it is that answer, whatever came later.
    exact = AskedAgain(
        _words,
        {"group:brisa_stain kelt_line": approved, "group:brisal kelt_line": declined},
        rank=lambda found: found[0],
    )
    assert exact.find("group:brisa_stain kelt_line") == approved


def test_an_answer_the_wording_has_not_changed_is_found_without_reading_the_words() -> None:
    def never() -> WordMap:
        raise AssertionError("the words are read only where the wording changed")

    again = AskedAgain(never, {"question:r:kelt vorn [x and 1 more]": 1})
    assert again.find("question:r:kelt vorn [x and 1 more]") == 1
    # A target that is no group question is found as written, and only so.
    assert AskedAgain(_words, {"term:kelt": 1}).find("term:kelt_line") is None


def test_under_spellings_a_stored_spelling_stays_itself_and_a_stored_term_is_spelled_out() -> None:
    as_spellings = WordMap(
        [("brisa", "brisa_stain", 3), ("brisal", "brisa_stain", 2)], words_as="spellings"
    )
    again = AskedAgain(
        lambda: as_spellings,
        {
            "question:r:brisa, added to the group kelt vorn": "spelling",
            "question:r:brisa_stain, missing from the group kelt vorn": "term",
        },
    )
    assert again.find("question:r:brisa, added to the group kelt vorn") == "spelling"
    # A word the run writes as a spelling is not read as the other spelling of its term.
    assert again.find("question:r:brisal, added to the group kelt vorn") is None
    # A term the run never writes as itself is every way the run writes it.
    assert again.find("question:r:brisa, missing from the group kelt vorn") == "term"
    assert again.find("question:r:brisal, missing from the group kelt vorn") == "term"


def _deviations(project: Path) -> list[Any]:
    with DashboardServer(project, port=0) as server:
        url = server.url.rstrip("/") + "/api/v1/groups/resolutions"
        with urllib.request.urlopen(url) as response:
            shown = json.loads(response.read())
    return [found for core in shown["cores"] for found in core["deviations"]]


# -- a project, answered and then reworded -----------------------------------------------------


@pytest.mark.parametrize("answered", ["on_the_page", "on_the_question"])
def test_a_proposal_confirmed_in_spellings_still_groups_when_the_run_reads_terms(
    tmp_path: Path, answered: str
) -> None:
    project = _project(tmp_path, "spellings").path
    raised = _the_proposal(project)
    assert raised.startswith("kelt vorn [")
    if answered == "on_the_page":
        _on_the_page(project, PROPOSAL_RULE, raised, "one_thing")
    else:
        _on_the_question(project, PROPOSAL_RULE, raised)
    _rerun(project, "spellings", 4)
    together = _claims(project, "experiments_approved_together")
    assert {path.split("/")[0] for path in together} == {
        "20240301 kelt vorn brisa",
        "20240302 kelt vorn quolt",
    }
    # The words of the question are now terms, and the run is none the wiser about the group.
    _rerun(project, "terms", 5)
    assert _claims(project, "experiments_approved_together") == together
    if answered == "on_the_page":
        # Its core is approved, so the proposal is not made again in any words.
        assert not [r for r in _asked(project, PROPOSAL_RULE) if r.startswith("kelt")]


def test_a_proposal_with_other_members_behind_the_same_first_folder_and_count_is_asked_again(
    tmp_path: Path,
) -> None:
    """The answer was given to ``kelt vorn [20240301 ... and 1 more]``, whose second member was one
    folder; a later run proposes a group worded the same way with the same first folder and count,
    but the second member is another folder. It is another group: nothing is grouped by the old
    answer, and its row is a new question."""
    project = _project(tmp_path, "spellings").path
    _on_the_question(project, PROPOSAL_RULE, _the_proposal(project))
    _rerun(project, "spellings", 4)
    assert _claims(project, "experiments_approved_together")
    pool = tmp_path / "pool"
    (pool / "20240302 kelt vorn quolt").rename(pool / "20240303 kelt vorn quolt")
    Project.open(project).save_settings(Settings(groups=GroupSettings(words_as="terms")))
    run_census(Project.open(project), run_id="run_20260101T000005Z_555555555555", started_at="t")
    run_inference(Project.open(project), run_id="run_20260101T000006Z_666666666666", started_at="t")
    now = _the_proposal(project)
    assert now == "kelt_line vorn [20240301 kelt vorn brisa and 1 more]"
    assert not _claims(project, "experiments_approved_together")
    with open_catalog(project) as catalog:
        rows = page(catalog, project, "classes", answered=True)["rows"]
    (row,) = [found for found in rows if found["target"] == f"question:{PROPOSAL_RULE}:{now}"]
    assert row["in_force"] is None


def test_other_members_behind_the_very_same_text_are_asked_again(tmp_path: Path) -> None:
    """Nothing is reworded: the second member is another folder, behind the same words, first
    folder and count. The old confirmation is not carried to the new group."""
    project = _project(tmp_path, "spellings").path
    _on_the_question(project, PROPOSAL_RULE, _the_proposal(project))
    _rerun(project, "spellings", 4)
    assert _claims(project, "experiments_approved_together")
    pool = tmp_path / "pool"
    (pool / "20240302 kelt vorn quolt").rename(pool / "20240303 kelt vorn quolt")
    run_census(Project.open(project), run_id="run_20260101T000005Z_555555555555", started_at="t")
    run_inference(Project.open(project), run_id="run_20260101T000006Z_666666666666", started_at="t")
    now = _the_proposal(project)
    assert now == "kelt vorn [20240301 kelt vorn brisa and 1 more]"  # the text it was answered by
    assert not _claims(project, "experiments_approved_together")
    with open_catalog(project) as catalog:
        rows = page(catalog, project, "classes", answered=True)["rows"]
    (row,) = [found for found in rows if found["target"] == f"question:{PROPOSAL_RULE}:{now}"]
    assert row["in_force"] is None


def test_an_answer_made_against_a_run_that_is_gone_stands_by_its_text(tmp_path: Path) -> None:
    import shutil

    from ingence.storage.commit import run_path

    project = _project(tmp_path, "spellings").path
    _on_the_question(project, PROPOSAL_RULE, _the_proposal(project))  # against INFERRED
    _rerun(project, "spellings", 4)
    shutil.rmtree(run_path(project, INFERRED))
    _rerun(project, "spellings", 5)
    assert {path.split("/")[0] for path in _claims(project, "experiments_approved_together")} == {
        "20240301 kelt vorn brisa",
        "20240302 kelt vorn quolt",
    }


def test_a_question_answered_in_spellings_shows_its_answer_when_asked_in_terms(
    tmp_path: Path,
) -> None:
    """The row of the question the run raises now names the person who answered the old wording,
    so the question is not shown as new."""
    project = _project(tmp_path, "spellings").path
    _on_the_question(project, PROPOSAL_RULE, _the_proposal(project))
    _rerun(project, "terms", 4)
    now = _the_proposal(project)
    assert now.startswith("kelt_line vorn [")
    with open_catalog(project) as catalog:
        rows = page(catalog, project, "classes", answered=True)["rows"]
    (row,) = [found for found in rows if found["target"] == f"question:{PROPOSAL_RULE}:{now}"]
    assert row["in_force"] is not None
    assert row["in_force"]["actor"] == PERSON
    assert row["in_force"]["kind"] == "confirm"


def test_a_deviation_answered_in_spellings_is_still_answered_on_the_resolutions_page(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, "spellings").path
    _on_the_page(project, PROPOSAL_RULE, _the_proposal(project), "one_thing")
    _rerun(project, "spellings", 4)
    wording = "brisa, added to the group kelt vorn"
    assert wording in _asked(project, DEVIATION_RULE)
    _on_the_question(project, DEVIATION_RULE, wording)

    before = {found["word"]: found["answered"] for found in _deviations(project)}
    assert before == {"brisa": True, "quolt": False}
    _rerun(project, "terms", 5)
    after = {found["word"]: found["answered"] for found in _deviations(project)}
    assert after == {"brisa_stain": True, "quolt": False}
