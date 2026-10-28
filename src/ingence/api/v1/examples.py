"""Invented examples of the responses of the overview, assumptions, checks, overlap, story,
workflows, Ask, areas and goals routes.

The examples live in one module, so that they stay available to the tests and to the pages' own
fixtures. Everything here is invented in the shape of the shipped sample laboratory
(``ingence demo``). Areas are instrument-like or project-like folders, never a person. Experiments
are named the way the demo's generator names them, with its words. Every count is made up and
consistent with the others on the same page. No name, folder, file, instrument model or count comes
from any real laboratory.

Each function takes the run it answers for and returns the route's model, built once per call so
a caller may not mutate what another reads.
"""

from __future__ import annotations

from ingence import SCHEMA_VERSION
from ingence.api.v1.areas import AreaItem, AreasResponse
from ingence.api.v1.ask import (
    AskDates,
    AskExperiment,
    AskFilters,
    AskResponse,
    AskSameTheme,
    Expansion,
    MatchedWord,
    WordsRead,
)
from ingence.api.v1.assumptions import (
    REJECT_ROUTE,
    AssumptionExample,
    AssumptionItem,
    AssumptionsResponse,
    RejectAction,
)
from ingence.api.v1.checks import (
    ANSWER_ROUTE,
    NO_RESULT_UNTIL_ANSWERED,
    AnswerAction,
    CheckDefault,
    CheckItem,
    CheckOption,
    ChecksResponse,
)
from ingence.api.v1.experiment_story import (
    ExperimentStoryResponse,
    StoryDay,
    StoryMadeFrom,
    StoryRepeat,
    StoryWorkflow,
)
from ingence.api.v1.goals import GoalItem, GoalMatch, GoalsResponse, GoalSummary
from ingence.api.v1.models import AreaKindName, JobPhaseName, MatchTierName
from ingence.api.v1.overlap import (
    OverlapArea,
    OverlapByYear,
    OverlapConnections,
    OverlapEdge,
    OverlapEvidence,
    OverlapMode,
    OverlapNode,
    OverlapResponse,
)
from ingence.api.v1.overview import (
    OverviewArea,
    OverviewCompleteness,
    OverviewCounts,
    OverviewCoverage,
    OverviewDateRange,
    OverviewFileType,
    OverviewGoal,
    OverviewRepeats,
    OverviewResponse,
    OverviewTheme,
)
from ingence.api.v1.themes import (
    Substitute,
    ThemeOverTime,
    ThemeShare,
    WordCount,
    WordSpan,
    YearStory,
)
from ingence.api.v1.workflows import (
    WorkflowChain,
    WorkflowEvidence,
    WorkflowItem,
    WorkflowsResponse,
    WorkflowStage,
)

__all__ = [
    "areas_example",
    "ask_example",
    "assumptions_example",
    "checks_example",
    "experiment_story_example",
    "goals_example",
    "overlap_example",
    "overview_example",
    "workflows_example",
]

# ---------------------------------------------------------------------------
# The invented laboratory: four areas, four experiments, two workflows.
# ---------------------------------------------------------------------------

#: The areas: an instrument's folder confirmed, an instrument's folder assumed, a project's year
#: folder, and a plain shared folder. (id, name, kind, kind_assumed, experiments, files, first
#: year, last year.)
_AREAS: tuple[tuple[str, str, AreaKindName, bool, int, int, int | None, int | None], ...] = (
    ("area_confocal", "Confocal", "instrument", False, 14, 1240, 2023, 2024),
    ("area_imaging_2024", "Imaging 2024", "project", True, 7, 865, 2024, 2024),
    ("area_plate_reader", "Plate reader", "instrument", True, 9, 310, 2023, 2024),
    ("area_shared", "Shared", "folder", False, 2, 96, None, None),
)

#: The experiments: two repeats of a timecourse under the confocal, a titration on the plate
#: reader, and a z-stack in the project folder.
EXPERIMENT_TIMECOURSE = "entry_2a7f10c4e9b1"
EXPERIMENT_TIMECOURSE_REPEAT = "entry_5c0d93ab7e22"
EXPERIMENT_TITRATION = "entry_9e41b7d0f3a5"
EXPERIMENT_ZSTACK = "entry_c38a55e2d617"

_THEME_TIMECOURSE = "timecourse imaging"
_THEME_TITRATION = "titration plate"
_THEME_ZSTACK = "zstack imaging"

