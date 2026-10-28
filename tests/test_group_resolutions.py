"""A group a person approved, resolved for every experiment sharing its core set of terms.

Approving a core set of terms resolves every occurrence that shares it. Identical sets become a
group per experiment (each day its own) and one group across the experiments. For supersets and
subsets, the shared core is concluded as a group common to them all, and each deviation (a word
added, a word missing) is its own question, asked first. It does not recurse into finer groups. A
group is asked again only when new files raise a new question. A plain new replicate joins its
group unasked.

Every name is invented; the person approving is an invented person, since the rule reads any actor's
decision alike. These tests pin the behaviour reachable as ``derivations: each_deviation``. The
lattice is the default.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fixtures.revision_4 import each_deviation, tune
from fixtures.revision_4 import write_revision as write_revision_4
from ingence.api.routes.review import review
from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.grouping.resolutions import SUBSET, SUPERSET, resolve_groups
from ingence.infer.run import run_inference
from ingence.record.log import append_event
from ingence.record.targets import group_target, group_terms_named_by, target_kind
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

PERSON = "brisa (an invented person)"


def write_revision(project_path: Path) -> Path:
    """Revision 4 with the ``derivations: each_deviation`` behaviour, which these tests pin
    unchanged beside the lattice's (``test_group_lattice.py``)."""
    return each_deviation(write_revision_4(project_path))


# ---------------------------------------------------------------------------
# The sets, alone.
# ---------------------------------------------------------------------------


def test_identical_sets_are_a_group_per_experiment_and_one_across() -> None:
    found = resolve_groups(
        [("kelt", "vorn", "zib")],
        {"day1": {"kelt", "vorn", "zib"}, "day2": {"zib", "vorn", "kelt"}, "other": {"brisa"}},
    )
    assert set(found) == {"day1", "day2"}
    assert found["day1"].within != found["day2"].within  # each day its own
    assert found["day1"].across == found["day2"].across == "kelt vorn zib"
    assert found["day1"].deviations == ()


def test_a_superset_shares_the_core_and_its_added_word_is_a_question() -> None:
    found = resolve_groups(
        [("kelt", "vorn", "zib")],
        {"day1": {"kelt", "vorn", "zib"}, "day3": {"kelt", "vorn", "zib", "trell"}},
    )
    assert found["day3"].relation == SUPERSET
    assert found["day3"].across == found["day1"].across == "kelt vorn zib"
    assert found["day3"].deviations == ("trell, added to the group kelt vorn zib",)


def test_a_subset_narrows_the_group_common_to_them_all_and_asks_the_missing_word() -> None:
    found = resolve_groups(
        [("kelt", "vorn", "zib")],
        {"day1": {"kelt", "vorn", "zib"}, "day4": {"kelt", "vorn"}},
    )
    assert found["day4"].relation == SUBSET
    common = "kelt vorn (of the approved kelt vorn zib)"
    assert found["day1"].across == found["day4"].across == common  # common to them all
    assert found["day4"].deviations == ("zib, missing from the group kelt vorn zib",)


def test_it_does_not_recurse_and_leaves_partial_overlaps_and_small_subsets_alone() -> None:
    found = resolve_groups(
        [("kelt", "vorn", "zib")],
        {
            "day1": {"kelt", "vorn", "zib"},
            "one_word": {"kelt"},  # too small a core to share
            "overlap": {"kelt", "vorn", "brisa"},  # neither superset nor subset
        },
    )
    assert set(found) == {"day1"}


def test_two_approved_cores_narrowed_to_one_common_set_stay_two_groups() -> None:
    """The across key names the approved core, so no group forms nobody approved."""
    found = resolve_groups(
        [("a", "b", "c", "d"), ("a", "b", "e", "f")],
        {
            "A1": {"a", "b", "c", "d"},
            "A2": {"a", "b", "d"},
            "A3": {"a", "b", "c"},
            "C1": {"a", "b", "e", "f"},
            "C2": {"a", "b", "e"},
            "C3": {"a", "b", "f"},
        },
    )
    assert found["A1"].across == found["A2"].across == found["A3"].across
    assert found["C1"].across == found["C2"].across == found["C3"].across
    assert found["A1"].across != found["C1"].across
    assert "a b c d" in found["A1"].across and "a b e f" in found["C1"].across


