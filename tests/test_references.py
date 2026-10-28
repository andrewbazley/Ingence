"""Reference resolution: a mention read against the Index.

Every case is the grammar of paths, written with invented names: POSIX ``..`` and ``.``, a
Windows separator, a rooted path under a root and under none, a code include, a container
member's own folder, two roots answering one spelling, and a filesystem that ignores case.
"""

from __future__ import annotations

from ingence.core.ids import CaseSensitivity, LocatorStep
from ingence.core.schema import Entry, EntryKind, EntryType, Root
from ingence.infer.references import (
    BY_NAME,
    CODE_INCLUDE_NOT_RESOLVED,
    FROM_ROOT,
    IN_FOLDER,
    NO_MATCH,
    OUTSIDE_POOL,
    ROOTED,
    SEVERAL_MATCHES,
    Mention,
    PoolPaths,
    mentions_of,
    resolve,
    tally_resolutions,
)


def _root(root_id: str, mount: str, sensitivity=CaseSensitivity.SENSITIVE) -> Root:
    return Root(
        root_id=root_id,
        root_uri=f"file://{mount}",
        mount_path=mount,
        case_sensitivity=sensitivity,
        first_seen_run="r",
        last_seen_run="r",
    )


def _entry(entry_id: str, path: str, *, root: str = "root_a", **extra: object) -> Entry:
    name = path.rsplit("/", 1)[-1]
    return Entry(
        entry_id=entry_id,
        root_id=root,
        relative_path=path,
        name=name,
        entry_type=EntryType.FILE,
        extension=name.rsplit(".", 1)[-1] if "." in name else "",
        first_seen_run="r",
        last_seen_run="r",
        **extra,
    )


def _pool(*entries: Entry, sensitivity=CaseSensitivity.SENSITIVE, second_root: bool = False):
    roots = {"root_a": _root("root_a", "/mnt/vorn", sensitivity)}
    if second_root:
        roots["root_b"] = _root("root_b", "/mnt/kelt", sensitivity)
    return PoolPaths.of(entries, roots)


def _mention(path: str, shape: str, entry_id: str = "script") -> Mention:
    return Mention(entry_id=entry_id, observer="text", path=path, shape=shape)


def test_a_relative_path_resolves_beside_the_mentioning_file_first():
    pool = _pool(
        _entry("script", "proj/run.py"),
        _entry("near", "proj/data/plate.csv"),
        _entry("far", "data/plate.csv"),
    )
    found = resolve(_mention("data/plate.csv", "relative"), pool)
    assert (found.outcome, found.target_id) == (IN_FOLDER, "near")


def test_then_from_the_pool_s_root_which_is_circumstantial():
    pool = _pool(_entry("script", "proj/run.py"), _entry("far", "data/plate.csv"))
    found = resolve(_mention("data/plate.csv", "relative"), pool)
    assert (found.outcome, found.target_id) == (FROM_ROOT, "far")


def test_dot_dot_is_path_syntax_and_climbing_out_of_the_root_is_outside_the_pool():
    pool = _pool(_entry("script", "proj/code/run.py"), _entry("up", "proj/data/plate.csv"))
    assert resolve(_mention("../data/./plate.csv", "relative"), pool).target_id == "up"
    assert resolve(_mention("../../../plate.csv", "relative"), pool).outcome == OUTSIDE_POOL


def test_a_windows_separator_is_a_separator():
    pool = _pool(_entry("script", "proj/run.m"), _entry("near", "proj/data/plate.csv"))
    assert resolve(_mention("data\\plate.csv", "relative"), pool).target_id == "near"


def test_a_rooted_path_under_a_root_is_looked_for_under_that_root_only():
    pool = _pool(_entry("script", "proj/run.py"), _entry("there", "proj/data/plate.csv"))
    found = resolve(_mention("/mnt/vorn/proj/data/plate.csv", "absolute_local"), pool)
    assert (found.outcome, found.target_id) == (ROOTED, "there")
    missing = resolve(_mention("/mnt/vorn/proj/data/other.csv", "absolute_local"), pool)
    assert missing.outcome == NO_MATCH


