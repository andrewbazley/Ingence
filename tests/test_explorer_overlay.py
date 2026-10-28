"""A person's graph layer: groups, exclusions and proposed relationships.

On a copy of the explorer fixture (``fixtures/explorer_project.py``, invented names), served as a
person runs the Dashboard, so the one guarded write route is what is exercised. Held here:

- a batch is written as attributed annotations on ``overlay:`` targets, beside labels, and read
  back with its state: projected where the run holds what it names, and whether the run agrees;
- a batch with one bad edit writes nothing;
- a removal and a revert are more events, and a statement's history keeps every one;
- a later run (by the runs' own parentage) that agrees by its own evidence, where the run the
  statement was written against did not, confirms it; a run over the same census that already
  agreed when it was said confirms nothing, and the moment is the server's, never the body's;
- an exclusion agrees only where a doubt rule of the run raised the entry or a folder above it,
  never on a quality mark, filesystem noise or a doubt word the name negates;
- a statement naming an entry the run does not hold is stale, never dropped;
- nothing the layer says reaches the run: here the run read stays the run read, and
  ``test_explorer_labels`` runs inference with and without the layer and compares claims,
  assertions and findings.
"""

from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.request
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from fixtures.explorer_project import ExplorerProject

SAID_AT = "2026-06-01T10:00:00Z"


@pytest.fixture(scope="module")
def explorer(explorer_project: ExplorerProject) -> ExplorerProject:
    return explorer_project


@pytest.fixture
def served(explorer, tmp_path) -> Iterator[tuple[Path, str]]:
    from ingence.api.server import DashboardServer

    copy = tmp_path / "project"
    shutil.copytree(explorer.project, copy)
    server = DashboardServer(copy, port=0, config_home=tmp_path / "config")
    server.start()
    try:
        yield copy, server.url.rstrip("/")
    finally:
        server.stop()