def test_an_experiment_adding_many_words_shares_the_core_and_each_word_is_asked() -> None:
    """A proposal's core is what its members all hold, so every member adds words to it, often
    a dozen. The member joins the group, and each added word is a question."""
    found = resolve_groups(
        [("kelt", "vorn")],
        {
            "near": {"kelt", "vorn", "zib"},
            "far": {"kelt", "vorn", "brisa", "trell", "quolt", "sorn", "tamen"},
        },
    )
    assert set(found) == {"near", "far"}
    assert found["far"].across == found["near"].across == "kelt vorn"
    assert found["near"].deviations == ("zib, added to the group kelt vorn",)
    assert len(found["far"].deviations) == 5  # every added word, however many


def test_of_two_cores_held_equally_the_one_fewer_experiments_hold_wins() -> None:
    common = {f"day{n}": {"kelt", "vorn", "x" + str(n)} for n in range(4)}
    both = {"both": {"kelt", "vorn", "zib", "brisa"}, "rare": {"zib", "brisa", "trell"}}
    found = resolve_groups([("kelt", "vorn"), ("brisa", "zib")], {**common, **both})
    assert found["both"].core == ("brisa", "zib")  # held by two, where kelt vorn by five


def test_an_experiment_holding_one_approved_set_exactly_is_not_asked_about_a_smaller_one() -> None:
    found = resolve_groups(
        [("kelt", "vorn"), ("kelt", "vorn", "zib")],
        {"day1": {"kelt", "vorn", "zib"}, "day2": {"kelt", "vorn"}},
    )
    assert found["day1"].core == ("kelt", "vorn", "zib")
    assert found["day1"].deviations == ()
    assert found["day2"].core == ("kelt", "vorn")


def test_a_group_core_is_a_record_target_of_its_own_in_any_order() -> None:
    target = group_target(["Vorn", "kelt", "vorn"])
    assert target == group_target(["kelt", "vorn"]) == "group:kelt vorn"
    assert target_kind(target) == "group"
    assert group_terms_named_by(target) == ("kelt", "vorn")
    assert group_terms_named_by("question:x:y") is None
    assert group_terms_named_by('group:some_rule:{"stage": "raw"}') is None  # a page's own key


# ---------------------------------------------------------------------------
# Through the pass.
# ---------------------------------------------------------------------------


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _pool(base: Path) -> Path:
    pool = base / "pool"
    for day in ("20240301 kelt vorn", "20240302 kelt vorn"):
        for name in ("kelt_vorn_01.nd2", "kelt_vorn_02.nd2"):
            _write(pool / day / name)
    _write(pool / "20240303 kelt vorn" / "kelt_vorn_01.nd2")
    _write(pool / "20240303 kelt vorn" / "kelt_vorn_trell_02.nd2")
    _write(pool / "20240304 brisa quolt" / "brisa_quolt_01.nd2")
    return pool


def _run(project: Project, stamp: int) -> str:
    run_id = f"run_20260101T0000{stamp:02d}Z_{stamp:012d}"
    run_inference(project, run_id=run_id, started_at="t")
    return run_id


def _approve(project: Project, terms: list[str], run_id: str) -> None:
    append_event(
        project.path,
        kind=RecordEventKind.CONFIRM,
        actor=PERSON,
        occurred_at="2026-01-01T00:00:30Z",
        target=group_target(terms),
        against_run_id=run_id,
        payload={"terms": sorted(terms)},
    )


def _claims(project: Project, rule_id: str) -> dict[str, str]:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.relative_path, c.object_reference FROM claims c "
            "JOIN entries e ON e.entry_id = c.subject "
            "WHERE c.rule_id = ? AND c.resolution_status = 'resolved'",
            [rule_id],
        ).fetchall()
    return dict(rows)


def _deviations(project: Project) -> set[str]:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT raised FROM findings WHERE rule_id = 'group_resolution_deviation'"
        ).fetchall()
    return {value for (raised,) in rows for value in raised}


def _project(tmp_path: Path) -> tuple[Project, Path]:
    pool = _pool(tmp_path)
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(created.path)
    run_census(created, run_id="run_20260101T000001Z_000000000001", started_at="t")
    return created, pool


