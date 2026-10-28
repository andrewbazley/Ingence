"""Group approvals as a lattice of additive derivations.

A group is a set of files with common themes. Approving one confirms that the terms the engine
saw together are really linked. Approval flows only to what contains it. The most common core is
asked first, then each derivation as an addition to it, never a question about a missing term.
Identical sets are resolved by the core directly. Approving a derivation flags its own
derivations. Approval never merges experiments into one unit.

The lattice is the closed term sets of the experiments (every set that is exactly the terms
shared by the experiments holding it), ordered by inclusion: a concept lattice. The most general
sets with at least ``minimum_core_terms`` terms and ``minimum_experiments`` experiments are asked
first. A child is asked once every parent it has is approved, spelled as what it adds. Every name
is invented.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fixtures.revision_4 import each_deviation, tune, write_revision
from ingence.api.routes.decide import Decision, DecisionContext, decide_target
from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.grouping.resolutions import (
    APPROVED,
    ASKED,
    DECLINED,
    DECLINED_ABOVE,
    IDENTICAL,
    NOT_ASKED,
    SUPERSET,
    WAITING,
    group_lattice,
    lattice_terms,
    lattice_value,
    resolve_by_lattice,
)
from ingence.infer.run import run_inference
from ingence.record.log import append_event
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

CORE = ("kelt", "vorn", "zib")

#: Five days of experiments, with invented words.
FIVE = {
    "day A": {"kelt", "vorn", "zib", "vornzib"},
    "day B": {"kelt", "vorn", "zib", "vornzib", "brisa"},
    "day C": {"kelt", "vorn", "zib"},
    "day D": {"kelt", "vorn", "zib"},
    "day E": {"kelt", "vorn", "zib", "brisa", "trell"},
}


def _states(approved=(), declined=()) -> dict[tuple[str, ...], tuple[str, tuple[str, ...]]]:
    lattice = group_lattice(FIVE, approved=approved, declined=declined)
    return {concept.terms: (concept.state, concept.experiments) for concept in lattice.concepts}


def _asked(approved=(), declined=()) -> list[str]:
    lattice = group_lattice(FIVE, approved=approved, declined=declined)
    return [concept.value for concept in lattice.concepts if concept.state == ASKED]


def test_the_five_days_are_asked_one_question_first_the_core_they_all_share() -> None:
    assert _asked() == ["a group: kelt vorn zib"]
    state, experiments = _states()[CORE]
    assert state == ASKED
    assert experiments == ("day A", "day B", "day C", "day D", "day E")


def test_the_lattice_is_the_closed_sets_ordered_by_inclusion() -> None:
    lattice = group_lattice(FIVE)
    assert {concept.terms: concept.experiments for concept in lattice.concepts} == {
        CORE: ("day A", "day B", "day C", "day D", "day E"),
        ("kelt", "vorn", "vornzib", "zib"): ("day A", "day B"),
        ("brisa", "kelt", "vorn", "zib"): ("day B", "day E"),
        ("brisa", "kelt", "vorn", "vornzib", "zib"): ("day B",),
        ("brisa", "kelt", "trell", "vorn", "zib"): ("day E",),
    }
    parents = {concept.terms: concept.parents for concept in lattice.concepts}
    assert parents[CORE] == ()
    assert parents[("brisa", "kelt", "vorn", "vornzib", "zib")] == (
        ("brisa", "kelt", "vorn", "zib"),
        ("kelt", "vorn", "vornzib", "zib"),
    )
    assert parents[("brisa", "kelt", "trell", "vorn", "zib")] == (("brisa", "kelt", "vorn", "zib"),)
    assert not lattice.bound_reached


def test_after_the_core_is_approved_its_derivations_are_asked_each_an_addition() -> None:
    assert _asked(approved=[CORE]) == [
        "a group: kelt vorn zib + brisa",
        "a group: kelt vorn zib + vornzib",
    ]
    states = _states(approved=[CORE])
    assert states[CORE][0] == APPROVED
    assert states[("brisa", "kelt", "vorn", "vornzib", "zib")][0] == WAITING
    assert states[("brisa", "kelt", "trell", "vorn", "zib")][0] == WAITING
    # Nothing asks about a missing term: every question after the first adds.
    lattice = group_lattice(FIVE, approved=[CORE])
    assert all(
        set(CORE) <= set(concept.terms) for concept in lattice.concepts if concept.state == ASKED
    )


def test_identical_sets_are_resolved_by_the_core_and_the_rest_hold_it_as_their_own_units() -> None:
    found = resolve_by_lattice([CORE], FIVE)
    assert set(found) == set(FIVE)
    assert found["day C"].relation == found["day D"].relation == IDENTICAL
    assert found["day A"].relation == SUPERSET
    assert found["day C"].within != found["day D"].within  # each day its own unit
    assert {resolution.across for resolution in found.values()} == {"kelt vorn zib"}
    assert all(resolution.deviations == () for resolution in found.values())


def test_a_set_missing_a_term_is_never_resolved_by_a_larger_approved_set() -> None:
    """Approval cannot apply to something it does not contain."""
    found = resolve_by_lattice([("brisa", "kelt", "vorn", "zib")], FIVE)
    assert set(found) == {"day B", "day E"}


def test_a_declined_core_asks_none_of_its_derivations() -> None:
    assert _asked(declined=[CORE]) == []
    states = _states(declined=[CORE])
    assert states[CORE][0] == DECLINED
    assert {state for terms, (state, _) in states.items() if terms != CORE} == {DECLINED_ABOVE}


def test_approving_a_derivation_flags_its_own_derivations() -> None:
    """A confirmed approval flags the derivatives of what it approved."""
    with_brisa = ("brisa", "kelt", "vorn", "zib")
    assert _asked(approved=[CORE, with_brisa]) == [
        "a group: kelt vorn zib + vornzib",  # two experiments hold it, so it is asked first
        "a group: brisa kelt vorn zib + trell",
    ]
    # A child of two parents waits for both, and is then spelled as it adds to what they share.
    with_vornzib = ("kelt", "vorn", "vornzib", "zib")
    assert _asked(approved=[CORE, with_brisa, with_vornzib]) == [
        "a group: brisa kelt vorn zib + trell",
        "a group: kelt vorn zib + brisa vornzib",
    ]


def test_an_experiment_sits_in_the_group_of_every_approved_set_it_holds() -> None:
    """Scales: a day in its series and in its derivation's group, never merged into one unit."""
    with_brisa = ("brisa", "kelt", "vorn", "zib")
    found = resolve_by_lattice([CORE, with_brisa], FIVE)
    assert found["day E"].core == with_brisa  # most specific first, for its label
    assert found["day E"].held == (with_brisa, CORE)
    assert found["day E"].across == "brisa kelt vorn zib"
    assert found["day E"].series == "kelt vorn zib"
    assert found["day C"].held == (CORE,)
    assert found["day C"].across == found["day C"].series == "kelt vorn zib"


