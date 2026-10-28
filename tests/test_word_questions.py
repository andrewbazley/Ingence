"""Word questions: one question per unbound word, every place it stands gathered beneath it.

Names are invented (``kelt``, ``vorn``, ``zib`` are words nothing binds; ``atp``, ``adp`` and
``tcep`` are the three reagents the shipped Dictionary holds). The alignment and the questions are
tested on slots built by hand; the rule and the page function are tested through a real census and
inference over an invented pool, so the rule and the page are shown to agree. Runs are stamped when
they run.
"""

from __future__ import annotations

import datetime as dt
import itertools
from typing import TYPE_CHECKING

import pytest

from fixtures.revision_4 import REVISION, tune, write_revision
from ingence.api.routes import pages
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.rulebook.predicates.substitution import reason_for
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog
from ingence.storage.layout import RULEBOOK_DIR
from ingence.textword import questions, slots
from ingence.textword.slots import BOUND, UNBOUND, AlignedName, Slot, paradigms, places_of_a_kind

if TYPE_CHECKING:
    from pathlib import Path

_TICKS = itertools.count(1)


def _stamp(tail: str) -> str:
    """A run identifier stamped now, a second apart each time, so no file is born after its run."""
    moment = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=next(_TICKS))
    return f"run_{moment:%Y%m%dT%H%M%S}Z_{tail * 12}"


def _name(entry: str, folder: str, *slots: Slot) -> AlignedName:
    return AlignedName(entry_id=entry, folder_id=folder, slots=slots)


def _word(token: str, state: str | None = None, kind: str | None = None) -> Slot:
    return Slot(token=token, component_type="word", state=state, kind=kind)


def _reagent(token: str) -> Slot:
    return _word(token, BOUND, "reagent")


def _folder(folder: str, second: list[Slot]) -> list[AlignedName]:
    return [
        _name(f"{folder}{index}", folder, _word("subx", BOUND, "sample"), place)
        for index, place in enumerate(second)
    ]


def test_a_place_where_three_reagents_stand_asks_about_the_word_beside_them() -> None:
    names = _folder(
        "f1", [_reagent("atp"), _reagent("adp"), _reagent("tcep"), _word("kelt", UNBOUND)]
    )
    readings = places_of_a_kind(paradigms(names))
    assert len(readings) == 1
    (reading,) = readings
    assert (reading.unbound, reading.alongside, reading.kind) == (
        ("kelt",),
        ("adp", "atp", "tcep"),
        "reagent",
    )
    assert reading.position == 1


def test_a_place_that_never_varies_or_holds_too_few_bound_words_asks_nothing() -> None:
    constant = _folder("f1", [_word("kelt", UNBOUND)] * 4)
    assert places_of_a_kind(paradigms(constant)) == []
    thin = _folder("f2", [_reagent("atp"), _word("kelt", UNBOUND), _word("vorn", UNBOUND)])
    assert places_of_a_kind(paradigms(thin)) == []
    assert len(places_of_a_kind(paradigms(thin), minimum_bound=1)) == 1


def test_bound_words_that_disagree_on_a_kind_ask_nothing() -> None:
    names = _folder(
        "f1",
        [
            _reagent("atp"),
            _word("dna", BOUND, "sample"),
            _word("gfp", BOUND, "sample"),
            _word("kelt", UNBOUND),
        ],
    )
    # Two of three bound words disagree with the leader: two thirds is below three quarters.
    assert places_of_a_kind(paradigms(names)) == []
    assert len(places_of_a_kind(paradigms(names), agreement=0.6)) == 1


def test_a_template_needs_enough_names_and_a_conflict_counts_as_neither() -> None:
    few = _folder("f1", [_reagent("atp"), _reagent("adp")])
    assert paradigms(few, minimum_names=3) == []
    names = _folder(
        "f2", [_reagent("atp"), _reagent("adp"), _word("kelt", None), _word("vorn", UNBOUND)]
    )
    (reading,) = places_of_a_kind(paradigms(names))
    assert reading.unbound == ("vorn",)