_WORKFLOW_IMAGING = "wf_imaging_then_analysis"
_WORKFLOW_PLATE = "wf_plate_then_summary"
_IMAGING_STAGES = ["acquisition", "processed", "analysis"]

#: Words the Dictionary reads as connectives, dropped from a question or a goal.
_DROPPED = frozenset({"a", "an", "and", "in", "of", "or", "the", "with"})


def _node(
    experiment_id: str,
    label: str,
    area: str,
    area_kind: AreaKindName,
    theme: str,
    start: str,
    end: str,
    completeness: float | None,
    repeats: int,
) -> OverlapNode:
    return OverlapNode(
        experiment_id=experiment_id,
        label=label,
        area=area,
        area_kind=area_kind,
        theme=theme,
        start=start,
        end=end,
        completeness=completeness,
        repeats=repeats,
    )


def _nodes() -> list[OverlapNode]:
    return [
        _node(
            EXPERIMENT_TIMECOURSE,
            "2024-03-12 HeLa DMSO timecourse",
            "Confocal",
            "instrument",
            _THEME_TIMECOURSE,
            "2024-03-12",
            "2024-03-15",
            0.8,
            3,
        ),
        _node(
            EXPERIMENT_TIMECOURSE_REPEAT,
            "2024-03-19 HeLa DMSO timecourse rep2",
            "Confocal",
            "instrument",
            _THEME_TIMECOURSE,
            "2024-03-19",
            "2024-03-20",
            0.5,
            3,
        ),
        _node(
            EXPERIMENT_TITRATION,
            "U2OS titration plate 1",
            "Plate reader",
            "instrument",
            _THEME_TITRATION,
            "2023-11-02",
            "2023-11-02",
            1.0,
            1,
        ),
        _node(
            EXPERIMENT_ZSTACK,
            "zstack 2024-06-04 U2OS",
            "Imaging 2024",
            "project",
            _THEME_ZSTACK,
            "2024-06-04",
            "2024-06-07",
            None,
            1,
        ),
    ]


def _area_rows() -> list[OverviewArea]:
    return [
        OverviewArea(
            id=area_id,
            name=name,
            kind=kind,
            kind_assumed=assumed,
            experiments=experiments,
            files=files,
        )
        for area_id, name, kind, assumed, experiments, files, _, _ in _AREAS
    ]


# ---------------------------------------------------------------------------
# A1. The overview.
# ---------------------------------------------------------------------------


def overview_example(run_id: str) -> OverviewResponse:
    """The invented laboratory in one screen: 32 experiments, 2,511 files, four areas."""
    file_types = [
        OverviewFileType(
            type_id="nd2", label="ND2 image", category="instrument", files=1180, share=0.47
        ),
        OverviewFileType(
            type_id="tiff", label="TIFF image", category="instrument", files=612, share=0.244
        ),
        OverviewFileType(
            type_id="csv", label="CSV table", category="general", files=402, share=0.16
        ),
        OverviewFileType(
            type_id="xlsx", label="Spreadsheet", category="document", files=148, share=0.059
        ),
        OverviewFileType(
            type_id="py", label="Python script", category="program", files=71, share=0.028
        ),
        OverviewFileType(
            type_id="docx", label="Word document", category="document", files=60, share=0.024
        ),
        OverviewFileType(
            type_id="zip", label="Zip archive", category="archive", files=38, share=0.015
        ),
    ]
    return OverviewResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        counts=OverviewCounts(experiments=32, files=2511, areas=len(_AREAS)),
        date_range=OverviewDateRange(
            first="2023-01-09",
            last="2024-09-27",
            basis=(
                "a date in each experiment's name, else the earliest believed time of a file in it"
            ),
        ),
        areas=_area_rows(),
        file_types=file_types,
        completeness=OverviewCompleteness(
            raw_files=1792,
            raw_with_analysis=1344,
            experiments_complete=18,
            experiments_partial=7,
            experiments_raw_only=5,
            experiments_analysis_only=2,
            not_read=120,
        ),
        repeats=OverviewRepeats(seen_once=20, repeated=12, groups=5),
        themes_top=[
            OverviewTheme(id="theme_3", label=_THEME_TIMECOURSE, experiments=11),
            OverviewTheme(id="theme_1", label=_THEME_TITRATION, experiments=8),
            OverviewTheme(id="theme_6", label=_THEME_ZSTACK, experiments=6),
        ],
        goals=[OverviewGoal(id="goal_1", text="HeLa timecourses with analysis", matches=7)],
        assumptions_count=6,
        checks_count=4,
        coverage=OverviewCoverage(read=2180, represented=211, not_reached=120),
    )