def test_an_earlier_answer_on_a_set_that_is_not_closed_approves_its_closure() -> None:
    """A group answer (or a ticked subset of words) names a set every holder extends alike."""
    assert _asked(approved=[("kelt", "vorn")]) == [
        "a group: kelt vorn zib + brisa",
        "a group: kelt vorn zib + vornzib",
    ]
    found = resolve_by_lattice([("kelt", "vorn")], FIVE)
    assert found["day C"].across == "kelt vorn"  # the key names what the person approved


def test_the_most_general_sets_are_asked_by_how_many_experiments_hold_them() -> None:
    terms = {
        "one": {"kelt", "vorn", "quolt"},
        "two": {"kelt", "vorn", "sorn"},
        "three": {"brisa", "trell", "dask"},
        "four": {"brisa", "trell", "pell"},
        "five": {"brisa", "trell", "morv"},
        "alone": {"tamen", "gorb"},
    }
    lattice = group_lattice(terms, minimum_experiments=2)  # a floor of two experiments
    asked = [(c.terms, c.experiments) for c in lattice.concepts if c.state == ASKED]
    assert asked == [
        (("brisa", "trell"), ("five", "four", "three")),
        (("kelt", "vorn"), ("one", "two")),
    ]


def test_a_most_general_set_is_asked_from_the_fewest_experiments_a_person_chooses() -> None:
    """A person may ask only the more widely shared most general sets first.

    A copy of a real folder offered tens of most general sets, most held by few experiments.
    ``minimum_experiments`` leaves a most general set held by fewer experiments not asked.
    Additions are unaffected."""
    terms = {
        "one": {"kelt", "vorn", "quolt"},
        "two": {"kelt", "vorn", "sorn"},
        "three": {"brisa", "trell", "dask"},
        "four": {"brisa", "trell", "pell"},
        "five": {"brisa", "trell", "morv"},
    }
    lattice = group_lattice(terms, minimum_experiments=3)
    states = {concept.terms: concept.state for concept in lattice.concepts}
    assert states[("brisa", "trell")] == ASKED
    assert states[("kelt", "vorn")] == NOT_ASKED
    approved = group_lattice(FIVE, approved=[CORE], minimum_experiments=3)
    assert [c.value for c in approved.concepts if c.state == ASKED] == [
        "a group: kelt vorn zib + brisa",
        "a group: kelt vorn zib + vornzib",
    ]