def test_a_word_bound_in_some_names_and_unbound_in_others_is_not_a_question() -> None:
    names = _folder(
        "f1", [_reagent("atp"), _reagent("adp"), _word("atp", UNBOUND), _word("kelt", UNBOUND)]
    )
    (reading,) = places_of_a_kind(paradigms(names))
    assert reading.unbound == ("kelt",)


def test_names_of_different_shapes_or_folders_are_not_aligned_together() -> None:
    one = _folder("f1", [_reagent("atp"), _reagent("adp")])
    other = _folder("f2", [_word("kelt", UNBOUND)])
    other_shape = [_name("x", "f1", _word("subx"), Slot(token="1", component_type="number"))]
    found = paradigms([*one, *other, *other_shape], minimum_names=2)
    assert {(place.folder_id, place.position) for place in found} == {("f1", 0), ("f1", 1)}


def test_a_word_raised_in_several_folders_is_one_question_with_every_place_beneath_it() -> None:
    names = [
        *_folder(
            "f1", [_reagent("atp"), _reagent("adp"), _reagent("tcep"), _word("kelt", UNBOUND)]
        ),
        *_folder(
            "f2", [_reagent("atp"), _reagent("adp"), _word("kelt", UNBOUND), _word("vorn", UNBOUND)]
        ),
        *_folder("f3", [_reagent("atp"), _reagent("tcep"), _word("Kelt", UNBOUND)]),
    ]
    asked = questions.word_questions(places_of_a_kind(paradigms(names)))
    assert [q.word for q in asked] == ["kelt", "vorn"]
    kelt = asked[0]
    assert (kelt.places, len(kelt.folders), kelt.kind, kelt.agreeing) == (3, 3, "reagent", 3)
    assert kelt.sentence() == "What is kelt? It stands where atp, adp and tcep stand, in 3 folders."
    reading = questions.engine_reading(kelt, paths={"f1": "plates/one"})
    assert reading["by"] == questions.READER
    # The kind is the engine's to say, as a hint, and in words a person reads.
    assert reading["reading"] == (
        "At all 3 places it stands, most of the words the Dictionary knows beside it are "
        "reagents: a hint, not an answer."
    )
    assert "meaning" not in reading and reading["kind_hint"] == "reagent"
    assert reading["appearances"][0]["folder"] == "plates/one"


def test_a_folder_writing_two_templates_counts_two_places_in_one_folder() -> None:
    plain = _folder("f1", [_reagent("atp"), _reagent("adp"), _word("kelt", UNBOUND)])
    other = [
        _name(f"g{index}", "f1", _word("subx", BOUND, "sample"), Slot("1", "number"), place)
        for index, place in enumerate([_reagent("atp"), _reagent("adp"), _word("kelt", UNBOUND)])
    ]
    (asked,) = questions.word_questions(places_of_a_kind(paradigms([*plain, *other])))
    assert "in 1 folder, at 2 places in names written alike." in asked.sentence()


def test_two_templates_with_the_word_at_the_same_part_are_two_places() -> None:
    """The place record carries its template, so two templates of one folder with the word at
    the same part are two places, and never "leaning 2 of 1"."""
    names: list[AlignedName] = []
    for index, second in enumerate([_reagent("atp"), _reagent("adp"), _word("kelt", UNBOUND)]):
        names.append(_name(f"a{index}", "f1", _word("subx", BOUND, "sample"), second))
        names.append(
            _name(f"b{index}", "f1", _word("subx", BOUND, "sample"), second, Slot("1", "number"))
        )
    (asked,) = questions.word_questions(places_of_a_kind(paradigms(names)))
    reading = questions.engine_reading(asked)
    assert (asked.places, reading["leaning"], reading["of"]) == (2, 2, 2)
    assert len({(a.template, a.position) for a in asked.appearances}) == 2
    assert reading["reading"].startswith("At both places it stands")


