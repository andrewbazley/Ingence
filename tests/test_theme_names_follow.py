"""A person's name for a line of work survives a re-run that gives the line another label.

A line's Record target is built from its three-word label (``theme:<key>``). A re-run that reads
other words for the same experiments gives the line another label, so a name keyed by the label
would be lost. The name follows the line: it is served on the line of the run holding most of the
experiments the line held when it was named (ties: the most words in common, then the key's order).
Held here:

- the pure matching: a changed label, a tie, a statement that kept no experiments (read from the run
  it was written against), one that no run can read (it names the line with its key, if any), and a
  later statement winning, a removal included;
- on the engine: a name given through the one answer route stores the line's experiments; after a
  re-run that gives the line another label the themes route serves the name on the line; so does a
  name written before experiments were kept, through the run it was written against.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING, Any

import pytest

from ingence.themes.lines import Line, NamedEvent, follow_names

if TYPE_CHECKING:
    from pathlib import Path


def _line(theme: int, label: str, experiments: list[str]) -> Line:
    words = tuple(label.split(" · "))
    return Line(
        theme=theme,
        label=label,
        key="+".join(sorted(word.lower() for word in words)),
        label_words=words,
        words=frozenset(word.casefold() for word in words),
        experiments=frozenset(experiments),
    )


def _said(
    sequence: int, key: str, *, experiments: list[str] | None = None, run_id: str | None = None
) -> NamedEvent:
    return NamedEvent(
        sequence=sequence,
        key=key,
        run_id=run_id,
        experiments=None if experiments is None else frozenset(experiments),
    )


NO_EARLIER: Any = lambda _run: None  # noqa: E731 -- a reader that finds no earlier run

LINES = [
    _line(0, "alpha · beta · gamma", ["e1", "e2", "e3"]),
    _line(1, "delta · epsilon · zeta", ["e4", "e5"]),
]


def test_a_name_given_under_another_label_follows_the_line_holding_most_of_its_experiments() -> (
    None
):
    said = _said(1, "eta+theta+iota", experiments=["e1", "e2", "e9"])
    followed = follow_names(LINES, [said], run_id="run_2", earlier=NO_EARLIER)
    assert followed == {"alpha+beta+gamma": said}


def test_a_tie_goes_to_the_most_words_in_common_then_to_the_key() -> None:
    lines = [
        _line(0, "alpha · beta", ["e1", "e2"]),
        _line(1, "gamma · delta", ["e2", "e3"]),
    ]
    # One experiment each: the words of the key it was written under break the tie.
    by_words = _said(1, "delta+gamma+kappa", experiments=["e2"])
    assert follow_names(lines, [by_words], run_id="r", earlier=NO_EARLIER) == {
        "delta+gamma": by_words
    }
    # No word in common: the lower key.
    by_key = _said(2, "kappa+lambda", experiments=["e2"])
    assert follow_names(lines, [by_key], run_id="r", earlier=NO_EARLIER) == {"alpha+beta": by_key}


def test_the_latest_statement_about_a_line_wins_and_a_removal_is_a_statement() -> None:
    older = _said(1, "eta+theta", experiments=["e4", "e5"])
    newer = _said(5, "delta+epsilon+zeta", experiments=["e4", "e5"])
    followed = follow_names(LINES, [older, newer], run_id="r", earlier=NO_EARLIER)
    assert followed["delta+epsilon+zeta"] is newer
    again = follow_names(LINES, [newer, older], run_id="r", earlier=NO_EARLIER)
    assert again["delta+epsilon+zeta"] is newer


def test_a_statement_that_kept_no_experiments_is_read_from_the_run_it_was_written_against() -> None:
    asked: list[str] = []

    def earlier(run_id: str) -> dict[str, Any]:
        asked.append(run_id)
        # In that run the key belonged to a line holding e4 and e5.
        return {
            "themes": [{"theme": 0, "label": "eta · theta · iota", "words": []}],
            "experiments": [
                {"id": "e4", "theme": 0},
                {"id": "e5", "theme": 0},
                {"id": "e6", "theme": None},
            ],
        }

    said = _said(1, "eta+iota+theta", run_id="run_1")
    followed = follow_names(LINES, [said], run_id="run_2", earlier=earlier)
    assert followed == {"delta+epsilon+zeta": said}
    assert asked == ["run_1"]
    # The key found in this run is used without reading any other.
    direct = _said(2, "alpha+beta+gamma", run_id="run_1")
    asked.clear()
    assert follow_names(LINES, [direct], run_id="run_2", earlier=earlier) == {
        "alpha+beta+gamma": direct
    }
    assert asked == []


def test_a_statement_no_run_can_read_names_the_line_with_its_key_or_none() -> None:
    gone = _said(1, "nothing+like+it", run_id="run_0")
    assert follow_names(LINES, [gone], run_id="run_2", earlier=NO_EARLIER) == {}
    same = _said(2, "delta+epsilon+zeta")
    assert follow_names(LINES, [same], run_id="run_2", earlier=NO_EARLIER) == {
        "delta+epsilon+zeta": same
    }


def test_a_recorded_statement_names_the_line_its_experiments_point_to_not_a_key_coincidence() -> (
    None
):
    """Another line now carries the key the name was written under: the experiments decide."""
    lines = [
        _line(0, "alpha · beta", ["e1", "e2"]),
        _line(1, "gamma · delta", ["e3", "e4"]),
    ]
    # Written when "alpha+beta" was the line of e3 and e4.
    said = _said(1, "alpha+beta", experiments=["e3", "e4"])
    assert follow_names(lines, [said], run_id="r", earlier=NO_EARLIER) == {"delta+gamma": said}


def test_a_name_is_not_shown_on_a_line_holding_half_or_less_of_what_it_was_given_to() -> None:
    lines = [
        _line(0, "alpha \u00b7 beta", ["e1", "e2"]),
        _line(1, "gamma \u00b7 delta", ["e3", "e4"]),
    ]
    said = _said(1, "eta+theta", experiments=["e1", "e2", "e3", "e4"])
    assert follow_names(lines, [said], run_id="r", earlier=NO_EARLIER) == {}
    more = [lines[0], _line(1, "gamma \u00b7 delta", ["e2", "e3", "e4"])]
    assert follow_names(more, [said], run_id="r", earlier=NO_EARLIER) == {"delta+gamma": said}
    assert follow_names(more, [said], run_id="r", earlier=NO_EARLIER, minimum_share=0.75) == {}


def test_two_named_lines_that_merge_settle_by_share_then_key_then_date() -> None:
    first = _said(1, "alpha+beta", experiments=["a1", "a2", "a3", "a4"])
    second = _said(2, "gamma+delta", experiments=["b1", "b2", "b3", "b4"])
    key = "alpha+beta"
    # The merged line holds all of the first and only half of the second: the first is named.
    merged = [_line(0, "zeta \u00b7 eta", ["a1", "a2", "a3", "a4", "b1", "b2"])]
    assert follow_names(merged, [first, second], run_id="r", earlier=NO_EARLIER) == {
        "eta+zeta": first
    }
    # It holds three of the second's four: the first still has the larger share.
    three = [_line(0, "zeta \u00b7 eta", ["a1", "a2", "a3", "a4", "b1", "b2", "b3"])]
    assert follow_names(three, [first, second], run_id="r", earlier=NO_EARLIER) == {
        "eta+zeta": first
    }
    # All of both: the same share, neither key is the line's, so the latest wins.
    whole = [_line(0, "zeta \u00b7 eta", ["a1", "a2", "a3", "a4", "b1", "b2", "b3", "b4"])]
    assert follow_names(whole, [first, second], run_id="r", earlier=NO_EARLIER) == {
        "eta+zeta": second
    }
    # The same share, and one statement was written under the line's own key: it wins over a
    # later one that was not.
    own = [_line(0, "alpha \u00b7 beta", ["a1", "a2", "a3", "a4", "b1", "b2", "b3", "b4"])]
    assert follow_names(own, [first, second], run_id="r", earlier=NO_EARLIER) == {key: first}


def test_one_experiment_drifting_into_another_named_line_leaves_each_name_on_its_own_line() -> None:
    """Line B keeps its label and its four experiments and gains one from line A, which was named
    later: B keeps B's name, and A's name stays on what is left of A."""
    named_b = _said(1, "gamma+delta", experiments=["b1", "b2", "b3", "b4"])
    named_a = _said(5, "alpha+beta", experiments=["a1", "a2", "a3", "a4"])
    lines = [
        _line(0, "alpha \u00b7 beta", ["a1", "a2", "a3"]),
        _line(1, "gamma \u00b7 delta", ["b1", "b2", "b3", "b4", "a4"]),
    ]
    followed = follow_names(lines, [named_b, named_a], run_id="r", earlier=NO_EARLIER)
    assert followed == {"alpha+beta": named_a, "delta+gamma": named_b}
    # Even if A's own line had gone, one experiment in B does not carry A's name onto B.
    only_b = [lines[1]]
    assert follow_names(only_b, [named_b, named_a], run_id="r", earlier=NO_EARLIER) == {
        "delta+gamma": named_b
    }


def test_a_rename_stands_over_the_older_name_when_the_line_loses_an_experiment() -> None:
    """Run 1: the line holds e1-e4 and is named. Run 2: it holds five and is renamed. Run 3: the
    fifth leaves, so the older name holds 4 of 4 and the newer 4 of 5 -- the newer still stands."""
    first = _said(1, "alpha+beta", experiments=["e1", "e2", "e3", "e4"])
    second = _said(2, "alpha+beta", experiments=["e1", "e2", "e3", "e4", "e5"])
    run_three = [_line(0, "alpha \u00b7 beta", ["e1", "e2", "e3", "e4"])]
    assert follow_names(run_three, [first, second], run_id="r", earlier=NO_EARLIER) == {
        "alpha+beta": second
    }
    # Whichever order the statements are listed in: the Record's order decides.
    assert follow_names(run_three, [second, first], run_id="r", earlier=NO_EARLIER) == {
        "alpha+beta": second
    }
    # Even where the line was relabeled in between, so neither statement holds its key.
    relabeled = [_line(0, "gamma \u00b7 delta", ["e1", "e2", "e3", "e4"])]
    assert follow_names(relabeled, [first, second], run_id="r", earlier=NO_EARLIER) == {
        "delta+gamma": second
    }


def test_a_removed_name_stays_removed_when_the_line_loses_an_experiment() -> None:
    """The statement that takes a name away is a statement: it stands over the name it took away."""
    named = _said(1, "alpha+beta", experiments=["e1", "e2", "e3", "e4"])
    removed = _said(2, "alpha+beta", experiments=["e1", "e2", "e3", "e4", "e5"])
    line = [_line(0, "alpha \u00b7 beta", ["e1", "e2", "e3", "e4"])]
    assert follow_names(line, [named, removed], run_id="r", earlier=NO_EARLIER) == {
        "alpha+beta": removed
    }


def test_a_name_written_before_experiments_were_kept_gives_way_to_a_newer_one_on_the_line() -> None:
    old = _said(1, "alpha+beta")  # kept no experiments: found by its key
    newer = _said(2, "gamma+delta", experiments=["e1", "e2", "e3"])
    line = [_line(0, "alpha \u00b7 beta", ["e1", "e2", "e3", "e4"])]
    # Both land on the one line: the older one's share (by key) is whole, the newer's is 3 of 3;
    # the newer is the person's last word and stands.
    assert follow_names(line, [old, newer], run_id="r", earlier=NO_EARLIER) == {"alpha+beta": newer}
    # A newer statement about other experiments leaves the older name where it was.
    other = _said(3, "eta+theta", experiments=["e9", "e10"])
    both = [*line, _line(1, "eta \u00b7 theta", ["e9", "e10"])]
    assert follow_names(both, [old, other], run_id="r", earlier=NO_EARLIER) == {
        "alpha+beta": old,
        "eta+theta": other,
    }


def test_a_statement_holding_exactly_half_of_an_older_ones_experiments_replaces_nothing() -> None:
    """Only a later statement holding *more than* half of the earlier one's experiments replaces
    it: exactly half does not, and each name stays on the line its own experiments point to."""
    older = _said(1, "alpha+beta", experiments=["e1", "e2", "e3", "e4"])
    half = _said(2, "gamma+delta", experiments=["e1", "e2", "e8", "e9", "e10"])
    lines = [
        _line(0, "alpha \u00b7 beta", ["e1", "e2", "e3", "e4"]),
        _line(1, "gamma \u00b7 delta", ["e8", "e9", "e10"]),
    ]
    assert follow_names(lines, [older, half], run_id="r", earlier=NO_EARLIER) == {
        "alpha+beta": older,
        "delta+gamma": half,
    }
    # One more of the older one's experiments, and the newer replaces it.
    more = _said(3, "gamma+delta", experiments=["e1", "e2", "e3", "e8", "e9", "e10"])
    assert follow_names(lines, [older, more], run_id="r", earlier=NO_EARLIER) == {}
    again = [lines[0], _line(1, "gamma \u00b7 delta", ["e1", "e2", "e3", "e8", "e9", "e10"])]
    assert follow_names(again, [older, more], run_id="r", earlier=NO_EARLIER) == {
        "delta+gamma": more
    }


# ---------------------------------------------------------------------------------------------
# On the engine.
# ---------------------------------------------------------------------------------------------

pytest.importorskip("sklearn", reason="the fixture project draws themes")

ACTOR = "a. person"


def _themes(project: Path) -> dict[str, Any]:
    from ingence.api.v1 import context_for, dispatch

    status, body = dispatch(context_for(project, settled=None, job_running=False), "themes", {})
    assert status == 200, body
    return body  # type: ignore[no-any-return]


def _line_of(body: dict[str, Any], word: str) -> dict[str, Any]:
    return next(theme for theme in body["themes"] if word in theme["label"].split(" · "))


def _units_of(built: Any, body: dict[str, Any], theme: int) -> list[str]:
    ids = {row["id"] for row in body["experiments"] if row["theme"] == theme}
    return [unit for unit, entry in built.ids.items() if entry in ids]


def test_a_name_given_through_the_answer_route_keeps_the_experiments_of_its_line(
    tmp_path: Path,
) -> None:
    import json

    from fixtures.topic_project import build_topic_project
    from ingence.api.routes.decide import Decision, DecisionContext, decide_target
    from ingence.storage.catalog import open_catalog

    built = build_topic_project(tmp_path)
    first = _themes(built.project)
    line = _line_of(first, "droska")
    with open_catalog(built.project) as catalog:
        event = decide_target(
            DecisionContext(catalog=catalog, project=built.project),
            Decision.from_body(
                {"target": line["target"], "described": "Stain series", "reading": line["label"]}
            ),
            actor=ACTOR,
            occurred_at="2026-10-09T05:00:00Z",
        )
    held = sorted(row["id"] for row in first["experiments"] if row["theme"] == line["theme"])
    assert json.loads(event.payload)["experiments"] == held
    assert len(held) == 10


def _name_a_line_under(
    built: Any,
    key_words: list[str],
    *,
    experiments: list[str] | None,
    run_id: str,
    name: str | None,
    note: str | None = None,
    at: str = "2026-10-09T05:00:00Z",
) -> None:
    """Write a name as a run that read these words for the line would have: its key is not the
    key of any line of the run the themes route now reads."""
    from ingence.core.ids import PROJECT_SCOPE
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import append_event
    from ingence.record.targets import theme_key, theme_target

    append_event(
        built.project,
        kind=RecordEventKind.ANNOTATE,
        actor=ACTOR,
        occurred_at=at,
        target=theme_target(theme_key(key_words)),
        against_run_id=run_id,
        payload={
            **({"described": name} if name else {}),
            **({"note": note} if note else {}),
            "source": {},
            **({"experiments": experiments} if experiments is not None else {}),
        },
        scope=PROJECT_SCOPE,
    )


def test_a_name_written_under_another_label_is_served_on_the_line_its_experiments_point_to(
    tmp_path: Path,
) -> None:
    from fixtures.topic_project import build_topic_project

    built = build_topic_project(tmp_path)
    first = _themes(built.project)
    line = _line_of(first, "droska")
    held = sorted(row["id"] for row in first["experiments"] if row["theme"] == line["theme"])
    other = _line_of(first, "hoben")
    # The line was named when it read other words: its experiments are recorded, its key is gone.
    _name_a_line_under(
        built,
        ["eta", "theta", "iota"],
        experiments=held[:8]
        + sorted(r["id"] for r in first["experiments"] if r["theme"] == other["theme"])[:1],
        run_id=first["run_id"],
        name="Stain series",
    )
    after = _themes(built.project)
    named = [t for t in after["themes"] if t["person_label"]]
    assert [(t["theme"], t["person_label"], t["labelled_by"]) for t in named] == [
        (line["theme"], "Stain series", ACTOR)
    ]
    assert named[0]["target"] == line["target"], "served on the line's current target"
    assert named[0]["label"] == line["label"], "the engine's own words stay"
    # The map serves the same name on the same line.
    from ingence.api.v1 import context_for, dispatch

    status, body = dispatch(
        context_for(built.project, settled=None, job_running=False),
        "themes/map",
        {"themes_only": ["true"]},
    )
    assert status == 200
    assert [(t["theme"], t["person_label"]) for t in body["themes"] if t["person_label"]] == [
        (line["theme"], "Stain series")
    ]


def test_a_name_that_kept_no_experiments_is_read_from_the_run_it_was_written_against(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fixtures.topic_project import build_topic_project, rerun
    from ingence.themes import projection as projections

    built = build_topic_project(tmp_path)
    first = _themes(built.project)
    line = _line_of(first, "droska")
    held = sorted(row["id"] for row in first["experiments"] if row["theme"] == line["theme"])
    # In the first run the line read other words, as a run before a relabel would have.
    _name_a_line_under(
        built, ["eta", "theta", "iota"], experiments=None, run_id=first["run_id"], name="Older name"
    )
    again = rerun(built, {})
    asked: list[str] = []
    real = projections.themes_projection

    def earlier_run(project: Path, run_id: str, *, settings: Any = None) -> dict[str, Any]:
        built_run = real(project, run_id, settings=settings)
        if run_id != first["run_id"]:
            return built_run
        asked.append(run_id)
        # What that run's projection said: the line of these experiments was labeled eta theta iota.
        changed = {**built_run, "themes": [dict(t) for t in built_run["themes"]]}
        for theme in changed["themes"]:
            if theme["theme"] == line["theme"]:
                theme["label"] = "eta \u00b7 theta \u00b7 iota"
        return changed

    monkeypatch.setattr(projections, "themes_projection", earlier_run)
    after = _themes(built.project)
    assert after["run_id"] == again
    named = [t for t in after["themes"] if t["person_label"]]
    assert [(t["theme"], t["person_label"]) for t in named] == [(line["theme"], "Older name")]
    assert asked == [first["run_id"]]
    assert len(held) == 10


def test_a_name_on_a_line_that_did_not_change_is_served_as_before(tmp_path: Path) -> None:
    from fixtures.topic_project import build_topic_project
    from ingence.api.routes.decide import Decision, DecisionContext, decide_target
    from ingence.storage.catalog import open_catalog

    shutil.rmtree(tmp_path, ignore_errors=True)
    tmp_path.mkdir()
    built = build_topic_project(tmp_path)
    first = _themes(built.project)
    line = first["themes"][0]
    with open_catalog(built.project) as catalog:
        decide_target(
            DecisionContext(catalog=catalog, project=built.project),
            Decision.from_body(
                {"target": line["target"], "described": "Kept", "reading": line["label"]}
            ),
            actor=ACTOR,
            occurred_at="2026-10-09T05:00:00Z",
        )
    after = _themes(built.project)
    named = [t for t in after["themes"] if t["person_label"]]
    assert [(t["target"], t["person_label"]) for t in named] == [(line["target"], "Kept")]


def test_the_latest_name_or_removal_stands_through_a_re_run_that_changes_the_line(
    tmp_path: Path,
) -> None:
    """The Record's order decides between a person's statements about one line, however the share
    of the line each holds has changed: a rename and a removal stand over what came before."""
    from fixtures.topic_project import build_topic_project

    built = build_topic_project(tmp_path)
    first = _themes(built.project)
    line = _line_of(first, "droska")
    words = line["label"].split(" \u00b7 ")
    held = sorted(row["id"] for row in first["experiments"] if row["theme"] == line["theme"])
    stray = next(row["id"] for row in first["experiments"] if row["theme"] != line["theme"])
    run = first["run_id"]
    # Named when the line held nine; renamed when it held ten and a stray one of another line (so
    # the newer holds ten of its eleven, the older all nine of its own).
    _name_a_line_under(
        built, words, experiments=held[:9], run_id=run, name="first", at="2026-10-09T05:00:00Z"
    )
    _name_a_line_under(
        built,
        words,
        experiments=[*held, stray],
        run_id=run,
        name="second",
        at="2026-10-09T06:00:00Z",
    )
    named = [t for t in _themes(built.project)["themes"] if t["person_label"]]
    assert [(t["theme"], t["person_label"]) for t in named] == [(line["theme"], "second")]
    # Then the name is taken away (a note alone) with the line as it is, also over eleven: the
    # removal stands, and the older names do not come back.
    _name_a_line_under(
        built,
        words,
        experiments=[*held, stray],
        run_id=run,
        name=None,
        note="the name was removed",
        at="2026-10-09T07:00:00Z",
    )
    after = _themes(built.project)
    [shown] = [t for t in after["themes"] if t["theme"] == line["theme"]]
    assert shown["person_label"] is None and shown["person_note"] == "the name was removed"
    assert [t for t in after["themes"] if t["person_label"]] == []