def test_the_bound_on_the_sets_is_stated_when_it_is_reached() -> None:
    lattice = group_lattice(FIVE, maximum_concepts=2)
    assert lattice.bound_reached
    assert lattice.maximum_concepts == 2
    assert len(lattice.concepts) <= 2


def test_a_question_names_the_set_it_asks_about() -> None:
    assert lattice_value((), CORE) == "a group: kelt vorn zib"
    assert lattice_value(CORE, ("brisa",)) == "a group: kelt vorn zib + brisa"
    assert lattice_terms("a group: kelt vorn zib + brisa") == ("brisa", "kelt", "vorn", "zib")
    assert lattice_terms("a group: kelt vorn zib") == CORE
    assert lattice_terms("brisa, added to the group kelt vorn") is None


# ---------------------------------------------------------------------------
# Through the pass, answered through the page's one write route.
# ---------------------------------------------------------------------------

PERSON = "quolt (an invented person)"
MOMENT = "2026-01-01T00:00:30Z"
_FIVE_DAYS = (
    ("20240301", "kelt vorn zib vornzib"),
    ("20240302", "kelt vorn zib vornzib brisa"),
    ("20240303", "kelt vorn zib"),
    ("20240304", "kelt vorn zib"),
    ("20240305", "kelt vorn zib brisa trell"),
)


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _five_days(base: Path) -> Project:
    pool = base / "pool"
    for day, words in _FIVE_DAYS:
        for number in (1, 2):
            _write(pool / f"{day} {words}" / f"{words.replace(' ', '_')}_{number:02d}.nd2")
    created = Project.create(base / "project", created_at="t", roots=[pool])
    write_revision(created.path)
    run_census(created, run_id="run_20260101T000001Z_000000000001", started_at="t")
    _infer(created, 2)
    return created


def _infer(project: Project, stamp: int) -> None:
    run_inference(project, run_id=f"run_20260101T0000{stamp:02d}Z_{stamp:012d}", started_at="t")


def _fewest(path: Path, experiments: int = 2) -> Path:
    """Set ``minimum_experiments`` as a person's rule change would.

    These tests assume a floor of two experiments, and the default is five.
    ``path`` is where :func:`write_revision` says the retired file would stand."""
    tune(path.parent.parent, "minimum_experiments", experiments)
    return path


def _asked_in_run(project: Project) -> dict[str, set[str]]:
    """Each lattice question the run raised, with the experiments' folders raising it."""
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT u.value, e.relative_path FROM findings f, unnest(f.raised) AS u(value) "
            "JOIN entries e ON e.entry_id = regexp_replace(f.subject, '^folder:', '') "
            "WHERE f.rule_id = 'group_resolution_deviation'"
        ).fetchall()
    found: dict[str, set[str]] = {}
    for value, path in rows:
        found.setdefault(str(value), set()).add(str(path).split(" ", 1)[0])
    return found


