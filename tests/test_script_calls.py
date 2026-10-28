"""A script's own file names, read from its read and write calls and never run.

A laboratory's script names the files it reads and writes as literals in its calls:
``read_csv("kelt_counts.csv")``, ``savefig("kelt_plot.png")``, ``open(dir + "kelt_001.tif")``. Each
call with a literal file name is recorded with its direction, read or written. It is fed to
reference resolution as a mention resolved within the script's own folder or experiment. Where one
script reads one file and writes another, the second is proposed as derived from the first. Every
name here is invented.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ingence.core.project import Project
from ingence.infer.script_outputs import READ, WRITTEN, names_called

if TYPE_CHECKING:
    from pathlib import Path


def _called(text: str, language: str) -> set[tuple[str, str]]:
    return {(called.name, called.direction) for called in names_called(text, language)}


def test_a_python_script_s_literal_reads_and_writes_are_read_with_their_direction() -> None:
    text = (
        "import pandas as pd\n"
        'counts = pd.read_csv("kelt_counts.csv")\n'
        'table = pd.read_excel(os.path.join(folder, "vorn_table.xlsx"))\n'
        'image = tifffile.imread("../raw/kelt_001.tif")\n'
        'plt.savefig("kelt_plot.png")\n'
        'counts.to_csv(stem + "_summary.csv")\n'
        'with open("kelt_notes.txt") as handle:\n'
        "    pass\n"
        'with open("kelt_log.txt", "w") as handle:\n'
        "    pass\n"
        '# pd.read_csv("zib_commented.csv")\n'
    )
    assert _called(text, "python") == {
        ("kelt_counts.csv", READ),
        ("vorn_table.xlsx", READ),
        ("../raw/kelt_001.tif", READ),
        ("kelt_plot.png", WRITTEN),
        ("kelt_notes.txt", READ),
        ("kelt_log.txt", WRITTEN),
    }


def test_a_macro_s_folder_variable_is_a_folder_and_a_computed_name_is_no_name() -> None:
    text = (
        'dir = getDirectory("Choose a folder");\n'
        'open(dir + "brisa_003.tif");\n'
        'saveAs("Results", dir + "brisa_003_counts.csv");\n'
        'saveAs("Tiff", dir + title + "_mask");\n'
        'File.openAsString(dir + "brisa_notes.txt");\n'
    )
    assert _called(text, "imagej_macro") == {
        ("brisa_003.tif", READ),
        ("brisa_003_counts.csv", WRITTEN),
        ("brisa_notes.txt", READ),
    }


def test_a_matlab_script_s_reads_and_writes_are_read_in_each_way_it_builds_a_path() -> None:
    text = (
        "T = readtable('trell_counts.csv');\n"
        "I = imread(fullfile(folder, 'trell_004.tif'));\n"
        "writetable(T, [folder filesep 'trell_summary.csv']);\n"
        "fid = fopen('trell_log.txt', 'w');\n"
        "cfg = load('trell_settings.mat');\n"
        "imwrite(I, strcat(stem, '_mask.tif'));\n"
    )
    assert _called(text, "matlab") == {
        ("trell_counts.csv", READ),
        ("trell_004.tif", READ),
        ("trell_summary.csv", WRITTEN),
        ("trell_log.txt", WRITTEN),
        ("trell_settings.mat", READ),
    }


def test_a_name_without_an_extension_or_a_stem_is_not_a_file_name() -> None:
    text = 'pd.read_csv("counts")\nplt.savefig(".png")\nopen("x.csv")\n'
    assert _called(text, "python") == set()


# ---------------------------------------------------------------------------
# A dive records them, beside the save calls' patterns.
# ---------------------------------------------------------------------------


def _dived(tmp_path: Path, files: dict[str, str]) -> Project:
    """Index and dive a pool of the given files, under revision 4, and return the project."""
    from fixtures.revision_4 import write_revision
    from ingence.core.settings import ExtractionSettings
    from ingence.scan.census import run_census
    from ingence.scan.deepen import run_deepen

    pool = tmp_path / "pool"
    for relative, text in files.items():
        (pool / relative).parent.mkdir(parents=True, exist_ok=True)
        (pool / relative).write_text(text)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    project = project.save_settings(
        project.settings.model_copy(update={"extraction": ExtractionSettings(enabled=True)})
    )
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_deepen(
        project,
        run_id="run_20260101T000002Z_222222222222",
        started_at="2026-01-01T00:00:02Z",
        budget_seconds=60,
    )
    return project


def _reading(project: Project, name: str, key: str) -> object:
    from ingence.storage.catalog import open_catalog

    with open_catalog(project.path) as catalog:
        row = catalog.query(
            "SELECT o.value_structure FROM observations o JOIN entries e USING (entry_id) "
            "WHERE e.name = ? AND o.key = ?",
            [name, key],
        ).fetchone()
    return None if row is None else json.loads(row[0])


_ANALYSIS_SCRIPT = (
    'import pandas as pd\ncounts = pd.read_csv("kelt_counts.csv")\nplt.savefig("kelt_plot.png")\n'
)


def test_a_dive_records_a_script_s_called_names_with_their_direction(tmp_path: Path) -> None:
    project = _dived(
        tmp_path,
        {"kelt/analyse_kelt.py": _ANALYSIS_SCRIPT, "kelt/kelt_counts.csv": "a,b\n1,2\n"},
    )
    assert _reading(project, "analyse_kelt.py", "script.file_names") == {
        "read": ["kelt_counts.csv"],
        "written": ["kelt_plot.png"],
    }


# ---------------------------------------------------------------------------
# Resolved within the script's own folder or experiment.
# ---------------------------------------------------------------------------


def _pool_of(*paths: tuple[str, str], experiments: dict[str, str] | None = None) -> object:
    """A pool of files (identifier, path) under one root, and each file's experiment folder."""
    from ingence.core.ids import CaseSensitivity
    from ingence.core.schema import Entry, EntryType, Root
    from ingence.infer.references import PoolPaths

    root = Root(
        root_id="root_a",
        root_uri="file:///mnt/vorn",
        mount_path="/mnt/vorn",
        case_sensitivity=CaseSensitivity.SENSITIVE,
        first_seen_run="r",
        last_seen_run="r",
    )
    entries = [
        Entry(
            entry_id=entry_id,
            root_id="root_a",
            relative_path=path,
            name=path.rsplit("/", 1)[-1],
            entry_type=EntryType.DIRECTORY
            if "." not in path.rsplit("/", 1)[-1]
            else EntryType.FILE,
            extension="",
            first_seen_run="r",
            last_seen_run="r",
        )
        for entry_id, path in paths
    ]
    return PoolPaths.of(entries, {"root_a": root}, experiments=experiments or {})