# ---------------------------------------------------------------------------
# A2. The assumptions.
# ---------------------------------------------------------------------------


def _assumption(
    assumption_id: str,
    kind: str,
    text: str,
    count: int,
    examples: list[tuple[str, str]],
    tier: str,
) -> AssumptionItem:
    return AssumptionItem.model_validate(
        {
            "id": assumption_id,
            "kind": kind,
            "text": text,
            "count": count,
            "examples": [
                AssumptionExample(entry_id=entry_id, label=label) for entry_id, label in examples
            ],
            "reject": RejectAction(route=REJECT_ROUTE, body={"id": assumption_id}),
            "tier": tier,
        }
    )


def assumptions_example(run_id: str) -> AssumptionsResponse:
    """Six assumptions, most files first, each with its tier word and how to reject it."""
    items = [
        _assumption(
            "asm_file_type_nd2",
            "file_type",
            "Files ending .nd2 are microscope images.",
            1180,
            [
                ("entry_0b11aa3c5d7e", "Confocal/2024-03-12 HeLa DMSO timecourse/raw/t001.nd2"),
                ("entry_0b11aa3c5d7f", "Confocal/2024-03-12 HeLa DMSO timecourse/raw/t002.nd2"),
            ],
            "defeasible",
        ),
        _assumption(
            "asm_writer_processed_tiff",
            "writer",
            "TIFF files under a processed folder were written by the image program.",
            612,
            [("entry_1c22bb4d6e8f", "Confocal/2024-03-12 HeLa DMSO timecourse/processed/t001.tif")],
            "abductive",
        ),
        _assumption(
            "asm_workflow_confocal",
            "workflow",
            "Experiments under Confocal follow Imaging then analysis.",
            540,
            [("entry_2a7f10c4e9b1", "Confocal/2024-03-12 HeLa DMSO timecourse")],
            "defeasible",
        ),
        _assumption(
            "asm_area_kind_plate_reader",
            "area_kind",
            "Plate reader is an instrument's folder.",
            310,
            [("entry_3d33cc5e7f90", "Plate reader")],
            "abductive",
        ),
        _assumption(
            "asm_domain_channel",
            "domain",
            "channel means a microscope channel in the Confocal folder.",
            240,
            [("entry_4e44dd6f8a01", "Confocal/2024-03-12 HeLa DMSO timecourse/raw/channel2.nd2")],
            "defeasible",
        ),
        _assumption(
            "asm_spelling_timecorse",
            "spelling",
            "timecorse is a slip for timecourse.",
            12,
            [("entry_5f55ee7a9b12", "Confocal/2024-03-19 HeLa DMSO timecorse rep2/raw/t001.nd2")],
            "abductive",
        ),
    ]
    return AssumptionsResponse(
        schema_version=SCHEMA_VERSION, run_id=run_id, total=len(items), assumptions=items
    )


# ---------------------------------------------------------------------------
# A3. The checks.
# ---------------------------------------------------------------------------


def _check(
    check_id: str,
    question_id: str,
    kind: str,
    title: str,
    text: str,
    default: tuple[str, str] | None,
    settles_n: int,
    settles_unit: str,
    options: list[tuple[str, str]],
) -> CheckItem:
    return CheckItem.model_validate(
        {
            "id": check_id,
            "question_id": question_id,
            "kind": kind,
            "title": title,
            "text": text if default is not None else NO_RESULT_UNTIL_ANSWERED,
            "default": CheckDefault(value=default[0], text=default[1]) if default else None,
            "settles_n": settles_n,
            "settles_unit": settles_unit,
            "options": [CheckOption(value=value, text=words) for value, words in options],
            "answer": AnswerAction(route=ANSWER_ROUTE, body={"question_id": question_id}),
        }
    )


