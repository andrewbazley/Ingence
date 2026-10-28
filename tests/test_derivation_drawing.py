"""The derivation proposals' pair drawing, bounded as its class.

A large census must not draw millions of candidate pairs within the named-for-an-acquisition
groups, because the drawing's peak would be the largest of the pass. A subject must not be handed
a copy of its whole group before any pair is read, and a pair must not be read end to end before
the stage refuses it.

What holds, each tested here on invented pools:

- A pair is formed only where its ends' own order admits one. A proposing predicate may rank
  each end on its own (``end_rank``) and say how far a supported pair's target stands above its
  source (``least_rise``); the drawing ranks each member of a class once and forms a pair only
  where the rise holds. A group holding no stage pair forms no pair at all.
- The rank is a contract, not a guess (``tests/test_derivation_ranks.py``). Every pair the
  stage predicate supports is one the rank admits, so the candidates, claims, findings and failures
  are the same with the drawing bounded or not, which is tested here on three invented pools.
- Pairs stream. A subject's counterparts are a view over its class, never a copy of it per
  subject, so the drawing holds a class, not the square of one.

No stage forms a pair, because derivations come before stages. The files named for one
acquisition are each paired with that acquisition, looked up by the group's key. So a group of n
forms n - 1 pairs, every one of them proposed, and none between two files the key does not join.
No proposing rule in revision 4 ranks its ends, so the drawing with ranks set aside is the
drawing. The rows-identical tests below check that.
"""

from __future__ import annotations

import tracemalloc
from typing import TYPE_CHECKING

import pytest

from fixtures.revision_4 import write_revision
from ingence.core.project import Project
from ingence.rulebook.predicates import load_builtins
from ingence.scan.census import run_census

if TYPE_CHECKING:
    from pathlib import Path

    from ingence.infer.run import InferenceReport

load_builtins()

CENSUS = "run_20260101T000001Z_111111111111"
DIVE = "run_20260101T000001Z_444444444444"
FIRST = "run_20260101T000002Z_222222222222"
SECOND = "run_20260101T000003Z_333333333333"
NAMED_FOR = "derivation_named_for_an_acquisition"
IDENTITY = "derivation_within_name_identity"
DESCRIBED = "derivation_named_by_a_describing_file"
ACQUISITION = "zephvorn001"


# ---------------------------------------------------------------------------
# Invented pools: one experiment, one acquisition, and files named for it.
# ---------------------------------------------------------------------------


def _pool(tmp_path: Path, unstaged: int, *, analysed: int = 0) -> Path:
    """An experiment holding one acquisition and files named for it across subfolders.

    ``unstaged`` files state no stage, so no pair holding one can be proposed; ``analysed`` files
    state ``analysis``, so the acquisition's group proposes each of them from the acquisition.
    """
    pool = tmp_path / "pool"
    experiment = pool / "2020-02-21 brisa kelt"
    (experiment / "raw").mkdir(parents=True)
    (experiment / "raw" / f"{ACQUISITION}.nd2").write_bytes(b"\0" * 16)
    for index in range(unstaged):
        folder = experiment / f"tiles{index % 7}"
        folder.mkdir(exist_ok=True)
        (folder / f"{ACQUISITION}_tile{index:04d}.txt").write_text("x\n")
    for index in range(analysed):
        (experiment / f"{ACQUISITION}_vorn{index}_analysis.csv").write_text("x,y\n1,2\n")
    return pool


def _censused(tmp_path: Path, pool: Path) -> Project:
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    run_census(project, run_id=CENSUS, started_at="2026-01-01T00:00:01Z")
    return project