def test_an_approved_core_groups_every_experiment_sharing_it_and_asks_the_added_word(
    tmp_path: Path,
) -> None:
    created, _ = _project(tmp_path)
    first = _run(created, 2)
    assert _claims(created, "group_resolved_across_experiments") == {}  # nothing approved yet
    _approve(created, ["kelt", "vorn"], first)
    _run(created, 3)
    within = _claims(created, "group_resolved_within_experiment")
    across = _claims(created, "group_resolved_across_experiments")
    day1, day2, day3 = (f"2024030{n} kelt vorn" for n in (1, 2, 3))
    assert within[f"{day1}/kelt_vorn_01.nd2"] == within[f"{day1}/kelt_vorn_02.nd2"]
    assert within[f"{day1}/kelt_vorn_01.nd2"] != within[f"{day2}/kelt_vorn_01.nd2"]  # each day
    assert {across[f"{d}/kelt_vorn_01.nd2"] for d in (day1, day2, day3)} == {"kelt vorn"}
    assert across[f"{day3}/kelt_vorn_trell_02.nd2"] == "kelt vorn"  # it joins, and is asked
    assert not any(path.startswith("20240304 brisa") for path in across)
    assert _deviations(created) == {"trell, added to the group kelt vorn"}


def test_a_plain_new_replicate_joins_unasked_and_a_new_word_asks_again(tmp_path: Path) -> None:
    """Only new files raising a new question are asked about."""
    pool = tmp_path / "pool"
    for name in ("zib_sorn_01.nd2", "zib_sorn_02.nd2"):
        _write(pool / "20240305 zib sorn" / name)
    _write(pool / "20240306 zib sorn" / "zib_sorn_01.nd2")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(created.path)
    run_census(created, run_id="run_20260101T000001Z_000000000001", started_at="t")
    first = _run(created, 2)
    _approve(created, ["zib", "sorn"], first)
    _run(created, 3)
    assert _deviations(created) == set()

    _write(pool / "20240305 zib sorn" / "zib_sorn_03.nd2")  # the next replicate
    run_census(created, run_id="run_20260101T000004Z_000000000004", started_at="t")
    _run(created, 5)
    assert _deviations(created) == set()
    assert "20240305 zib sorn/zib_sorn_03.nd2" in _claims(
        created, "group_resolved_across_experiments"
    )

    _write(pool / "20240305 zib sorn" / "zib_sorn_kelt_04.nd2")  # a new word
    run_census(created, run_id="run_20260101T000006Z_000000000006", started_at="t")
    _run(created, 7)
    assert _deviations(created) == {"kelt, added to the group sorn zib"}


def test_a_deviation_leads_the_review_queue(tmp_path: Path) -> None:
    """Asked first by its rule's own parameter, ahead of a question touching more files."""

    created, pool = _project(tmp_path)
    for number in range(5):  # five empty files: a question touching five, the deviation one
        (pool / "zib blanks").mkdir(exist_ok=True)
        (pool / "zib blanks" / f"zib_blank_{number:02d}.dat").write_text("")
    run_census(created, run_id="run_20260101T000001Z_000000000011", started_at="t")
    first = _run(created, 12)
    _approve(created, ["kelt", "vorn"], first)
    _run(created, 13)
    with open_catalog(created.path) as catalog:
        page = review(catalog)
    # A vocabulary pack's question leads even a deviation. A pack loaded changes
    # the words a group is asked in, so the deviation leads the questions after it.
    after_packs = [q for q in page["questions"] if not q["asked_before_groups"]]
    first_question = after_packs[0]
    assert first_question["rule_id"] == "group_resolution_deviation"
    assert first_question["asked_first"] is True

    tune(created.path, "asked_first", False, rules=("group_resolution_deviation",))
    _run(created, 14)
    with open_catalog(created.path) as catalog:
        page = review(catalog)
    after_packs = [q for q in page["questions"] if not q["asked_before_groups"]]
    assert after_packs[0]["rule_id"] != "group_resolution_deviation"