def test_another_machine_s_path_is_outside_the_pool_however_it_is_spelled():
    pool = _pool(_entry("script", "proj/run.py"), _entry("there", "proj/data/plate.csv"))
    for path in ("/Users/kelt/proj/data/plate.csv", "C:\\vorn\\proj\\data\\plate.csv"):
        assert resolve(_mention(path, "absolute_foreign"), pool).outcome == OUTSIDE_POOL


def test_a_code_include_is_never_resolved_against_data():
    """A file sharing an include's name is the worst false positive available."""
    pool = _pool(_entry("script", "proj/run.c"), _entry("header", "proj/lib/vorn.h"))
    found = resolve(_mention("lib/vorn.h", "code_include"), pool)
    assert (found.outcome, found.target_id) == (CODE_INCLUDE_NOT_RESOLVED, None)


def test_a_bare_name_resolves_in_its_own_folder_and_elsewhere_only_by_name():
    """In its own folder it is in_folder. Anywhere else only the weakest rung finds it."""
    pool = _pool(_entry("script", "proj/run.py"), _entry("near", "proj/plate.csv"))
    assert resolve(_mention("plate.csv", "bare"), pool).outcome == IN_FOLDER
    pool = _pool(_entry("script", "proj/run.py"), _entry("far", "other/plate.csv"))
    found = resolve(_mention("plate.csv", "bare"), pool)
    assert (found.outcome, found.target_id) == (BY_NAME, "far")


def test_a_path_whose_folder_moved_is_found_by_its_name_and_two_such_files_abstain():
    """The weaker rule: a relative path whose folder is gone still names its file.

    A workspace moved away from the folder its paths were relative to is the case that needs
    it. One file of that name answers. Two abstain, like any other several matches.
    """
    pool = _pool(_entry("script", "proj/run.wsp"), _entry("far", "elsewhere/zorn_01.fcs"))
    found = resolve(_mention("acq/zorn_01.fcs", "relative"), pool)
    assert (found.outcome, found.target_id) == (BY_NAME, "far")
    pool = _pool(
        _entry("script", "proj/run.wsp"),
        _entry("one", "a/zorn_01.fcs"),
        _entry("two", "b/zorn_01.fcs"),
    )
    found = resolve(_mention("acq/zorn_01.fcs", "relative"), pool)
    assert (found.outcome, found.answered) == (SEVERAL_MATCHES, 2)
    # Another machine's path is never searched for by name: that is a person's prefix mapping.
    assert resolve(_mention("/Users/kelt/zorn_01.fcs", "absolute_foreign"), pool).outcome == (
        OUTSIDE_POOL
    )


def test_two_roots_answering_one_spelling_abstain_and_say_how_many():
    pool = _pool(
        _entry("script", "proj/run.py"),
        _entry("one", "data/plate.csv"),
        _entry("two", "data/plate.csv", root="root_b"),
        second_root=True,
    )
    found = resolve(_mention("data/plate.csv", "relative"), pool)
    assert (found.outcome, found.target_id, found.answered) == (SEVERAL_MATCHES, None, 2)


def test_case_is_folded_only_where_the_root_s_filesystem_ignores_it():
    """UNKNOWN keeps spellings apart, which is the direction that can be recovered from."""
    entries = (_entry("script", "proj/run.py"), _entry("near", "proj/Data/Plate.csv"))
    assert resolve(_mention("data/plate.csv", "relative"), _pool(*entries)).outcome == NO_MATCH
    unknown = _pool(*entries, sensitivity=CaseSensitivity.UNKNOWN)
    assert resolve(_mention("data/plate.csv", "relative"), unknown).outcome == NO_MATCH
    folded = _pool(*entries, sensitivity=CaseSensitivity.INSENSITIVE)
    assert resolve(_mention("data/plate.csv", "relative"), folded).target_id == "near"


def test_a_member_looks_in_its_folder_inside_the_container_then_beside_the_container():
    container = _entry("zip", "proj/bundle.zip")

    def member_at(entry_id: str, inner: str) -> Entry:
        return _entry(
            entry_id,
            "proj/bundle.zip",
            entry_kind=EntryKind.DERIVED,
            parent_entry_id="zip",
            locator_chain=(LocatorStep(scheme="zip", target=inner),),
            transformation="zip",
            ordinal=0,
        ).model_copy(update={"name": inner})

    member = member_at("member", "code/run.py")
    sibling = member_at("inside", "code/data/plate.csv")
    beside = _entry("beside", "proj/data/table.csv")
    pool = _pool(container, member, sibling, beside)
    assert resolve(_mention("data/plate.csv", "relative", "member"), pool).target_id == "inside"
    assert resolve(_mention("data/table.csv", "relative", "member"), pool).target_id == "beside"


