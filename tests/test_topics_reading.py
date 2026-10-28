"""A topic's members resolved against a run, and what drifted between two runs.

The test is pure: the readings are built from values (a run's experiments, their terms, their lines
of work), so every case is one sentence of the rule. Every name is invented.

Held here:

- a topic over two cores and a line resolves to the union of their experiments, with files by the
  shared rule (an experiment's files are those whose highest experiment it is), and a member that
  resolves to nothing is listed with why;
- a re-run where an experiment comes to carry a core's words: the topic gains it and the drift says
  why; a line whose label changes keeps its experiments in the topic (it is found by them), and says
  its words have drifted;
- unknown is not nothing: a member that could not be read is named, and a count is never 0 for it;
- overlap between two topics is counted, and a topic's own experiments are counted once.
"""

from __future__ import annotations

from ingence.api.routes.overlay import Topic, TopicMember, line_key
from ingence.themes.lines import Line, match_line
from ingence.topics.model import TopicReading
from ingence.topics.reading import compare, overlaps, read_topic
from ingence.topics.runview import EntryInfo, LineReading, RunBase, RunView
from ingence.topics.said import drift_said, topic_said

SAID_AT = "2026-06-01T10:00:00Z"


def _base(
    run_id: str,
    experiments: dict[str, str],
    *,
    files: dict[str, int] | None = None,
    folders: dict[str, str | None] | None = None,
    units: dict[str, str] | None = None,
) -> RunBase:
    """A run: experiments (id to folder path), the folders that lie inside them and above them."""
    unit_of = dict(units or {})
    for experiment in experiments:
        unit_of.setdefault(experiment, experiment)
    return RunBase(
        run_id=run_id,
        experiments=experiments,
        units=unit_of,
        parents=dict(folders or dict.fromkeys(experiments)),
        files=files or dict.fromkeys(experiments, 0),
    )


def _line(theme: int, label: str, experiments: list[str], extra: tuple[str, ...] = ()) -> Line:
    words = tuple(label.split(" · "))
    return Line(
        theme=theme,
        label=label,
        key="+".join(sorted(word.lower() for word in words)),
        label_words=words,
        words=frozenset(word.casefold() for word in (*words, *extra)),
        experiments=frozenset(experiments),
    )


def _topic(name: str, *members: TopicMember, topic_id: str = "hgtopic1") -> Topic:
    return Topic(
        topic_id=topic_id,
        target=f"overlay:group:{topic_id}",
        name=name,
        note=None,
        members=members,
        made_by="a. person",
        made_at=SAID_AT,
        edited_by="a. person",
        edited_at=SAID_AT,
        event_id="evt1",
        sequence=1,
        against_run_id=None,
        earlier=0,
    )


def _core(*terms: str) -> TopicMember:
    ordered = tuple(sorted(terms))
    return TopicMember(kind="core", key="group:" + " ".join(ordered), terms=ordered)


def _line_member(anchors: list[str], words: tuple[str, ...]) -> TopicMember:
    return TopicMember(
        kind="line", key=line_key(anchors), anchors=tuple(sorted(anchors)), words=words
    )


def _view(
    base: RunBase,
    *,
    terms: dict[str, set[str]] | None = None,
    lines: list[Line] | None = None,
    entries: dict[str, EntryInfo] | None = None,
) -> RunView:
    return RunView(
        base=base,
        entries=entries or {},
        core_terms=None if terms is None else {e: frozenset(t) for e, t in terms.items()},
        cores_why=None if terms is not None else "the words experiments carry were not read",
        lines=None if lines is None else LineReading(lines=tuple(lines), broad=()),
        lines_why=None if lines is not None else "lines of work could not be read",
    )


EXPERIMENTS = {f"exp_{n}": f"2026{n:02d}01 stain{n}" for n in range(1, 7)}
FILES = {"exp_1": 12, "exp_2": 8, "exp_3": 5, "exp_4": 0, "exp_5": 30, "exp_6": 9}
TERMS = {
    "exp_1": {"dapi", "gfp", "wt"},
    "exp_2": {"dapi", "gfp", "ko"},
    "exp_3": {"actin", "tubulin"},
    "exp_4": {"tubulin", "dmso"},
    "exp_5": {"dapi", "ko"},
    "exp_6": {"pbs"},
}
LINES = [
    _line(0, "dapi · wt · gfp", ["exp_1", "exp_2"]),
    _line(1, "actin · tubulin · dmso", ["exp_3", "exp_4"]),
    _line(2, "pbs · ko · blot", ["exp_5", "exp_6"]),
]


def _first_run() -> RunView:
    return _view(_base("run_1", EXPERIMENTS, files=FILES), terms=TERMS, lines=LINES)