def _answer(project: Project, raised: str, word: str) -> object:
    with open_catalog(project.path) as catalog:
        context = DecisionContext(catalog, project.path)
        return decide_target(
            context,
            Decision(target=f"question:group_resolution_deviation:{raised}", word=word),
            actor=PERSON,
            occurred_at=MOMENT,
        )


def _claims(project: Project, rule_id: str) -> dict[str, str]:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.relative_path, c.object_reference FROM claims c JOIN entries e "
            "ON e.entry_id = c.subject WHERE c.rule_id = ? AND c.resolution_status = 'resolved'",
            [rule_id],
        ).fetchall()
    return dict(rows)


def test_the_five_days_through_the_pass_ask_the_core_first_then_each_addition(
    tmp_path: Path,
) -> None:
    created = _five_days(tmp_path)
    days = {day for day, _ in _FIVE_DAYS}
    assert _asked_in_run(created) == {"a group: kelt vorn zib": days}
    event = _answer(created, "a group: kelt vorn zib", "correct")
    assert event.target == "group:kelt vorn zib"  # type: ignore[attr-defined]
    _infer(created, 3)
    across = _claims(created, "group_resolved_across_experiments")
    assert {path.split(" ", 1)[0] for path in across} == days  # all five hold it
    assert set(across.values()) == {"kelt vorn zib"}
    within = _claims(created, "group_resolved_within_experiment")
    assert len({within[p] for p in within if p.startswith(("20240303", "20240304"))}) == 2
    assert _asked_in_run(created) == {
        "a group: kelt vorn zib + brisa": {"20240302", "20240305"},
        "a group: kelt vorn zib + vornzib": {"20240301", "20240302"},
    }

    event = _answer(created, "a group: kelt vorn zib + brisa", "correct")
    assert event.target == "group:brisa kelt vorn zib"  # type: ignore[attr-defined]
    _infer(created, 4)
    assert _asked_in_run(created) == {
        "a group: brisa kelt vorn zib + trell": {"20240305"},
        "a group: kelt vorn zib + vornzib": {"20240301", "20240302"},
    }
    across = _claims(created, "group_resolved_across_experiments")
    assert across["20240305 kelt vorn zib brisa trell/kelt_vorn_zib_brisa_trell_01.nd2"] == (
        "brisa kelt vorn zib"
    )
    assert across["20240303 kelt vorn zib/kelt_vorn_zib_01.nd2"] == "kelt vorn zib"


def test_a_declined_core_through_the_pass_asks_nothing_more(tmp_path: Path) -> None:
    created = _five_days(tmp_path)
    event = _answer(created, "a group: kelt vorn zib", "wrong")
    assert event.target == "group:kelt vorn zib"  # type: ignore[attr-defined]
    _infer(created, 3)
    assert _asked_in_run(created) == {}
    assert _claims(created, "group_resolved_across_experiments") == {}


def test_the_lattice_asks_a_proposals_core_once_and_its_answer_groups_the_members(
    tmp_path: Path,
) -> None:
    """A proposal offering the core the lattice asks is one question, not two.

    Its answer, on ``group:<core>``, groups every experiment holding the core. It approves no
    proposal nobody was shown, because otherwise one membership would carry three group claims."""
    pool = tmp_path / "pool"
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
            _write(pool / f"{day} {words}" / f"{words.replace(' ', '_')}_{number:02d}.nd2")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    _fewest(write_revision(created.path))
    run_census(created, run_id="run_20260101T000001Z_000000000001", started_at="t")
    _infer(created, 2)
    with open_catalog(created.path) as catalog:
        proposed = catalog.query(
            "SELECT count(*) FROM findings WHERE rule_id = 'experiments_may_belong_together'"
        ).fetchone()
    assert proposed == (0,)  # the lattice asks it
    assert _asked_in_run(created) == {"a group: kelt vorn": {"20240301", "20240302"}}
    _answer(created, "a group: kelt vorn", "correct")
    _infer(created, 3)
    across = _claims(created, "group_resolved_across_experiments")
    assert {path.split("/")[0] for path in across} == {
        "20240301 kelt vorn brisa",
        "20240302 kelt vorn quolt",
    }
    assert _claims(created, "experiments_approved_together") == {}