def _called_mention(path: str, direction: str, entry_id: str = "script") -> object:
    from ingence.infer.references import Mention

    return Mention(
        entry_id=entry_id,
        observer="keywords",
        path=path,
        shape="bare" if "/" not in path else "relative",
        direction=direction,
    )


def test_a_script_s_bare_name_resolves_in_its_folder_then_by_name_in_its_experiment_only() -> None:
    from ingence.infer.references import BY_NAME, IN_FOLDER, NO_MATCH, SEVERAL_MATCHES, resolve

    pool = _pool_of(
        ("exp", "zib exp"),
        ("script", "zib exp/analysis/analyse.py"),
        ("beside", "zib exp/analysis/kelt_plot.png"),
        ("counts", "zib exp/processed/kelt_counts.csv"),
        ("twin_a", "zib exp/day1/vorn_counts.csv"),
        ("twin_b", "zib exp/day2/vorn_counts.csv"),
        ("elsewhere", "other/brisa_counts.csv"),
        experiments={"script": "exp"},
    )
    outcome = {
        path: (resolved.outcome, resolved.target_id)
        for path in ("kelt_plot.png", "kelt_counts.csv", "vorn_counts.csv", "brisa_counts.csv")
        for resolved in (resolve(_called_mention(path, READ), pool),)
    }
    assert outcome == {
        "kelt_plot.png": (IN_FOLDER, "beside"),
        "kelt_counts.csv": (BY_NAME, "counts"),
        "vorn_counts.csv": (SEVERAL_MATCHES, None),
        # Unique in the pool and outside the script's experiment: a bare name is not looked for
        # there.
        "brisa_counts.csv": (NO_MATCH, None),
    }


def test_a_script_beneath_no_experiment_resolves_its_bare_names_in_its_own_folder_only() -> None:
    from ingence.infer.references import IN_FOLDER, NO_MATCH, resolve

    pool = _pool_of(
        ("script", "loose/analyse.py"),
        ("beside", "loose/trell_plot.png"),
        ("below", "loose/data/trell_counts.csv"),
    )
    assert resolve(_called_mention("trell_plot.png", WRITTEN), pool).outcome == IN_FOLDER
    assert resolve(_called_mention("trell_counts.csv", READ), pool).outcome == NO_MATCH
    assert resolve(_called_mention("data/trell_counts.csv", READ), pool).outcome == IN_FOLDER