def test_confirming_a_deviation_makes_it_a_group_of_its_own(tmp_path: Path) -> None:
    """Each deviation is left as its own group for confirmation."""
    from ingence.grouping.resolutions import core_confirming

    assert core_confirming("trell, added to the group kelt vorn") == ("kelt", "trell", "vorn")
    assert core_confirming("zib, missing from the group kelt vorn zib") == ("kelt", "vorn")
    assert core_confirming("kelt vorn [a and 2 more]") is None
    created, _ = _project(tmp_path)
    first = _run(created, 2)
    _approve(created, ["kelt", "vorn"], first)
    (value,) = _deviations(created) or {""}
    _run(created, 3)
    (value,) = _deviations(created)
    confirmed = core_confirming(value)
    assert confirmed is not None
    _approve(created, list(confirmed), first)
    _run(created, 4)
    across = _claims(created, "group_resolved_across_experiments")
    assert across["20240303 kelt vorn/kelt_vorn_trell_02.nd2"] == "kelt trell vorn"
    assert _deviations(created) == set()


def test_a_proposed_group_says_its_core_terms_for_a_person_to_approve(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    for day, words in (
        ("20240301", "kelt vorn"),
        ("20240302", "kelt vorn"),
        ("20240303", "brisa quolt"),
        ("20240304", "zib sorn"),
        ("20240305", "trell tamen"),
    ):
        for number in (1, 2):
            _write(pool / f"{day} {words}" / f"{words.replace(' ', '_')}_{number:02d}.nd2")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(created.path)
    run_census(created, run_id="run_20260101T000001Z_000000000001", started_at="t")
    _run(created, 2)
    with open_catalog(created.path) as catalog:
        reasons = [
            reason
            for (reason,) in catalog.query(
                "SELECT reason FROM findings WHERE rule_id = 'experiments_may_belong_together'"
            ).fetchall()
        ]
    assert reasons
    assert all("core terms: kelt vorn" in reason for reason in reasons)


def test_an_analysis_output_s_words_and_a_glued_label_are_no_terms(tmp_path: Path) -> None:
    """The default reads the folder's name and its acquisitions, words without digits only."""
    pool = tmp_path / "pool"
    for name in ("kelt_vorn_01.nd2", "kelt_vorn_02.nd2", "Traj_kelt_vorn_01_brisaplot.csv"):
        _write(pool / "20240301 kelt vorn" / name)
    for name in ("kelt_vorn_01.nd2", "kelt_vorn_02.nd2"):
        _write(pool / "20240302 kelt vorn" / name)
    _write(pool / "20240303 kelt vorn" / "kelt_vorn_zib1hr001.nd2")
    _write(pool / "20240304 brisa quolt" / "brisa_quolt_01.nd2")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(created.path)
    run_census(created, run_id="run_20260101T000001Z_000000000001", started_at="t")
    first = _run(created, 2)
    _approve(created, ["kelt", "vorn"], first)
    _run(created, 3)
    across = _claims(created, "group_resolved_across_experiments")
    assert {path.split("/")[0] for path in across} == {
        "20240301 kelt vorn",
        "20240302 kelt vorn",
        "20240303 kelt vorn",
    }
    assert _deviations(created) == set()


def test_an_approved_group_is_not_proposed_again_when_a_new_day_joins_it(tmp_path: Path) -> None:
    """At the level of the proposal, a new replicate day joins unasked."""
    pool = tmp_path / "pool"
    days = [("20240301", "kelt vorn"), ("20240302", "kelt vorn")]
    # Enough others that three days of one pair stay a rare link (at most a quarter of them).
    others = [
        (f"202404{number:02d}", f"{first} {second}")
        for number, (first, second) in enumerate(
            (
                ("brisa", "quolt"),
                ("zib", "sorn"),
                ("trell", "tamen"),
                ("quolt", "morv"),
                ("sorn", "dask"),
                ("tamen", "pell"),
                ("morv", "brisa"),
                ("dask", "zib"),
                ("pell", "trell"),
            ),
            start=1,
        )
    ]
    for day, words in days + others:
        for number in (1, 2):
            _write(pool / f"{day} {words}" / f"{words.replace(' ', '_')}_{number:02d}.nd2")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(created.path)
    run_census(created, run_id="run_20260101T000001Z_000000000001", started_at="t")
    first = _run(created, 2)

    def proposed() -> set[str]:
        with open_catalog(created.path) as catalog:
            rows = catalog.query(
                "SELECT e.relative_path FROM findings f JOIN entries e "
                "ON f.subject LIKE '%' || e.entry_id || '%' "
                "WHERE f.rule_id = 'experiments_may_belong_together' "
                "AND e.entry_type = 'directory'"
            ).fetchall()
        return {path for (path,) in rows}

    assert {"20240301 kelt vorn", "20240302 kelt vorn"} <= proposed()
    _approve(created, ["kelt", "vorn"], first)
    for number in (1, 2):
        _write(pool / "20240306 kelt vorn" / f"kelt_vorn_{number:02d}.nd2")
    run_census(created, run_id="run_20260101T000003Z_000000000003", started_at="t")
    _run(created, 4)
    assert not {path for path in proposed() if path.endswith("kelt vorn")}
    across = _claims(created, "group_resolved_across_experiments")
    assert across["20240306 kelt vorn/kelt_vorn_01.nd2"] == "kelt vorn"
    assert _deviations(created) == set()


def test_approving_a_proposed_groups_offered_core_resolves_its_members(tmp_path: Path) -> None:
    """The core the proposal offers is held by every member with several words more.
    Approving it resolves every member and asks every word."""
    pool = tmp_path / "pool"
    days = [
        ("20240301", "kelt vorn brisa quolt sorn"),
        ("20240302", "kelt vorn trell tamen morv"),
        ("20240303", "kelt vorn dask pell zib"),
    ]
    others = [
        (f"202404{number:02d}", words)
        for number, words in enumerate(
            ("alpa bret", "cind dorn", "elk fenn", "gorb hask", "ilm jorv", "kasp lunt"), start=1
        )
    ]
    for day, words in days + others:
        for number in (1, 2):
            _write(pool / f"{day} {words}" / f"{words.replace(' ', '_')}_{number:02d}.nd2")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(created.path)
    run_census(created, run_id="run_20260101T000001Z_000000000001", started_at="t")
    first = _run(created, 2)
    _approve(created, ["kelt", "vorn"], first)
    _run(created, 3)
    across = _claims(created, "group_resolved_across_experiments")
    assert {path.split("/")[0] for path in across} == {f"{day} {words}" for day, words in days}
    assert set(across.values()) == {"kelt vorn"}
    assert len(_deviations(created)) == 9  # three words more each, every one asked


def test_a_root_that_is_itself_an_experiment_asks_its_missing_word(tmp_path: Path) -> None:
    """A root folder ``.`` is an experiment like any other; its children are read by
    their parent's identifier, and its deviation is raised."""
    root = tmp_path / "20240303 kelt vorn"
    for name in ("kelt_vorn_01.nd2", "kelt_vorn_02.nd2"):
        _write(root / name)
    created = Project.create(tmp_path / "project", created_at="t", roots=[root])
    write_revision(created.path)
    run_census(created, run_id="run_20260101T000001Z_000000000001", started_at="t")
    first = _run(created, 2)
    _approve(created, ["kelt", "vorn", "zib"], first)
    _run(created, 3)
    assert set(_claims(created, "group_resolved_across_experiments").values()) == {
        "kelt vorn (of the approved kelt vorn zib)"
    }
    assert _deviations(created) == {"zib, missing from the group kelt vorn zib"}


def test_two_roots_each_an_experiment_are_two_experiments(tmp_path: Path) -> None:
    """Two roots each a folder ``.`` never share one path-keyed experiment."""
    roots = []
    for day in ("20240301 kelt vorn", "20240302 kelt vorn"):
        root = tmp_path / "pools" / day
        for name in ("kelt_vorn_01.nd2", "kelt_vorn_02.nd2"):
            _write(root / name)
        roots.append(root)
    created = Project.create(tmp_path / "project", created_at="t", roots=roots)
    write_revision(created.path)
    run_census(created, run_id="run_20260101T000001Z_000000000001", started_at="t")
    first = _run(created, 2)
    _approve(created, ["kelt", "vorn"], first)
    _run(created, 3)
    with open_catalog(created.path) as catalog:
        rows = catalog.query(
            "SELECT e.root_id, c.object_reference FROM claims c JOIN entries e "
            "ON e.entry_id = c.subject WHERE c.rule_id = 'group_resolved_within_experiment' "
            "AND c.resolution_status = 'resolved' AND e.entry_type = 'file'"
        ).fetchall()
    keys_by_root: dict[str, set[str]] = {}
    for root_id, key in rows:
        keys_by_root.setdefault(root_id, set()).add(key)
    assert len(keys_by_root) == 2
    assert all(len(keys) == 1 for keys in keys_by_root.values())
    assert len({key for keys in keys_by_root.values() for key in keys}) == 2  # each its own