def test_a_topic_over_two_cores_and_a_line_is_the_union_of_their_experiments() -> None:
    cores = _core("dapi", "gfp"), _core("actin", "tubulin")
    line = _line_member(["exp_5"], ("pbs", "ko", "blot"))
    topic = _topic("Nuclear counts", *cores, line)
    reading = read_topic(topic, _first_run(), earlier=None)
    by_key = {m.key: m for m in reading.members}
    assert by_key["group:dapi gfp"].experiments == ("exp_1", "exp_2")
    assert by_key["group:actin tubulin"].experiments == ("exp_3",)
    assert by_key[line.key].experiments == ("exp_5", "exp_6")
    assert reading.experiments == ("exp_1", "exp_2", "exp_3", "exp_5", "exp_6")
    # Files by the shared rule: the files of those experiments, each experiment once.
    assert reading.files == 12 + 8 + 5 + 30 + 9
    assert reading.complete is True
    assert reading.nothing == ()
    assert reading.drift is None


def test_files_are_those_whose_highest_experiment_it_is() -> None:
    """A folder inside an experiment is part of it: its files are counted in the experiment."""
    base = _base(
        "run_1",
        {"exp_1": "a"},
        files={"exp_1": 7},
        folders={"exp_1": None, "inner": "exp_1"},
        units={"inner": "exp_1"},
    )
    held = EntryInfo(entry_id="inner", kind="folder", parent="exp_1", name="inner", path="a/inner")
    topic = _topic("One folder", TopicMember(kind="entry", key="inner", entry="inner"))
    reading = read_topic(topic, _view(base, entries={"inner": held}), earlier=None)
    assert reading.experiments == ("exp_1",)
    assert reading.files == 7


def test_a_member_that_resolves_to_nothing_is_listed_with_why() -> None:
    gone = TopicMember(kind="experiment", key="exp_gone", entry="exp_gone")
    stray = TopicMember(kind="entry", key="file_lost", entry="file_lost")
    orphan = TopicMember(kind="entry", key="file_orphan", entry="file_orphan")
    nobody = _core("never", "written")
    topic = _topic("Mixed", gone, stray, orphan, nobody, _core("pbs"))
    topic = Topic(
        **{
            **topic.__dict__,
            "names": {"file_lost": "lost_scan.tif"},
            "paths": {"file_lost": "old/lost_scan.tif"},
        }
    )
    entries = {
        "file_orphan": EntryInfo("file_orphan", "file", "no_experiment", "orphan.txt", "orphan.txt")
    }
    reading = read_topic(
        topic,
        _view(_base("run_1", EXPERIMENTS, files=FILES), terms=TERMS, entries=entries),
        earlier=None,
    )
    assert set(reading.nothing) == {"exp_gone", "file_lost", "file_orphan", nobody.key}
    states = {m.key: m for m in reading.members}
    assert states["file_lost"].in_run is False
    assert states["file_lost"].name == "lost_scan.tif"
    assert "lies in no experiment" in (states["file_orphan"].why or "")
    assert "carries all of its words" in (states[nobody.key].why or "")
    # The member that did resolve still counts; the others are shown, never dropped.
    assert reading.experiments == ("exp_6",)
    said = topic_said(reading, 6)
    assert "1 of 6 experiments" in said and "4 members resolve to no experiment" in said


def test_a_member_that_could_not_be_read_is_not_nothing() -> None:
    topic = _topic("Cannot say", _core("dapi"), _line_member(["exp_1"], ()))
    unread = _view(_base("run_1", EXPERIMENTS, files=FILES))
    reading = read_topic(topic, unread, earlier=None)
    assert reading.nothing == ()
    assert len(reading.not_computed) == 2
    assert reading.complete is False
    assert all(m.state == "not_computed" and m.why for m in reading.members)
    assert reading.experiments == ()
    assert reading.read is False and topic_said(reading, 6).startswith("Not read: ")


