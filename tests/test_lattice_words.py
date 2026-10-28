"""The lattice reads a bound word as its Dictionary term (``groups.words_as``).

The class: teaching the engine that two spellings are one thing sharpens the groups, because
the groups are made of words and a bound word enters the lattice as its Dictionary term. An
unbound word stays its spelling; ``spellings`` keeps the lattice as it was; and a person's stored
approval of a group is read in whichever words the lattice holds, so switching loses none.

Built on invented words: five dated experiments share ``kelt vorn zib``; three also write
``brisa`` and two write ``brisal``, which a person says are one thing (``brisa_stain``).
"""

from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any, Literal

import pytest

from fixtures.revision_4 import write_revision
from ingence.api.server import DashboardServer
from ingence.core.ids import PROJECT_SCOPE
from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.core.settings import GroupSettings, Settings
from ingence.grouping.lattice_words import WordMap, lattice_word
from ingence.infer.run import run_inference
from ingence.record.log import append_event
from ingence.scan.census import run_census
from ingence.workspace.rerun import Step, needs_of_settings

if TYPE_CHECKING:
    from pathlib import Path

DAYS = (
    ("20240301", "kelt vorn zib brisa"),
    ("20240302", "kelt vorn zib brisa"),
    ("20240303", "kelt vorn zib brisa"),
    ("20240304", "kelt vorn zib brisal"),
    ("20240305", "kelt vorn zib brisal"),
)
CENSUS = "run_20260101T000001Z_111111111111"
REBOUND = "run_20260101T000002Z_222222222222"
INFERRED = "run_20260101T000003Z_333333333333"


def _pool(base: Path) -> Path:
    pool = base / "pool"
    for day, words in DAYS:
        for number in (1, 2):
            path = pool / f"{day} {words}" / f"{words.replace(' ', '_')}_{number:02d}.nd2"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")
    return pool


def _say(project: Path, target: str, kind: RecordEventKind, payload: dict[str, Any]) -> None:
    append_event(
        project,
        kind=kind,
        actor="dana",
        occurred_at="2026-01-02T00:00:00Z",
        target=target,
        against_run_id=CENSUS,
        payload=payload,
        scope=PROJECT_SCOPE,
    )


def _teach_one_thing(project: Path) -> None:
    """A person says ``brisa`` and ``brisal`` are one thing, ``brisa_stain`` (a reagent)."""
    for word, kind in (("brisa", RecordEventKind.DEFINE), ("brisal", RecordEventKind.ASSIGN)):
        _say(
            project,
            f"term:{word}",
            kind,
            {"raised": word, "answer": "brisa_stain", "theme_kind": "reagent"},
        )


def _build(base: Path, *, words_as: Literal["terms", "spellings"], taught: bool = True) -> Path:
    """A project reading the five experiments, the two spellings taught as one, inferred."""
    project = Project.create(
        base / "project",
        created_at="t",
        roots=[_pool(base)],
        settings=Settings(groups=GroupSettings(words_as=words_as)),
    )
    write_revision(project.path)
    run_census(project, run_id=CENSUS, started_at="2026-01-01T00:00:01Z")
    if taught:
        _teach_one_thing(project.path)
        run_census(
            Project.open(project.path),
            run_id=REBOUND,
            started_at="2026-01-01T00:00:02Z",
            rebind=True,
        )
    run_inference(Project.open(project.path), run_id=INFERRED, started_at="2026-01-01T00:00:03Z")
    return project.path


