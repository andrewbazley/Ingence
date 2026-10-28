"""The comparison reads the lattice's words and shows them as the files wrote them.

``groups.words_as`` decides what the lattice is made of: a bound word as its Dictionary term
(``terms``), or every word as the spelling a name wrote (``spellings``). The compare route reads
the lattice through the one reading the lattice route and the resolutions route use, so a set
compared is a set the Groupings page shows, in the same words. It finds a set named in the other
reading by the run's own map of words, and says each word as the run's files wrote it
(``shown_as`` and the label), where the run read terms.

Built on invented words: seven dated experiments share ``dror trel``; five of them also write
``mond``, three of those ``quen`` and two ``quenal``, which a person says are one thing
(``quen_stain``); the other two write ``zath``.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING, Any, Literal

import pytest

from fixtures.revision_4 import write_revision
from ingence.api.server import DashboardServer
from ingence.core.ids import PROJECT_SCOPE
from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.core.settings import GroupSettings, Settings
from ingence.infer.run import run_inference
from ingence.record.log import append_event
from ingence.scan.census import run_census

if TYPE_CHECKING:
    from pathlib import Path

DAYS = (
    ("20240301", "dror trel mond quen"),
    ("20240302", "dror trel mond quen"),
    ("20240303", "dror trel mond quen"),
    ("20240304", "dror trel mond quenal"),
    ("20240305", "dror trel mond quenal"),
    ("20240306", "dror trel zath"),
    ("20240307", "dror trel zath"),
)
CENSUS = "run_20260101T000001Z_111111111111"
REBOUND = "run_20260101T000002Z_222222222222"
INFERRED = "run_20260101T000003Z_333333333333"


def _build(base: Path, words_as: Literal["terms", "spellings"]) -> Path:
    pool = base / "pool"
    for day, words in DAYS:
        for number in (1, 2):
            path = pool / f"{day} {words}" / f"{words.replace(' ', '_')}_{number:02d}.nd2"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")
    project = Project.create(
        base / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(groups=GroupSettings(words_as=words_as)),
    )
    write_revision(project.path)
    run_census(project, run_id=CENSUS, started_at="2026-01-01T00:00:01Z")
    for word, kind in (("quen", RecordEventKind.DEFINE), ("quenal", RecordEventKind.ASSIGN)):
        append_event(
            project.path,
            kind=kind,
            actor="dana",
            occurred_at="2026-01-02T00:00:00Z",
            target=f"term:{word}",
            against_run_id=CENSUS,
            payload={"raised": word, "answer": "quen_stain", "theme_kind": "reagent"},
            scope=PROJECT_SCOPE,
        )
    run_census(
        Project.open(project.path), run_id=REBOUND, started_at="2026-01-01T00:00:02Z", rebind=True
    )
    run_inference(Project.open(project.path), run_id=INFERRED, started_at="2026-01-01T00:00:03Z")
    return project.path


@pytest.fixture(scope="module")
def read_as_terms(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _build(tmp_path_factory.mktemp("compare_terms"), "terms")


@pytest.fixture(scope="module")
def read_as_spellings(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _build(tmp_path_factory.mktemp("compare_spellings"), "spellings")


def _get(project: Path, route: str, groups: list[str] | None = None) -> tuple[int, dict[str, Any]]:
    query = urllib.parse.urlencode([("group", group) for group in groups or []])
    with DashboardServer(project, port=0) as server:
        url = server.url.rstrip("/") + "/api/v1/" + route + (f"?{query}" if query else "")
        try:
            with urllib.request.urlopen(url) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            payload = json.loads(error.read())
            error.close()
            return error.code, payload


def _sets(project: Path) -> dict[tuple[str, ...], dict[str, Any]]:
    status, shown = _get(project, "groups/lattice")
    assert status == 200, shown
    return {tuple(found["terms"]): found for found in shown["sets"]}


def test_where_terms_are_read_the_sets_compared_are_the_lattices_in_its_words(
    read_as_terms: Path,
) -> None:
    sets = _sets(read_as_terms)
    assert ("dror", "mond", "quen_stain", "trel") in sets, sorted(sets)
    status, shown = _get(
        read_as_terms, "groups/compare", ["words:dror|trel", "words:dror|mond|quen_stain|trel"]
    )
    assert status == 200, shown
    wide, narrow = shown["groups"]
    # The experiments are the lattice's own, set by set.
    assert [e["label"] for e in wide["experiments"]] == sorted(
        sets[("dror", "trel")]["experiments"]
    )
    assert [e["label"] for e in narrow["experiments"]] == sorted(
        sets[("dror", "mond", "quen_stain", "trel")]["experiments"]
    )
    assert (len(wide["experiments"]), len(narrow["experiments"])) == (7, 5)
    # The words are the lattice's, as terms; the label is the files' own spelling.
    assert narrow["words"] == ["dror", "mond", "quen_stain", "trel"]
    assert narrow["label"] == "dror, mond, quen, trel"
    assert wide["label"] == "dror, trel"
    assert shown["shown_as"] == {"quen_stain": "quen"}
    assert narrow["words_only"] == ["mond", "quen_stain"]
    assert shown["words_in_all"] == ["dror", "trel"]
    assert shown["experiments_in_all"] and len(shown["experiments_in_all"]) == 5


def test_a_set_named_in_spellings_is_found_by_the_runs_own_map_of_words(
    read_as_terms: Path,
) -> None:
    status, spelled = _get(
        read_as_terms, "groups/compare", ["words:dror|trel", "words:dror|mond|quen|trel"]
    )
    status_terms, termed = _get(
        read_as_terms, "groups/compare", ["words:dror|trel", "words:dror|mond|quen_stain|trel"]
    )
    assert (status, status_terms) == (200, 200), spelled
    # The same set, found whichever way it was named, keyed as the run's lattice holds it.
    assert spelled["groups"][1]["words"] == termed["groups"][1]["words"]
    assert spelled["groups"][1]["experiments"] == termed["groups"][1]["experiments"]
    assert spelled["groups"][1]["key"] == "words:dror|mond|quen_stain|trel"
    assert spelled["groups"][1]["key"] == termed["groups"][1]["key"]
    assert [pair["second"] for pair in spelled["pairs"]] == ["words:dror|mond|quen_stain|trel"]


def test_the_same_set_asked_once_in_each_reading_is_refused_as_a_duplicate(
    read_as_terms: Path,
) -> None:
    status, shown = _get(
        read_as_terms,
        "groups/compare",
        ["words:dror|mond|quen|trel", "words:dror|mond|quen_stain|trel"],
    )
    assert status == 400, shown
    assert shown["error"]["code"] == "bad_parameter" and shown["error"]["parameter"] == "group"
    assert "named once" in shown["error"]["message"]
    # Two different sets, one of them asked in spellings, are still compared.
    status, fine = _get(
        read_as_terms, "groups/compare", ["words:dror|trel", "words:dror|mond|quen|trel"]
    )
    assert status == 200, fine
    assert [group["key"] for group in fine["groups"]] == [
        "words:dror|trel",
        "words:dror|mond|quen_stain|trel",
    ]


def test_where_spellings_are_read_each_word_is_its_own_text_and_nothing_is_moved(
    read_as_spellings: Path,
) -> None:
    sets = _sets(read_as_spellings)
    assert ("dror", "mond", "quen", "trel") in sets, sorted(sets)
    status, shown = _get(
        read_as_spellings, "groups/compare", ["words:dror|trel", "words:dror|mond|quen|trel"]
    )
    assert status == 200, shown
    narrow = shown["groups"][1]
    assert narrow["words"] == ["dror", "mond", "quen", "trel"]
    assert narrow["label"] == "dror, mond, quen, trel"
    # A term's written form is a reading of terms: where spellings are read, none moves.
    assert shown["shown_as"] == {}
    assert len(narrow["experiments"]) == 3


def test_the_comparison_and_the_lattice_name_the_same_words_for_one_set(
    read_as_terms: Path,
) -> None:
    sets = _sets(read_as_terms)
    status, lattice = _get(read_as_terms, "groups/lattice")
    assert status == 200
    held = {tuple(found["terms"]): found for found in lattice["sets"]}
    core = held[("dror", "mond", "quen_stain", "trel")]
    status, shown = _get(
        read_as_terms, "groups/compare", ["words:dror|trel", "words:dror|mond|quen_stain|trel"]
    )
    assert status == 200
    assert shown["groups"][1]["label"] == ", ".join(core["terms_shown"])
    assert sets[("dror", "trel")]["state"] == shown["groups"][0]["state"]
