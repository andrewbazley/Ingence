"""Groups are made of words: never a date, a clock, a number or a replicate identifier.

Grouping relies on words a person reads, and on scientific nouns. The tokeniser recognises a
date. A replicate identifier is recognised by its shape (``001``, ``01``, ``-1``, ``_1``,
``_001``) and by its siblings (files of one type in one folder holding the same shape, in the
same place, with other values). The default for ``minimum_experiments`` is 5. Every name here is
invented.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from fixtures.revision_4 import write_revision
from ingence.api.routes import pages
from ingence.core.project import Project
from ingence.grouping.resolutions import (
    ASKED,
    DEFAULT_MINIMUM_EXPERIMENTS,
    NOT_ASKED,
    group_lattice,
)
from ingence.infer.run import run_inference
from ingence.rulebook.predicates.experiments import RESOLUTION_DEFAULTS, lattice_of
from ingence.rulebook.revision_4 import REVISION
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path


def _held_by(count: int) -> dict[str, set[str]]:
    """``count`` experiments sharing the words ``kelt vorn``, each with a word of its own."""
    own = ("brisa", "zeph", "trell", "quolt", "marn", "sulk", "dover")
    return {f"day {index}": {"kelt", "vorn", own[index]} for index in range(count)}


def _state(lattice: object, terms: tuple[str, ...]) -> str:
    return next(c.state for c in lattice.concepts if c.terms == terms)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# A most general set is asked from five experiments.
# ---------------------------------------------------------------------------


def test_a_set_four_experiments_share_is_not_asked_and_five_is() -> None:
    assert DEFAULT_MINIMUM_EXPERIMENTS == 5
    assert _state(group_lattice(_held_by(4)), ("kelt", "vorn")) == NOT_ASKED
    assert _state(group_lattice(_held_by(5)), ("kelt", "vorn")) == ASKED


def test_every_fallback_asks_from_five_experiments() -> None:
    assert RESOLUTION_DEFAULTS["minimum_experiments"] == 5
    # A rule declaring no minimum reads the default, never two.
    assert _state(lattice_of(_held_by(4), {}), ("kelt", "vorn")) == NOT_ASKED
    assert _state(lattice_of(_held_by(5), {}), ("kelt", "vorn")) == ASKED


def test_the_rule_ships_five_and_a_person_may_still_choose_two() -> None:
    rule = next(r for r in REVISION["rules"] if r["id"] == "group_resolved_across_experiments")
    parameter = next(p for p in rule["parameters"] if p["name"] == "minimum_experiments")
    assert parameter["default"] == 5
    assert parameter["minimum"] == 2
    held = _held_by(2)
    assert _state(group_lattice(held, minimum_experiments=2), ("kelt", "vorn")) == ASKED
    # One experiment alone is never a group, whatever a person writes.
    alone = group_lattice(_held_by(1), minimum_experiments=1)
    assert _state(alone, ("brisa", "kelt", "vorn")) == NOT_ASKED


# ---------------------------------------------------------------------------
# One classifier, and the name pattern naming a date and a clock.
# ---------------------------------------------------------------------------


def test_an_eight_digit_date_that_varies_is_a_date_never_a_counter() -> None:
    from ingence.api.name_patterns import name_pattern

    shaped = name_pattern([f"kelt_vorn_202603{day:02d}.nd2" for day in (2, 9, 16, 23)])
    assert shaped.pattern == "kelt_vorn_{20260302,20260309,20260316,20260323}.nd2"
    assert shaped.dimensions == ("date",)
    assert shaped.said == "varying by a date"


def test_a_written_date_is_one_part_and_a_clock_is_a_clock() -> None:
    from ingence.api.name_patterns import name_pattern

    dated = name_pattern(["zib_2026-03-02_kelt.tif", "zib_2026-03-09_kelt.tif"])
    assert dated.pattern == "zib_{2026-03-02,2026-03-09}_kelt.tif"
    assert dated.dimensions == ("date",)
    timed = name_pattern(["zib 2026-03-02 at 14-32-05.png", "zib 2026-03-02 at 15-01-22.png"])
    assert timed.dimensions == ("clock",)
    from ingence.grouping.identifiers import date_spans

    assert date_spans("kelt_v1.23.45.tif") == ()  # a version's numbers, never a time


def test_numbers_that_do_not_count_are_numbers_and_may_name_a_condition() -> None:
    from ingence.api.name_patterns import name_pattern

    counted = name_pattern([f"kelt_{index:03d}.tif" for index in range(1, 6)])
    assert counted.dimensions == ("counter",)
    lines = name_pattern(["kelt_488.tif", "kelt_561.tif", "kelt_640.tif"])
    assert lines.dimensions == ("number",)
    assert lines.said == "varying by a number"


def test_a_replicate_identifier_by_its_shape() -> None:
    from ingence.grouping.identifiers import slot_kind

    for values in (["001", "002", "003"], ["01", "02"], ["1", "2", "3"], ["2", "3", "5"]):
        assert slot_kind(values) == "counter", values
    # Too wide for a replicate's number, or not counting with its siblings.
    assert slot_kind(["10001", "10002"]) == "number"
    assert slot_kind(["10", "20", "30"]) == "number"
    # A part holding a letter is a word's.
    assert slot_kind(["r1", "r2"]) is None


def test_a_series_across_experiments_is_a_name_counted_differently_in_each() -> None:
    from ingence.grouping.identifiers import masked, series_across

    assert masked("Kelt_Vorn_003.nd2") == masked("kelt_vorn_017.nd2") == "kelt_vorn_#.nd2"
    assert masked("zib_2026-03-02_kelt.tif") == "zib_<date>_kelt.tif"
    counted = [("A", "kelt_vorn_001.nd2"), ("A", "kelt_vorn_002.nd2"), ("B", "kelt_vorn_003.nd2")]
    assert series_across(counted)
    # A file held in one experiment only may vary by its words too.
    assert series_across([*counted, ("A", "kelt_vorn_001_results.csv")])
    assert series_across([("A", "kelt_20240301.nd2"), ("B", "kelt_20240308.nd2")])
    assert series_across([("A", "kelt_r1.nd2"), ("B", "kelt_r2.nd2"), ("C", "kelt_r3.nd2")])
    # Linked by a word: each experiment's names differ by more than what they count.
    staged = [("A", "kelt_vorn_raw_001.nd2"), ("B", "kelt_vorn_processed_002.nd2")]
    assert not series_across(staged)
    # The same names in two experiments, nothing counted: asked, as copies or one family.
    assert not series_across([("A", "kelt_vorn.nd2"), ("B", "kelt_vorn.nd2")])
    # Numbers that do not count may name a condition.
    assert not series_across([("A", "kelt_488.tif"), ("B", "kelt_561.tif")])
    # In one experiment, a class is shown under it already.
    assert not series_across([("A", "kelt_1.nd2"), ("A", "kelt_2.nd2")])


# ---------------------------------------------------------------------------
# Through the pass: three experiments whose files differ only by what they count.
# ---------------------------------------------------------------------------

#: The run's identifiers, as the neighbouring lattice tests write them.
_CENSUS = "run_20260101T000001Z_000000000001"


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _project(base: Path, layout: Sequence[tuple[str, Sequence[str]]]) -> Project:
    """A pool of experiment folders, each holding the files named, censused and inferred."""
    pool = base / "pool"
    for folder, names in layout:
        for name in names:
            _write(pool / folder / name)
    created = Project.create(base / "project", created_at="t", roots=[pool])
    # The rules in force as the neighbouring tests compose them.
    write_revision(created.path)
    run_census(created, run_id=_CENSUS, started_at="t")
    _infer(created, 2)
    return created


def _infer(project: Project, stamp: int) -> None:
    run_inference(project, run_id=f"run_20260101T0000{stamp:02d}Z_{stamp:012d}", started_at="t")


def _groupings(project: Project) -> dict[str, Any]:
    with open_catalog(project.path) as catalog:
        return pages.page(catalog, project.path, "classes", limit=10_000)


def _asked_across(project: Project) -> list[str]:
    """The name patterns of the classes the Groupings page asks across experiments."""
    return sorted(
        str(row.get("pattern"))
        for row in _groupings(project)["rows"]
        if row["level"] == "files" and row.get("experiments_spanned", 0) >= 2
    )


def _lattice_asked(project: Project) -> set[str]:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT DISTINCT u.value FROM findings f, unnest(f.raised) AS u(value) "
            "WHERE f.rule_id = 'group_resolution_deviation'"
        ).fetchall()
    return {str(value) for (value,) in rows}


_DAYS = ("20240301", "20240308", "20240315")


def _counted(day: str) -> list[str]:
    return [f"kelt_vorn_{index:03d}.nd2" for index in range(1, 6)]


def _dated(day: str) -> list[str]:
    return [f"kelt_vorn_{day}.nd2", f"kelt_vorn_{int(day) + 1}.nd2"]


def _replicated(day: str) -> list[str]:
    return [f"kelt_vorn_r{index}.nd2" for index in range(1, 4)]


def _monthly(day: str) -> list[str]:
    month = {"20240301": "march", "20240308": "april", "20240315": "may"}[day]
    return [f"kelt_vorn_{month}.nd2", f"kelt_vorn_{month}_results.csv"]


def _staged(day: str) -> list[str]:
    return ["kelt_vorn_raw.nd2", "kelt_vorn_processed.nd2"]


@pytest.mark.parametrize(
    "files", [_counted, _dated, _replicated, _monthly], ids=["counter", "date", "r", "month"]
)
def test_files_differing_only_by_what_they_count_are_asked_across_no_experiments(
    tmp_path: Path, files: Callable[[str], list[str]]
) -> None:
    created = _project(tmp_path, [(f"{day} kelt vorn", files(day)) for day in _DAYS])
    assert _asked_across(created) == []
    assert _lattice_asked(created) == set()  # three experiments: fewer than five
    # Shown inside each experiment, a series of its own there.
    within = {
        held["experiment"]: held["groups"] for held in _groupings(created)["within_experiments"]
    }
    assert set(within) == {f"{day} kelt vorn" for day in _DAYS}


def test_the_same_pool_with_a_word_varying_still_asks(tmp_path: Path) -> None:
    created = _project(tmp_path, [(f"{day} kelt vorn", _staged(day)) for day in _DAYS])
    assert _asked_across(created) == ["kelt_vorn_{raw,processed}.nd2"]


def test_a_series_a_project_rule_forms_across_experiments_is_shown_never_asked(
    tmp_path: Path,
) -> None:
    """The name family keys files by their words across the pool, so the counted files of three
    experiments are one class: the Groupings page reads the class whole and lists it inside each
    experiment as a series, never asked across them."""
    created = _project(tmp_path, [(f"{day} kelt vorn", _counted(day)) for day in _DAYS])
    shown = _groupings(created)
    assert _asked_across(created) == []
    listed = {
        held["experiment"]: [group["pattern"] for group in held["groups"]]
        for held in shown["within_experiments"]
    }
    for day in _DAYS:
        assert "kelt_vorn_{001-005}.nd2" in listed[f"{day} kelt vorn"]
    series = next(
        group
        for held in shown["within_experiments"]
        for group in held["groups"]
        if group["pattern"] == "kelt_vorn_{001-005}.nd2"
    )
    assert series["series"] is True
    assert series["experiments_spanned"] == len(_DAYS)


# ---------------------------------------------------------------------------
# The lattice's terms: a month's word leaves; an unknown word stays.
# ---------------------------------------------------------------------------

_FIVE = ("20240301", "20240308", "20240315", "20240322", "20240329")


def _answer(project: Project, raised: str, word: str) -> None:
    from ingence.api.routes.decide import Decision, DecisionContext, decide_target

    with open_catalog(project.path) as catalog:
        decide_target(
            DecisionContext(catalog, project.path),
            Decision(target=f"question:group_resolution_deviation:{raised}", word=word),
            actor="ines",
            occurred_at="2026-01-01T00:00:30Z",
        )


def _approved_then_asked(tmp_path: Path, extra: Sequence[str]) -> set[str]:
    """Five experiments sharing ``kelt vorn``, each with one more word; the core approved."""
    layout = [
        (f"{day} kelt vorn {word}", [f"kelt_vorn_{word}_{index:02d}.nd2" for index in (1, 2)])
        for day, word in zip(_FIVE, extra, strict=True)
    ]
    created = _project(tmp_path, layout)
    assert _lattice_asked(created) == {"a group: kelt vorn"}
    _answer(created, "a group: kelt vorn", "correct")
    _infer(created, 3)
    return _lattice_asked(created)


def test_a_month_word_is_no_term_of_a_group(tmp_path: Path) -> None:
    months = ("march", "march", "march", "april", "april")
    assert _approved_then_asked(tmp_path, months) == set()
    # The Groupings page reads the lattice by its own loader, and leaves them out too.
    from ingence.api.v1 import V1Context, groups

    project = tmp_path / "project"
    run = "run_20260101T000003Z_000000000003"  # the run after the approval
    context = V1Context(project=project, run_current=run, run_pinned=run, job_running=False)
    terms = {term for found in groups.lattice(context, {}).sets for term in found.terms}
    assert terms == {"kelt", "vorn"}


def test_an_unknown_word_stays_a_term_of_a_group(tmp_path: Path) -> None:
    """A strain's or a construct's name the Dictionary does not know is a scientific noun."""
    unknown = ("brisa", "brisa", "brisa", "zeph", "zeph")
    assert _approved_then_asked(tmp_path, unknown) == {
        "a group: kelt vorn + brisa",
        "a group: kelt vorn + zeph",
    }


# ---------------------------------------------------------------------------
# The shipped name rules: a version family, and one acquisition's files.
# ---------------------------------------------------------------------------


def _in_force(tmp_path: Path) -> dict[str, Any]:
    """The Rulebook in force, each rule by its id: the shipped book, revision 4 within it."""
    from ingence.rulebook.load import load_revisions
    from ingence.rulebook.predicates import load_builtins
    from ingence.rulebook.resolve import effective_rulebook

    del tmp_path
    load_builtins()
    return {rule.rule_id: rule for rule in effective_rulebook(load_revisions()).rules}


def _key(rule: Any, *parts: tuple[str, str]) -> object:
    """The class key a rule in force gives a name read as these components, pool-wide."""
    from types import SimpleNamespace

    from ingence.core.schema import Component
    from ingence.infer.evaluate import parameters_in_force
    from ingence.rulebook.predicates.group import SameComponentValues

    components = tuple(
        Component(
            component_id=f"c{index}",
            entry_id="e",
            run_id="r",
            component_type=kind,
            normalized_value=value,
            normalizer="n",
            normalizer_version="1",
        )
        for index, (kind, value) in enumerate(parts)
    )
    subject = SimpleNamespace(components=components, bindings=())
    found = SameComponentValues().evaluate(subject, parameters_in_force(rule))
    return None if found is None else found.key


def test_folders_differing_only_by_a_date_are_no_version_family(tmp_path: Path) -> None:
    """``same_version_family``: a folder matching a sibling's name but for a version marker.

    Every part of the name but the version marker is its key, so a date or a count keeps two
    folders apart. Keyed by its words alone, folders differing only by a date would be one
    family."""
    family = _in_force(tmp_path)["same_version_family"]
    words = (("word", "kelt"), ("word", "vorn"))
    first = _key(family, *words, ("date", "2024-03-01"))
    assert first != _key(family, *words, ("date", "2024-03-08"))
    assert first == _key(family, *words, ("date", "2024-03-01"), ("version", "v2"))
    assert _key(family, *words, ("number", "1")) != _key(family, *words, ("number", "2"))


def test_no_rule_keys_a_group_by_an_index_alone(tmp_path: Path) -> None:
    """``same_acquisition_event`` is retired.

    Keyed by indexed labels alone, pool-wide, ``s1`` in every experiment would be one class, which
    is a group made by a replicate identifier. One acquisition's files are grouped by the rules
    reading its name."""
    in_force = _in_force(tmp_path)
    assert "same_acquisition_event" not in in_force
    for rule in in_force.values():
        if rule.predicate == "same_component_values":
            kinds = set(parameters_in_force_of(rule)["component_kinds"])
            assert kinds - {"indexed", "number", "date", "version"}, rule.rule_id


def parameters_in_force_of(rule: Any) -> dict[str, Any]:
    from ingence.infer.evaluate import parameters_in_force

    return dict(parameters_in_force(rule))


# ---------------------------------------------------------------------------
# The principle in the group rules' own plain words, on the Rules page.
# ---------------------------------------------------------------------------


def test_the_group_rules_say_every_question_is_a_set_experiments_share_exactly() -> None:
    from ingence.api.plain_words import plain_words

    said = plain_words().rules
    for rule_id in ("group_resolved_across_experiments", "group_resolution_deviation"):
        assert "share exactly" in said[rule_id], rule_id
        assert "date" in said[rule_id] and "number" in said[rule_id], rule_id


def test_the_lattice_route_says_from_how_many_experiments_a_set_is_asked(tmp_path: Path) -> None:
    """The Groupings page lists a set held by two to four experiments as not asked, and says why:
    the route says the fewest the run asks from."""
    from ingence.api.v1 import V1Context, groups

    layout = [(f"{day} kelt vorn", ["kelt_vorn_01.nd2", "kelt_vorn_02.nd2"]) for day in _DAYS]
    created = _project(tmp_path, layout)
    run = "run_20260101T000002Z_000000000002"
    context = V1Context(project=created.path, run_current=run, run_pinned=run, job_running=False)
    found = groups.lattice(context, {})
    assert found.minimum_experiments == 5
    (only,) = found.sets
    assert (only.terms, only.state, len(only.experiments)) == (["kelt", "vorn"], "not_asked", 3)


# ---------------------------------------------------------------------------
# The experiments' clustering: no proposal of a set too few experiments share.
# ---------------------------------------------------------------------------


def test_a_set_fewer_experiments_share_than_the_run_asks_from_is_asked_by_nobody(
    tmp_path: Path,
) -> None:
    """With ``minimum_experiments`` at five, a set three experiments share is listed as not
    asked, and the clustering does not ask it in its place."""
    layout = [(f"{day} quolt sorn", ["quolt_sorn_001.nd2", "quolt_sorn_002.nd2"]) for day in _DAYS]
    created = _project(tmp_path, layout)
    with open_catalog(created.path) as catalog:
        raised = catalog.query(
            "SELECT count(*) FROM findings WHERE rule_id = 'experiments_may_belong_together'"
        ).fetchone()
    assert raised == (0,)
    assert _lattice_asked(created) == set()


# ---------------------------------------------------------------------------
# The clustering's whole names, doses, constructs and wide counters.
# ---------------------------------------------------------------------------


def test_a_whole_names_feature_reads_the_name_without_what_it_counts() -> None:
    """Two experiments holding ``img_001`` and ``img_002`` are not linked by their counters."""
    from ingence.grouping.experiment_links import experiment_rows

    rows = experiment_rows(
        {"A": ((), ""), "B": ((), "")},
        [
            ("A", ("img",), "img_001.tif"),
            ("A", ("scan",), "scan_kelt_20240301_01.tif"),
            ("B", ("img",), "img_002.tif"),
        ],
    )
    named = {feature for found in rows.values() for feature in found if feature.startswith("n:")}
    assert named == {"n:scankelt"}  # img alone is too short; no date and no counter


@pytest.mark.parametrize(
    "files",
    [
        lambda day: ["img_001.tif", "img_002.tif"],
        lambda day: ["scan_20240301_01.tif", "scan_20240301_02.tif"],
    ],
    ids=["counters", "dated"],
)
def test_experiments_holding_counted_names_alike_are_linked_by_no_counted_name(
    tmp_path: Path, files: Callable[[str], list[str]]
) -> None:
    """What may still link them is a word they share and the folder they sit in, never a name
    with its counter or date (not "sharing img001, img002" and not "scan2024030101")."""
    layout = [
        ("20240301 brisa", files("")),
        ("20240308 zeph", files("")),
        ("20240315 marn", ["sulk_a.nd2", "trell_b.nd2"]),
    ]
    created = _project(tmp_path, layout)
    with open_catalog(created.path) as catalog:
        reasons = [
            str(reason).split(": ", 1)[0].split("sharing ", 1)[-1]
            for (reason,) in catalog.query(
                "SELECT reason FROM findings WHERE rule_id = 'experiments_may_belong_together'"
            ).fetchall()
        ]
    assert not any(character.isdigit() for reason in reasons for character in reason), reasons


def test_a_dose_or_a_time_across_experiments_is_no_series_and_is_asked() -> None:
    """A number a unit follows is a condition, never what a name counts."""
    from ingence.grouping.identifiers import series_across

    doses = [("A", "kelt_vorn_1mM.tif"), ("B", "kelt_vorn_2mM.tif"), ("C", "kelt_vorn_5mM.tif")]
    assert not series_across(doses)
    times = [("A", "kelt_vorn_1h.tif"), ("B", "kelt_vorn_2h.tif"), ("C", "kelt_vorn_4h.tif")]
    assert not series_across(times)


def test_an_indexed_label_is_a_replicates_only_where_its_label_is_one() -> None:
    """``pQ1`` names a construct and ``v1`` a version; ``rep1`` and ``r1`` count repeats."""
    from ingence.grouping.identifiers import series_across

    places = ("A", "B", "C")
    for label in ("pQ", "v", "s"):
        named = [(p, f"kelt_vorn_{label}{i}.nd2") for i, p in enumerate(places, 1)]
        assert not series_across(named), label
    for label in ("r", "rep"):
        named = [(p, f"kelt_vorn_{label}{i}.nd2") for i, p in enumerate(places, 1)]
        assert series_across(named, replicate_labels={"rep"}), label


def test_a_counter_padded_to_five_digits_counts_and_siblings_count_in_their_folder() -> None:
    """``00001`` to ``00009`` count; and a folder's own siblings decide."""
    from ingence.grouping.identifiers import series_across, slot_kind

    assert slot_kind([f"{i:05d}" for i in range(1, 10)]) == "counter"
    padded = [(p, f"kelt_vorn_{i:05d}.tif") for p in ("A", "B", "C") for i in range(1, 10)]
    assert series_across(padded)
    # Each experiment counts 1 to 3; pooled over three experiments the values still count.
    counted = [(p, f"kelt_{n}_{i}.tif") for n, p in ((1, "A"), (5, "B")) for i in (1, 2, 3)]
    assert series_across(counted)