def test_an_experiment_sits_in_its_series_and_in_its_derivations_group_as_claims(
    tmp_path: Path,
) -> None:
    """Scales: the five days are one series under the core however many derivations
    are approved, while a day holding an approved derivation is also in that derivation's group;
    each day stays its own experiment."""
    created = _five_days(tmp_path)
    _answer(created, "a group: kelt vorn zib", "correct")
    _infer(created, 3)
    _answer(created, "a group: kelt vorn zib + brisa", "correct")
    _infer(created, 4)
    series = _claims(created, "group_series_across_experiments")
    across = _claims(created, "group_resolved_across_experiments")
    # The series is claimed only where it is another group than the across one: the days
    # holding the approved derivation. The others' across group is the series itself.
    assert {path.split(" ", 1)[0] for path in series} == {"20240302", "20240305"}
    assert set(series.values()) == {"kelt vorn zib"}
    held_by_e = "20240305 kelt vorn zib brisa trell/kelt_vorn_zib_brisa_trell_01.nd2"
    assert (series[held_by_e], across[held_by_e]) == ("kelt vorn zib", "brisa kelt vorn zib")
    assert {key for path, key in across.items() if path.startswith("20240303")} == {"kelt vorn zib"}
    within = _claims(created, "group_resolved_within_experiment")
    assert len({key for path, key in within.items() if "brisa" in path}) == 2  # B and E apart


def test_under_each_deviation_the_series_rule_publishes_nothing(tmp_path: Path) -> None:
    created = _five_days(tmp_path)
    each_deviation(created.path / "rulebook" / "01-revision-4.yaml")
    _infer(created, 3)
    append_event(
        created.path,
        kind=RecordEventKind.CONFIRM,
        actor=PERSON,
        occurred_at=MOMENT,
        target="group:kelt vorn zib",
        against_run_id="run_20260101T000003Z_000000000003",
        payload={},
    )
    _infer(created, 4)
    assert _claims(created, "group_series_across_experiments") == {}
    assert set(_claims(created, "group_resolved_across_experiments").values()) == {"kelt vorn zib"}


def _search(project: Project, text: str) -> dict[str, object]:
    from ingence.api.routes import search as routes

    with open_catalog(project.path) as catalog:
        return routes.search(catalog, project.path, text=text, limit=50)


def _units_found(found: dict[str, object]) -> dict[str, list[dict[str, object]]]:
    """Every experiment a search brought up, by its day, with the reasons given for it."""
    shown: dict[str, list[dict[str, object]]] = {}
    with_ids: list[tuple[str | None, list[dict[str, object]]]] = []
    for card in found["results"]:  # type: ignore[attr-defined]
        with_ids.append((card["path"], card["why"]))
        for member in card["folded"]:
            with_ids.append((member["name"], card["why"]))
    for path, why in with_ids:
        if path:
            shown[str(path).split("/")[-1].split(" ", 1)[0]] = why
    return shown


def test_search_for_the_cores_words_brings_up_every_experiment_holding_it_and_says_why(
    tmp_path: Path,
) -> None:
    """Every experiment holding the approved core comes up, approved directly or not,
    each its own experiment, with the approved group as a reason it matches."""
    created = _five_days(tmp_path)
    _answer(created, "a group: kelt vorn zib", "correct")
    _infer(created, 3)
    _answer(created, "a group: kelt vorn zib + brisa", "correct")
    _infer(created, 4)
    shown = _units_found(_search(created, "kelt vorn zib"))
    assert set(shown) >= {day for day, _ in _FIVE_DAYS}
    for day, _ in _FIVE_DAYS:
        reasons = [str(line.get("where")) for line in shown[day]]
        assert "approved group {kelt vorn zib}" in reasons, (day, shown[day])
    # A day holding the approved derivation says so too, when the search names its words.
    brisa = _units_found(_search(created, "brisa"))
    reasons = [str(line.get("where")) for line in brisa["20240305"]]
    assert "approved group {brisa kelt vorn zib}" in reasons


