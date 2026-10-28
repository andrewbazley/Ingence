"""One experiment's files, each with what the engine found about it.

The class: a person judging an experiment needs every file in it with everything the engine
found about that file, each value with where it came from: its clocks, each with its source,
and the one the engine believes and why; its size, format, stage and role; its claims with their
rules, evidence and defeaters; the derivation pairs proposed or published for it, with their
support; and the copies it belongs to, by what evidence. Paged, since an experiment can hold
thousands of files, and never saying a thing is absent where it was only not found.

On the explorer fixture (``fixtures/explorer_project.py``): invented names and dates, three dated
experiments, raw instrument files, published derivations that fork and join, and files whose
bytes repeat across experiments.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from fixtures.explorer_project import ExplorerProject


@pytest.fixture(scope="module")
def explorer(explorer_project: ExplorerProject) -> ExplorerProject:
    return explorer_project


def _ask(project: Path, **query: Any) -> tuple[HTTPStatus, dict[str, Any]]:
    from ingence.api.v1 import context_for, dispatch

    context = context_for(project, settled=None, job_running=False)
    return dispatch(context, "experiments/files", {name: [str(v)] for name, v in query.items()})


def _listing(project: Path, **query: Any) -> dict[str, Any]:
    status, body = _ask(project, **query)
    assert status == HTTPStatus.OK, body
    return body


def _first(explorer: ExplorerProject) -> tuple[str, str]:
    folder = explorer.experiments[0]
    return folder, explorer.ids[folder]


def _files_in(explorer: ExplorerProject, folder: str) -> list[str]:
    """The fixture's files beneath one experiment's folder: every path with an extension."""
    return sorted(
        path
        for path in explorer.ids
        if path.startswith(folder + "/") and "." in path.rsplit("/", 1)[-1]
    )


def test_every_file_of_an_experiment_is_listed_once_page_by_page(explorer) -> None:
    folder, experiment = _first(explorer)
    whole = _listing(explorer.project, experiment=experiment, limit=500)
    assert whole["experiment"]["folder"] == folder
    assert whole["experiment"]["id"] == experiment
    paths = [row["path"] for row in whole["rows"]]
    assert paths == _files_in(explorer, folder)
    assert whole["files"] == len(paths)
    # Paged: two pages hold what one does, in the same order, nothing twice.
    first = _listing(explorer.project, experiment=experiment, limit=3, offset=0)
    rest = _listing(explorer.project, experiment=experiment, limit=500, offset=3)
    assert [row["path"] for row in first["rows"] + rest["rows"]] == paths
    assert first["files"] == whole["files"]


def test_each_file_says_its_clocks_with_their_sources_and_the_one_believed(explorer) -> None:
    _, experiment = _first(explorer)
    rows = _listing(explorer.project, experiment=experiment, limit=500)["rows"]
    for row in rows:
        sources = [clock["source"] for clock in row["clocks"]]
        assert "modified" in sources, row["path"]
        assert all(clock["detail"] for clock in row["clocks"] if clock["source"] == "modified")
        assert len(sources) == len(set(sources)), row["path"]  # never two of one source merged
        believed = row["believed"]
        if believed is not None:
            assert believed["rule_id"] == "earliest_credible_time"
            assert believed["rule_version"]
            assert believed["source"]
            assert believed["why"]
        else:
            # Never "it has no time": what the rules concluded, said as found.
            assert "not found" in row["believed_said"] or "concluded no" in row["believed_said"]
    assert any(row["believed"] is not None for row in rows)
    named = [row for row in rows if any(c["source"] == "name" for c in row["clocks"])]
    assert named, "a file in a dated folder carries its name's date as context"
    for row in named:
        clock = next(c for c in row["clocks"] if c["source"] == "name")
        assert clock["precision"] in ("day", "range", "instant")


def test_each_file_s_claims_carry_their_rule_evidence_and_defeaters(explorer) -> None:
    _, experiment = _first(explorer)
    rows = _listing(explorer.project, experiment=experiment, limit=500)["rows"]
    raw = next(row for row in rows if row["path"].endswith("_01.nd2"))
    assert raw["size_bytes"] is not None
    assert raw["extension"] == "nd2"
    assert raw["format"], "the format a rule read"
    assert raw["stage"], "the stage a rule read"
    for row in rows:
        for claim in row["claims"]:
            assert claim["rule_id"] and claim["rule_version"], claim
            assert claim["status"] in ("resolved", "examined_conflicting", "unexamined", "unknown")
            assert isinstance(claim["evidence"], list)
            for defeater in claim["defeaters"]:
                assert defeater["rule_id"] and defeater["reason"]
                assert isinstance(defeater["fired"], bool)
    # The format and stage shown are the claims' own values, never a guess.
    formats = {c["value"] for c in raw["claims"] if c["predicate"].startswith("classifies.format")}
    assert {read["value"] for read in raw["format"]} <= formats
    for read in raw["format"] + raw["stage"] + raw["role"]:
        assert isinstance(read["conflicting"], bool)


def test_derivations_proposed_and_published_are_listed_with_support_per_acquisition(
    explorer,
) -> None:
    folder, experiment = _first(explorer)
    body = _listing(explorer.project, experiment=experiment, limit=500)
    by_path = {row["path"]: row for row in body["rows"]}
    published = [(s, t) for s, t in explorer.derivations if s.startswith(folder + "/")]
    assert published
    for source, target in published:
        made = by_path[source]["derivations"]
        assert any(
            pair["other_path"] == target and pair["direction"] == "made_into" and pair["published"]
            for pair in made
        ), (source, target)
        into = by_path[target]["derivations"]
        assert any(
            pair["other_path"] == source and pair["direction"] == "made_from" for pair in into
        )
    for row in body["rows"]:
        for pair in row["derivations"]:
            assert pair["rule_id"]
            assert isinstance(pair["support"], list)
    # Per acquisition, the files derived from it, from the same pairs.
    acquisitions = {item["path"]: item for item in body["acquisitions"]}
    assert acquisitions, "the raw files are acquisitions"
    for source, target in published:
        if source in acquisitions:
            assert target in {item["path"] for item in acquisitions[source]["derived"]}


def test_copy_groups_say_by_what_evidence(explorer) -> None:
    _, experiment = _first(explorer)
    rows = _listing(explorer.project, experiment=experiment, limit=500)["rows"]
    grouped = [row for row in rows if row["copies"]]
    assert grouped, "the fixture's files repeat their bytes across experiments"
    for row in grouped:
        for group in row["copies"]:
            assert group["kind"] in ("identical", "supposed copies")
            assert group["rule_id"] and group["evidence"]
            assert group["members"] >= 1
            assert all(other["id"] != row["id"] for other in group["others"])
    kinds = {group["kind"] for row in grouped for group in row["copies"]}
    assert "identical" in kinds  # a whole-file digest proves equal bytes


def test_a_folder_no_rule_reads_as_an_experiment_is_refused_in_words(explorer) -> None:
    status, body = _ask(explorer.project, experiment=explorer.ids["notes"])
    assert status == HTTPStatus.NOT_FOUND
    assert "experiment" in body["error"]["message"]
    status, body = _ask(explorer.project)
    assert status == HTTPStatus.BAD_REQUEST