def test_the_three_lattice_predicates_changed_their_versions() -> None:
    """Their versions moved because their verdicts changed for unchanged input (a date's or a
    replicate's word is not a term, and an approval naming one is read without it). They moved
    again when a bound word entered the lattice as its Dictionary term."""
    from ingence.rulebook.predicates.experiments import (
        GroupDeviation,
        ResolvedGroupAcrossExperiments,
        ResolvedGroupWithinExperiment,
    )
    from ingence.rulebook.registry import PREDICATES

    for predicate, version in (
        (ResolvedGroupWithinExperiment, "3"),
        (ResolvedGroupAcrossExperiments, "4"),
        (GroupDeviation, "3"),
    ):
        assert predicate.version == version, predicate.name
        assert PREDICATES.declaration(predicate.name).version == version, predicate.name


def test_an_earlier_approval_naming_a_months_word_still_applies_read_without_it(
    tmp_path: Path,
) -> None:
    """An approval made when five experiments shared "kelt march vorn" is read without March,
    which is not a term. It still groups the five, and the Groupings page says what it was
    approved as."""
    from ingence.api.v1 import V1Context, groups
    from ingence.record.targets import group_target

    layout = [
        (f"{day} kelt vorn march", [f"kelt_vorn_march_{index:02d}.nd2" for index in (1, 2)])
        for day in _FIVE
    ]
    created = _project(tmp_path, layout)
    with open_catalog(created.path) as catalog:
        from ingence.api.routes.decide import Decision, DecisionContext, decide_target

        decide_target(
            DecisionContext(catalog, created.path),
            Decision(target=group_target(("kelt", "march", "vorn")), word="accept"),
            actor="ines",
            occurred_at="2026-01-01T00:00:30Z",
        )
    _infer(created, 3)
    with open_catalog(created.path) as catalog:
        (claims,) = catalog.query(
            "SELECT count(*) FROM claims WHERE rule_id = 'group_resolved_across_experiments' "
            "AND resolution_status = 'resolved'"
        ).fetchone()
    assert claims == 2 * len(_FIVE)
    assert _lattice_asked(created) == set()  # nothing asked again
    run = "run_20260101T000003Z_000000000003"
    context = V1Context(project=created.path, run_current=run, run_pinned=run, job_running=False)
    (core,) = [s for s in groups.lattice(context, {}).sets if s.terms == ["kelt", "vorn"]]
    assert (core.state, core.decided_by) == ("approved", "ines")
    assert (core.answered_as, core.read_without) == ("kelt march vorn", ["march"])


