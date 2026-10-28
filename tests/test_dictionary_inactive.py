"""Inactivating a sense or a spelling, read by the binder.

Inactive says more than absent. A person inactivates a spelling of one sense, or a whole
sense, as a Record event through the one way in; the next rebind binds nothing with it, a
reactivation binds it again, the shipped files are never touched, and two runs over one Record
reproduce each other. Every name here is invented.
"""

from __future__ import annotations

import pytest

from ingence.api.apply import changed_spellings, meanings_in_force
from ingence.api.routes.dictionary import DictionaryRefusedError, inactivate, reactivate
from ingence.core.ids import PROJECT_SCOPE
from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.dictionary.inactive import inactivations_in_force
from ingence.dictionary.store import BUILTIN_DIRECTORY, PACKS_DIRECTORY, DictionaryStore
from ingence.record.targets import sense_target, surface_target
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

#: A shipped sense and one of its spellings, asserted below rather than assumed.
TERM = "plate"
SPELLING = "plate"
OTHER = "plates"
MOMENT = "2026-09-30T02:00:00Z"

RUNS = iter(f"run_20260101T0000{index:02d}Z_{index:012d}" for index in range(1, 60))


@pytest.fixture
def project(tmp_path) -> Project:
    pool = tmp_path / "pool"
    (pool / "day_vorn").mkdir(parents=True)
    for index in (1, 2):
        (pool / "day_vorn" / f"{SPELLING}_kelt_{index}.csv").write_text("a,b\n")
    (pool / "day_vorn" / f"{OTHER}_brisa.csv").write_text("a,b\n")
    # A name holding a spelling nothing ships, which a pack or an upgrade may give a sense.
    (pool / "day_vorn" / "zephyl_mirdo_2.csv").write_text("a,b\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id=next(RUNS), started_at="t")
    return project


def _bound(project: Project, run_id: str, token: str) -> list[str | None]:
    with open_catalog(project.path, run_id=run_id) as catalog:
        return sorted(
            {
                row[0]
                for row in catalog.query(
                    "SELECT b.term FROM dictionary_bindings b JOIN components c USING "
                    "(component_id) WHERE c.normalized_value = ?",
                    [token],
                ).fetchall()
            },
            key=str,
        )


def _rebind(project: Project, **kwargs) -> str:
    return run_census(project, run_id=next(RUNS), started_at="t", rebind=True, **kwargs).run_id


def _shipped_bytes() -> dict[str, bytes]:
    return {
        str(path): path.read_bytes()
        for directory in (BUILTIN_DIRECTORY, PACKS_DIRECTORY)
        for path in sorted(directory.rglob("*.yaml"))
    }


def test_the_shipped_sense_is_what_the_test_assumes() -> None:
    hit = DictionaryStore.builtin().look_up(SPELLING, scope=PROJECT_SCOPE)
    assert hit is not None and hit.sense.term == TERM
    assert OTHER in hit.sense.surfaces


def test_an_inactive_spelling_binds_nothing_after_a_rebind_and_binds_again_after_reactivation(
    project,
) -> None:
    shipped = _shipped_bytes()
    first = _rebind(project)
    assert _bound(project, first, SPELLING) == [TERM]

    with open_catalog(project.path) as catalog:
        event = inactivate(
            catalog,
            project.path,
            actor="vorn",
            occurred_at="2026-09-30T02:00:00Z",
            term=TERM,
            spelling=SPELLING,
        )
    assert event.kind is RecordEventKind.REJECT
    assert event.target == surface_target(TERM, SPELLING)
    store = DictionaryStore.for_project(project.path)
    assert SPELLING not in store.sense_for_term(TERM).surfaces
    assert OTHER in store.sense_for_term(TERM).surfaces

    second = _rebind(project)
    assert _bound(project, second, SPELLING) == [None]
    # The other spelling of the sense still binds: one spelling of one sense was inactivated.
    assert _bound(project, second, OTHER) == [TERM]

    with open_catalog(project.path) as catalog:
        back = reactivate(
            catalog,
            project.path,
            actor="vorn",
            occurred_at="2026-09-30T02:05:00Z",
            term=TERM,
            spelling=SPELLING,
        )
    assert back.kind is RecordEventKind.CONFIRM
    third = _rebind(project)
    assert _bound(project, third, SPELLING) == [TERM]
    assert _shipped_bytes() == shipped


def test_an_inactive_sense_is_offered_nowhere_and_every_spelling_binds_nothing(project) -> None:
    with open_catalog(project.path) as catalog:
        event = inactivate(
            catalog, project.path, actor="vorn", occurred_at="2026-09-30T02:00:00Z", term=TERM
        )
    assert event.target == sense_target(TERM)
    store = DictionaryStore.for_project(project.path)
    assert store.sense_for_term(TERM) is None
    assert all(sense.term != TERM for sense in store.senses())
    run = _rebind(project)
    assert _bound(project, run, SPELLING) == [None]
    assert _bound(project, run, OTHER) == [None]
    [held] = inactivations_in_force(project.path).senses.values()
    assert (held.actor, held.occurred_at) == ("vorn", "2026-09-30T02:00:00Z")
    assert SPELLING in held.spellings and OTHER in held.spellings


def test_two_runs_over_one_record_reproduce_each_other(project) -> None:
    with open_catalog(project.path) as catalog:
        inactivate(
            catalog, project.path, actor="vorn", occurred_at=MOMENT, term=TERM, spelling=OTHER
        )
    first, second = _rebind(project), _rebind(project)
    with open_catalog(project.path, run_id=first) as catalog:
        one = catalog.query("SELECT dictionary_hash, record_watermark FROM run").fetchone()
        rows_one = catalog.query(
            "SELECT component_id, term FROM dictionary_bindings ORDER BY component_id"
        ).fetchall()
    with open_catalog(project.path, run_id=second) as catalog:
        two = catalog.query("SELECT dictionary_hash, record_watermark FROM run").fetchone()
        rows_two = catalog.query(
            "SELECT component_id, term FROM dictionary_bindings ORDER BY component_id"
        ).fetchall()
    assert one == two
    assert rows_one == rows_two
    # And the fingerprint says the vocabulary is not the shipped one's.
    assert one[0] != DictionaryStore.builtin().fingerprint()


def _rows(project: Project, run_id: str) -> list[tuple]:
    with open_catalog(project.path, run_id=run_id) as catalog:
        return catalog.query(
            "SELECT component_id, term FROM dictionary_bindings ORDER BY component_id"
        ).fetchall()


def _refused_partial(project: Project, spellings) -> None:
    """The partial rebind refuses (the worker then reads every name again, jobs/worker.py)."""
    with pytest.raises(ValueError, match="re-bind them all"):
        _rebind(project, spellings=spellings)


def _changed_by(project: Project, act, **kwargs) -> tuple[str, ...]:
    before = meanings_in_force(project.path)
    with open_catalog(project.path) as catalog:
        act(catalog, project.path, actor="vorn", occurred_at=MOMENT, **kwargs)
    return changed_spellings(before, meanings_in_force(project.path))


def test_an_inactivation_takes_effect_with_the_whole_rebind_the_partial_one_refusing(
    project,
) -> None:
    """An inactivation changes a sense no answer names, so the partial rebind refuses it.

    The partial rebind refuses every change but a person's answers. The whole rebind that the
    worker falls back to applies the inactivation."""
    _rebind(project)
    changed = _changed_by(project, inactivate, term=TERM, spelling=SPELLING)
    assert changed == (SPELLING,)
    _refused_partial(project, changed)
    whole = _rebind(project)
    assert _bound(project, whole, SPELLING) == [None]


#: A laboratory pack giving the shipped sense another spelling.
EXTENDING = {
    "section": "zephpack",
    "summary": "invented",
    "entries": [{"term": TERM, "theme_kind": "vessel", "surfaces": ["zephyl"]}],
}


def _add_unrelated(project: Project) -> tuple[str, ...]:
    from ingence.api.routes.dictionary import add_spelling

    before = meanings_in_force(project.path)
    with open_catalog(project.path) as catalog:
        add_spelling(
            catalog,
            project.path,
            actor="vorn",
            occurred_at=MOMENT,
            spelling="kelt",
            term="template",
        )
    return changed_spellings(before, meanings_in_force(project.path))


def test_a_pack_installed_after_an_inactivation_is_never_carried_past_by_a_partial_rebind(
    project,
) -> None:
    import yaml

    from ingence.api.routes.dictionary import install_from_text

    _rebind(project)
    _changed_by(project, inactivate, term=TERM, spelling=OTHER)
    _changed_by(project, reactivate, term=TERM, spelling=OTHER)
    _rebind(project)
    install_from_text(project.path, "zephpack.yaml", yaml.safe_dump(EXTENDING), actor="vorn")
    _refused_partial(project, _add_unrelated(project))


def test_a_shipped_change_after_an_inactivation_is_never_carried_past_by_a_partial_rebind(
    project, monkeypatch
) -> None:
    from dataclasses import replace

    from ingence.dictionary import store as store_module

    _rebind(project)
    _changed_by(project, inactivate, term=TERM, spelling=OTHER)
    _rebind(project)
    real = store_module.load_shipped_sections

    def upgraded():
        return tuple(
            replace(
                section,
                senses=tuple(
                    replace(sense, surfaces=(*sense.surfaces, "zephyl"))
                    if sense.term == TERM
                    else sense
                    for sense in section.senses
                ),
            )
            for section in real()
        )

    monkeypatch.setattr(store_module, "load_shipped_sections", upgraded)
    _refused_partial(project, _add_unrelated(project))


def test_a_sense_brought_back_after_a_pack_extended_it_is_read_whole(project) -> None:
    import yaml

    from ingence.api.routes.dictionary import install_from_text

    _rebind(project)
    _changed_by(project, inactivate, term=TERM)
    _rebind(project)
    install_from_text(project.path, "zephpack.yaml", yaml.safe_dump(EXTENDING), actor="vorn")
    _rebind(project)
    changed = _changed_by(project, reactivate, term=TERM)
    assert "zephyl" not in changed  # the reject recorded the spellings it stopped, not later ones
    _refused_partial(project, changed)
    whole = _rebind(project)
    assert _bound(project, whole, TERM) == [TERM]


def test_inactivating_refuses_in_words_what_it_cannot_do(project) -> None:
    with open_catalog(project.path) as catalog:
        with pytest.raises(DictionaryRefusedError, match="no sense"):
            inactivate(catalog, project.path, actor="vorn", occurred_at=MOMENT, term="zorbexine")
        with pytest.raises(DictionaryRefusedError, match="not a spelling"):
            inactivate(
                catalog, project.path, actor="vorn", occurred_at=MOMENT, term=TERM, spelling="kelt"
            )
        with pytest.raises(DictionaryRefusedError, match="names who"):
            inactivate(catalog, project.path, actor=" ", occurred_at=MOMENT, term=TERM)
        with pytest.raises(DictionaryRefusedError, match="not inactive"):
            reactivate(catalog, project.path, actor="vorn", occurred_at=MOMENT, term=TERM)
        inactivate(catalog, project.path, actor="vorn", occurred_at=MOMENT, term=TERM)
        with pytest.raises(DictionaryRefusedError, match="already inactive"):
            inactivate(catalog, project.path, actor="vorn", occurred_at=MOMENT, term=TERM)


def test_a_withdrawn_inactivation_is_in_force_nowhere(project) -> None:
    """A restore withdraws the decisions made since (schema 1.22), so the inactivation is
    no longer in force and the sense is back."""
    from ingence.record.log import append_event

    with open_catalog(project.path) as catalog:
        event = inactivate(catalog, project.path, actor="vorn", occurred_at=MOMENT, term=TERM)
    append_event(
        project.path,
        kind=RecordEventKind.WITHDRAW,
        actor="vorn",
        occurred_at=MOMENT,
        target="restore:run_20260101T000001Z_000000000001",
        against_run_id=event.against_run_id,
        payload={"withdrawn": [event.event_id], "restored_to": "r", "previewed": "p"},
    )
    held = inactivations_in_force(project.path)
    assert not held.senses
    assert DictionaryStore.for_project(project.path).sense_for_term(TERM) is not None


def test_a_spelling_inactivated_covers_its_case_twin_in_that_sense_and_the_page_says_so(
    project,
) -> None:
    """One spelling of one sense covers the forms of it that differ only in case.

    Without this, inactivating ``FPS`` while ``fps`` stayed would leave the lookup's case-folded
    index binding ``FPS`` names while the page struck it through. The page strikes both forms,
    and either form brings both back."""
    from ingence.api.routes.dictionary import term_detail

    with open_catalog(project.path) as catalog:
        event = inactivate(
            catalog,
            project.path,
            actor="vorn",
            occurred_at=MOMENT,
            term="frames_per_second",
            spelling="FPS",
        )
        assert event.target == surface_target("frames_per_second", "FPS")
        with pytest.raises(DictionaryRefusedError, match="already inactive"):
            inactivate(
                catalog,
                project.path,
                actor="vorn",
                occurred_at=MOMENT,
                term="frames_per_second",
                spelling="fps",
            )
        detail = term_detail(catalog, project.path, "frames_per_second")
    store = DictionaryStore.for_project(project.path)
    for token in ("FPS", "fps", "Fps"):
        hit = store.look_up(token)
        assert hit is None or hit.sense.term != "frames_per_second", token
    assert {item["spelling"]: bool(item["inactive"]) for item in detail["surfaces"]} == {
        "fps": True,
        "FPS": True,
    }
    with open_catalog(project.path) as catalog:
        back = reactivate(
            catalog,
            project.path,
            actor="vorn",
            occurred_at=MOMENT,
            term="frames_per_second",
            spelling="fps",
        )
    assert back.target == event.target
    hit = DictionaryStore.for_project(project.path).look_up("FPS")
    assert hit is not None and hit.sense.term == "frames_per_second"