def checks_example(run_id: str, *, limit: int) -> ChecksResponse:
    """Four checks ranked by what one answer settles, cut to ``limit``."""
    items = [
        _check(
            "chk_file_type_nd2",
            "q_file_type_nd2",
            "file_type",
            "Are .nd2 files microscope images?",
            "1,180 files end .nd2; the run reads them as microscope images.",
            ("yes", "Read as microscope images"),
            1180,
            "files",
            [
                ("yes", "Yes, microscope images"),
                ("no", "No, something else"),
                ("cannot_say", "Can't say"),
            ],
        ),
        _check(
            "chk_area_kind_plate_reader",
            "q_area_kind_plate_reader",
            "area_kind",
            "What is the Plate reader folder?",
            "",
            None,
            310,
            "files",
            [
                ("instrument", "An instrument's folder"),
                ("project", "A project's folder"),
                ("folder", "A plain folder"),
            ],
        ),
        _check(
            "chk_workflow_confocal",
            "q_workflow_confocal",
            "workflow",
            "Do Confocal experiments follow Imaging then analysis?",
            "21 experiments under Confocal; 12 reached analysis, 9 stopped at acquisition.",
            ("yes", "Read as following Imaging then analysis"),
            21,
            "folders",
            [("yes", "Yes"), ("no", "No, another workflow"), ("cannot_say", "Can't say")],
        ),
        _check(
            "chk_repeat_timecourse",
            "q_repeat_timecourse",
            "repeat",
            "Are the three HeLa DMSO timecourses repeats of one experiment?",
            "Three folders share every word but a replicate label.",
            ("yes", "Read as three repeats"),
            3,
            "pairs",
            [("yes", "Yes, repeats"), ("no", "No, different experiments")],
        ),
    ]
    return ChecksResponse(
        schema_version=SCHEMA_VERSION, run_id=run_id, total=len(items), checks=items[:limit]
    )


# ---------------------------------------------------------------------------
# A4. The overlap.
# ---------------------------------------------------------------------------


def _connections() -> OverlapConnections:
    edges = [
        OverlapEdge(
            a=EXPERIMENT_TIMECOURSE,
            b=EXPERIMENT_TIMECOURSE_REPEAT,
            kinds=["repeat_of", "shared_words"],
            weight=0.9,
            evidence=[
                OverlapEvidence(kind="repeat_of", words=[], count=None, unit="experiments"),
                OverlapEvidence(
                    kind="shared_words", words=["hela", "dmso", "timecourse"], count=3, unit="words"
                ),
            ],
        ),
        OverlapEdge(
            a=EXPERIMENT_TIMECOURSE,
            b=EXPERIMENT_ZSTACK,
            kinds=["workflow"],
            weight=0.4,
            evidence=[
                OverlapEvidence(kind="workflow", words=_IMAGING_STAGES, count=3, unit="experiments")
            ],
        ),
        OverlapEdge(
            a=EXPERIMENT_TITRATION,
            b=EXPERIMENT_ZSTACK,
            kinds=["shared_words"],
            weight=0.25,
            evidence=[OverlapEvidence(kind="shared_words", words=["u2os"], count=1, unit="words")],
        ),
    ]
    return OverlapConnections(nodes=_nodes(), edges=edges)


def _by_year() -> OverlapByYear:
    themes = [
        ThemeOverTime(
            theme=3,
            label=_THEME_TIMECOURSE,
            broad="imaging",
            experiments=11,
            first=2023,
            peak=2024,
            last=2024,
            going=True,
            direction="rising",
            by_year={"2023": 4, "2024": 7},
            succession=[
                WordSpan(word="widefield", experiments=3, first=2023, last=2023, median=2023.0),
                WordSpan(word="confocal", experiments=8, first=2024, last=2024, median=2024.0),
            ],
            specific_words=[
                WordSpan(word="dmso", experiments=6, first=2023, last=2024, median=2024.0)
            ],
        ),
        ThemeOverTime(
            theme=1,
            label=_THEME_TITRATION,
            broad="plate assays",
            experiments=8,
            first=2023,
            peak=2023,
            last=2024,
            going=True,
            direction="falling",
            by_year={"2023": 6, "2024": 2},
            succession=[],
            specific_words=[
                WordSpan(word="titration", experiments=8, first=2023, last=2024, median=2023.0)
            ],
        ),
    ]
    story = {
        "2023": YearStory(
            experiments=13,
            most=[ThemeShare(label=_THEME_TITRATION, experiments=6, lift=1.6)],
            under=[ThemeShare(label=_THEME_TIMECOURSE, experiments=4, lift=0.8)],
            began=[_THEME_TITRATION, _THEME_TIMECOURSE],
            last_seen_year_before=[],
            new_words=[WordCount(word="titration", experiments=6)],
        ),
        "2024": YearStory(
            experiments=19,
            most=[ThemeShare(label=_THEME_TIMECOURSE, experiments=7, lift=1.4)],
            under=[ThemeShare(label=_THEME_TITRATION, experiments=2, lift=0.6)],
            began=[_THEME_ZSTACK],
            last_seen_year_before=["western blot"],
            new_words=[WordCount(word="zstack", experiments=6)],
        ),
    }
    substitutes = [
        Substitute(
            earlier="widefield",
            later="confocal",
            similarity=0.71,
            together=1,
            earlier_years="2023",
            later_years="2024",
            earlier_n=3,
            later_n=8,
        )
    ]
    areas = [
        OverlapArea(
            id=area_id,
            name=name,
            kind=kind,
            kind_assumed=assumed,
            experiments=experiments,
            first=first,
            last=last,
        )
        for area_id, name, kind, assumed, experiments, _, first, last in _AREAS
    ]
    return OverlapByYear(themes=themes, story=story, substitutes=substitutes, areas=areas)