def test_places_beyond_the_shown_cap_are_counted_and_not_dropped() -> None:
    names: list[AlignedName] = []
    for index in range(5):
        names += _folder(f"f{index}", [_reagent("atp"), _reagent("adp"), _word("kelt", UNBOUND)])
    (asked,) = questions.word_questions(places_of_a_kind(paradigms(names)), appearances_shown=2)
    assert (asked.places, len(asked.appearances), len(asked.folders)) == (5, 2, 5)
    assert "in 5 folders" in asked.sentence()


def test_the_scoped_definition_reaches_no_further_than_the_folders_it_was_raised_in() -> None:
    assert questions.answer_scope(["plates/day1/raw", "plates/day1/tracks"]) == "plates/day1"
    assert questions.answer_scope(["plates/day1", "plates/day1/raw"]) == "plates/day1"
    assert questions.answer_scope(["plates/day1", "other"]) is None
    assert questions.answer_scope(["only/here"]) == "only/here"
    assert questions.answer_scope([]) is None
    # The top of a folder the project reads, which the census writes '.', names no folder.
    assert questions.answer_scope(["."]) is None
    assert questions.answer_scope([".", "plates/day1"]) is None


def test_a_place_is_said_in_plain_words_with_an_example_name() -> None:
    """The reason agrees with its words, shows the place by an example name rather than "place
    2", and says a kind as a person says it."""
    names = [
        AlignedName(f"n{index}", "f1", (_word("subx", BOUND, "sample"), place), name=f"SubX_{t}")
        for index, (t, place) in enumerate(
            [
                ("atp", _reagent("atp")),
                ("adp", _reagent("adp")),
                ("kelt", _word("kelt", UNBOUND)),
                ("vorn", _word("vorn", UNBOUND)),
            ]
        )
    ]
    (reading,) = places_of_a_kind(paradigms(names))
    assert reason_for(reading) == (
        "kelt and vorn stand where adp and atp stand (the second part of 4 names written alike, "
        "such as SubX_adp), and all 2 words known there are reagents"
    )
    assert slots.plural("file_role") == "file roles" and slots.plural("quality") == "qualities"
    assert slots.known_there(3, 4, "file_role") == "3 of the 4 words known there are file roles"
    assert slots.ordinal(2) == "second" and slots.ordinal(12) == "12th"


def test_the_three_dials_are_written_once_and_read_everywhere() -> None:
    """The rule's defaults live in one place, and the revision declaring the rule, the page
    function and the predicate read them from it."""
    assert questions.DEFAULTS is slots.DEFAULTS
    (declared,) = [rule for rule in REVISION["rules"] if rule["id"] == questions.RULE_ID]  # type: ignore[index]
    assert {item["name"]: item["default"] for item in declared["parameters"]} == dict(  # type: ignore[index]
        slots.DEFAULTS
    )


# --- the rule and the page, through a census of an invented pool --------------------------


def _write(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")


def _template(folder: Path, words: tuple[str, ...]) -> None:
    for word in words:
        for replicate in (1, 2):
            _write(folder / f"SubX_{word}_r{replicate}.txt")


def _inferred(base: Path, roots: list[Path]) -> Project:
    created = Project.create(base / "project", created_at="t", roots=roots)
    write_revision(created.path)
    run_census(created, run_id=_stamp("1"), started_at="t")
    run_inference(created, run_id=_stamp("2"), started_at="t")
    return created


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    base = tmp_path_factory.mktemp("word-questions")
    pool = base / "pool"
    for folder, extra in (("first", "kelt"), ("second", "kelt"), ("third", "vorn")):
        _template(pool / folder, ("atp", "adp", "tcep", extra))
    # The same habit inside an installed program's folder is a program's, and is set aside.
    _template(pool / "Kit.app", ("atp", "adp", "tcep", "quill"))
    # A place holding words nothing binds beside nothing bound is no question.
    for word in ("zib", "quolt", "brisa"):
        _write(pool / "lonely" / f"SubX_{word}_r1.txt")
    return _inferred(base, [pool])


def test_the_rule_raises_each_unbound_word_where_reagents_stand(project: Project) -> None:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT raised, reason FROM findings WHERE rule_id = ?", [questions.RULE_ID]
        ).fetchall()
    raised = sorted(word for values, _ in rows for word in values)
    # quill is raised beneath the program too, and recorded as one a defeater fired on.
    assert raised == ["kelt", "kelt", "quill", "vorn"]
    assert all(
        "where adp, atp and tcep stand (the second part of 8 names written alike, such as "
        "SubX_adp_r1.txt), and all 3 words known there are reagents" in reason
        for _, reason in rows
    )