# ---------------------------------------------------------------------------
# An earlier answer reaches only what it named; rep labels count.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("word", ["accept", "refuse"])
def test_an_earlier_answer_read_without_its_month_reaches_only_the_experiments_naming_it(
    tmp_path: Path, word: str
) -> None:
    """An approval cannot apply to something it does not contain.

    Five March experiments were answered as "kelt march vorn"; two April ones holding kelt vorn
    were never asked. The answer covers the March five alone; the April two are asked."""
    from ingence.api.routes.decide import Decision, DecisionContext, decide_target
    from ingence.api.v1 import V1Context, groups
    from ingence.record.targets import group_target

    layout = [
        (f"{day} kelt vorn march", [f"kelt_vorn_march_{index:02d}.nd2" for index in (1, 2)])
        for day in _FIVE
    ] + [
        (f"2024040{n} kelt vorn april", [f"kelt_vorn_april_{index:02d}.nd2" for index in (1, 2)])
        for n in (2, 9)
    ]
    created = _project(tmp_path, layout)
    with open_catalog(created.path) as catalog:
        decide_target(
            DecisionContext(catalog, created.path),
            Decision(target=group_target(("kelt", "march", "vorn")), word=word),
            actor="ines",
            occurred_at="2026-01-01T00:00:30Z",
        )
    _infer(created, 3)
    with open_catalog(created.path) as catalog:
        grouped = {
            str(path).split("/", 1)[0]
            for (path,) in catalog.query(
                "SELECT e.relative_path FROM claims c JOIN entries e ON e.entry_id = c.subject "
                "WHERE c.rule_id = 'group_resolved_across_experiments' "
                "AND c.resolution_status = 'resolved'"
            ).fetchall()
        }
        raising = {
            str(path)
            for (path,) in catalog.query(
                "SELECT e.relative_path FROM findings f, unnest(f.raised) AS u(value) "
                "JOIN entries e ON e.entry_id = regexp_replace(f.subject, '^folder:', '') "
                "WHERE f.rule_id = 'group_resolution_deviation' AND u.value = 'a group: kelt vorn'"
            ).fetchall()
        }
    march = {f"{day} kelt vorn march" for day in _FIVE}
    april = {"20240402 kelt vorn april", "20240409 kelt vorn april"}
    assert grouped == (march if word == "accept" else set())
    assert raising == april  # asked of the two nobody was asked about, never of the five again
    run = "run_20260101T000003Z_000000000003"
    context = V1Context(project=created.path, run_current=run, run_pinned=run, job_running=False)
    (core,) = [s for s in groups.lattice(context, {}).sets if s.terms == ["kelt", "vorn"]]
    assert (core.answered_as, core.read_without) == ("kelt march vorn", ["march"])
    assert sorted(core.read_for) == sorted(march)
    assert core.state == "asked"