@pytest.fixture(scope="module")
def reading_terms(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _build(tmp_path_factory.mktemp("lattice_terms"), words_as="terms")


@pytest.fixture(scope="module")
def reading_spellings(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _build(tmp_path_factory.mktemp("lattice_spellings"), words_as="spellings")


@pytest.fixture
def terms_copy(reading_terms: Path, tmp_path: Path) -> Path:
    shutil.copytree(reading_terms, tmp_path / "project")
    return tmp_path / "project"


@pytest.fixture
def spellings_copy(reading_spellings: Path, tmp_path: Path) -> Path:
    shutil.copytree(reading_spellings, tmp_path / "project")
    return tmp_path / "project"


def _get(server: DashboardServer, route: str) -> Any:
    with urllib.request.urlopen(server.url.rstrip("/") + route) as response:
        return json.loads(response.read())


def _sets(project: Path) -> dict[str, dict[str, Any]]:
    with DashboardServer(project, port=0) as server:
        shown = _get(server, "/api/v1/groups/lattice")
    return {" ".join(found["terms"]): found for found in shown["sets"]}


# -- the setting and the one mapping -----------------------------------------------------------


def test_words_as_defaults_to_terms_and_a_change_waits_for_the_rules() -> None:
    assert Settings().groups.words_as == "terms"
    assert Settings(groups=GroupSettings(words_as="spellings")).groups.words_as == "spellings"
    with pytest.raises(ValueError, match="words_as"):
        GroupSettings(words_as="stems")
    assert needs_of_settings(["groups.words_as"]) == (Step.INFERENCE,)


def test_the_mapping_function_reads_a_bound_word_as_its_term_and_the_rest_as_written() -> None:
    assert lattice_word("Brisal", "Brisa_Stain") == "brisa_stain"
    assert lattice_word("Brisal", "Brisa_Stain", words_as="spellings") == "brisal"
    # No sense holds it (or two claim it level): it stays its spelling, whatever is read.
    assert lattice_word("Quolt", None) == "quolt"
    assert lattice_word("Quolt", "", words_as="terms") == "quolt"


def test_a_stored_answer_is_read_in_the_words_the_lattice_holds() -> None:
    rows = [("brisa", "brisa_stain", 3), ("brisal", "brisa_stain", 2), ("kelt", "kelt_line", 1)]
    as_terms = WordMap(rows, words_as="terms")
    assert as_terms.answer(["Kelt", "brisal", "zib"]) == [("brisa_stain", "kelt_line", "zib")]
    # An answer already in terms is unchanged.
    assert as_terms.answer(["brisa_stain", "zib"]) == [("brisa_stain", "zib")]
    as_spellings = WordMap(rows, words_as="spellings")
    # A term is spelled out as each way the run writes it, one answer for each.
    assert as_spellings.answer(["brisa_stain", "zib"]) == [("brisa", "zib"), ("brisal", "zib")]
    # A spelling stays as written, and a word the run does not bind stays.
    assert as_spellings.answer(["brisal", "zib"]) == [("brisal", "zib")]
    assert as_spellings.spellings("brisa_stain") == ("brisa", "brisal")


def test_a_word_the_run_writes_as_a_spelling_stays_itself_even_when_it_is_a_terms_name() -> None:
    """A reagent term written as its own name and as a short form: an answer naming the name is
    about that spelling alone, never about the short form too (it would approve a core nobody
    approved)."""
    rows = [("gfp", "gfp", 3), ("egfp", "gfp", 2), ("dapi", "dapi", 1)]
    as_spellings = WordMap(rows, words_as="spellings")
    assert as_spellings.answer(["dapi", "gfp"]) == [("dapi", "gfp")]
    assert as_spellings.answer(["dapi", "egfp"]) == [("dapi", "egfp")]
    # Under terms, both spellings are the term: the two stored answers are one reading.
    as_terms = WordMap(rows, words_as="terms")
    assert (
        as_terms.answer(["dapi", "gfp"]) == as_terms.answer(["dapi", "egfp"]) == [("dapi", "gfp")]
    )
    # A term the run never writes as itself is still spelled out, each way once.
    written_only_short = WordMap([("egfp", "gfp", 2), ("gfp1", "gfp", 1)], words_as="spellings")
    assert written_only_short.answer(["gfp"]) == [("egfp",), ("gfp1",)]


def test_a_word_the_run_writes_stays_itself_whether_or_not_anything_binds_it() -> None:
    """A term's own name that the run writes but leaves unbound (it is contested, say), beside
    other spellings that bind to the term: the approval's own word is kept, never replaced by those
    spellings (it would give the approval another core)."""
    rows = [("egfp", "gfp", 2), ("gfp1", "gfp", 1)]
    as_spellings = WordMap(rows, words_as="spellings", written=["gfp", "egfp", "gfp1", "dapi"])
    assert as_spellings.answer(["dapi", "gfp"]) == [("dapi", "gfp")]
    # A term the run never writes at all is still spelled out, each way once.
    assert WordMap(rows, words_as="spellings", written=["egfp"]).answer(["gfp"]) == [
        ("egfp",),
        ("gfp1",),
    ]


def test_the_runs_written_words_are_read_from_its_names_bound_or_not(tmp_path: Path) -> None:
    """``kelt`` is a term here (taught for two other spellings) and a word the names write that
    nothing binds: an approval naming it keeps it, under spellings."""
    from ingence.storage.catalog import open_catalog

    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[_pool(tmp_path)],
        settings=Settings(groups=GroupSettings(words_as="spellings")),
    )
    write_revision(project.path)
    run_census(project, run_id=CENSUS, started_at="2026-01-01T00:00:01Z")
    for word, kind in (("brisa", RecordEventKind.DEFINE), ("brisal", RecordEventKind.ASSIGN)):
        _say(
            project.path,
            f"term:{word}",
            kind,
            {"raised": word, "answer": "kelt", "theme_kind": "reagent"},
        )
    run_census(
        Project.open(project.path), run_id=REBOUND, started_at="2026-01-01T00:00:02Z", rebind=True
    )
    with open_catalog(project.path) as catalog:
        reading = WordMap.of_run(catalog, words_as="spellings")
    assert reading.terms() == {"brisa": "kelt", "brisal": "kelt"}
    assert reading.answer(["kelt", "vorn"]) == [("kelt", "vorn")]


def test_the_ways_a_term_is_spelled_out_are_bounded() -> None:
    rows = [(f"{term}{number}", term, 1) for term in ("aa", "bb", "cc") for number in range(5)]
    found = WordMap(rows, words_as="spellings").answer(["aa", "bb", "cc"])
    assert len(found) == len(set(found)) == 16  # 125 ways, cut at the bound


# -- the lattice, over a project ---------------------------------------------------------------


def test_two_spellings_a_person_called_one_thing_are_one_word_to_the_lattice(
    reading_terms: Path,
) -> None:
    sets = _sets(reading_terms)
    assert "brisa_stain kelt vorn zib" in sets
    assert len(sets["brisa_stain kelt vorn zib"]["experiments"]) == 5
    # Neither spelling is a word of the lattice any more, and the words no sense holds stay as
    # written: the five experiments hold one closed set, and it holds both.
    assert set(sets) == {"brisa_stain kelt vorn zib"}


def test_a_set_made_of_terms_is_shown_as_the_files_wrote_its_words(reading_terms: Path) -> None:
    """The key and the target are the terms; the page shows ``brisa``, the spelling most files
    hold, never the Dictionary's name for the sense."""
    found = _sets(reading_terms)["brisa_stain kelt vorn zib"]
    assert found["terms"] == ["brisa_stain", "kelt", "vorn", "zib"]
    assert found["terms_shown"] == ["brisa", "kelt", "vorn", "zib"]
    assert found["target"] == "group:brisa_stain kelt vorn zib"


def test_spellings_keeps_the_lattice_as_the_files_wrote_it(reading_spellings: Path) -> None:
    sets = _sets(reading_spellings)
    assert set(sets) >= {"kelt vorn zib", "brisa kelt vorn zib", "brisal kelt vorn zib"}
    assert len(sets["brisa kelt vorn zib"]["experiments"]) == 3
    assert len(sets["brisal kelt vorn zib"]["experiments"]) == 2
    assert not any("brisa_stain" in name for name in sets)


def test_with_nothing_taught_terms_and_spellings_make_the_same_sets(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The words the Dictionary binds to nothing are spellings either way: no sense, no term."""
    one = _build(tmp_path_factory.mktemp("untaught_terms"), words_as="terms", taught=False)
    two = _build(tmp_path_factory.mktemp("untaught_spellings"), words_as="spellings", taught=False)
    assert {name: found["experiments"] for name, found in _sets(one).items()} == {
        name: found["experiments"] for name, found in _sets(two).items()
    }


# -- a stored approval survives the switch -----------------------------------------------------


def _approve(project: Path, words: str) -> None:
    _say(project, f"group:{words}", RecordEventKind.CONFIRM, {"words": words})


def test_under_spellings_an_approval_and_a_decline_name_only_the_cores_they_say(
    tmp_path: Path,
) -> None:
    """The term is spelled as its own name (``brisa``) and by a short form (``brisal``): the
    approval of the first core and the decline of the second are about those two cores only."""
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[_pool(tmp_path)],
        settings=Settings(groups=GroupSettings(words_as="spellings")),
    )
    write_revision(project.path)
    run_census(project, run_id=CENSUS, started_at="2026-01-01T00:00:01Z")
    for word, kind in (("brisa", RecordEventKind.DEFINE), ("brisal", RecordEventKind.ASSIGN)):
        _say(
            project.path,
            f"term:{word}",
            kind,
            {"raised": word, "answer": "brisa", "theme_kind": "reagent"},
        )
    run_census(
        Project.open(project.path), run_id=REBOUND, started_at="2026-01-01T00:00:02Z", rebind=True
    )
    _approve(project.path, "brisa kelt vorn zib")
    _say(project.path, "group:brisal kelt vorn zib", RecordEventKind.REJECT, {})
    run_inference(Project.open(project.path), run_id=INFERRED, started_at="2026-01-01T00:00:03Z")
    sets = _sets(project.path)
    assert sets["brisa kelt vorn zib"]["state"] == "approved"
    assert sets["brisal kelt vorn zib"]["state"] == "declined"


def _read_with(project: Path, words_as: Literal["terms", "spellings"], number: int) -> None:
    """The setting changed, and a run that reads it: the page follows the run, not the file."""
    Project.open(project).save_settings(Settings(groups=GroupSettings(words_as=words_as)))
    run_inference(
        Project.open(project),
        run_id=f"run_20260101T00000{number}Z_{str(number) * 12}",
        started_at=f"2026-01-01T00:00:0{number}Z",
    )


def test_an_approval_given_on_spellings_finds_its_core_when_terms_are_read(
    spellings_copy: Path,
) -> None:
    # Approved while the lattice held spellings...
    _approve(spellings_copy, "brisa kelt vorn zib")
    assert _sets(spellings_copy)["brisa kelt vorn zib"]["state"] == "approved"
    # ...and the setting then reads terms. Nothing changes until a run has read it: the page
    # shows the run's groups, in the words the run asked them in.
    Project.open(spellings_copy).save_settings(Settings(groups=GroupSettings(words_as="terms")))
    before = _sets(spellings_copy)
    assert before["brisa kelt vorn zib"]["state"] == "approved"
    assert "brisa_stain kelt vorn zib" not in before
    # A run that reads terms: the same approval is the core it always was.
    _read_with(spellings_copy, "terms", 4)
    sets = _sets(spellings_copy)
    assert sets["brisa_stain kelt vorn zib"]["state"] == "approved"
    assert sets["brisa_stain kelt vorn zib"]["decided_by"] == "dana"
    assert "brisa kelt vorn zib" not in sets


def test_an_approval_given_on_terms_finds_its_cores_when_spellings_are_read(
    terms_copy: Path,
) -> None:
    _approve(terms_copy, "brisa_stain kelt vorn zib")
    assert _sets(terms_copy)["brisa_stain kelt vorn zib"]["state"] == "approved"
    Project.open(terms_copy).save_settings(Settings(groups=GroupSettings(words_as="spellings")))
    assert "brisa kelt vorn zib" not in _sets(terms_copy)  # still the run's terms until Re-run
    _read_with(terms_copy, "spellings", 4)
    sets = _sets(terms_copy)
    # The term is spelled out as the two ways the files write it; each is approved.
    assert sets["brisa kelt vorn zib"]["state"] == "approved"
    assert sets["brisal kelt vorn zib"]["state"] == "approved"


def test_the_resolutions_page_follows_the_run_and_not_the_file(spellings_copy: Path) -> None:
    _approve(spellings_copy, "brisa kelt vorn zib")
    _read_with(spellings_copy, "spellings", 4)
    with DashboardServer(spellings_copy, port=0) as server:
        before = _get(server, "/api/v1/groups/resolutions")
    Project.open(spellings_copy).save_settings(Settings(groups=GroupSettings(words_as="terms")))
    with DashboardServer(spellings_copy, port=0) as server:
        unchanged = _get(server, "/api/v1/groups/resolutions")
    assert unchanged == before
    assert {group["key"] for core in before["cores"] for group in core["across"]} == {
        "brisa kelt vorn zib"
    }


def _run_directory(project: Path) -> Path:
    from ingence.storage.commit import resolve_current_run

    found = resolve_current_run(project)
    assert found is not None
    return found


def _status(server: DashboardServer, route: str) -> tuple[int, Any]:
    try:
        with urllib.request.urlopen(server.url.rstrip("/") + route) as response:
            return int(response.status), json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            return int(error.code), json.loads(error.read())
        finally:
            error.close()


def test_a_run_says_which_words_it_made_its_groups_of(
    reading_terms: Path, reading_spellings: Path
) -> None:
    """In a file of the run's own directory, which inference writes and the manifest lists."""
    from ingence.grouping.lattice_words import WORDS_AS_FILE, reading_in, run_words_as
    from ingence.storage.catalog import open_catalog

    for project, said in ((reading_terms, "terms"), (reading_spellings, "spellings")):
        directory = _run_directory(project)
        assert json.loads((directory / WORDS_AS_FILE).read_text()) == {"words_as": said}
        assert reading_in(directory) == said
        with open_catalog(project) as catalog:
            assert run_words_as(catalog) == said
        manifest = json.loads((directory / "manifest.json").read_text())
        assert WORDS_AS_FILE in {item["path"] for item in manifest["files"]}
    # Anything that is no run's catalog reads spellings, as a run did before terms existed.
    assert run_words_as(object()) == "spellings"


def test_the_reading_does_not_depend_on_the_journal(tmp_path: Path) -> None:
    """A stable build switches the journal off; a terms run is still read as terms."""
    from ingence.core.settings import JournalSettings
    from ingence.grouping.lattice_words import reading_in

    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[_pool(tmp_path)],
        settings=Settings(journal=JournalSettings(enabled=False)),
    )
    write_revision(project.path)
    run_census(project, run_id=CENSUS, started_at="2026-01-01T00:00:01Z")
    run_inference(Project.open(project.path), run_id=REBOUND, started_at="2026-01-01T00:00:02Z")
    assert reading_in(_run_directory(project.path)) == "terms"


def test_a_dive_after_a_terms_inference_still_reads_terms(terms_copy: Path) -> None:
    """A dive carries the inference's claims into a run of its own, with a journal written afresh:
    the run says what its claims were made of, and the groups pages follow it."""
    from ingence.grouping.lattice_words import WORDS_AS_FILE, reading_in
    from ingence.scan.deepen import run_deepen

    _approve(terms_copy, "brisa_stain kelt vorn zib")
    run_inference(
        Project.open(terms_copy), run_id="run_20260101T000004Z_444444444444", started_at="t"
    )
    before = _run_directory(terms_copy)
    run_deepen(
        Project.open(terms_copy),
        run_id="run_20260101T000005Z_555555555555",
        started_at="t",
        budget_seconds=60.0,
    )
    after = _run_directory(terms_copy)
    assert after != before
    assert json.loads((after / WORDS_AS_FILE).read_text()) == {"words_as": "terms"}
    assert reading_in(after) == "terms"
    sets = _sets(terms_copy)
    assert sets["brisa_stain kelt vorn zib"]["state"] == "approved"


def _as_an_earlier_engine_made_it(directory: Path) -> None:
    """The run as an engine before terms left it: no file, and the lattice's rules at the versions
    they had (one with a suffix, as a rule's version may carry)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from ingence.grouping.lattice_words import RULES_BEFORE_TERMS, WORDS_AS_FILE

    (directory / WORDS_AS_FILE).unlink()
    for path in sorted((directory / "rules").glob("*.parquet")):
        table = pq.read_table(path)
        versions = [
            f"{RULES_BEFORE_TERMS[rule]}+d1" if rule in RULES_BEFORE_TERMS else version
            for rule, version in zip(
                table.column("rule_id").to_pylist(),
                table.column("version").to_pylist(),
                strict=True,
            )
        ]
        at = table.schema.get_field_index("version")
        field = table.schema.field("version")
        pq.write_table(table.set_column(at, field, pa.array(versions, type=field.type)), path)


def test_a_run_an_earlier_engine_made_reads_spellings_and_its_dive_says_so(tmp_path: Path) -> None:
    from ingence.grouping.lattice_words import WORDS_AS_FILE, reading_in
    from ingence.scan.deepen import run_deepen

    project = _build(tmp_path, words_as="terms")
    directory = _run_directory(project)
    _as_an_earlier_engine_made_it(directory)
    assert reading_in(directory) == "spellings"
    # A dive of this version carries claims that engine made, and says what they were made of.
    run_deepen(
        Project.open(project),
        run_id="run_20260101T000005Z_555555555555",
        started_at="t",
        budget_seconds=60.0,
    )
    dived = _run_directory(project)
    assert dived != directory
    assert json.loads((dived / WORDS_AS_FILE).read_text()) == {"words_as": "spellings"}


def test_a_run_whose_rules_read_terms_and_that_says_nothing_is_not_guessed_at(
    tmp_path: Path,
) -> None:
    """The pages say they cannot tell, in words, where the run holds groups."""
    from ingence.grouping.lattice_words import WORDS_AS_FILE, reading_in, words_said_by_run
    from ingence.scan.deepen import run_deepen
    from ingence.storage.catalog import open_catalog

    project = _build(tmp_path, words_as="terms")
    _approve(project, "brisa_stain kelt vorn zib")
    run_inference(Project.open(project), run_id="run_20260101T000004Z_444444444444", started_at="t")
    directory = _run_directory(project)
    (directory / WORDS_AS_FILE).unlink()
    assert reading_in(directory) is None
    with open_catalog(project) as catalog:
        assert words_said_by_run(catalog) is None
    with DashboardServer(project, port=0) as server:
        for route in ("/api/v1/groups/lattice", "/api/v1/groups/resolutions"):
            status, body = _status(server, route)
            assert status == 409, (route, body)
            assert body["error"]["code"] == "words_not_said"
            assert "does not say which words" in body["error"]["message"]
    # A dive of it cannot say either: nothing is written, and nothing is guessed.
    run_deepen(
        Project.open(project),
        run_id="run_20260101T000005Z_555555555555",
        started_at="t",
        budget_seconds=60.0,
    )
    assert not (_run_directory(project) / WORDS_AS_FILE).exists()


def test_a_run_that_holds_no_groups_needs_no_saying(tmp_path: Path) -> None:
    """A census alone makes no group claims and holds none of the lattice's rules: it cannot be
    told, and there is nothing to misread, so the pages serve it as they always did."""
    from ingence.grouping.lattice_words import words_said_by_run
    from ingence.storage.catalog import open_catalog

    project = Project.create(tmp_path / "project", created_at="t", roots=[_pool(tmp_path)])
    write_revision(project.path)
    run_census(project, run_id=CENSUS, started_at="2026-01-01T00:00:01Z")
    with open_catalog(project.path) as catalog:
        assert words_said_by_run(catalog) is None
    with DashboardServer(project.path, port=0) as server:
        for route in ("/api/v1/groups/lattice", "/api/v1/groups/resolutions"):
            status, body = _status(server, route)
            assert status == 200, (route, body)


def test_a_fetch_after_inference_carries_the_reading_with_the_claims(tmp_path: Path) -> None:
    """A listing root's fetch publishes a run over the current one, claims and rules carried: the
    file that says what the claims were made of goes with them."""
    from ingence.core.outcomes import IdentityScope
    from ingence.grouping.lattice_words import WORDS_AS_FILE, reading_in
    from ingence.listing.remote import RemoteReading, publish_remote_readings
    from ingence.storage.catalog import open_catalog

    project = _build(tmp_path, words_as="terms")
    with open_catalog(project) as catalog:
        (entry_id,) = catalog.query(
            "SELECT entry_id FROM entries WHERE name = 'kelt_vorn_zib_brisa_01.nd2' LIMIT 1"
        ).fetchone() or (None,)
    before = _run_directory(project)
    reading = RemoteReading(str(entry_id), "blake3_whole", "1", IdentityScope.WHOLE, "f" * 64, 3)
    publish_remote_readings(
        Project.open(project), [reading], run_id="run_20260101T000005Z_555555555555", started_at="t"
    )
    fetched = _run_directory(project)
    assert fetched != before
    assert json.loads((fetched / WORDS_AS_FILE).read_text()) == {"words_as": "terms"}
    assert reading_in(fetched) == "terms"
    assert _sets(project)["brisa_stain kelt vorn zib"]["experiments"]


def test_only_the_one_helper_copies_a_runs_tables_into_the_next_run() -> None:
    """A pass that copied the tables itself could miss what describes them: the passes that carry
    claims forward all call ``carry_run``."""
    import re
    from pathlib import Path as FilePath

    source = FilePath(__file__).resolve().parent.parent / "src" / "ingence"
    copiers = sorted(
        path.relative_to(source).as_posix()
        for path in source.rglob("*.py")
        if re.search(r"copytree\([^)]*spec\.directory", path.read_text(encoding="utf-8"))
    )
    assert copiers == ["storage/carry.py"]
    for caller in ("infer/run.py", "scan/deepen.py", "listing/remote.py"):
        assert "carry_run(" in (source / caller).read_text(encoding="utf-8"), caller


#: The columns of the run table and the fields of its record as the last release wrote them: this
#: version adds none, so a build that came earlier reads a project this one has run as before.
#: Schema 1.24 (0.4.0) adds three, and a build before it refuses the project by its schema.
RUN_COLUMNS_BEFORE = [
    "schema_version",
    "run_id",
    "parent_run_id",
    "started_at",
    "ended_at",
    "engine_version",
    "id_scheme_version",
    "profile",
    "deepening_budget_granted_seconds",
    "deepening_budget_spent_seconds",
    # Schema 1.24: how long a run took, the work it did and the workers it used.
    "wall_seconds",
    "work_seconds",
    "workers",
    "extraction_ceiling_seconds_json",
    "rulebook_hash",
    "dictionary_hash",
    "config_hash",
    "components_json",
    "seeds_json",
    "library_versions_json",
    "platform_json",
    "counts_json",
    "reuse_decisions_json",
    "recognised_json",
    "excluded_json",
    "mentions_json",
    "packs_json",
    "resolutions_json",
    "rebind_spellings_json",
    "engine_commit",
    "record_watermark",
    "job_id",
    "hash_mode",
    "hash_mode_set_by",
    "date_years_json",
    "case_reading_json",
    "warnings_json",
    "streamed",
    "streaming_reason",
    "invariants_json",
    "published",
    "skip_remote_filesystems",
    "skip_remote_filesystems_set_by",
    "warnings_tallied",
    "normalizer_version",
]


def test_the_run_table_has_the_columns_it_had_before_this_version(reading_terms: Path) -> None:
    from ingence.core.schema import RunRecord
    from ingence.storage.catalog import open_catalog
    from ingence.storage.layout import table_for

    assert table_for(RunRecord).schema.names == RUN_COLUMNS_BEFORE
    assert "words_as" not in RunRecord.model_fields
    # And a run this version made stores exactly those columns.
    with open_catalog(reading_terms) as catalog:
        stored = [column[0] for column in catalog.query("SELECT * FROM run").description]
    assert stored == RUN_COLUMNS_BEFORE


def test_inference_resolves_a_group_from_an_approval_given_on_spellings(
    spellings_copy: Path,
) -> None:
    """The run itself, not only the page: the approval's core is found among the terms."""
    _approve(spellings_copy, "brisa kelt vorn zib")
    Project.open(spellings_copy).save_settings(Settings(groups=GroupSettings(words_as="terms")))
    run_inference(
        Project.open(spellings_copy),
        run_id="run_20260101T000004Z_444444444444",
        started_at="2026-01-01T00:00:04Z",
    )
    with DashboardServer(spellings_copy, port=0) as server:
        shown = _get(server, "/api/v1/groups/resolutions")
    (core,) = shown["cores"]
    # The core is shown as it was stored (the person's own words), and it resolved the five
    # experiments that hold it as a term.
    assert core["core"] == ["brisa", "kelt", "vorn", "zib"]
    assert core["target"] == "group:brisa kelt vorn zib"
    assert {group["key"] for group in core["across"]} == {"brisa_stain kelt vorn zib"}
    assert core["across"][0]["members"] > 0


def test_a_search_for_either_spelling_finds_the_group_made_of_their_term(terms_copy: Path) -> None:
    """The approved group's reason names the words its key holds; a person types the spelling
    their files write, and the search reads it as the term the run binds it to."""
    from ingence.api.routes import search as routes
    from ingence.storage.catalog import open_catalog

    _approve(terms_copy, "brisa_stain kelt vorn zib")
    run_inference(
        Project.open(terms_copy),
        run_id="run_20260101T000005Z_555555555555",
        started_at="2026-01-01T00:00:05Z",
    )
    for typed in ("brisa", "brisal"):
        with open_catalog(terms_copy) as catalog:
            found = routes.search(catalog, terms_copy, text=typed, limit=50)
        reasons = {str(line.get("where")) for card in found["results"] for line in card["why"]}
        assert "approved group {brisa_stain kelt vorn zib}" in reasons, (typed, reasons)