def test_the_page_function_agrees_with_the_rule_and_gathers_a_word_once(project: Project) -> None:
    with open_catalog(project.path) as catalog:
        page = questions.word_question_page(catalog, project.path)
    assert page["in_force"] is True and page["asked"] is True
    assert page["run_id"].startswith("run_")
    assert [row["word"] for row in page["questions"]] == ["kelt", "vorn"]
    kelt = page["questions"][0]
    assert kelt["places"] == 2 and kelt["target"] == "term:kelt"
    assert kelt["question_target"] == f"question:{questions.RULE_ID}:kelt"
    assert sorted(kelt["folders"]) == ["first", "second"]
    # Two folders at the top of the pool: kept to where it stands is everywhere, so the one
    # choice is everywhere, and the page says why.
    assert [choice["value"] for choice in kelt["choices"]] == [questions.EVERYWHERE]
    assert kelt["where"]["folder"] is None and "everywhere" in kelt["where"]["because"]
    assert kelt["in_force"] is None
    assert page["source"] == "the run's findings"
    # Neither word is one slip from a word the Dictionary knows.
    assert [row["spelling_proposed"] for row in page["questions"]] == [None, None]


def test_a_word_beneath_an_installed_program_is_set_aside_and_not_asked(project: Project) -> None:
    with open_catalog(project.path) as catalog:
        page = questions.word_question_page(catalog, project.path)
        rows = catalog.query(
            "SELECT count(*) FROM findings, unnest(raised) AS u(value) "
            "WHERE rule_id = ? AND u.value = 'quill'",
            [questions.RULE_ID],
        ).fetchone()
    assert rows is not None and rows[0] == 1  # raised, and recorded as one a defeater fired on
    assert "quill" not in [row["word"] for row in page["questions"]]
    assert page["places_set_aside_beneath_programs"] == 1