def test_a_replicates_label_counts_through_the_pass_and_is_asked_across_no_experiments(
    tmp_path: Path,
) -> None:
    """The labels the Dictionary binds to the replicate kind (``rep2`` for one) reach the page
    and the clustering from the run itself, never passed by hand."""
    for label in ("rep", "trial"):
        base = tmp_path / label
        layout = [
            (f"{day} kelt vorn", [f"kelt_vorn_{label}{index}.nd2" for index in (1, 2, 3)])
            for day in _DAYS
        ]
        created = _project(base, layout)
        assert _asked_across(created) == [], label
        with open_catalog(created.path) as catalog:
            reasons = [
                str(reason)
                for (reason,) in catalog.query(
                    "SELECT reason FROM findings WHERE rule_id = 'experiments_may_belong_together'"
                ).fetchall()
            ]
        assert not any(f"{label}1" in reason for reason in reasons), reasons


def test_a_labels_number_that_counts_no_repeat_is_said_to_be_a_number() -> None:
    """The pattern says what the page judges: ``pQ{1-3}`` is not a counter."""
    from ingence.api.name_patterns import name_pattern

    for label in ("pQ", "v", "s"):
        shaped = name_pattern([f"kelt_vorn_{label}{i}.nd2" for i in (1, 2, 3)])
        assert shaped.dimensions == ("number",), label
    counted = name_pattern([f"kelt_vorn_rep{i}.nd2" for i in (1, 2, 3)], replicate_labels={"rep"})
    assert counted.dimensions == ("counter",)
    assert name_pattern([f"kelt_vorn_r{i}.nd2" for i in (1, 2, 3)]).dimensions == ("counter",)