def test_the_inner_level_is_asked_as_an_addition_once_its_core_is_approved(
    tmp_path: Path,
) -> None:
    """The inner level of a lattice (the core and one word more) is asked as an addition once
    the core is approved. The clustering does not ask it before its core."""
    pool = tmp_path / "pool"
    days = (
        ("20180704", "kelt vorn"),
        ("20180711", "kelt vorn brisa"),
        ("20180718", "kelt vorn trell"),
        ("20180725", "kelt vorn brisa zib"),
    )
    others = [
        (f"2019040{number}", words)
        for number, words in enumerate(("quolt sorn", "dask pell", "morv gisk", "tamen alpa"))
    ]
    for day, words in (*days, *others):
        _write(pool / f"{day} {words}" / f"{words.replace(' ', '_')}_001.nd2")
        _write(pool / f"{day} {words}" / f"{words.replace(' ', '_')}_001_results.csv")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    _fewest(write_revision(created.path))
    run_census(created, run_id="run_20260101T000001Z_000000000001", started_at="t")
    _infer(created, 2)
    with open_catalog(created.path) as catalog:
        proposed = catalog.query(
            "SELECT DISTINCT u.value FROM findings f, unnest(f.raised) AS u(value) "
            "WHERE f.rule_id = 'experiments_may_belong_together'"
        ).fetchall()
    assert proposed == []  # the lattice asks about word sets, most general first
    assert _asked_in_run(created) == {"a group: kelt vorn": {day for day, _ in days}}
    _answer(created, "a group: kelt vorn", "correct")
    _infer(created, 3)
    assert _asked_in_run(created) == {
        "a group: kelt vorn + brisa": {"20180711", "20180725"},
        "a group: kelt vorn + trell": {"20180718"},
    }


def test_under_the_lattice_the_clustering_asks_no_set_of_words_before_its_turn(
    tmp_path: Path,
) -> None:
    """Every question after the most common core is an addition to what is approved.
    A pair of experiments the clustering links by rare words would be asked about their whole set
    (the core and two words more) before the core. Under the lattice it waits for the core."""
    pool = tmp_path / "pool"
    extras = ("", "brisa zib", "brisa zib", "trell", "quolt", "sorn")
    days = [(f"2018070{n}", f"kelt vorn {extra}".strip()) for n, extra in enumerate(extras)]
    others = [
        (f"2019040{number}", words)
        for number, words in enumerate(
            ("alpa bret", "cind dorn", "elk fenn", "gorb hask", "ilm jorv", "kasp lunt")
        )
    ]
    for day, words in (*days, *others):
        _write(pool / f"{day} {words}" / f"{words.replace(' ', '_')}_001.nd2")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    each_deviation(write_revision(created.path))
    run_census(created, run_id="run_20260101T000001Z_000000000001", started_at="t")
    _infer(created, 2)

    def proposed() -> list[str]:
        with open_catalog(created.path) as catalog:
            return [
                str(reason).rsplit("core terms: ", 1)[-1]
                for (reason,) in catalog.query(
                    "SELECT DISTINCT reason FROM findings "
                    "WHERE rule_id = 'experiments_may_belong_together'"
                ).fetchall()
            ]

    assert "brisa kelt vorn zib" in proposed()  # the clustering links the pair
    tune(created.path, "derivations", "lattice")  # the lattice again, as a person's change
    _infer(created, 3)
    assert "brisa kelt vorn zib" not in proposed()
    assert "a group: kelt vorn" in _asked_in_run(created)
    _answer(created, "a group: kelt vorn", "correct")
    _infer(created, 4)
    assert _asked_in_run(created)["a group: kelt vorn + brisa zib"] == {"20180701", "20180702"}


def test_the_fewest_experiments_reaches_the_run_through_the_rule(tmp_path: Path) -> None:
    """``minimum_experiments`` on the across rule is what the run and the page read."""

    created = _five_days(tmp_path)
    tune(created.path, "minimum_experiments", 6)
    _infer(created, 3)
    assert _asked_in_run(created) == {}  # five days hold the core: fewer than six