def overlap_example(run_id: str, *, mode: OverlapMode) -> OverlapResponse:
    """The connections map, or the run by year, for the invented laboratory."""
    return OverlapResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        mode=mode,
        connections=_connections() if mode == "connections" else None,
        by_year=_by_year() if mode == "by_year" else None,
    )


# ---------------------------------------------------------------------------
# A5. An experiment's story.
# ---------------------------------------------------------------------------


def experiment_story_example(run_id: str, experiment_id: str) -> ExperimentStoryResponse:
    """The timecourse's four days, for whichever experiment was asked about."""
    return ExperimentStoryResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        experiment_id=experiment_id,
        days=[
            StoryDay(date="2024-03-12", stage="acquisition", files=48, types=["nd2"]),
            StoryDay(date="2024-03-13", stage="acquisition", files=52, types=["nd2"]),
            StoryDay(date="2024-03-14", stage="processed", files=96, types=["tiff"]),
            StoryDay(date="2024-03-15", stage="analysis", files=6, types=["csv", "py"]),
        ],
        workflow=StoryWorkflow(
            id=_WORKFLOW_IMAGING,
            reached="analysis",
            expected=_IMAGING_STAGES,
            raw_files=100,
            raw_reached=96,
            reached_type="csv",
        ),
        repeats=[
            StoryRepeat(
                experiment_id=EXPERIMENT_TIMECOURSE_REPEAT, label="2024-03-19 HeLa DMSO timecourse"
            )
        ],
        made_from=[StoryMadeFrom(experiment_id=EXPERIMENT_TITRATION, label="2024-02-20 titration")],
    )


# ---------------------------------------------------------------------------
# A6. The workflows.
# ---------------------------------------------------------------------------


def workflows_example(run_id: str) -> WorkflowsResponse:
    """A suggested imaging workflow and a declared plate-reader workflow."""
    imaging = WorkflowItem(
        id=_WORKFLOW_IMAGING,
        name="Imaging then analysis",
        origin="suggested",
        stages=[
            WorkflowStage(
                stage="acquisition", types=["nd2", "tiff"], folders=["raw", "images"], names=["raw"]
            ),
            WorkflowStage(
                stage="processed",
                types=["tiff"],
                folders=["processed"],
                names=["proc", "processed"],
            ),
            WorkflowStage(
                stage="analysis",
                types=["csv", "xlsx", "py"],
                folders=["analysis", "results"],
                names=["analysis", "results", "summary"],
            ),
        ],
        exempt=["notes", "readme"],
        scope=None,
        evidence=WorkflowEvidence(
            experiments=21,
            chains=[
                WorkflowChain(chain=_IMAGING_STAGES, experiments=12),
                WorkflowChain(chain=["acquisition", "analysis"], experiments=9),
            ],
        ),
        in_force=True,
    )
    plate = WorkflowItem(
        id=_WORKFLOW_PLATE,
        name="Plate reader then summary",
        origin="declared",
        stages=[
            WorkflowStage(
                stage="plate", types=["xlsx", "csv"], folders=["plates"], names=["plate"]
            ),
            WorkflowStage(stage="summary", types=["xlsx"], folders=["summary"], names=["summary"]),
        ],
        exempt=[],
        scope="Plate reader",
        evidence=WorkflowEvidence(
            experiments=9, chains=[WorkflowChain(chain=["plate", "summary"], experiments=9)]
        ),
        in_force=True,
    )
    return WorkflowsResponse(
        schema_version=SCHEMA_VERSION, run_id=run_id, workflows=[imaging, plate]
    )


# ---------------------------------------------------------------------------
# A7. Ask.
# ---------------------------------------------------------------------------