def _counting_pairs(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Count, per proposing rule, the pairs handed to its predicate: a spy on the drawing."""
    import ingence.infer.candidates as candidates

    counted: dict[str, int] = {}
    original = candidates.support_for_pair

    def spy(rule, predicate, pair):  # type: ignore[no-untyped-def]
        counted[rule.rule_id] = counted.get(rule.rule_id, 0) + 1
        return original(rule, predicate, pair)

    monkeypatch.setattr(candidates, "support_for_pair", spy)
    return counted


def _infer(project: Project, run_id: str = FIRST) -> InferenceReport:
    from ingence.infer.run import run_inference

    return run_inference(project, run_id=run_id, started_at="2026-01-01T00:00:02Z")


def _set_ranks_aside(monkeypatch: pytest.MonkeyPatch) -> None:
    """Draw with the ranks set aside: every predicate reads as declaring none, so all pairs form."""
    import ingence.infer.candidates as candidates

    monkeypatch.setattr(candidates._Ranks, "of", classmethod(lambda cls, predicate, rule: None))


def _tally(report: InferenceReport, rule_id: str):  # type: ignore[no-untyped-def]
    return {tally.rule_id: tally for tally in report.proposals}[rule_id]


# ---------------------------------------------------------------------------
# The bound: no pair is formed where the ends' order admits none.
# ---------------------------------------------------------------------------


def test_a_group_forms_one_pair_per_file_its_key_joins_and_never_its_square(
    tmp_path, monkeypatch
) -> None:
    """Twenty files named for one acquisition, none stating a stage.

    Each file is paired with its acquisition alone, and each is proposed as made from it. The
    group's square, 380 ordered pairs, is never formed."""
    project = _censused(tmp_path, _pool(tmp_path, unstaged=19))
    counted = _counting_pairs(monkeypatch)

    report = _infer(project)

    tally = _tally(report, NAMED_FOR)
    assert tally.pairs == tally.formed == counted[NAMED_FOR] == 19
    assert (tally.supported, tally.truncated) == (19, 0)


def test_pairs_are_formed_from_the_acquisition_to_each_file_whatever_it_states(
    tmp_path, monkeypatch
) -> None:
    """Nine unstaged files and two analyses named for the acquisition.

    The acquisition's eleven pairs are formed, and never an analysis to its sibling."""
    project = _censused(tmp_path, _pool(tmp_path, unstaged=9, analysed=2))
    counted = _counting_pairs(monkeypatch)

    report = _infer(project)

    tally = _tally(report, NAMED_FOR)
    assert tally.pairs == counted[NAMED_FOR] == tally.formed == 11
    assert tally.supported == 11


def test_the_drawing_holds_a_class_and_never_its_square(tmp_path, monkeypatch) -> None:
    """Six hundred files named for one acquisition, the rank set aside.

    A subject is not handed a copy of its group. A copy per subject would hold the group's
    square in references at once, about 2.9 MB here, and a view holds the group once. The margin
    is generous (a quarter of the square) so the figure is stable. Each file is drawn with its
    acquisition alone, 599 pairs where 15,000 were cut from the square, and every one is
    proposed. The candidates are the phase's output rather than its drawing, so the pairs'
    support is left unread here, as the bound's truncation claims were, and only the drawing is
    measured.
    """
    members = 600
    project = _censused(tmp_path, _pool(tmp_path, unstaged=members - 1))
    import ingence.infer.candidates as candidates

    _set_ranks_aside(monkeypatch)
    import ingence.infer.run as run

    monkeypatch.setattr(candidates, "why_not_claim_ready", lambda bound, in_force: "unmade")
    monkeypatch.setattr(candidates, "support_for_pair", lambda rule, predicate, pair: ())

    original = run.propose
    peaks: dict[str, int] = {}

    def measured(rule, records, **kwargs):  # type: ignore[no-untyped-def]
        if rule.rule_id != NAMED_FOR:
            return original(rule, records, **kwargs)
        tracemalloc.start(1)
        try:
            return original(rule, records, **kwargs)
        finally:
            peaks[rule.rule_id] = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()

    monkeypatch.setattr(run, "propose", measured)

    report = _infer(project)

    assert _tally(report, NAMED_FOR).pairs == members - 1
    assert _tally(report, NAMED_FOR).truncated == 0
    square = members * (members - 1) * 8
    assert peaks[NAMED_FOR] < square // 4


def test_the_pairs_formed_are_the_bound_s_survivors_the_ranks_admit_in_every_arrangement() -> None:
    """Against the drawing written out whole: the pool but for the subject, cut to the first so
    many, then filtered by rank. Every pool of up to five, every place of the subject (or none, a
    mentioning file), every count the bounds may leave, ranks of None, 0 and 1, rises 0 and 1."""
    import itertools
    from types import SimpleNamespace

    from ingence.infer.candidates import _Counterparts, _Draw, _formed, _Member, _Ranks

    class Ranked:
        def __init__(self, ranks: dict[str, int | None], rise: int) -> None:
            self.ranks, self.rise = ranks, rise

        def end_rank(self, records, parameters):  # type: ignore[no-untyped-def]
            return self.ranks[records.entry.entry_id]

        def least_rise(self, parameters):  # type: ignore[no-untyped-def]
            return self.rise

    def member(name: str) -> _Member:
        records = SimpleNamespace(entry=SimpleNamespace(entry_id=name))
        return _Member(records=records, membership=name, key_records=(name,))  # type: ignore[arg-type]

    checked = 0
    for size in range(1, 6):
        pool = tuple(member(f"m{at}") for at in range(size))
        outside = member("mentioning")
        for position in (None, *range(size)):
            subject = outside if position is None else pool[position]
            others = [each for at, each in enumerate(pool) if at != position]
            for kept in range(len(others) + 1):
                view = _Counterparts(pool, position, kept)
                assert list(view) == [each.records for each in others[:kept]]
                assert view[:] == tuple(each.records for each in others[:kept])
                draw = _Draw(subject, pool=pool, position=position)
                assert list(_formed(draw, kept, None)) == others[:kept]
                for values in itertools.product((None, 0, 1), repeat=size + 1):
                    names = [each.entry_id for each in pool] + ["mentioning"]
                    ranks = dict(zip(names, values, strict=True))
                    for rise in (0, 1):
                        ranked = _Ranks(Ranked(ranks, rise), {})  # type: ignore[arg-type]
                        mine = ranks[subject.entry_id]
                        expected = [
                            each
                            for each in others[:kept]
                            if mine is not None
                            and ranks[each.entry_id] is not None
                            and ranks[each.entry_id] - mine >= rise
                        ]
                        assert list(_formed(draw, kept, ranked)) == expected
                        checked += 1
    assert checked > 10_000


# ---------------------------------------------------------------------------
# Rows identical: the drawing bounded by rank, and every pair drawn.
# ---------------------------------------------------------------------------


def _same_rows_bounded_or_not(project: Project, monkeypatch) -> tuple[object, object]:
    """Infer once drawing every pair (the rank set aside), once bounded; return both reports."""
    from ingence.storage.catalog import compare_runs

    with monkeypatch.context() as unbounded:
        _set_ranks_aside(unbounded)
        every = _infer(project, FIRST)
    bounded = _infer(project, SECOND)
    assert compare_runs(project.path, FIRST, SECOND) == {}
    assert (every.claims, every.candidates, every.findings, every.failures) == (
        bounded.claims,
        bounded.candidates,
        bounded.findings,
        bounded.failures,
    )
    for rule_id in (NAMED_FOR, IDENTITY, DESCRIBED):
        before, after = _tally(every, rule_id), _tally(bounded, rule_id)
        assert (before.pairs, before.supported, before.blocked, before.truncated) == (
            after.pairs,
            after.supported,
            after.blocked,
            after.truncated,
        )
        assert after.formed <= before.formed == before.pairs
    return every, bounded


def test_rows_are_the_same_on_the_generated_laboratory_pool(tmp_path, monkeypatch) -> None:
    from fixtures.laboratory_pool import write_pool

    pool = write_pool(tmp_path / "pool")
    project = _censused(tmp_path, pool.root)

    every, _ = _same_rows_bounded_or_not(project, monkeypatch)

    assert every.candidates > 0


def test_rows_are_the_same_on_the_evaluation_s_invented_laboratory(tmp_path, monkeypatch) -> None:
    from ingence.evaluation.generate import generate

    written = generate(tmp_path / "evaluation", seed=0, size=1)
    project = _censused(tmp_path, written.pool)

    every, bounded = _same_rows_bounded_or_not(project, monkeypatch)

    assert _tally(every, NAMED_FOR).supported > 0
    # No rule in force ranks its ends: the key alone bounds what is formed.
    assert _tally(bounded, NAMED_FOR).formed == _tally(every, NAMED_FOR).formed


def test_rows_are_the_same_where_a_group_proposes(tmp_path, monkeypatch) -> None:
    """Twenty-four members. Each of the 23 named for the acquisition is proposed as made from it."""
    project = _censused(tmp_path, _pool(tmp_path, unstaged=20, analysed=3))

    every, bounded = _same_rows_bounded_or_not(project, monkeypatch)

    assert every.candidates == 23
    assert _tally(bounded, NAMED_FOR).formed == 23


def test_rows_are_the_same_where_a_script_names_staged_and_unstaged_files(
    tmp_path, monkeypatch
) -> None:
    """A dived script naming a raw file, its processed file and six files stating no stage. Every
    pair of the eight is formed. None is ordered by time (a describing file's pair is not, in
    revision 4), and each is proposed once as related."""
    from ingence.core.settings import ExtractionSettings
    from ingence.scan.deepen import run_deepen

    pool = tmp_path / "pool"
    (pool / "vorn" / "data").mkdir(parents=True)
    named = ["raw_plate.csv", "processed_plate.csv"] + [f"kelt_{at}.csv" for at in range(6)]
    (pool / "vorn" / "run_kelt.py").write_text(
        "".join(f'FILE_{at} = "data/{name}"\n' for at, name in enumerate(named))
    )
    for name in named:
        (pool / "vorn" / "data" / name).write_text("well,value\nA1,1\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    project = project.save_settings(
        project.settings.model_copy(update={"extraction": ExtractionSettings(enabled=True)})
    )
    run_census(project, run_id=CENSUS, started_at="2026-01-01T00:00:01Z")
    run_deepen(project, run_id=DIVE, started_at="2026-01-01T00:00:01Z")

    every, bounded = _same_rows_bounded_or_not(project, monkeypatch)

    assert _tally(every, DESCRIBED).formed == _tally(bounded, DESCRIBED).formed == 8 * 7
    assert _tally(bounded, DESCRIBED).supported == 0
    assert _tally(bounded, "related_by_a_describing_file").supported == 8 * 7 // 2