def test_a_connecting_word_is_no_term_of_a_group(tmp_path: Path) -> None:
    """A connecting word is not a term of a group.

    On a copy of a real folder, an addition the lattice asked was a connecting word. The
    Dictionary binds such words (``connective``) so that no rule reads one as a subject. The
    group rules leave them out of an experiment's terms, as they leave out stage words."""
    pool = tmp_path / "pool"
    for day, words in (
        ("20240301", "kelt to vorn"),
        ("20240302", "kelt to vorn"),
        ("20240303", "kelt vorn"),
    ):
        _write(pool / f"{day} {words}" / f"{words.replace(' ', '_')}_001.nd2")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    _fewest(write_revision(created.path))
    run_census(created, run_id="run_20260101T000001Z_000000000001", started_at="t")
    _infer(created, 2)
    assert _asked_in_run(created) == {"a group: kelt vorn": {"20240301", "20240302", "20240303"}}
    _answer(created, "a group: kelt vorn", "correct")
    _infer(created, 3)
    assert _asked_in_run(created) == {}  # nothing adds a connecting word to the approved set


# ---------------------------------------------------------------------------
# Further cases: what an answer approves, the bound on the sets, and the version.
# ---------------------------------------------------------------------------


def _proposed_cores(project: Project) -> list[str]:
    with open_catalog(project.path) as catalog:
        return sorted(
            {
                str(reason).rsplit("core terms: ", 1)[-1]
                for (reason,) in catalog.query(
                    "SELECT DISTINCT reason FROM findings "
                    "WHERE rule_id = 'experiments_may_belong_together'"
                ).fetchall()
            }
        )


def _month_pool(base: Path) -> Project:
    """Three days of one condition in one month's folder, a fourth elsewhere, and others."""
    pool = base / "pool"
    layout = [
        ("2024-03/20240301 kelt vorn", "kelt_vorn"),
        ("2024-03/20240302 kelt vorn", "kelt_vorn"),
        ("2024-03/20240303 kelt vorn", "kelt_vorn"),
        ("2023-11/20231101 kelt vorn brisa", "kelt_vorn_brisa"),
        *[
            (f"2024-04/202404{n:02d} {words}", words.replace(" ", "_"))
            for n, words in enumerate(
                ("alpa bret", "cind dorn", "elk fenn", "gorb hask", "ilm jorv"), 1
            )
        ],
    ]
    for folder, stem in layout:
        for number in (1, 2):
            _write(pool / folder / f"{stem}_{number:02d}.nd2")
    created = Project.create(base / "project", created_at="t", roots=[pool])
    _fewest(write_revision(created.path))
    run_census(created, run_id="run_20260101T000001Z_000000000001", started_at="t")
    _infer(created, 2)
    return created


def test_a_lattice_answer_approves_no_proposal_nobody_was_shown(tmp_path: Path) -> None:
    """An answer to a lattice question approves only a proposal the answered run raised.

    The clustering left its proposal to the lattice, so nobody saw it. If the answer approved it
    anyway, one membership would carry three group claims. A person approves what was put to
    them."""
    created = _month_pool(tmp_path)
    assert _proposed_cores(created) == []  # left to the lattice
    _answer(created, "a group: kelt vorn", "correct")
    _infer(created, 3)
    assert _claims(created, "experiments_approved_together") == {}
    across = _claims(created, "group_resolved_across_experiments")
    assert {path.split("/")[1] for path in across} == {
        "20240301 kelt vorn",
        "20240302 kelt vorn",
        "20240303 kelt vorn",
        "20231101 kelt vorn brisa",
    }
    # The series is published only where it differs from the across group.
    assert _claims(created, "group_series_across_experiments") == {}