def test_an_experiment_that_comes_to_carry_a_cores_words_joins_and_the_drift_says_why() -> None:
    core = _core("dapi", "gfp")
    topic = _topic("Dye controls", core)
    before = _view(_base("run_1", EXPERIMENTS, files=FILES), terms=TERMS, lines=LINES)
    # In the re-run exp_5 is named with gfp as well, and exp_2 drops it.
    changed = {**TERMS, "exp_5": {"dapi", "ko", "gfp"}, "exp_2": {"dapi", "ko"}}
    now = _view(_base("run_2", EXPERIMENTS, files=FILES), terms=changed, lines=LINES)
    reading = read_topic(topic, now, earlier=(before, "run_1"))
    assert reading.experiments == ("exp_1", "exp_5")
    drift = reading.drift
    assert drift is not None and drift.since_run_id == "run_1"
    assert [(c.experiment, c.reasons) for c in drift.joined] == [
        ("exp_5", ("now carries the words dapi, gfp",))
    ]
    assert [(c.experiment, c.reasons) for c in drift.left] == [
        ("exp_2", ("no longer carries the words dapi, gfp",))
    ]
    sentences = drift_said(reading)
    assert sentences == [
        "1 experiment joined “Dye controls”: it now carries the words dapi, gfp.",
        "1 experiment left “Dye controls”: it no longer carries the words dapi, gfp.",
    ]


def test_a_line_whose_label_changes_keeps_its_experiments_in_the_topic() -> None:
    line = _line_member(["exp_3", "exp_4"], ("actin", "tubulin", "dmso"))
    topic = _topic("Cytoskeleton", line)
    before = _view(_base("run_1", EXPERIMENTS, files=FILES), terms=TERMS, lines=LINES)
    # The re-run reads other words for the same two experiments, and a third joins the line.
    relabelled = [
        LINES[0],
        _line(1, "tubulin · phalloidin · blot", ["exp_3", "exp_4", "exp_6"]),
        _line(2, "pbs · ko · blot", ["exp_5"]),
    ]
    now = _view(_base("run_2", EXPERIMENTS, files=FILES), terms=TERMS, lines=relabelled)
    reading = read_topic(topic, now, earlier=(before, "run_1"))
    [member] = reading.members
    assert member.experiments == ("exp_3", "exp_4", "exp_6")
    # The same line by its experiments, with other words now: it says so.
    assert member.line_now == "tubulin · phalloidin · blot"
    assert member.line_drifted is True
    assert (member.line_kept, member.line_added_by) == (2, 2)
    assert reading.drift is not None
    assert [(c.experiment, c.reasons) for c in reading.drift.joined] == [
        ("exp_6", ("the line's experiments changed",))
    ]
    assert reading.drift.left == ()
    assert drift_said(reading) == [
        "1 experiment joined “Cytoskeleton”: the line's experiments changed."
    ]


def test_a_line_is_found_by_most_of_its_experiments_then_words_then_key() -> None:
    lines = [
        _line(0, "alpha · beta", ["e1", "e2"]),
        _line(1, "gamma · delta", ["e2", "e3"], extra=("kappa",)),
        _line(2, "zeta · eta", ["e3", "e4"], extra=("kappa",)),
    ]
    # Most experiments held wins.
    found = match_line(["e1", "e2"], [], lines)
    assert found is not None and found.line.theme == 0 and (found.held, found.of) == (2, 2)
    # A tie on experiments goes to the line sharing most words, then to the lower key.
    tie = match_line(["e2", "e3"], ["kappa", "delta"], lines)
    assert tie is not None and tie.line.theme == 1
    by_key = match_line(["e3"], [], lines)
    assert by_key is not None and by_key.line.theme == 1  # gamma+delta sorts before eta+zeta
    assert match_line(["nobody"], ["alpha"], lines) is None


def test_a_member_not_read_in_one_run_is_named_and_gives_or_takes_nothing() -> None:
    topic = _topic("Half known", _core("dapi", "gfp"), _line_member(["exp_3"], ()))
    before = _view(_base("run_1", EXPERIMENTS, files=FILES), terms=TERMS)  # lines not read
    now = _view(_base("run_2", EXPERIMENTS, files=FILES), terms=TERMS, lines=LINES)
    reading = read_topic(topic, now, earlier=(before, "run_1"))
    assert reading.drift is not None
    assert reading.drift.joined == () and reading.drift.left == ()
    assert reading.drift.unread == (topic.members[1].key,)
    assert "could not be read in one of the two runs" in drift_said(reading)[0]


def test_an_experiment_another_member_still_holds_neither_joined_nor_left() -> None:
    core = _core("dapi", "gfp")
    held_by_line = _line_member(["exp_2"], ("dapi", "wt", "gfp"))
    topic = _topic("Two ways in", core, held_by_line)
    before = _view(_base("run_1", EXPERIMENTS, files=FILES), terms=TERMS, lines=LINES)
    lost_words = {**TERMS, "exp_2": {"ko"}}
    now = _view(_base("run_2", EXPERIMENTS, files=FILES), terms=lost_words, lines=LINES)
    reading = read_topic(topic, now, earlier=(before, "run_1"))
    assert reading.drift is not None
    assert reading.drift.left == ()
    assert reading.experiments == ("exp_1", "exp_2")