def _switch(project: Path, number: int, verb: str) -> None:
    """A person's rule change switching the word rule off or on (it is in every project's
    rules)."""
    import yaml

    (project / RULEBOOK_DIR).mkdir(parents=True, exist_ok=True)
    (project / RULEBOOK_DIR / f"project-{number:04d}.yaml").write_text(
        yaml.safe_dump(
            {
                "project_revision": number,
                "summary": f"ines: the word rule {verb}d",
                "changes": [{verb: questions.RULE_ID}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_a_project_without_the_rule_asks_no_word_question(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    _template(pool / "one", ("atp", "adp", "kelt"))
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    _switch(created.path, 1, "disable")
    run_census(created, run_id=_stamp("1"), started_at="t")
    run_inference(created, run_id=_stamp("2"), started_at="t")
    with open_catalog(created.path) as catalog:
        page = questions.word_question_page(catalog, created.path)
    assert page["in_force"] is False and page["questions"] == []


def test_no_word_is_asked_before_the_rule_has_run_and_the_page_says_why(tmp_path: Path) -> None:
    """After a census alone, or with the rule added after the last inference, the page offers no
    questions and says why. Such questions would include a program's words, and every answer to
    them would be refused."""
    pool = tmp_path / "pool"
    _template(pool / "one", ("atp", "adp", "tcep", "kelt"))
    _template(pool / "Kit.app", ("atp", "adp", "tcep", "quill"))
    counted = Project.create(tmp_path / "counted", created_at="t", roots=[pool])
    write_revision(counted.path)
    run_census(counted, run_id=_stamp("1"), started_at="t")
    with open_catalog(counted.path) as catalog:
        page = questions.word_question_page(catalog, counted.path)
    assert (page["asked"], page["questions"]) == (False, [])
    assert page["waiting"].startswith("No inference has run since the names were read")
    added = _added_after_inference(tmp_path, pool)
    with open_catalog(added.path) as catalog:
        page = questions.word_question_page(catalog, added.path)
    assert (page["asked"], page["questions"]) == (False, [])
    assert page["waiting"].startswith("The last inference ran before this rule was in force")


def _added_after_inference(base: Path, pool: Path) -> Project:
    """A project inferred without the rule, which a person then switches on."""
    added = Project.create(base / "added", created_at="t", roots=[pool])
    _switch(added.path, 1, "disable")
    run_census(added, run_id=_stamp("1"), started_at="t")
    run_inference(added, run_id=_stamp("2"), started_at="t")
    _switch(added.path, 2, "enable")
    return added


@pytest.fixture(scope="module")
def placed(tmp_path_factory: pytest.TempPathFactory) -> Project:
    """Words standing in sibling folders, beneath a program, in more folders than a question
    shows places, and beside names the operating system writes."""
    base = tmp_path_factory.mktemp("word-rows")
    pool = base / "pool"
    for folder in ("day1/first", "day1/second", "Kit.app"):
        _template(pool / folder, ("atp", "adp", "tcep", "kelt"))
    for index in range(questions.APPEARANCES_SHOWN + 1):
        _template(pool / "grid" / f"f{index:02d}", ("atp", "adp", "tcep", "vorn"))
    for word in ("atp", "adp", "tcep"):
        _write(pool / "plates" / f"{word}.tif")
    _write(pool / "plates" / "Thumbs.db")
    _write(pool / "plates" / "desktop.ini")
    return _inferred(base, [pool])


def _asked(project: Project, **arguments: object) -> dict[str, dict[str, object]]:
    with open_catalog(project.path) as catalog:
        page = questions.word_question_page(catalog, project.path, **arguments)  # type: ignore[arg-type]
    return {str(row["word"]): row for row in page["questions"]}


def test_the_page_offers_the_folder_the_answer_s_route_would_write(placed: Project) -> None:
    """The page sets the program's folder aside as the route does, so the page never offers a
    folder the route refuses. Both read one function."""
    kelt = _asked(placed)["kelt"]
    assert kelt["folders"] == ["day1/first", "day1/second"]
    # Its places are listed as a person reads folders: by path.
    assert [place["folder"] for place in kelt["appearances"]] == ["day1/first", "day1/second"]  # type: ignore[attr-defined]
    assert kelt["evidence"] == "It stands where adp, atp and tcep stand, in 2 folders."
    assert [choice["value"] for choice in kelt["choices"]] == [  # type: ignore[attr-defined]
        questions.EVERYWHERE,
        questions.WHERE_IT_STANDS,
    ]
    assert kelt["choices"][1]["label"] == "only in day1 and below"  # type: ignore[index]
    with open_catalog(placed.path) as catalog:
        stands = questions.scope_where_it_stands(catalog, questions.RULE_ID, "kelt")
    assert stands.folder == "day1"


def test_a_question_counts_every_folder_beyond_the_places_it_shows(placed: Project) -> None:
    """A word in 13 folders is not read as "in 12 folders (13 places)": its folders are the run's
    own, uncapped, and are not taken from the places shown."""
    vorn = _asked(placed)["vorn"]
    shown = questions.APPEARANCES_SHOWN
    assert f"in {shown + 1} folders." in str(vorn["sentence"])
    assert len(vorn["folders"]) == shown + 1  # type: ignore[arg-type]
    assert (len(vorn["appearances"]), vorn["places_not_shown"]) == (shown, 1)  # type: ignore[arg-type]
    narrow = _asked(placed, appearances_shown=2)["vorn"]
    assert (len(narrow["appearances"]), narrow["places_not_shown"]) == (2, shown - 1)  # type: ignore[arg-type]


def test_names_the_operating_system_writes_are_not_asked_about(placed: Project) -> None:
    """``Thumbs.db`` and ``desktop.ini`` line up with ``atp.tif`` but are not asked about as
    words. Names the run's Dictionary spells as files nobody chose to make are left out, and the
    page says how many words that set aside."""
    with open_catalog(placed.path) as catalog:
        page = questions.word_question_page(catalog, placed.path)
    asked = {row["word"] for row in page["questions"]}
    assert not asked & {"thumbs", "desktop"}
    # ``thumbs`` binds as the plural of the Dictionary's ``thumb`` (a thumbnail), so it is never
    # raised to be set aside. ``desktop`` is the one noise word left out.
    assert page["words_left_out_as_noise"] == 1
    assert any("Thumbs.db" in said for said in page["not_asked"])


def test_the_page_reads_a_run_once(placed: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reading takes seconds on a large census and every page and tab count asks for it, so
    it is kept by the run it read. A different cap is a different reading."""
    read: list[int] = []
    real = questions.names_from_catalog

    def counting(*args: object, **kwargs: object) -> object:
        read.append(1)
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(questions, "names_from_catalog", counting)
    monkeypatch.setattr(questions, "_KEPT", {})
    with open_catalog(placed.path) as catalog:
        first = questions.word_question_page(catalog, placed.path)
        again = questions.word_question_page(catalog, placed.path)
        questions.word_question_page(catalog, placed.path, appearances_shown=3)
    assert first["questions"] == again["questions"]
    assert len(read) == 2


def test_the_page_reads_the_parameters_the_run_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The questions are the run's findings, so their places are read at the parameters the run
    used: a dial turned in the project's Rulebook since changes nothing until the next inference,
    and once the run has evaluated the rule the page reads no Rulebook at all."""
    pool = tmp_path / "pool"
    _template(pool / "one", ("atp", "adp", "tcep", "kelt"))
    created = _inferred(tmp_path, [pool])
    # More than the three reagents standing beside kelt, as a person's rule change.
    tune(created.path, "minimum_bound", 4, rules=(questions.RULE_ID,))

    def unreadable(project: object) -> object:
        message = f"the page read the project's Rulebook at {project}"
        raise AssertionError(message)

    monkeypatch.setattr("ingence.rulebook.project_rulebook", unreadable)
    with open_catalog(created.path) as catalog:
        page = questions.word_question_page(catalog, created.path)
    assert [row["word"] for row in page["questions"]] == ["kelt"]
    assert page["parameters"]["minimum_bound"] == slots.DEFAULTS["minimum_bound"]


# --- the Questions page's rows ------------------------------------------------------------


def _word_rows(project: Project) -> dict[str, dict[str, object]]:
    with open_catalog(project.path) as catalog:
        shown = pages.page(catalog, project.path, "questions")
    return {str(row["raised"]): row for row in shown["rows"] if row["rule_id"] == questions.RULE_ID}


def test_a_word_question_is_one_row_of_its_own_kind_and_never_an_ordinary_one(
    placed: Project,
) -> None:
    """A word question is one row of its own kind, built on the word-question page. The rule's
    questions have no senses, so an ordinary flag row ("Is kelt right about these files?") cannot
    ask them. No program's folder is among a row's places, and a word in names the operating
    system writes is not a row at all."""
    rows = _word_rows(placed)
    assert sorted(rows) == ["kelt", "vorn"]
    kelt = rows["kelt"]
    assert kelt["word_question"] is True and kelt["about_a_word"] is True
    assert str(kelt["sentence"]).startswith("What is kelt? It stands where adp, atp and tcep")
    assert kelt["reading"]["by"] == questions.READER  # type: ignore[index]
    assert kelt["where_folders"] == ["day1/first", "day1/second"]
    assert {place["folder"] for place in kelt["appearances"]} == {"day1/first", "day1/second"}  # type: ignore[attr-defined]
    assert [word["word"] for word in kelt["words"]] == ["not_a_word", "cannot_say"]  # type: ignore[attr-defined]
    assert kelt["target"] == f"question:{questions.RULE_ID}:kelt"
    assert kelt["folders"] == ["day1"]
    assert kelt["choices"][1]["label"] == "only in day1 and below"  # type: ignore[index]


def test_the_questions_page_says_why_no_word_is_asked_yet(tmp_path: Path) -> None:
    """The rule added after the last inference, on the page a person reads."""
    pool = tmp_path / "pool"
    _template(pool / "one", ("atp", "adp", "tcep", "kelt"))
    added = _added_after_inference(tmp_path, pool)
    with open_catalog(added.path) as catalog:
        shown = pages.page(catalog, added.path, "questions")
    assert shown["word_questions"]["waiting"].startswith(
        "The last inference ran before this rule was in force"
    )


# -- a slip of a known word ------------------------------------------------------------------------


@pytest.fixture(scope="module")
def slipped(tmp_path_factory: pytest.TempPathFactory) -> Project:
    """``hoachsd`` (two letters changed) standing where three reagents stand, in two folders.

    A slip the resolver reads as the word itself (the same letters swapped) is bound as that
    word, marked corrected. Two letters changed, the last among them, is only offered.
    """
    base = tmp_path_factory.mktemp("word-slips")
    pool = base / "pool"
    for folder in ("first", "second"):
        _template(pool / folder, ("atp", "adp", "tcep", "hoachsd"))
    return _inferred(base, [pool])


def test_a_slip_of_a_known_word_shows_its_candidates_beside_the_question(slipped: Project) -> None:
    rows = _asked(slipped)
    hoachsd = rows["hoachsd"]
    assert [found["spelling"] for found in hoachsd["spellings_proposed"]] == ["hoechst"]  # type: ignore[index]
    assert hoachsd["did_you_mean"] == "Perhaps a slip for hoechst, a word the Dictionary knows."
    # Offered, never bound: the word is still one nothing binds.
    with open_catalog(slipped.path) as catalog:
        (status,) = catalog.query(
            "SELECT b.resolution_status FROM dictionary_bindings b "
            "JOIN components c ON c.component_id = b.component_id WHERE c.raw_value = 'hoachsd' "
            "LIMIT 1"
        ).fetchone()
    assert status == "examined_no_evidence"


def test_a_persons_answer_binds_a_slip_on_the_next_rebind(slipped: Project) -> None:
    from ingence.api.routes.decide import define_word
    from ingence.dictionary.store import DictionaryStore

    stain = DictionaryStore.builtin().look_up("hoechst")
    assert stain is not None
    with open_catalog(slipped.path) as catalog:
        define_word(
            catalog,
            slipped.path,
            actor="kelt",
            occurred_at="2026-01-01T00:00:09Z",
            word="hoachsd",
            answer=stain.sense.term,
            theme_kind=stain.sense.theme_kind,
        )
    run_census(Project.open(slipped.path), run_id=_stamp("3"), started_at="t", rebind=True)
    with open_catalog(slipped.path) as catalog:
        found = catalog.query(
            "SELECT DISTINCT b.resolution_status, b.term FROM dictionary_bindings b "
            "JOIN components c ON c.component_id = b.component_id WHERE c.raw_value = 'hoachsd'"
        ).fetchall()
    assert found == [("resolved", stain.sense.term)]