def test_a_run_s_recorded_call_names_are_mentions_carrying_their_direction() -> None:
    from ingence.infer.references import mentions_of

    observations = [
        (
            "obs_9",
            "script",
            "keywords",
            "script.file_names",
            '{"read":["kelt_counts.csv"],"written":["kelt_plot.png","../out/kelt.csv"]}',
        ),
    ]
    mentions = mentions_of(observations)
    assert {(m.path, m.direction, m.shape, m.observation_id) for m in mentions} == {
        ("kelt_counts.csv", READ, "bare", "obs_9"),
        ("kelt_plot.png", WRITTEN, "bare", "obs_9"),
        ("../out/kelt.csv", WRITTEN, "relative", "obs_9"),
    }


# ---------------------------------------------------------------------------
# End to end: a script that reads one file and writes another proposes a derivation.
# ---------------------------------------------------------------------------


def test_a_script_reading_one_file_and_writing_another_proposes_the_second_derived(
    tmp_path: Path,
) -> None:
    """The direction is the script's own calls', never a stage word's.

    Neither table's name states a stage. The script reads its input from another stage folder of
    its experiment, found by name there and so below the reference rule's floor, and writes its
    plot beside itself; the plot is proposed as derived from the counts, never the reverse, and as
    a proposal: nothing asserts it.
    """
    from ingence.infer.run import run_inference
    from ingence.storage.catalog import open_catalog

    experiment = "2020-03-04 vorn kelt"
    project = _dived(
        tmp_path,
        {
            f"{experiment}/raw/kelt_001.nd2": "x",
            f"{experiment}/processed/kelt_counts.csv": "a,b\n1,2\n",
            f"{experiment}/analysis/analyse_kelt.py": _ANALYSIS_SCRIPT,
            f"{experiment}/analysis/kelt_plot.png": "x",
            "elsewhere/kelt_counts.csv": "a,b\n3,4\n",
        },
    )
    report = run_inference(project, run_id="run_20260101T000003Z_333333333333", started_at="t")
    assert not report.cancelled
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT c.rule_id, s.relative_path, t.relative_path, c.decision_tier, c.support_json "
            "FROM candidates c JOIN entries s ON s.entry_id = c.source_id "
            "JOIN entries t ON t.entry_id = c.target_id ORDER BY 1, 2, 3"
        ).fetchall()
        asserted = catalog.query(
            "SELECT count(*) FROM assertions WHERE primitive = 'derives'"
        ).fetchone()
    by_calls = {
        (str(source), str(target), str(tier))
        for rule, source, target, tier, _ in rows
        if rule == "derivation_by_a_scripts_calls"
    }
    assert by_calls == {
        (
            f"{experiment}/processed/kelt_counts.csv",
            f"{experiment}/analysis/kelt_plot.png",
            "circumstantial",
        )
    }
    referenced = {
        (str(source), str(target))
        for rule, source, target, _, _ in rows
        if rule == "references_a_mentioned_file"
    }
    assert referenced == {
        (f"{experiment}/analysis/analyse_kelt.py", f"{experiment}/analysis/kelt_plot.png")
    }
    assert asserted == (0,)


# ---------------------------------------------------------------------------
# The two predicates, on pairs built by hand.
# ---------------------------------------------------------------------------


def _records(entry_id: str, *classified: str) -> object:
    from ingence.core.ids import PROJECT_SCOPE
    from ingence.core.schema import Claim, Defeater, Entry
    from ingence.core.tiers import ResolutionStatus
    from ingence.infer.subjects import EntryRecords

    return EntryRecords(
        entry=Entry(
            entry_id=entry_id,
            root_id="root",
            relative_path=f"kelt/{entry_id}",
            name=entry_id,
            depth=1,
            entry_type="file",
            extension="",
            compound_extension="",
            first_seen_run="r",
            last_seen_run="r",
        ),
        claims=tuple(
            Claim(
                claim_id=f"claim:{entry_id}:{value}",
                run_id="r",
                subject=entry_id,
                predicate="classifies.format_from_extension",
                object_reference=value,
                rule_id="format_from_extension",
                rule_version="1",
                scope=PROJECT_SCOPE,
                defeaters=(Defeater(reason="a test", rule_id="d", rule_version="1"),),
                resolution_status=ResolutionStatus.RESOLVED,
            )
            for value in classified
        ),
    )