def test_overlap_between_two_topics_is_counted_once_each() -> None:
    view = _first_run()
    one = _topic("Dyes", _core("dapi"), topic_id="hga")
    two = _topic("Knockouts", _core("ko"), topic_id="hgb")
    three = _topic("Blots", _core("pbs"), topic_id="hgc")
    readings = [read_topic(topic, view, earlier=None) for topic in (one, two, three)]
    held = {r.topic.topic_id: r.experiments for r in readings}
    names = {r.topic.topic_id: r.topic.name for r in readings}
    found = overlaps(held, names)
    assert held["hga"] == ("exp_1", "exp_2", "exp_5")
    assert [(o.topic_id, o.experiments) for o in found["hga"]] == [("hgb", ("exp_2", "exp_5"))]
    assert [(o.topic_id, o.experiments) for o in found["hgb"]] == [("hga", ("exp_2", "exp_5"))]
    assert found["hgc"] == []


def test_a_core_is_read_by_the_one_key_function(monkeypatch) -> None:
    """The mapping from a word to the key the lattice uses is one function, so switching the key
    from spellings to senses changes one line.
    """
    from ingence.topics import reading, runview

    view = _first_run()
    topic = _topic("Case", _core("DAPI", "GFP"))
    assert read_topic(topic, view, earlier=None).experiments == ("exp_1", "exp_2")
    monkeypatch.setattr(reading, "key_of_word", lambda word: runview.key_of_word(word) + "x")
    assert read_topic(topic, view, earlier=None).experiments == ()


def test_compare_names_the_member_that_explains_each_change() -> None:
    core = _core("dapi")
    topic = _topic("Why", core, _line_member(["exp_3"], ()))
    before = [
        read_topic(topic, _first_run(), earlier=None).members[0],
    ]
    now = read_topic(
        topic,
        _view(
            _base("run_2", EXPERIMENTS, files=FILES),
            terms={**TERMS, "exp_6": {"dapi"}},
            lines=LINES,
        ),
        earlier=None,
    )
    drift = compare(topic, now.members[:1], before, "run_1")
    assert [c.experiment for c in drift.joined] == ["exp_6"]
    assert isinstance(now, TopicReading)


def test_a_line_is_followed_only_where_it_holds_more_than_half_of_what_it_was_added_by() -> None:
    """Exactly half follows nothing: a line that split is not followed by whichever half stayed
    together; the member resolves to nothing, with a sentence saying why."""
    split = [
        _line(0, "alpha \u00b7 beta \u00b7 gamma", ["exp_1", "exp_2"]),
        _line(1, "delta \u00b7 epsilon \u00b7 zeta", ["exp_3", "exp_4"]),
    ]
    member = _line_member(["exp_1", "exp_2", "exp_3", "exp_4"], ("alpha", "beta", "gamma"))
    topic = _topic("Broken up", member)
    reading = read_topic(
        topic, _view(_base("run_2", EXPERIMENTS, files=FILES), lines=split), earlier=None
    )
    [read] = reading.members
    assert read.state == "nothing" and read.experiments == ()
    assert read.why == (
        "the line has split: no line of work holds more than half of the experiments it was "
        "added by (the closest holds 2 of 4)"
    )
    assert reading.nothing == (member.key,) and reading.read is True
    # One more experiment in one line is more than half: the line is followed.
    held = [split[0], _line(1, "delta \u00b7 epsilon", ["exp_2", "exp_3", "exp_4"])]
    again = read_topic(
        topic, _view(_base("run_2", EXPERIMENTS, files=FILES), lines=held), earlier=None
    )
    assert again.members[0].state == "resolved"
    assert again.members[0].experiments == ("exp_2", "exp_3", "exp_4")
    assert (again.members[0].line_kept, again.members[0].line_added_by) == (3, 4)
    # And the share is a parameter, never a buried number.
    assert match_line(member.anchors, [], held, minimum_share=0.75) is None
    assert match_line(member.anchors, [], held, minimum_share=0.74) is not None


def test_a_topic_none_of_whose_members_was_read_is_not_read() -> None:
    topic = _topic("Not read", _core("dapi"), _line_member(["exp_1"], ()))
    unread = _view(_base("run_1", EXPERIMENTS, files=FILES))
    reading = read_topic(topic, unread, earlier=None)
    assert reading.read is False and reading.complete is False
    assert topic_said(reading, 6) == "Not read: the words experiments carry were not read."
    # One member read is a topic read: its counts are the readable members', at least.
    half = read_topic(
        topic, _view(_base("run_1", EXPERIMENTS, files=FILES), terms=TERMS), earlier=None
    )
    assert half.read is True and half.complete is False