def test_a_mention_recorded_without_a_shape_is_resolved_by_its_spelling_and_counted_apart():
    observations = [
        ("obs_1", "script", "text", "references.paths", '["data/plate.csv", "/elsewhere/x.csv"]'),
    ]
    mentions = mentions_of(observations)
    assert {mention.shape for mention in mentions} == {"unclassified"}
    assert {mention.observation_id for mention in mentions} == {"obs_1"}
    pool = _pool(_entry("script", "proj/run.py"), _entry("near", "proj/data/plate.csv"))
    outcomes = {mention.path: resolve(mention, pool).outcome for mention in mentions}
    assert outcomes == {"data/plate.csv": IN_FOLDER, "/elsewhere/x.csv": OUTSIDE_POOL}
    rows = tally_resolutions(
        [resolve(mention, pool) for mention in mentions], {"script": "py", "near": "csv"}
    )
    assert rows == (
        ("py", "unclassified", IN_FOLDER, 1, 1),
        ("py", "unclassified", OUTSIDE_POOL, 1, 1),
    )


# ---------------------------------------------------------------------------
# End to end: a script's mentions resolved, proposed and tallied.
# ---------------------------------------------------------------------------


def test_a_script_s_mentions_become_references_and_a_described_step_a_derivation(tmp_path):
    """A census, a dive, revision 4 and an inference over one folder's script and its data.

    The script names two data files beside it, a header it includes, and another machine's
    path. The two data files are proposed as referenced. The include and the foreign path
    abstain, each with its reason, and the run record counts every outcome by extension.
    A mention says neither which file was read nor which written, and a stage word gives no
    direction, even when the two files sit on one step of the workflow. So the two are proposed
    once as related, and neither as made from the other.
    """
    from fixtures.revision_4 import write_revision
    from ingence.core.project import Project
    from ingence.core.settings import ExtractionSettings
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census
    from ingence.scan.deepen import run_deepen
    from ingence.storage.catalog import open_catalog

    pool = tmp_path / "pool"
    (pool / "vorn" / "data").mkdir(parents=True)
    (pool / "vorn" / "lib").mkdir()
    (pool / "vorn" / "run_kelt.py").write_text(
        '#include "lib/kelt.h"\n'
        'SOURCE = "data/raw_plate.csv"\n'
        'TARGET = "data/processed_plate.csv"\n'
        'ELSEWHERE = "/Users/somebody/zorn/data/raw_plate.csv"\n'
    )
    (pool / "vorn" / "lib" / "kelt.h").write_text("int kelt;\n")
    (pool / "vorn" / "data" / "raw_plate.csv").write_text("well,value\nA1,1\n")
    (pool / "vorn" / "data" / "processed_plate.csv").write_text("well,value\nA1,2\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    project = project.save_settings(
        project.settings.model_copy(update={"extraction": ExtractionSettings(enabled=True)})
    )
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_deepen(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    report = run_inference(project, run_id="run_20260101T000003Z_333333333333", started_at="t")

    outcomes = {(row.shape, row.outcome): row.mentions for row in report.resolutions}
    assert outcomes[("relative", IN_FOLDER)] == 2
    assert outcomes[("absolute_foreign", OUTSIDE_POOL)] == 1
    assert outcomes[("code_include", CODE_INCLUDE_NOT_RESOLVED)] == 1
    assert {row.extension for row in report.resolutions} == {"py"}

    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT c.rule_id, s.name, t.name, c.decision_tier FROM candidates c "
            "JOIN entries s ON s.entry_id = c.source_id JOIN entries t ON t.entry_id = c.target_id "
            "ORDER BY 1, 2, 3"
        ).fetchall()
        recorded = catalog.query("SELECT resolutions_json FROM run").fetchone()
    referenced = {(source, target) for rule, source, target, _ in rows if "mentioned" in rule}
    assert referenced == {
        ("run_kelt.py", "raw_plate.csv"),
        ("run_kelt.py", "processed_plate.csv"),
    }
    described = {
        (rule, frozenset((source, target)))
        for rule, source, target, _ in rows
        if "describing" in rule
    }
    assert described == {
        ("related_by_a_describing_file", frozenset(("raw_plate.csv", "processed_plate.csv")))
    }
    assert recorded is not None and "outside_pool" in recorded[0]