def _post(base: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        f"{base}/api/review/answer",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Ingence-Write": "1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def _get(base: str, path: str) -> dict[str, Any]:
    with urllib.request.urlopen(f"{base}{path}", timeout=30) as response:
        return json.loads(response.read())


def _overlay_events(project: Path) -> list[Any]:
    from ingence.record.log import read_events

    return [e for e in read_events(project, held=False) if e.target.startswith("overlay:")]


def _by_target(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {statement["target"]: statement for statement in body["statements"]}


def _paths(explorer: ExplorerProject) -> dict[str, str]:
    return {entry: path for path, entry in explorer.ids.items()}


def test_a_layer_is_written_as_annotations_and_read_with_its_state(explorer, served) -> None:
    project, base = served
    first, second, third = explorer.experiments
    raw, mask = explorer.derivations[0]
    scratch = next(path for path in explorer.ids if path.startswith("scratch/"))
    other_raw = next(path for path in explorer.ids if path.startswith(f"{second}/raw/"))
    before = _get(base, "/api/v1/explorer/wheel")["run_id"]
    status, body = _post(
        base,
        {
            "actor": "a. person",
            "occurred_at": SAID_AT,
            "labels": [{"entry_id": explorer.ids[first], "label": "the first series"}],
            "overlay": [
                {
                    "kind": "group",
                    "name": "one series, two folders",
                    "members": [explorer.ids[first], explorer.ids[third]],
                },
                {"kind": "exclude", "entry": explorer.ids[scratch], "reason": "scratch, not data"},
                {
                    "kind": "relate",
                    "source": explorer.ids[raw],
                    "target": explorer.ids[mask],
                    "relation": "derives",
                },
                {
                    "kind": "relate",
                    "source": explorer.ids[raw],
                    "target": explorer.ids[other_raw],
                    "relation": "same_experiment",
                    "note": "the same sample, imaged twice",
                },
            ],
        },
    )
    assert status == HTTPStatus.CREATED, body
    assert body["written"] == 5
    events = _overlay_events(project)
    assert len(events) == 4
    assert {event.kind.value for event in events} == {"annotate"}
    assert all(event.actor == "a. person" for event in events)
    # What it names, as it was: a later reader sees what the person saw.
    group_event = next(e for e in events if e.target.startswith("overlay:group:"))
    said = json.loads(group_event.payload)
    assert said["names"][explorer.ids[first]] == first
    overlay = _get(base, "/api/v1/explorer/overlay")
    statements = _by_target(overlay)
    assert len(statements) == 4
    assert all(item["state"] == "projected" for item in statements.values())
    derives = statements[f"overlay:relate:{explorer.ids[raw]}:{explorer.ids[mask]}:derives"]
    # The run asserts this line already; it predates the statement (its start is no time), so it
    # agrees and does not confirm.
    assert derives["agreement"]["agrees"] is True
    assert derives["agreement"]["run_is_newer"] is not True
    group = next(item for item in statements.values() if item["kind"] == "group")
    assert group["name"] == "one series, two folders"
    assert group["agreement"]["agrees"] is False  # two experiment units, not one
    assert {member["name"] for member in group["members"]} == {first, third}
    excluded = next(item for item in statements.values() if item["kind"] == "exclude")
    assert excluded["reason"] == "scratch, not data"
    # Its folder's name says scratch, which a doubt rule raises and no rule repudiates: a person
    # may keep scratch, so the run does not agree.
    assert excluded["agreement"]["agrees"] is False
    assert "repudiates" in excluded["agreement"]["words"]
    same = next(item for item in statements.values() if item["relation"] == "same_experiment")
    assert same["agreement"]["agrees"] is False
    assert same["note"] == "the same sample, imaged twice"
    assert overlay["counts"] == {"recorded": 4, "projected": 4, "confirmed": 0, "stale": 0}
    # Nothing the layer says reaches the run.
    assert _get(base, "/api/v1/explorer/wheel")["run_id"] == before


def test_a_batch_with_one_bad_edit_writes_nothing(explorer, served) -> None:
    project, base = served
    raw, mask = explorer.derivations[0]
    for bad in (
        {
            "kind": "relate",
            "source": explorer.ids[raw],
            "target": explorer.ids[mask],
            "relation": "caused",
        },
        {
            "kind": "relate",
            "source": explorer.ids[raw],
            "target": explorer.ids[raw],
            "relation": "derives",
        },
        {"kind": "exclude", "entry": explorer.ids[raw], "reason": ""},
        {"kind": "group", "name": "nobody", "members": ["entry_not_in_this_run"]},
        {"kind": "group", "id": "hgnotmadebyanyone", "name": "x", "members": [explorer.ids[raw]]},
        {"kind": "hue", "entry": explorer.ids[raw]},
    ):
        status, body = _post(
            base,
            {
                "actor": "a. person",
                "labels": [{"entry_id": explorer.ids[mask], "label": "kept out too"}],
                "overlay": [
                    {"kind": "exclude", "entry": explorer.ids[mask], "reason": "a good edit"},
                    bad,
                ],
            },
        )
        assert status == HTTPStatus.BAD_REQUEST, (bad, body)
    from ingence.record.log import read_events

    assert not _overlay_events(project)
    assert not [e for e in read_events(project, held=False) if e.target.startswith("label:")]


def test_a_removal_and_a_revert_are_more_events_and_the_history_keeps_them(
    explorer, served
) -> None:
    project, base = served
    entry = explorer.ids[explorer.derivations[0][0]]
    target = f"overlay:exclude:{entry}"
    for edit in (
        {"kind": "exclude", "entry": entry, "reason": "a duplicate"},
        {"kind": "exclude", "entry": entry, "remove": True},
    ):
        status, body = _post(base, {"actor": "a. person", "overlay": [edit]})
        assert status == HTTPStatus.CREATED, body
    assert target not in _by_target(_get(base, "/api/v1/explorer/overlay"))
    history = _get(base, f"/api/v1/explorer/overlay?target={target}")["history"]
    assert [item["removed"] for item in history] == [False, True]
    # A revert says again exactly what was said before, from the history's own payload.
    before = json.loads(history[0]["payload"])
    status, body = _post(
        base,
        {
            "actor": "b. person",
            "overlay": [{"kind": "exclude", "entry": entry, "reason": before["reason"]}],
        },
    )
    assert status == HTTPStatus.CREATED, body
    now = _get(base, f"/api/v1/explorer/overlay?target={target}")
    assert [item["actor"] for item in now["history"]] == ["a. person", "a. person", "b. person"]
    standing = _by_target(now)[target]
    assert standing["reason"] == "a duplicate"
    assert standing["earlier"] == 2
    assert len(_overlay_events(project)) == 3


def test_a_newer_run_that_agrees_confirms_a_statement(explorer, served) -> None:
    project, base = served
    from fixtures.graph_project import publish_with
    from ingence.core.ids import Scope, ScopeKind
    from ingence.core.relations import Primitive
    from ingence.core.schema import Assertion, DecisionStatus
    from ingence.core.tiers import InferenceTier
    from ingence.storage.catalog import open_catalog

    first = explorer.experiments[0]
    stem = first.split("_")[1]
    raw = explorer.ids[f"{first}/raw/{stem}_03.nd2"]
    mask = explorer.ids[f"{first}/processed/{stem}_03_mask.tif"]
    status, body = _post(
        base,
        {
            "actor": "a. person",
            "occurred_at": SAID_AT,
            "overlay": [{"kind": "relate", "source": raw, "target": mask, "relation": "derives"}],
        },
    )
    assert status == HTTPStatus.CREATED, body
    target = f"overlay:relate:{raw}:{mask}:derives"
    assert _by_target(_get(base, "/api/v1/explorer/overlay"))[target]["state"] == "projected"
    with open_catalog(project) as catalog:
        rule = catalog.query("SELECT rule_id, version FROM rules ORDER BY 1, 2 LIMIT 1").fetchone()
        root_id = catalog.query("SELECT root_id FROM roots LIMIT 1").fetchone()
    assert rule is not None and root_id is not None
    # The pool observed again after the statement (a census walks it), then the line published.
    from ingence.core.project import Project
    from ingence.scan.census import run_census

    run_census(Project.open(project), run_id="run_20260615T000000Z_dddddddddddd", started_at="t")
    published_scope = Scope(ScopeKind.ROOT, str(root_id[0]))
    publish_with(
        project,
        "run_20260701T000000Z_eeeeeeeeeeee",
        [
            Assertion(
                edge_id="edge_overlay_confirmed",
                run_id="run_20260701T000000Z_eeeeeeeeeeee",
                source_id=raw,
                target_id=mask,
                primitive=Primitive.DERIVES,
                rule_id=str(rule[0]),
                rule_version=str(rule[1]),
                scope=published_scope,
                inference_tier=InferenceTier.DEFEASIBLE,
                decision_status=DecisionStatus.ASSERTED,
            )
        ],
        started_at="2026-07-01T00:00:00Z",
    )
    confirmed = _by_target(_get(base, "/api/v1/explorer/overlay"))[target]
    assert confirmed["state"] == "confirmed", confirmed["agreement"]
    assert confirmed["agreement"] == {
        "agrees": True,
        "words": "the run asserts this derives line",
        "run_is_newer": True,
        "held_when_said": False,
        "observed_since": True,
        "standing": None,
    }


def test_a_statement_naming_an_entry_the_run_does_not_hold_is_stale(explorer, served) -> None:
    project, base = served
    from ingence.core.ids import PROJECT_SCOPE
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import append_event

    kept = explorer.ids[explorer.experiments[0]]
    append_event(
        project,
        kind=RecordEventKind.ANNOTATE,
        actor="a. person",
        occurred_at=SAID_AT,
        target="overlay:group:hgwrittenearlier",
        against_run_id="run_20260101T000004Z_dddddddddddd",
        payload={
            "overlay": "group",
            "name": "from an earlier run",
            "members": [kept, "entry_gone_from_this_run"],
            "names": {"entry_gone_from_this_run": "an_old_name.tif"},
        },
        scope=PROJECT_SCOPE,
    )
    statement = _by_target(_get(base, "/api/v1/explorer/overlay"))["overlay:group:hgwrittenearlier"]
    assert statement["state"] == "stale"
    gone = next(member for member in statement["members"] if member["kind"] == "gone")
    assert gone["name"] is None and gone["name_then"] == "an_old_name.tif"


def test_a_person_s_groups_and_exclusions_arrange_the_wheel(explorer, served) -> None:
    _, base = served
    first, second, _ = explorer.experiments
    status, body = _post(
        base,
        {
            "actor": "a. person",
            "overlay": [
                {
                    "kind": "group",
                    "name": "the pair",
                    "members": [explorer.ids[first], explorer.ids[second]],
                },
                {
                    "kind": "exclude",
                    "entry": explorer.ids["scratch"],
                    "reason": "scratch, not data",
                },
            ],
        },
    )
    assert status == HTTPStatus.CREATED, body
    wheel = _get(base, "/api/v1/explorer/wheel?arrange=h.group")
    ring = {sector["name"]: sector["files"] for sector in wheel["sectors"] if sector["ring"] == 1}
    in_pair = sum(
        1
        for path in explorer.ids
        if path.startswith((f"{first}/", f"{second}/")) and "." in path.rsplit("/", 1)[-1]
    )
    assert ring["the pair"] == in_pair
    assert ring["in none of your collections"] == wheel["totals"]["files"] - in_pair
    shown = _get(base, "/api/v1/explorer/wheel?not=h.excluded%3Dset%20aside%20by%20you")
    scratch_files = sum(1 for path in explorer.ids if path.startswith("scratch/"))
    assert shown["totals"]["files"] == wheel["totals"]["files"] - scratch_files
    axes = {item["axis"]["key"]: item for item in _get(base, "/api/v1/explorer/axes")["axes"]}
    assert axes["h.group"]["values"] == 1
    assert axes["h.excluded"]["files_with_value"] == scratch_files


#: A small pool of invented names over the quality kind, each file to be set aside as "scratch":
#: what the run's own doubt rules raise, and what only reads as quality without raising doubt.
DOUBT_POOL = {
    # A quality mark: the opposite of doubt.
    "good": "brisa_raw/brisa_good_take.txt",
    "not_good": "brisa_raw/brisa_not_good_02.txt",
    # A name the operating system writes, not a person.
    "noise": "trell_raw/Thumbs.db",
    # A doubt word in a file's own name: the doubt rules read folder names only.
    "file_named": "zib_raw/zib_failed_04.txt",
    # A doubt word its folder's name negates, which the doubt rules still raise.
    "negated_folder": "not_failed_kelt/kelt_01.txt",
    # Negated once and written again unnegated: the name still says it.
    "negated_then_not": "not_failed_then_failed_brisa/brisa_03.txt",
    # Words recording a person unsure which version to keep, often the one they kept: a doubt
    # rule raises them, and they say nothing of setting anything aside.
    "use_this_one": "vorn_use_this_one/vorn_02.txt",
    "final_final": "final_final_zib/zib_03.txt",
    # A folder the run repudiates, two folders above the file: too far to speak for it.
    "far_above": "deprecated_trell/trell_raw/trell_04.txt",
    "above_unit": "do_not_use_brisa/brisa_exp1/raw/brisa_01.txt",
    # A folder the run repudiates, and a file in it.
    "doubted": "failed_mirdo/mirdo_01.txt",
    # A file in a folder the run reads as any other.
    "plain": "vorn_raw/vorn_01.txt",
}
DOUBT_CENSUS = "run_20260101T000001Z_aaaaaaaaaaaa"
DOUBT_INFER = "run_20260101T000003Z_cccccccccccc"


def _censused_doubt_pool(base: Path) -> Path:
    """``DOUBT_POOL`` under revision 4 (its doubt rules), after a census and nothing else."""
    from fixtures.revision_4 import write_revision
    from ingence.core.project import Project
    from ingence.scan.census import run_census

    pool = base / "pool"
    for relative in DOUBT_POOL.values():
        (pool / relative).parent.mkdir(parents=True, exist_ok=True)
        (pool / relative).write_text("one line\n")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    run_census(project, run_id=DOUBT_CENSUS, started_at="2026-01-01T00:00:01Z")
    return project.path


@pytest.fixture(scope="module")
def doubt_project(tmp_path_factory) -> Path:
    """A census and an inference under revision 4 (its doubt rules) over ``DOUBT_POOL``."""
    from ingence.core.project import Project
    from ingence.infer.run import run_inference

    project = _censused_doubt_pool(tmp_path_factory.mktemp("doubt"))
    run_inference(Project.open(project), run_id=DOUBT_INFER, started_at="2026-01-01T00:00:03Z")
    return project


@pytest.fixture
def served_doubt(doubt_project, tmp_path) -> Iterator[tuple[Path, str, dict[str, str]]]:
    from ingence.api.server import DashboardServer
    from ingence.storage.catalog import open_catalog

    copy = tmp_path / "project"
    shutil.copytree(doubt_project, copy)
    with open_catalog(copy) as catalog:
        ids = dict(
            catalog.query(
                "SELECT relative_path, entry_id FROM entries WHERE NOT is_deleted"
            ).fetchall()
        )
    server = DashboardServer(copy, port=0, config_home=tmp_path / "config")
    server.start()
    try:
        yield copy, server.url.rstrip("/"), {key: ids[path] for key, path in DOUBT_POOL.items()}
    finally:
        server.stop()


def _set_aside(base: str, entries: dict[str, str], **extra: Any) -> None:
    status, body = _post(
        base,
        {
            "actor": "a. person",
            "overlay": [
                {"kind": "exclude", "entry": entry, "reason": "scratch"}
                for entry in entries.values()
            ],
            **extra,
        },
    )
    assert status == HTTPStatus.CREATED, body


def _exclusions(base: str, entries: dict[str, str]) -> dict[str, dict[str, Any]]:
    statements = _by_target(_get(base, "/api/v1/explorer/overlay"))
    return {key: statements[f"overlay:exclude:{entry}"] for key, entry in entries.items()}


def test_an_exclusion_agrees_only_where_the_run_s_doubt_rules_raised_it(served_doubt) -> None:
    """A quality mark, noise, a negated doubt word, or a word recording which version a person
    kept is not an agreement with "scratch". Only the run repudiating the entry's own folder, or
    a folder above it within its experiment, is an agreement. A name negating the word
    everywhere cancels it, and the words say so."""
    _, base, entries = served_doubt
    _set_aside(base, entries)
    said = _exclusions(base, entries)
    agreeing = {key for key, statement in said.items() if statement["agreement"]["agrees"]}
    assert agreeing == {"doubted", "negated_then_not"}, {
        key: s["agreement"] for key, s in said.items()
    }
    words = said["doubted"]["agreement"]["words"]
    assert "repudiated_by_name" in words and "failed_mirdo" in words, words
    negated = said["negated_folder"]["agreement"]["words"]
    assert "negates" in negated and "failed" in negated, negated
    assert "folder names" in said["file_named"]["agreement"]["words"]
    # Said in words: a repudiating folder further above is not read for it.
    assert "further above" in said["above_unit"]["agreement"]["words"]
    for key in ("good", "not_good", "noise", "file_named", "negated_folder", "plain"):
        assert "quality mark" not in said[key]["agreement"]["words"], said[key]
    # Said against the run read: agreement, never confirmation.
    assert {statement["state"] for statement in said.values()} == {"projected"}


def test_a_run_over_the_same_census_confirms_nothing_new(served_doubt) -> None:
    """An inference over the same census cannot confirm what already agreed when it was said,
    and a moment the client sends is not the statement's."""
    project, base, entries = served_doubt
    from ingence.core.project import Project
    from ingence.infer.run import run_inference

    _set_aside(base, entries, occurred_at="2001-01-01T00:00:00Z")
    events = _overlay_events(project)
    assert events and all(not event.occurred_at.startswith("2001") for event in events)
    run_inference(
        Project.open(project),
        run_id="run_20260101T000005Z_eeeeeeeeeeee",
        started_at="2026-01-01T00:00:05Z",
    )
    said = _exclusions(base, entries)
    assert said["doubted"]["agreement"]["agrees"] is True
    assert said["doubted"]["agreement"]["run_is_newer"] is True
    assert said["doubted"]["agreement"]["held_when_said"] is True
    assert "agreed when said" in said["doubted"]["agreement"]["standing"]
    assert said["doubted"]["state"] == "projected"
    assert all(statement["state"] != "confirmed" for statement in said.values())


def test_an_inference_after_a_census_agrees_and_never_confirms(tmp_path) -> None:
    """Said against a census, then inferred over it: the rules read the same observation the
    person had, so the agreement is new and the evidence is not."""
    from ingence.api.server import DashboardServer
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.storage.catalog import open_catalog

    project = _censused_doubt_pool(tmp_path)
    with open_catalog(project) as catalog:
        entry = dict(
            catalog.query(
                "SELECT relative_path, entry_id FROM entries WHERE NOT is_deleted"
            ).fetchall()
        )[DOUBT_POOL["doubted"]]
    server = DashboardServer(project, port=0, config_home=tmp_path / "config")
    server.start()
    try:
        base = server.url.rstrip("/")
        _set_aside(base, {"doubted": entry})
        run_inference(Project.open(project), run_id=DOUBT_INFER, started_at="t")
        said = _exclusions(base, {"doubted": entry})["doubted"]
    finally:
        server.stop()
    assert said["agreement"]["agrees"] is True
    assert said["agreement"]["held_when_said"] is False
    assert said["agreement"]["observed_since"] is False
    assert "observed the files again" in said["agreement"]["standing"]
    assert said["state"] == "projected"


def test_a_person_s_relation_brings_its_other_end_into_the_lineage(explorer, served) -> None:
    _, base = served
    first = explorer.experiments[0]
    stem = first.split("_")[1]
    raw = explorer.ids[f"{first}/raw/{stem}_03.nd2"]
    quant = explorer.ids[f"{first}/processed/{stem}_03_quant.csv"]
    before = _get(base, f"/api/v1/explorer/lineage?focus={raw}&depth=1")
    assert quant not in {node["id"] for node in before["nodes"]}
    status, body = _post(
        base,
        {
            "actor": "a. person",
            "overlay": [{"kind": "relate", "source": raw, "target": quant, "relation": "derives"}],
        },
    )
    assert status == HTTPStatus.CREATED, body
    after = _get(base, f"/api/v1/explorer/lineage?focus={raw}&depth=1")
    assert quant in {node["id"] for node in after["nodes"]}
    line = next(edge for edge in after["edges"] if edge["kind"] == "person_relation")
    assert line["id"] == f"overlay:relate:{raw}:{quant}:derives"
    assert (line["flow_from"], line["flow_to"]) == (raw, quant)
    assert line["tier"] == "person" and line["rule_id"] is None
    # Asked for without the person's layer, the lineage is the run's alone.
    alone = _get(base, f"/api/v1/explorer/lineage?focus={raw}&depth=1&people=0")
    assert quant not in {node["id"] for node in alone["nodes"]}
    assert not [edge for edge in alone["edges"] if edge["kind"] == "person_relation"]


def test_a_new_group_s_identifier_cannot_repeat_for_the_same_words_and_moment() -> None:
    """A new group's identifier has a random part, so two groups said with one name in one
    second never become versions of one statement."""
    from ingence.api.routes.overlay import _new_group_id

    said = ("a. person", "2026-01-01T00:00:00Z", "the pair", 0)
    assert _new_group_id(*said) != _new_group_id(*said)


def test_no_confirmation_where_the_run_said_against_can_no_longer_be_read(explorer, served) -> None:
    """A later run agrees, but the run the statement was written against was removed: whether
    the agreement already held then is unknown, so it is said to agree, never to confirm."""
    project, base = served
    from fixtures.graph_project import publish_with
    from ingence.core.ids import Scope, ScopeKind
    from ingence.core.relations import Primitive
    from ingence.core.schema import Assertion, DecisionStatus
    from ingence.core.tiers import InferenceTier
    from ingence.storage.catalog import open_catalog
    from ingence.storage.commit import run_path

    first = explorer.experiments[0]
    stem = first.split("_")[1]
    raw = explorer.ids[f"{first}/raw/{stem}_03.nd2"]
    mask = explorer.ids[f"{first}/processed/{stem}_03_mask.tif"]
    status, body = _post(
        base,
        {
            "actor": "a. person",
            "overlay": [{"kind": "relate", "source": raw, "target": mask, "relation": "derives"}],
        },
    )
    assert status == HTTPStatus.CREATED, body
    said_against = _overlay_events(project)[0].against_run_id
    with open_catalog(project) as catalog:
        rule = catalog.query("SELECT rule_id, version FROM rules ORDER BY 1, 2 LIMIT 1").fetchone()
        root_id = catalog.query("SELECT root_id FROM roots LIMIT 1").fetchone()
    assert rule is not None and root_id is not None
    later = "run_20260701T000000Z_ffffffffffff"
    publish_with(
        project,
        later,
        [
            Assertion(
                edge_id="edge_overlay_unreadable_then",
                run_id=later,
                source_id=raw,
                target_id=mask,
                primitive=Primitive.DERIVES,
                rule_id=str(rule[0]),
                rule_version=str(rule[1]),
                scope=Scope(ScopeKind.ROOT, str(root_id[0])),
                inference_tier=InferenceTier.DEFEASIBLE,
                decision_status=DecisionStatus.ASSERTED,
            )
        ],
    )
    shutil.rmtree(run_path(project, said_against))
    statement = _by_target(_get(base, "/api/v1/explorer/overlay"))[
        f"overlay:relate:{raw}:{mask}:derives"
    ]
    assert statement["agreement"]["agrees"] is True
    assert statement["agreement"]["run_is_newer"] is True
    assert statement["agreement"]["held_when_said"] is None
    assert "can no longer be read" in statement["agreement"]["standing"]
    assert statement["state"] == "projected"
    # A run removed between the one read and the one said against: whether this run is later is
    # unknown, never "not after it".
    beyond = "run_20260801T000000Z_abcdefabcdef"
    publish_with(project, beyond, [])
    shutil.rmtree(run_path(project, later))
    statement = _by_target(_get(base, "/api/v1/explorer/overlay"))[
        f"overlay:relate:{raw}:{mask}:derives"
    ]
    assert statement["agreement"]["agrees"] is True
    assert statement["agreement"]["run_is_newer"] is None
    assert "unknown" in statement["agreement"]["standing"]


def test_a_run_published_before_the_statement_never_confirms_it_after_a_restore(tmp_path) -> None:
    """Census, inference, census, inference; restored to the first census; a statement said
    against it, read at the last inference: the census that inference rests on began before the
    statement, so it observed nothing after it."""
    from ingence.api.server import DashboardServer
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census
    from ingence.storage.catalog import open_catalog
    from ingence.workspace.restore import restore, restore_plan

    project = _censused_doubt_pool(tmp_path)
    run_inference(Project.open(project), run_id=DOUBT_INFER, started_at="2026-01-01T00:00:03Z")
    second_census = "run_20260101T000004Z_dddddddddddd"
    last = "run_20260101T000005Z_eeeeeeeeeeee"
    run_census(Project.open(project), run_id=second_census, started_at="2026-01-01T00:00:04Z")
    run_inference(Project.open(project), run_id=last, started_at="2026-01-01T00:00:05Z")
    plan = restore_plan(project, DOUBT_CENSUS)
    restore(project, run_id=DOUBT_CENSUS, actor="a. person", previewed=plan.digest)
    with open_catalog(project) as catalog:
        entry = dict(
            catalog.query(
                "SELECT relative_path, entry_id FROM entries WHERE NOT is_deleted"
            ).fetchall()
        )[DOUBT_POOL["doubted"]]
    server = DashboardServer(project, port=0, config_home=tmp_path / "config")
    server.start()
    try:
        base = server.url.rstrip("/")
        _set_aside(base, {"doubted": entry})
        statements = _by_target(_get(base, f"/api/v1/explorer/overlay?run={last}"))
    finally:
        server.stop()
    said = statements[f"overlay:exclude:{entry}"]
    assert said["agreement"]["agrees"] is True
    assert said["agreement"]["observed_since"] is False, said["agreement"]
    assert said["state"] == "projected"


def test_the_line_of_runs_says_what_is_later_and_what_observed() -> None:
    """A statement said against the very run read is never "unknown", even where that run's
    parent was removed; and every kind of run that observes the files is read as one."""
    from ingence.api.v1.explorer.overlay import _Line, _observed

    read = "run_20260101T000005Z_eeeeeeeeeeee"
    line = _Line(
        runs=((read, False, 3), ("run_20260101T000004Z_dddddddddddd", None, None)), broken=True
    )
    assert line.after(read, sequence=4, said_at="2026-01-01T00:00:06Z") == (False, None)
    inference = {"profile": "census", "parent_run_id": "run_x", "rulebook_hash": "h"}
    assert _observed({**inference, "date_years_json": "null"}) is False
    assert _observed({**inference, "profile": "rebind", "date_years_json": "[2001, 2002]"}) is False
    # A dive with no ceiling states what it spent, not what it was granted.
    assert _observed({**inference, "deepening_budget_spent_seconds": 12.0}) is True
    # A census that read no names still walked the tree: it names no parent.
    assert _observed({"profile": "census", "parent_run_id": None, "date_years_json": "null"})
    # A listing fetched from an archive.
    assert _observed({"profile": "remote", "parent_run_id": "run_x", "rulebook_hash": None})