def test_an_earlier_answer_whose_word_no_experiment_names_reaches_none() -> None:
    from ingence.grouping.resolutions import ScopedAnswer, group_lattice, resolve_by_lattice

    held = _held_by(5)
    nobody = ScopedAnswer(
        ("kelt", "vorn"), True, frozenset(), answered_as=("kelt", "march", "vorn")
    )
    assert resolve_by_lattice([], held, scoped=[nobody]) == {}
    assert _state(group_lattice(held, scoped=[nobody]), ("kelt", "vorn")) == ASKED
    some = ScopedAnswer(("kelt", "vorn"), True, frozenset({"day 0", "day 1"}))
    assert set(resolve_by_lattice([], held, scoped=[some])) == {"day 0", "day 1"}
    lattice = group_lattice(held, scoped=[some])
    core = next(c for c in lattice.concepts if c.terms == ("kelt", "vorn"))
    assert (core.state, core.covered) == (ASKED, ("day 0", "day 1"))


def test_a_later_answer_is_shown_as_its_own_and_the_earlier_one_on_its_own_line(
    tmp_path: Path,
) -> None:
    """A set takes who decided it from the answer that gave it its state, a plain answer on
    its own words winning over one read without a month's word; that one keeps its own line."""
    from ingence.api.routes.decide import Decision, DecisionContext, decide_target
    from ingence.api.v1 import V1Context, groups
    from ingence.record.targets import group_target

    layout = [
        (f"{day} kelt vorn march", [f"kelt_vorn_march_{index:02d}.nd2" for index in (1, 2)])
        for day in _FIVE
    ] + [
        (f"2024040{n} kelt vorn april", [f"kelt_vorn_april_{index:02d}.nd2" for index in (1, 2)])
        for n in (2, 9)
    ]
    created = _project(tmp_path, layout)
    with open_catalog(created.path) as catalog:
        decide_target(
            DecisionContext(catalog, created.path),
            Decision(target=group_target(("kelt", "march", "vorn")), word="accept"),
            actor="ines",
            occurred_at="2026-01-01T00:00:30Z",
        )
    _infer(created, 3)
    with open_catalog(created.path) as catalog:
        decide_target(
            DecisionContext(catalog, created.path),
            Decision(target="question:group_resolution_deviation:a group: kelt vorn", word="wrong"),
            actor="tomas",
            occurred_at="2026-01-01T00:00:40Z",
        )
    _infer(created, 4)
    run = "run_20260101T000004Z_000000000004"
    context = V1Context(project=created.path, run_current=run, run_pinned=run, job_running=False)
    (core,) = [s for s in groups.lattice(context, {}).sets if s.terms == ["kelt", "vorn"]]
    assert (core.state, core.decided_by) == ("declined", "tomas")
    assert (core.answered_by, core.answered_yes, core.answered_as) == (
        "ines",
        True,
        "kelt march vorn",
    )
    assert len(core.read_for) == len(_FIVE)