def test_a_set_too_few_experiments_share_is_asked_by_nobody(tmp_path: Path) -> None:
    """A set that too few experiments share (``minimum_experiments``) is asked by nobody.

    Neither the lattice nor the clustering asks it. The Groupings page lists it as not asked,
    and the clustering leaves it to the lattice too."""

    pool = tmp_path / "pool"
    days = [
        ("20240301", "kelt vorn brisa"),
        ("20240302", "kelt vorn quolt"),
        *[
            (f"202404{n:02d}", words)
            for n, words in enumerate(
                ("alpa bret", "cind dorn", "elk fenn", "gorb hask", "ilm jorv", "kasp lunt"), 1
            )
        ],
    ]
    for day, words in days:
        for number in (1, 2):
            _write(pool / f"{day} {words}" / f"{words.replace(' ', '_')}_{number:02d}.nd2")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    _fewest(write_revision(created.path))
    run_census(created, run_id="run_20260101T000001Z_000000000001", started_at="t")
    _infer(created, 2)
    assert _proposed_cores(created) == []  # the lattice asks it
    tune(created.path, "minimum_experiments", 3)
    _infer(created, 3)
    assert _asked_in_run(created) == {}
    assert _proposed_cores(created) == []  # not asked: too few experiments share it


BOUND = {
    "e1": {"ab1", "ab2", "xx"},
    "e2": {"cd1", "cd2", "yy"},
    "e3": {"ab1", "ab2", "cd1", "cd2", "ww"},
    "e4": {"ab1", "ab2", "cd1", "cd2", "zz"},
}


def test_under_the_bound_the_lattice_asks_nothing() -> None:
    """Where the bound is reached, nothing is asked from the lattice.

    A partial closure would find a set's parents wrongly and ask an addition before its true
    parent."""
    cores = [("ab1", "ab2"), ("cd1", "cd2")]
    for bound in range(1, 7):
        cut = group_lattice(BOUND, maximum_concepts=bound, approved=cores)
        assert cut.bound_reached
        assert [c.value for c in cut.concepts if c.state == ASKED] == []
    full = group_lattice(BOUND, approved=cores)
    assert not full.bound_reached
    assert "a group: ab1 ab2 + cd1 cd2" in [c.value for c in full.concepts if c.state == ASKED]


def test_an_addition_to_two_sets_sharing_no_words_is_spelled_as_an_addition() -> None:
    """An addition to two approved sets that share no words would read like a first question.

    It is spelled against its first parent."""
    full = group_lattice(BOUND, approved=[("ab1", "ab2"), ("cd1", "cd2")])
    (both,) = [c for c in full.concepts if c.terms == ("ab1", "ab2", "cd1", "cd2")]
    assert both.value == "a group: ab1 ab2 + cd1 cd2"
    assert lattice_terms(both.value) == both.terms


def test_under_the_bound_the_run_raises_no_lattice_question_and_says_so(tmp_path: Path) -> None:
    """Through the pass, the clustering resumes asking, and the run's journal says the bound
    was reached."""

    created = _month_pool(tmp_path)
    tune(created.path, "maximum_concepts", 1)
    _infer(created, 3)
    assert _asked_in_run(created) == {}
    assert _proposed_cores(created) == ["kelt vorn"]  # the clustering resumes
    with open_catalog(created.path) as catalog:
        said = catalog.query(
            "SELECT detail FROM journal_events WHERE event = 'groups.lattice_bound_reached'"
        ).fetchall()
    assert said


def test_the_clustering_predicate_changed_its_version() -> None:
    """The clustering predicate's version moves when its verdict changes for an unchanged input.

    The verdict changed with stage words and the lattice, with a name's numbers and a set too
    few experiments share, and with a bound word being its Dictionary term."""
    from ingence.rulebook.predicates.experiments import ExperimentsMayBelongTogether
    from ingence.rulebook.registry import PREDICATES

    assert ExperimentsMayBelongTogether.version == "4"
    assert PREDICATES.declaration("experiments_may_belong_together").version == "4"


def test_search_says_when_it_could_not_read_the_approved_groups() -> None:
    """A failure reading the approved groups is said in the response, never swallowed."""
    from ingence.search.engine import _approved_groups

    class Refusing:
        def query(self, *_: object) -> object:
            message = "no claims table in this run"
            raise RuntimeError(message)

    found, said = _approved_groups(Refusing(), ["folder"], {0: "kelt"})  # type: ignore[arg-type]
    assert found == {}
    assert said is not None and "no claims table" in said