def _read_words(text: str) -> WordsRead:
    """The words of a question or a goal, kept or dropped, as the example reads them."""
    words = [word.strip(".,;:!?").lower() for word in text.split()]
    words = [word for word in words if word]
    kept = [word for word in words if word not in _DROPPED]
    dropped = [word for word in words if word in _DROPPED]
    return WordsRead(kept=kept, dropped=dropped, unknown=[])


def _expansions(read: WordsRead) -> list[Expansion]:
    found: list[Expansion] = []
    for word in read.kept:
        if word == "timecourse":
            found.append(
                Expansion(word=word, to=["time course", "timelapse"], how="a Dictionary variant")
            )
        elif word == "timecourses":
            found.append(Expansion(word=word, to=["timecourse"], how="a plural"))
    return found


def _matches(
    read: WordsRead,
) -> list[tuple[str, str, MatchTierName, list[MatchedWord], AskDates, float | None]]:
    """The two timecourses as matches of whatever was asked: named where a kept word is in the
    name, associated otherwise."""
    named = [word for word in read.kept if word in ("hela", "dmso", "timecourse", "timecourses")]
    tier: MatchTierName = "named" if named else "associated"
    matched = [MatchedWord(word=word, how="in the experiment's name") for word in named] or [
        MatchedWord(word=word, how="in a file's name") for word in read.kept[:1]
    ]
    return [
        (
            EXPERIMENT_TIMECOURSE,
            "2024-03-12 HeLa DMSO timecourse",
            tier,
            matched,
            AskDates(first="2024-03-12", last="2024-03-15"),
            0.8,
        ),
        (
            EXPERIMENT_TIMECOURSE_REPEAT,
            "2024-03-19 HeLa DMSO timecourse rep2",
            "associated",
            [MatchedWord(word="rep2", how="a repeat of a named experiment")],
            AskDates(first="2024-03-19", last="2024-03-20"),
            0.5,
        ),
    ]


def ask_example(run_id: str, q: str) -> AskResponse:
    """The question's words read, widened, and answered with the timecourses."""
    read = _read_words(q)
    experiments = [
        AskExperiment(
            experiment_id=experiment_id,
            label=label,
            score_tier=tier,
            matched=matched,
            dates=dates,
            completeness=completeness,
        )
        for experiment_id, label, tier, matched, dates, completeness in _matches(read)
    ]
    return AskResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        q=q,
        read=read,
        expanded=_expansions(read),
        filters=AskFilters(years=None, types=None, areas=None),
        experiments=experiments,
        same_theme=[AskSameTheme(experiment_id=EXPERIMENT_ZSTACK, via_theme=_THEME_ZSTACK)],
    )


# ---------------------------------------------------------------------------
# A8. The areas.
# ---------------------------------------------------------------------------


def areas_example(run_id: str) -> AreasResponse:
    """The four areas, most files first."""
    rows = sorted(_AREAS, key=lambda row: -row[5])
    return AreasResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        areas=[
            AreaItem(id=area_id, name=name, kind=kind, kind_assumed=assumed, files=files)
            for area_id, name, kind, assumed, _, files, _, _ in rows
        ],
    )


# ---------------------------------------------------------------------------
# A9. The goals.
# ---------------------------------------------------------------------------


def goals_example(run_id: str, *, phase: JobPhaseName | None) -> GoalsResponse:
    """One goal, answered with the timecourses."""
    text = "HeLa timecourses with analysis"
    read = _read_words(text)
    matches = [
        GoalMatch(
            experiment_id=EXPERIMENT_TIMECOURSE,
            label="2024-03-12 HeLa DMSO timecourse",
            tier="named",
            why="its name holds hela and timecourse, and its raw files are followed by analysis",
        ),
        GoalMatch(
            experiment_id=EXPERIMENT_TIMECOURSE_REPEAT,
            label="2024-03-19 HeLa DMSO timecourse rep2",
            tier="associated",
            why="a repeat of a named match",
        ),
        GoalMatch(
            experiment_id=EXPERIMENT_ZSTACK,
            label="zstack 2024-06-04 U2OS",
            tier="same_theme",
            why="in the same broad theme, imaging, as a named match",
        ),
    ]
    return GoalsResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        phase=phase,
        goals=[
            GoalItem(
                id="goal_1",
                text=text,
                read=read,
                expanded=_expansions(read),
                matches=matches,
                summary=GoalSummary(
                    experiments=7,
                    named=4,
                    associated=2,
                    same_theme=1,
                    first="2023-05-02",
                    last="2024-06-07",
                    raw_with_analysis=5,
                    of=7,
                    repeated_groups=2,
                    seen_once=3,
                ),
            )
        ],
    )