def test_the_reference_predicates_are_evaluable_and_abstain_off_a_resolution():
    """A pair no resolved mention drew is an abstention, with why."""
    from ingence.rulebook.predicates import load_builtins
    from ingence.rulebook.registry import PREDICATES

    load_builtins()
    for name in ("references_by_declared_path", "derives_from_declared_reference"):
        predicate = PREDICATES.get(name)
        assert predicate.evaluate(object(), {}) is None
        assert predicate.explain_abstention(object(), {}).reason


def test_a_stored_number_after_a_slash_abstains_as_not_a_path():
    """A mention an earlier harvest recorded is read by the same rule, not reported missing."""
    from ingence.infer.references import NOT_A_PATH

    pool = _pool(_entry("script", "proj/run.wsp"), _entry("near", "proj/data/zorn_01.fcs"))
    assert resolve(_mention("Comp-FL1-A/1.0000", "relative"), pool).outcome == NOT_A_PATH
    assert resolve(_mention("data/zorn_01.fcs", "relative"), pool).outcome == IN_FOLDER


def test_a_stored_markup_fragment_abstains_as_not_a_path():
    from ingence.infer.references import NOT_A_PATH

    pool = _pool(_entry("script", "proj/page.xml"))
    assert resolve(_mention('/></a:b></c d="', "absolute_foreign"), pool).outcome == NOT_A_PATH
    assert resolve(_mention("/Users/kelt/zorn.csv", "absolute_foreign"), pool).outcome == (
        OUTSIDE_POOL
    )