def _resolved(target: str, direction: str, outcome: str = "in_folder") -> object:
    from ingence.infer.references import Mention, Resolution

    mention = Mention(
        entry_id="script.py",
        observer="keywords",
        path=target,
        shape="bare",
        observation_id=f"obs:{direction}:{target}",
        direction=direction,
    )
    return Resolution(mention=mention, outcome=outcome, target_id=target, answered=1)


def _drawn(source: str, target: str, *resolutions: object) -> object:
    from ingence.infer.subjects import PairRecords

    return PairRecords(
        source=_records(source),
        target=_records(target),
        within="co_mentioned",
        source_membership="a",
        target_membership="b",
        resolutions=tuple(resolutions),
        describing=_records("script.py", "python_source"),
    )


_SCRIPTS = {"describing_formats": ["python_source"], "minimum_resolution": "unique_basename"}


def test_the_script_s_calls_give_the_direction_and_a_file_read_and_written_gives_none() -> None:
    from ingence.rulebook.registry import PREDICATES

    predicate = PREDICATES.get("derives_from_script_calls")
    read, written = _resolved("counts.csv", READ), _resolved("plot.png", WRITTEN)
    support = predicate.evaluate(_drawn("counts.csv", "plot.png", read, written), _SCRIPTS)
    assert [(c.name, c.family) for c in support] == [
        ("read_and_written_by_one_script", "declared_reference")
    ]
    assert set(support[0].observations) == {
        "obs:read:counts.csv",
        "obs:written:plot.png",
        "claim:script.py:python_source",
    }
    assert predicate.evaluate(_drawn("plot.png", "counts.csv", read, written), _SCRIPTS) is None
    rewritten = _resolved("counts.csv", WRITTEN)
    both = _drawn("counts.csv", "plot.png", read, written, rewritten)
    assert predicate.evaluate(both, _SCRIPTS) is None
    assert "no direction" in predicate.explain_abstention(both, _SCRIPTS).reason


def test_a_name_found_only_below_the_floor_or_by_a_file_that_is_no_script_gives_nothing() -> None:
    from ingence.rulebook.registry import PREDICATES

    predicate = PREDICATES.get("derives_from_script_calls")
    read = _resolved("counts.csv", READ, outcome="by_name")
    written = _resolved("plot.png", WRITTEN)
    pair = _drawn("counts.csv", "plot.png", read, written)
    exact = {**_SCRIPTS, "minimum_resolution": "exact"}
    assert predicate.evaluate(pair, exact) is None
    assert "below the rule's floor" in predicate.explain_abstention(pair, exact).reason
    assert predicate.evaluate(pair, _SCRIPTS) is not None
    notes = {**_SCRIPTS, "describing_formats": ["r_source"]}
    assert "not a script" in predicate.explain_abstention(pair, notes).reason


def test_the_roles_constraint_reads_a_script_s_calls_as_roles_only_where_the_rule_says() -> None:
    from ingence.rulebook.registry import PREDICATES, Refusal

    constraint = PREDICATES.get("endpoint_holds_an_eligible_role")
    roles = {"source_roles": ["raw"], "target_roles": ["analysis"]}
    pair = _drawn(
        "counts.csv", "plot.png", _resolved("counts.csv", READ), _resolved("plot.png", WRITTEN)
    )
    assert isinstance(constraint.evaluate(pair, roles), Refusal)
    assert constraint.evaluate(pair, {**roles, "script_calls_are_roles": True}) is None
    reversed_pair = _drawn(
        "plot.png", "counts.csv", _resolved("counts.csv", READ), _resolved("plot.png", WRITTEN)
    )
    refused = constraint.evaluate(reversed_pair, {**roles, "script_calls_are_roles": True})
    assert isinstance(refused, Refusal)