def test_files_named_only_by_their_last_name_are_counted_and_license_nothing(tmp_path):
    """The weakest rung is tallied and places no reference and no derivation.

    A run log whose paths resolve only by name would otherwise propose many derivations among its
    files, so the derivation takes the reference rule's floor.
    """
    from fixtures.revision_4 import write_revision
    from ingence.core.project import Project
    from ingence.core.settings import ExtractionSettings
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census
    from ingence.scan.deepen import run_deepen
    from ingence.storage.catalog import open_catalog

    pool = tmp_path / "pool"
    (pool / "vorn").mkdir(parents=True)
    (pool / "kelt" / "moved").mkdir(parents=True)
    (pool / "vorn" / "run_kelt.py").write_text(
        'SOURCE = "gone/raw_plate.csv"\nTARGET = "gone/processed_plate.csv"\n'
    )
    (pool / "kelt" / "moved" / "raw_plate.csv").write_text("well,value\nA1,1\n")
    (pool / "kelt" / "moved" / "processed_plate.csv").write_text("well,value\nA1,2\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    project = project.save_settings(
        project.settings.model_copy(update={"extraction": ExtractionSettings(enabled=True)})
    )
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_deepen(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    report = run_inference(project, run_id="run_20260101T000003Z_333333333333", started_at="t")
    outcomes = {(row.shape, row.outcome): row.mentions for row in report.resolutions}
    assert outcomes[("relative", BY_NAME)] == 2
    with open_catalog(project.path) as catalog:
        # The two tables are one file at two steps, proposed by their names alone. The
        # mentions license nothing.
        assert catalog.query(
            "SELECT count(*) FROM candidates WHERE rule_id <> 'same_file_later_step'"
        ).fetchone() == (0,)


def test_a_script_naming_more_files_than_the_bound_still_publishes(tmp_path):
    """A truncation claim about a co-mentioned subject cites that subject.

    One macro names enough files beside it that the bound on candidates per subject cuts them. The
    truncation claim about each named file must cite an observation made about that file's own
    entry, not the macro's mention, which is an observation made about another entry. Otherwise the
    run refuses to publish. The mention stays the pair's.
    """
    from fixtures.revision_4 import write_revision
    from ingence.core.project import Project
    from ingence.core.settings import ExtractionSettings
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census
    from ingence.scan.deepen import run_deepen
    from ingence.storage.catalog import open_catalog

    pool = tmp_path / "pool"
    (pool / "vorn" / "data").mkdir(parents=True)
    named = [f"plate_{index:02d}.csv" for index in range(30)]
    (pool / "vorn" / "run_kelt.py").write_text(
        "".join(f'FILE_{index} = "data/{name}"\n' for index, name in enumerate(named))
    )
    for name in named:
        (pool / "vorn" / "data" / name).write_text("well,value\nA1,1\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    project = project.save_settings(
        project.settings.model_copy(update={"extraction": ExtractionSettings(enabled=True)})
    )
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_deepen(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    report = run_inference(project, run_id="run_20260101T000003Z_333333333333", started_at="t")

    assert all(outcome.passed for outcome in report.invariants), [
        outcome.invariant_id for outcome in report.invariants if not outcome.passed
    ]
    with open_catalog(project.path) as catalog:
        cut = catalog.query(
            "SELECT subject, inputs FROM claims WHERE rule_id = 'candidate_generation_is_bounded' "
            "AND object_reference LIKE '%co_mentioned%'"
        ).fetchall()
    assert cut, "the bound cut a co-mentioned subject, which is the case under test"
    assert all(list(inputs) == [subject] for subject, inputs in cut)


# ---------------------------------------------------------------------------
# A person's prefix mapping: another machine's folder is this root.
# ---------------------------------------------------------------------------


def test_another_machine_s_path_is_outside_the_pool_until_a_person_maps_its_prefix(tmp_path):
    from ingence.core.schema import RecordEventKind
    from ingence.infer.references import (
        BY_PREFIX,
        PREFIX_TARGET,
        PrefixMapping,
        map_foreign,
        prefix_mappings_in_force,
    )
    from ingence.record.log import append_event

    entries = (_entry("script", "proj/run.py"), _entry("plate", "tamen/plates/plate_01.csv"))
    foreign = "C:\\Users\\kelt\\Desktop\\Brisa Data\\tamen\\plates\\plate_01.csv"
    assert resolve(_mention(foreign, "absolute_foreign"), _pool(*entries)).outcome == OUTSIDE_POOL

    mapping = PrefixMapping(foreign="c:/users/kelt/desktop/brisa data", root_id="root_a")
    roots = {"root_a": _root("root_a", "/mnt/vorn")}
    mapped = PoolPaths.of(entries, roots, (mapping,))
    found = resolve(_mention(foreign, "absolute_foreign"), mapped)
    assert (found.outcome, found.target_id) == (BY_PREFIX, "plate")

    # The longest mapping wins, a folder inside the root is named by ``under``, and a path
    # climbing out of the root under a mapping is no path in the pool.
    inner = PrefixMapping(foreign="/Volumes/kelt/tamen", root_id="root_a", under="tamen")
    outer = PrefixMapping(foreign="/Volumes/kelt", root_id="root_a", under="elsewhere")
    ordered = PoolPaths.of(entries, roots, (outer, inner)).prefixes
    assert map_foreign("/Volumes/kelt/tamen/plates/plate_01.csv", ordered) == (
        "root_a",
        "tamen/plates/plate_01.csv",
    )
    assert map_foreign("/Volumes/kelt/../secret.csv", (outer,)) == ("root_a", "secret.csv")
    assert map_foreign("/Volumes/kelt/../../secret.csv", (outer,)) is None
    assert map_foreign("/Volumes/zeph/plate.csv", (outer, inner)) is None

    # What a person said is read from the Record, latest in force.
    from ingence.core.project import Project
    from ingence.scan.census import run_census

    (tmp_path / "pool").mkdir()
    (tmp_path / "pool" / "plate_01.csv").write_text("a,b\n1,2\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[tmp_path / "pool"])
    census = run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    append_event(
        project.path,
        kind=RecordEventKind.DEFINE,
        actor="kelt",
        occurred_at="2026-01-01T00:00:00Z",
        target=f"{PREFIX_TARGET}:C:\\Users\\kelt\\Desktop\\Brisa Data",
        against_run_id=census.run_id,
        payload={"root_id": "root_a"},
    )
    assert prefix_mappings_in_force(project.path) == (
        PrefixMapping(foreign="C:\\Users\\kelt\\Desktop\\Brisa Data", root_id="root_a"),
    )