def test_a_pair_a_script_s_calls_give_is_proposed_by_that_rule_alone(tmp_path: Path) -> None:
    """The describing-file rule does not propose the same pair again from the calls' mentions.

    Both names state a stage, so the describing-file reading could give a direction too. The
    script's calls are its own mentions, with their direction, and that pair is theirs.
    """
    from ingence.infer.run import run_inference
    from ingence.storage.catalog import open_catalog

    script = (
        "import pandas as pd\n"
        'image = imread("trell_processed.tif")\n'
        'pd.DataFrame().to_csv("trell_results.csv")\n'
    )
    project = _dived(
        tmp_path,
        {
            "vorn/analyse_trell.py": script,
            "vorn/trell_processed.tif": "x",
            "vorn/trell_results.csv": "a,b\n1,2\n",
        },
    )
    run_inference(project, run_id="run_20260101T000003Z_333333333333", started_at="t")
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT c.rule_id, s.name, t.name FROM candidates c "
            "JOIN entries s ON s.entry_id = c.source_id JOIN entries t ON t.entry_id = c.target_id "
            "WHERE c.primitive = 'derives'"
        ).fetchall()
    assert {(str(rule), str(source), str(target)) for rule, source, target in rows} == {
        ("derivation_by_a_scripts_calls", "trell_processed.tif", "trell_results.csv")
    }


def test_a_called_path_naming_folders_matches_only_a_file_under_those_folders() -> None:
    """A path that names folders finds its file only under them.

    ``../../vorn_b/kelt_counts.csv`` does not fall back to any ``kelt_counts.csv`` in the script's
    experiment. Where a path names folders, the file found must lie under them.
    """
    from ingence.infer.references import BY_NAME, NO_MATCH, resolve

    pool = _pool_of(
        ("exp", "zib exp"),
        ("script", "zib exp/analysis/deep/analyse.py"),
        ("counts", "zib exp/processed/kelt_counts.csv"),
        ("table", "zib exp/tables/vorn_b/brisa_table.csv"),
        experiments={"script": "exp"},
    )
    assert resolve(_called_mention("../../vorn_b/kelt_counts.csv", READ), pool).outcome == NO_MATCH
    assert resolve(_called_mention("../../processed/kelt_counts.csv", READ), pool).outcome in (
        "in_folder",
        BY_NAME,
    )
    found = resolve(_called_mention("../vorn_b/brisa_table.csv", READ), pool)
    assert (found.outcome, found.target_id) == (BY_NAME, "table")


def test_a_script_s_calls_are_read_by_an_extractor_of_their_own_and_only_in_scripts(
    tmp_path: Path,
) -> None:
    """The calls are read by an extractor of their own, declared for scripts' extensions only.

    The keyword extractor keeps its version, so no other text file is read again on the next dive
    for them. Bumping the keyword extractor to version 2 would have every text file read again.
    """
    from ingence.extractors import load_builtins
    from ingence.extractors.base import EXTRACTORS
    from ingence.storage.catalog import open_catalog

    load_builtins()
    assert EXTRACTORS.declaration("keywords").version == "1"
    declared = EXTRACTORS.declaration("script_calls")
    assert set(declared.attributes["extensions"].split(",")) == {".ijm", ".m", ".py"}
    project = _dived(
        tmp_path,
        {"kelt/analyse_kelt.py": _ANALYSIS_SCRIPT, "kelt/kelt_notes.txt": 'read_csv("zib.csv")\n'},
    )
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.name, o.observer FROM observations o JOIN entries e USING (entry_id) "
            "WHERE o.key = 'script.file_names'"
        ).fetchall()
    assert [(str(name), str(observer)) for name, observer in rows] == [
        ("analyse_kelt.py", "script_calls")
    ]


def test_the_harder_calls_are_read_or_left_as_they_should_be() -> None:
    """Calls are read from code and not from text that only looks like a call.

    A comment marker inside a string does not hide the call after it. ``direction`` is not read as a
    folder. A docstring's example, a glob and a web address are not read as files. A file opened to
    be updated is not recorded as read only. An archive member opened through the archive is still
    read as a file, which is a known limit.
    """
    python = (
        'print("#"); df = pd.read_csv("kelt_counts.csv")\n'
        'x = np.load(direction + "_kelt.npy")\n'
        '"""An example:\n    pd.read_csv("example_counts.csv")\n"""\n'
        'files = pd.read_csv("kelt_??.csv")\n'
        'webbrowser.open("http://example.org/index.html")\n'
        'with open("kelt_log.txt", "r+") as handle:\n'
        "    pass\n"
        'frame = pd.read_csv(os.path.join(dataFolder, "vorn_table.csv"))\n'
    )
    assert _called(python, "python") == {
        ("kelt_counts.csv", READ),
        ("kelt_log.txt", READ),
        ("kelt_log.txt", WRITTEN),
        ("vorn_table.csv", READ),
    }
    matlab = "fprintf('%d\\n', x); im = imread('kelt_001.tif');\n"
    assert _called(matlab, "matlab") == {("kelt_001.tif", READ)}
