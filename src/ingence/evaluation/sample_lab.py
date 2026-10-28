"""The sample collection: a shared drive over several years and several people, and its key.

``generate_sample_lab(destination, tier=..., seed=...)`` writes, from a tier and a seed and nothing
else, the same files byte for byte, the same names and the same modification times on every
machine, and beside them (never inside) the answer key, a manifest and a datasheet:

- ``destination/pool/``: the laboratory's drive, in the form of a folder to index;
- ``destination/answer_key.json``: the key in :mod:`ingence.evaluation.key`'s format, which
  ``python -m ingence.evaluation project`` scores a run against;
- ``destination/answer_key_unscored.json``: what a person who knew the laboratory would also say and
  the scorer does not yet score (see :data:`UNSCORED_SECTIONS`), among it each experiment's design
  (:mod:`ingence.evaluation.sample_lab_design` reads it);
- ``destination/manifest.json``: the generator and its version, the tier and seed, counts by kind,
  whether the links were made, and the total size;
- ``destination/DATASHEET.md``: a datasheet for the collection, as "Datasheets for Datasets" asks.

Three tiers: S (about five hundred files: the tutorial, the examples in the documentation and
tests), M (about ten thousand: a demonstration and screenshots), L (about a hundred thousand: a
benchmark and a test of speed and memory).

Everything is invented, in real words. Members are made-up people with ordinary names. Projects,
cell lines, genes, drugs and conditions are the common biology words and the words of the shipped
Dictionary listed in ``sample_lab_words.yaml``; there are no invented words. No file came from
anybody's disk, and no value in one is a measurement. Every instrument file is built from its
format's structure with invented values; the files shaped like another microscope's (Zeiss CZI,
Leica LIF, Olympus OIB) and like older Office files hold a header or an empty container only and
are not readable by the programs that save them.

A tripwire, never a target. The collection pins what a laboratory's drive can look like, and the
key says what a person who knew it would say from the files. It is never changed because the engine
and the key disagree.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import os
import random
import re
from collections import Counter
from dataclasses import dataclass, replace
from itertools import combinations
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ingence import __version__
from ingence.evaluation.key import (
    FAMILIES,
    PAIR_FAMILIES,
    AnswerKey,
    PairFamily,
    ValueFamily,
)
from ingence.evaluation.sample_lab_datasheet import datasheet
from ingence.evaluation.sample_lab_drive import Drive
from ingence.evaluation.sample_lab_people import (
    ALUMNI_FOLDER,
    ANALYSIS_FOLDERS,
    DATE_STYLES,
    DATE_WEIGHTS,
    MEMBER_FOLDER_STYLES,
    PROCESSED_FOLDERS,
    RAW_FOLDERS,
    REPLICATE_STYLES,
    REPLICATE_WEIGHTS,
    Habit,
    Member,
    Project,
    imaging_techniques,
    member_folder,
)
from ingence.evaluation.sample_lab_shapes import (
    KIND_WEIGHTS,
    KINDS,
    archive_links,
    backup_tree,
    borrowed_analysis,
    broken_link,
    copy_of,
    experiment,
    fiji_application,
    matlab_toolbox,
    moved_folder,
    odds_and_ends,
    python_package,
    sequencing_runs,
    shared_folder,
)
from ingence.evaluation.sample_lab_vocabulary import Applied, apply_vocabulary, originals_only
from ingence.evaluation.sample_lab_words import words

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "DATASHEET_FILE",
    "GENERATOR_NAME",
    "GENERATOR_VERSION",
    "KEY_FILE",
    "MANIFEST_FILE",
    "POOL_DIR",
    "TIERS",
    "UNSCORED_FILE",
    "UNSCORED_SECTIONS",
    "SampleLab",
    "Tier",
    "describe",
    "generate_sample_lab",
]

GENERATOR_NAME = "ingence.evaluation.sample_lab"
#: Bumped whenever the same tier and seed would write a different pool, key or manifest.
#: Version 2: real words, ordinary names, seven date spellings and five replicate labels, the design
#: of each experiment, more kinds of file, archive links, partial backups, copies in random places.
GENERATOR_VERSION = 2
POOL_DIR = "pool"
KEY_FILE = "answer_key.json"
UNSCORED_FILE = "answer_key_unscored.json"
MANIFEST_FILE = "manifest.json"
DATASHEET_FILE = "DATASHEET.md"
MANIFEST_FORMAT = "ingence-sample-lab-manifest"
UNSCORED_FORMAT = "ingence-answer-key-unscored"

#: The share of a tier's file target that dated experiments fill first. The rest is made beside
#: them (copies, backups, programs, shared folders) and by a last round of experiments.
MAIN_SHARE = 0.80


@dataclass(frozen=True)
class Tier:
    """How large one tier is."""

    name: str
    target_files: int
    members: int
    first_year: int
    projects_per_member: int
    #: Programs installed inside a data folder: Python packages, MATLAB toolboxes, copies of Fiji.
    packages: int
    toolboxes: int
    fijis: int
    #: Links to folders that were renamed away, and folders moved with a readme that names the old
    #: place.
    links: int
    moved: int
    borrowed: int
    shared: int
    #: Shares of the experiments copied in part into ``backup/``, of the files saved again, and of
    #: the entries replaced by a link to the archive server.
    backup_share: float
    copy_share: float
    archive_share: float
    #: Experiments with about fifty replicates, files of sequencing traces and files of kinds a
    #: laboratory seldom keeps.
    big: int
    sequencing: int
    odds: int
    #: Experiments with a temperature (``37°C``) or a length (``10µm``) in the folder's name.
    symbols: int
    #: Which members left (by position).
    departed: tuple[int, ...]
    purpose: str


TIERS: dict[str, Tier] = {
    "S": Tier(
        "S", 440, 5, 2019, 1, 1, 1, 1, 2, 1, 1, 8, 0.05, 0.05, 0.06, 0, 0, 0, 0, (1,),
        "the tutorial, the examples in the documentation, tests",
    ),
    "M": Tier(
        "M", 10_000, 7, 2016, 6, 3, 3, 2, 8, 5, 6, 160, 0.05, 0.05, 0.10, 0, 0, 0, 0, (1,),
        "a demonstration and screenshots",
    ),
    "L": Tier(
        "L", 100_000, 8, 2014, 24, 15, 15, 2, 30, 20, 40, 1600, 0.05, 0.05, 0.12, 4, 1000, 40, 12,
        (1, 5),
        "a benchmark and a test of speed and memory",
    ),
}  # fmt: skip
LAST_YEAR = 2025
#: Names the draw that deals the members' habits. One draw is as good as another. This one puts the
#: small tier's first collection, the one shown to newcomers, inside the parameters' ranges.
_DEAL = "habits-0"
_SYSTEMS = ("mac", "windows", "mac", "windows", "linux", "mac", "windows", "linux")

#: What the scorer does not yet score, one sentence for each section of the unscored file.
UNSCORED_SECTIONS: dict[str, str] = {
    "members": (
        "Each member of the laboratory: the folder named for them, the years they were "
        "active, and whether they left."
    ),
    "departed_member": (
        "A member who left: their folder is in Lab Alumni, stops early, holds an old-files "
        "subfolder, and a handover note."
    ),
    "projects": (
        "Each project a member keeps working on, written from the generator's own choices: its "
        "cell line and its protein or gene, the treatments that vary in it, the word its control "
        "is written with, its stain and its techniques, its experiment folders, and the "
        "treatment written into an experiment folder's own name (subgroups). What a question "
        "about a treatment, a target and a cell line is answered from."
    ),
    "designs": (
        "The design of each experiment, written from the generator's own choices: its cells, "
        "each a tuple of levels (for example control, or nocodazole at 10uM, or control at 24h) "
        "with the number of replicates done and the number of raw files made for it (those an "
        "archiving system later replaced by a link are counted, and listed in archive_links)."
    ),
    "complete_experiments": (
        "Experiment folders that hold acquisitions, processed files and analysis."
    ),
    "partial_experiments": (
        "Experiment folders that hold acquisitions and processed files and were never analyzed."
    ),
    "abandoned_experiments": (
        "Experiment folders that hold acquisitions only: never processed, never analyzed."
    ),
    "analysis_without_raw": (
        "Experiment folders that hold analysis whose acquisitions are gone; each lists the "
        "acquisition paths a script or note still names, and what names them."
    ),
    "analysis_in_another_members_folder": (
        "Analysis kept in one member's folder of data that lies in another member's folder."
    ),
    "data_in_another_members_folder": (
        "Experiments whose acquisitions are in another member's folder."
    ),
    "analysis_elsewhere": (
        "Experiments whose analysis is in the member's analysis folder, not in the experiment."
    ),
    "programs": (
        "Folders that are an installed program (a Python package, a MATLAB toolbox, a copy of "
        "Fiji), whose files hold no stage, replicate or experiment of the laboratory."
    ),
    "version_chains": (
        "Files saved again as _2, _new, _edit, _fixed, _final or with a date, earliest "
        "first (a space may stand for the underscore). The files differ in content."
    ),
    "copies_by_name": (
        "Files saved again byte for byte under a copy-of name: the first path is the original."
    ),
    "random_duplicates": (
        "Files saved again byte for byte under the same name in another folder of the same "
        "member: the first path is the original."
    ),
    "outputs_written_twice": (
        "A table written in the experiment's folder and again in the folder it was made in, "
        "under the same name."
    ),
    "near_copies": (
        "Pairs of files that differ in exactly one byte (a digit): the second was copied into "
        "a backup folder and then changed."
    ),
    "backup_folder_copies": (
        "Experiment folders copied in part into the member's backup folder: the original, the "
        "copy, the number of files in it and the number in the original."
    ),
    "moved_folders": (
        "A readme that names files by a path from when they lived in its folder; they now "
        "stand in an archive folder, and the readme says so only by the old path."
    ),
    "broken_links": (
        "Symbolic links whose targets do not exist. They are made only where the operating "
        "system allows it; the manifest says whether they were made."
    ),
    "archive_links": (
        "Files an archiving system replaced by a link to the archive server, oldest "
        "experiments first. The target does not exist where the pool is read. Each entry says "
        "what the file was."
    ),
    "unreadable_headers": (
        "Files shaped like another microscope's (Zeiss CZI, Leica LIF, Olympus OIB): a "
        "signature and a header with invented values, and nothing else. They cannot be opened "
        "as images."
    ),
    "noise": (
        "Files nobody made on purpose: folder files, thumbnail caches, editor lock files, and "
        "empty folders; by kind."
    ),
}

_NOISE_PREFIX = "noise_"
_NOISE_LABELS = {
    "noise_ds_store": ".DS_Store files",
    "noise_thumbs_db": "Thumbs.db files",
    "noise_office_lock": "~$ editor lock files",
}


@dataclass(frozen=True)
class SampleLab:
    """Where a collection was written, and what it holds."""

    pool: Path
    key_path: Path
    unscored_path: Path
    manifest_path: Path
    datasheet_path: Path
    key: AnswerKey
    manifest: dict[str, Any]
    files: tuple[str, ...]


# ---------------------------------------------------------------------------
# The people.
# ---------------------------------------------------------------------------


#: How a member's habits are shared out so that a small laboratory, not only a large one, shows each
#: in about the proportion the censuses showed: the options are dealt round the members and the
#: seed shuffles who gets which.
_TRAIT_OPTIONS: dict[str, tuple[Any, ...]] = {
    "file_words": (2, 2, 1, 2, 0, 2),
    # The shares 40/20/20/10/10, dealt so that five members show all five.
    "replicate": ("rep", "R", "bare", "take", "counter", "rep", "R", "bare", "rep", "rep"),
    "detail": ((), ("mask",), ("channel", "merge"), (), ("merge",), ("channel", "merge")),
    "naming": (
        ("instrument", False, False),
        ("descriptive", True, True),
        ("descriptive", False, True),
        ("descriptive", False, False),
        ("renamed", False, True),
        ("descriptive", False, False),
    ),
}


def _traits(rng: random.Random, count: int) -> list[dict[str, Any]]:
    dealt = {
        name: [options[index % len(options)] for index in range(count)]
        for name, options in _TRAIT_OPTIONS.items()
    }
    for values in dealt.values():
        rng.shuffle(values)
    chosen: list[dict[str, Any]] = []
    for index in range(count):
        raw_naming, raw_dated, names_follow = dealt["naming"][index]
        chosen.append(
            {
                "file_words": dealt["file_words"][index],
                "replicate": dealt["replicate"][index],
                "detail": dealt["detail"][index],
                "raw_naming": raw_naming,
                "raw_dated": raw_dated,
                "names_follow": names_follow,
            }
        )
    return chosen


def _habit(rng: random.Random, traits: dict[str, Any] | None = None) -> Habit:
    habit = _random_habit(rng)
    return replace(habit, **(traits or {}))


def _random_habit(rng: random.Random) -> Habit:
    raw_naming = rng.choices(["instrument", "descriptive", "renamed"], [20, 62, 18])[0]
    return Habit(
        date_style=rng.choices(DATE_STYLES, DATE_WEIGHTS)[0],
        date_first=rng.random() < 0.65,
        sep=rng.choice([" ", "_", "-"]),
        layout=rng.choices(["stages", "flat", "images_and_rest"], [62, 10, 28])[0],
        raw_folder=rng.choice(RAW_FOLDERS),
        processed_folder=rng.choice(PROCESSED_FOLDERS),
        analysis_folder=rng.choice(ANALYSIS_FOLDERS),
        process_word=rng.choice(["processed", "proc"]),
        results_word=rng.choice(["results", "analysis", "out"]),
        replicate=rng.choices(REPLICATE_STYLES, REPLICATE_WEIGHTS)[0],
        long_names=rng.random() < 0.8,
        raw_naming=raw_naming,
        raw_dated=rng.random() < 0.25,
        file_words=rng.choices([0, 1, 2], [20, 45, 35])[0],
        raw_word=rng.random() < 0.25,
        names_follow=rng.random() < 0.6,
        results_text=rng.random() < 0.25,
    )


def _kinds(rng: random.Random, index: int) -> tuple[str, ...]:
    """Each member's instruments. The first member always has the Nikon one: every size shows it."""
    chosen: list[str] = ["nd2"] if index == 0 else []
    while len(chosen) < rng.randint(1, 2):
        kind = rng.choices(KINDS, KIND_WEIGHTS)[0]
        if kind not in chosen:
            chosen.append(kind)
    return tuple(chosen)


def _members(rng: random.Random, spec: Tier, seed: int) -> list[Member]:
    vocabulary = words()
    first, last = spec.first_year, LAST_YEAR
    traits = _traits(random.Random(f"{_DEAL}:{spec.name}:{seed}"), spec.members)  # noqa: S311
    given = rng.sample(list(vocabulary.first_names), spec.members)
    family = rng.sample(list(vocabulary.family_names), spec.members)
    members: list[Member] = []
    # Every size has a Mac, a Windows and a Linux user; the seed says who is which.
    systems = list(_SYSTEMS[: spec.members])
    rng.shuffle(systems)
    for index in range(spec.members):
        start = first + (index * (last - first - 2)) // max(1, spec.members - 1)
        start = min(start + rng.randint(0, 1), last - 2)
        end = last
        departed = index in spec.departed
        if departed:
            start = first
            end = max(start + 2, first + (last - first) // 2 - 1 - (index // 5))
        style = rng.choice(MEMBER_FOLDER_STYLES)
        name = member_folder(style, given[index], family[index])
        eras = [(start, _habit(rng, traits[index]))]
        for _ in range(rng.randint(0, 2)):
            eras.append((rng.randint(start + 1, max(start + 1, end)), _habit(rng, traits[index])))
        member = Member(
            given=given[index],
            family=family[index],
            folder_name=name,
            folder=f"{ALUMNI_FOLDER}/{name}" if departed else name,
            first=start,
            last=end,
            departed=departed,
            system=systems[index],
            kinds=_kinds(rng, index),
            top=rng.choices(
                ["years", "projects", "project_year", "year_project", "flat"], [5, 5, 50, 38, 2]
            )[0],
            instrument_folder=rng.choice(
                [*vocabulary.dictionary["techniques"][:3], None, None, None]
            ),
            year_style=rng.choice(["{year}", "{year} data", "Data {year}"]),
            eras=tuple(sorted(eras, key=lambda era: era[0])),
            scripting=rng.choices(
                ["python", "matlab", "both", "fiji", "none"], [30, 20, 8, 22, 20]
            )[0],
            tidy=rng.uniform(0.2, 0.8),
        )
        if departed:
            member.old_folder = rng.choice(["_old", "archive"])
            cut = start + (end - start) * 0.55
            member.old_before = dt.datetime(int(cut), 1 + int((cut % 1) * 11), 1, tzinfo=dt.UTC)
        members.append(member)
    return members


def _projects(rng: random.Random, spec: Tier, members: list[Member]) -> list[Project]:
    """One project for each cell line and protein or gene drawn: no two share both."""
    vocabulary = words()
    cell_lines = list(vocabulary.biology["cell_lines"])
    targets = [*vocabulary.biology["proteins_and_genes"], *vocabulary.dictionary["tags"]]
    pairs = [(line, target) for line in cell_lines for target in targets]
    rng.shuffle(pairs)
    drugs = [*vocabulary.biology["drugs"][1:], *vocabulary.dictionary["treatments"][:2]]
    doses = list(vocabulary.dictionary["doses"])
    times = list(vocabulary.dictionary["times"])
    controls = ["ctrl", "control", "vehicle", "untreated", "mock", "wildtype", "DMSO"]
    imaging = imaging_techniques()
    stains = list(vocabulary.dictionary["stains"])
    taken = iter(pairs)
    everything: list[Project] = []
    for member in members:
        for _ in range(spec.projects_per_member):
            core = next(taken)
            extras = tuple(rng.sample(drugs, 3))
            span_start = dt.datetime(member.first, 1, 1, 10, tzinfo=dt.UTC)
            span_days = (dt.datetime(member.last, 12, 31, tzinfo=dt.UTC) - span_start).days
            start = span_start + dt.timedelta(days=rng.randint(0, max(1, int(span_days * 0.75))))
            kind = member.kinds[0] if not member.projects else rng.choice(member.kinds)
            project = Project(
                owner=member,
                core=core,
                extras=extras,
                doses={drug: tuple(rng.sample(doses, 3)) for drug in extras},
                times=tuple(times),
                control=rng.choice(controls),
                vehicle="DMSO" if rng.random() < 0.4 else None,
                techniques=tuple(rng.sample(imaging, rng.randint(1, 2))),
                stain=rng.choice(stains) if rng.random() < 0.7 else None,
                kind=kind,
                sidecar=(
                    rng.choice(["xml", None, None, None])
                    if kind == "nd2"
                    else rng.choice(["json", None, None, None])
                    if kind == "tiffstack"
                    else None
                ),
                plate_form=rng.choices(["csv", "txt", "xlsx"], [60, 25, 15])[0],
                start=start,
            )
            member.projects.append(project)
            everything.append(project)
    return everything


def _next_day(rng: random.Random, project: Project) -> dt.datetime:
    member = project.owner
    # Bursts of work, and now and then a long break.
    project.cursor += rng.randint(30, 200) if rng.random() < 0.15 else rng.randint(1, 12)
    day = project.start + dt.timedelta(days=project.cursor)
    end = dt.datetime(member.last, 12, 31, 10, tzinfo=dt.UTC)
    if day > end:
        project.cursor = rng.randint(0, 40)
        day = max(project.start, dt.datetime(member.first, 1, 1, 10, tzinfo=dt.UTC)) + dt.timedelta(
            days=project.cursor
        )
    return day.replace(hour=10, minute=0, second=0)


# ---------------------------------------------------------------------------
# The drive.
# ---------------------------------------------------------------------------


def _day_in_years(
    rng: random.Random, member: Member, *, not_before: dt.datetime | None = None
) -> dt.datetime:
    """A day between the year a member came and the year they left (or 2025), at ten."""
    start = dt.datetime(member.first, 1, 1, 10, tzinfo=dt.UTC)
    if not_before is not None:
        start = max(start, not_before)
    end = dt.datetime(min(member.last, LAST_YEAR), 12, 31, 10, tzinfo=dt.UTC)
    return start + dt.timedelta(days=rng.randint(0, max(0, (end - start).days)))


def _experiment_of(path: str, experiments: set[str]) -> str | None:
    parent = path.rpartition("/")[0]
    while parent:
        if parent in experiments:
            return parent
        parent = parent.rpartition("/")[0]
    return None


def _owner(path: str, by_folder: Mapping[str, Member]) -> Member | None:
    """The member whose folder holds ``path`` (a member who left is in ``Lab Alumni``)."""
    first, _, rest = path.partition("/")
    if first == ALUMNI_FOLDER:
        first = f"{first}/{rest.partition('/')[0]}"
    return by_folder.get(first)


#: The kinds of file that a person may save again somewhere else.
_COPIED_KINDS = {
    "csv",
    "txt_table",
    "mat_file",
    "xlsx",
    "python_script",
    "tiff_image",
    "tiff_stack",
    "nd2",
    "png",
}


def _build(tier: str, seed: int) -> Drive:
    spec = TIERS[tier]
    rng = random.Random(f"ingence-sample-lab:{GENERATOR_VERSION}:{tier}:{seed}")  # noqa: S311
    d = Drive(random=rng, busy_share=0.0 if tier == "S" else 0.12)
    vocabulary = words()
    members = _members(rng, spec, seed)
    projects = _projects(rng, spec, members)
    program_words = list(vocabulary.dictionary["program_words"])
    names = [f"{a}_{b}" for a in program_words for b in program_words if a != b]
    rng.shuffle(names)
    topics = [
        *vocabulary.dictionary["techniques"],
        *vocabulary.dictionary["stains"],
        *vocabulary.biology["cell_lines"],
    ]
    by_folder = {member.folder: member for member in members}
    for member in members:
        if member.departed:
            d.caps[member.folder] = dt.datetime(member.last, 12, 31, 18, tzinfo=dt.UTC)

    def others_of(project: Project, day: dt.datetime) -> list[Member]:
        return [
            m
            for m in members
            if m is not project.owner and not m.departed and m.first <= day.year <= m.last
        ]

    symbols = list(vocabulary.dictionary["symbols"])
    planted = [0, 0]  # experiments made, names given a symbol
    every = 300 if spec.target_files >= 50_000 else 7

    def symbol_for() -> str | None:
        """A temperature or a length for a few experiments of the largest tier."""
        planted[0] += 1
        if planted[1] < spec.symbols and planted[0] % every == every // 2:
            planted[1] += 1
            return symbols[planted[1] % len(symbols)]
        return None

    # -- the experiments with about fifty replicates (the largest tier only) ---------------------
    for number in range(spec.big):
        project = projects[(number * 11) % len(projects)]
        day = project.start + dt.timedelta(days=rng.randint(60, 400))
        if day.year > project.owner.last:
            day = project.start
        experiment(d, project, day.replace(hour=10, minute=0, second=0), big=True)
    # -- the dated experiments, project by project in turn ---------------------------------------
    budget = int(spec.target_files * MAIN_SHARE)
    while len(d.files) < budget:
        for project in projects:
            if len(d.files) >= budget:
                break
            day = _next_day(rng, project)
            experiment(d, project, day, others=others_of(project, day), symbol=symbol_for())
    for member in members:
        if member.old_before is None or not member.projects:
            continue
        project = member.projects[0]
        for early in (True, False):
            if any(
                folder.startswith(f"{member.folder}/{member.old_folder}/") == early
                for folder in project.experiments
            ):
                continue
            span = (member.old_before - dt.datetime(member.first, 1, 1, 10, tzinfo=dt.UTC)).days
            day = (
                dt.datetime(member.first, 1, 1, 10, tzinfo=dt.UTC)
                + dt.timedelta(days=rng.randint(10, max(11, span - 10)))
                if early
                else member.old_before + dt.timedelta(days=rng.randint(10, 120))
            )
            experiment(d, project, day)
    experiments = set(d.values.get("experiment_folder", {}))
    # -- the projects, written from the generator's own choices ----------------------------------
    for project in projects:
        d.note(
            "projects",
            {
                "cell_line": project.core[0],
                "target": project.core[1],
                "treatments": list(project.extras),
                "control": project.control,
                "vehicle": project.vehicle,
                "stain": project.stain,
                "techniques": list(project.techniques),
                "owner": project.owner.folder,
                "experiments": sorted(project.experiments),
                # The treatment written into an experiment folder's own name, by treatment:
                # the folder carries the word whether or not its design has a cell for it.
                "subgroups": {
                    drug: sorted(folders) for drug, folders in sorted(project.subgroups.items())
                },
            },
        )
    # -- what is done with the experiments afterwards --------------------------------------------
    files_of: dict[str, list[str]] = {}
    for path in sorted(d.files):
        owner = _experiment_of(path, experiments)
        if owner is not None:
            files_of.setdefault(owner, []).append(path)
    by_age = sorted(experiments)
    holders = [name for name in by_age if files_of.get(name) and _owner(name, by_folder)]
    for _ in range(spec.borrowed):
        for _attempt in range(30):
            source = rng.choice(holders)
            author = _owner(source, by_folder)
            sources = [
                path
                for path in files_of[source]
                if d.values.get("stage", {}).get(path) in {"processed", "raw"}
                and not d.kinds[path].endswith("_sidecar")
            ]
            if not sources:
                continue
            day = d.times[sources[0]] + dt.timedelta(days=rng.randint(20, 200))
            others = [
                p
                for p in projects
                if p.owner is not author and p.owner.first <= day.year <= p.owner.last
            ]
            if others:
                borrowed_analysis(d, rng.choice(others), source, sources, day)
                break
    with_tables = [
        name
        for name in holders
        if any(path.endswith((".csv", ".txt")) and d.files[path] for path in files_of[name])
    ]
    for number in range(max(1, round(len(experiments) * spec.backup_share))):
        # The first backup always holds a near-copy, so every size shows one.
        source = rng.choice(with_tables if number == 0 and with_tables else holders)
        keeper = _owner(source, by_folder)
        assert keeper is not None
        backup_tree(
            d,
            keeper,
            source,
            # Copied after the newest file in the folder, not the one that sorts last.
            max(d.times[path] for path in files_of[source]) + dt.timedelta(days=rng.randint(1, 60)),
            near_copy=number == 0,
        )
    candidates = [
        path
        for path in sorted(d.files)
        if d.kinds[path] in _COPIED_KINDS
        and d.files[path]
        and _owner(path, by_folder) is not None
        and "/backup/" not in path
        and path not in d.declined.get("stage", set())
        and path not in d.never_copied
    ]
    places: dict[str, list[str]] = {}
    for member in members:
        places[member.folder] = sorted(
            [
                folder
                for folder in d.folders
                if folder.startswith(f"{member.folder}/")
                and "/backup" not in folder
                and folder in experiments
            ]
            + [f"{member.folder}/misc", f"{member.folder}/to sort", f"{member.folder}/Desktop"]
        )
    for _ in range(round(len(d.files) * spec.copy_share)):
        if not candidates:
            break
        path = rng.choice(candidates)
        keeper = _owner(path, by_folder)
        assert keeper is not None
        copy_of(
            d,
            keeper,
            path,
            d.times[path] + dt.timedelta(days=rng.randint(1, 30)),
            folders=places[keeper.folder],
        )
    # -- programs, tools, moves, links -----------------------------------------------------------
    for number in range(spec.packages):
        name = names[number]
        host = rng.choice(holders)
        python_package(
            d,
            f"{host}/{name}",
            name,
            d.times[files_of[host][0]] + dt.timedelta(days=rng.randint(1, 90)),
        )
    for number in range(spec.toolboxes):
        name = names[number + 40]
        member = rng.choice(members)
        matlab_toolbox(
            d, f"{member.folder}/matlab/{name}-toolbox", name, _day_in_years(rng, member)
        )
    for member in rng.sample(members, min(spec.fijis, len(members))):
        fiji_application(d, f"{member.folder}/Fiji.app", _day_in_years(rng, member))
    for number in range(spec.moved):
        project = projects[(number * 7) % len(projects)]
        moved_folder(
            d, project.owner, project, project.start + dt.timedelta(days=rng.randint(10, 100))
        )
    # As many links as the tier asks for: a name already taken in a member's folder is not a link.
    planned = 0
    for _attempt in range(spec.links * 50):
        if planned >= spec.links:
            break
        project = rng.choice(projects)
        stale = rng.choice(project.experiments) if project.experiments else project.label
        if broken_link(
            d,
            project.owner,
            project.label,
            f"{stale.rsplit('/', 1)[-1]} (old)",
            _day_in_years(rng, project.owner, not_before=project.start),
        ):
            planned += 1
    shared_folder(d, members, topics, spec.first_year, LAST_YEAR, spec.shared)
    if spec.sequencing:
        constructs = [
            f"{target} {tag}"
            for target in vocabulary.biology["proteins_and_genes"]
            for tag in vocabulary.dictionary["tags"]
        ]
        living = [m for m in members if not m.departed]
        for member in living[:2]:
            sequencing_runs(d, member, constructs, spec.sequencing // 2)
    if spec.odds:
        odds_and_ends(d, [m for m in members if not m.departed], topics, spec.odds)
    for member in members:
        if member.departed and member.old_folder:
            ending = dt.datetime(member.last, 12, 15, 17, tzinfo=dt.UTC)
            note = f"{member.folder}/README.txt"
            d.file(
                note,
                (
                    f"These are the files of {member.name}, who left the lab in "
                    f"December {member.last}.\nOlder experiments are in {member.old_folder}/.\n"
                    f"({d.token().hex()})\n"
                ).encode(),
                ending,
                kind="text_note",
            )
            d.note(
                "departed_member",
                {
                    "folder": member.folder,
                    "last_active": member.last,
                    "old_files_folder": f"{member.folder}/{member.old_folder}",
                    "handover_note": note,
                },
            )
    # -- more experiments until the tier's size is reached ---------------------------------------
    while len(d.files) < spec.target_files:
        for project in projects:
            if len(d.files) >= spec.target_files:
                break
            day = _next_day(rng, project)
            experiment(d, project, day, others=others_of(project, day), symbol=symbol_for())
    # -- the groups: each project's experiments, and what a third word adds ----------------------
    for project in projects:
        if len(project.experiments) >= 2:
            d.group("group", tuple(project.experiments), name=" ".join(project.core))
        for extra, folders in project.subgroups.items():
            if len(folders) >= 2:
                d.named_groups[" ".join([*project.core, extra])] = tuple(sorted(folders))
                d.group("group", tuple(folders))
    for member in members:
        d.note(
            "members",
            {
                "name": member.name,
                "folder": member.folder,
                "first_year": member.first,
                "last_year": member.last,
                "departed": member.departed,
            },
        )
    for path, kind in d.kinds.items():
        if kind in {"czi_shaped_header", "lif_shaped_header", "oib_shaped_container"}:
            d.note("unreadable_headers", path)
    # -- last, the archive: older raw files become links to another server -----------------------
    archive_links(d, sorted(experiments), round(len(d.files) * spec.archive_share))
    return d


# ---------------------------------------------------------------------------
# The key.
# ---------------------------------------------------------------------------


def _key(d: Drive, *, tier: str, seed: int, vocabulary: Applied | None = None) -> AnswerKey:
    files = sorted(d.files)
    folders = sorted(d.folders | d.empty_folders)
    families: dict[str, ValueFamily | PairFamily] = {}
    for name in ("stage", "empty_file", "contradicted_extension", "experiment_folder", "program"):
        domain = files if name in {"stage", "empty_file", "contradicted_extension"} else folders
        ignored = d.declined.get(name, set())
        undecidable = d.undecidable_values.get(name, set()) - ignored
        stated = d.values.get(name, {})
        expected = {
            path: stated.get(path)
            for path in domain
            if path not in ignored and path not in undecidable
        }
        families[name] = ValueFamily(
            expected=expected,
            undecidable=tuple(sorted(undecidable)),
            ignored=tuple(sorted(ignored)),
        )
    families["name_date"] = ValueFamily(
        expected=dict(d.values.get("name_date", {})),
        undecidable=tuple(sorted(d.undecidable_values.get("name_date", set()))),
        complete=False,
    )
    # The chain each experiment's own files reached: one value per experiment folder the
    # generator laid out, and nothing said of a folder it did not.
    experiments_laid = sorted(d.values.get("workflow", {}))
    families["workflow"] = ValueFamily(
        expected={path: d.values["workflow"][path] for path in experiments_laid},
        undecidable=(),
        complete=False,
    )
    # Which experiments repeat each other: the same project, the same name words and the
    # same instrument format on two or more days; every pair of such a set, where two of its
    # members made on one day are each a repeat of the third.
    by_signature: dict[tuple[str, str, str], list[tuple[str, str]]] = {}
    for path, (signature, day) in d.signatures.items():
        by_signature.setdefault(signature, []).append((day, path))
    for members in by_signature.values():
        if len(members) >= 2 and len({day for day, _ in members}) >= 2:
            for first, second in combinations(sorted(path for _, path in members), 2):
                d.pair("repeats", first, second)
    by_bytes: dict[bytes, list[str]] = {}
    for path in files:
        if d.files[path]:
            by_bytes.setdefault(d.files[path], []).append(path)
    for name, directed in PAIR_FAMILIES.items():
        ignored = d.declined.get(name, set())
        pairs = set(d.pairs.get(name, set()))
        for together in d.groups.get(name, []):
            pairs.update(combinations(sorted(together), 2))
        if name == "copy":
            for identical in by_bytes.values():
                pairs.update(combinations(sorted(identical), 2))
        unsure = set(d.undecidable_pairs.get(name, set()))
        families[name] = PairFamily(
            directed=directed,
            expected=tuple(sorted(pair for pair in pairs if not ignored.intersection(pair))),
            undecidable=tuple(sorted(pair for pair in unsure if not ignored.intersection(pair))),
            ignored=tuple(sorted(ignored)),
            # The repeats family names experiment folders, which the scope lists so a pair the
            # engine reads between two of them is wrong, never set aside as outside the key.
            complete=name not in {"group", "repeats"},
            scope=tuple(experiments_laid) if name == "repeats" else (),
        )
    group = families["group"]
    assert isinstance(group, PairFamily)
    families["group"] = PairFamily(
        directed=False,
        expected=group.expected,
        undecidable=group.undecidable,
        ignored=group.ignored,
        complete=False,
        named={name: d.named_groups[name] for name in sorted(d.named_groups)},
    )
    key = AnswerKey(
        families={name: families[name] for name in FAMILIES},
        describes={
            "written_by": GENERATOR_NAME,
            "generator_version": GENERATOR_VERSION,
            "tier": tier,
            "seed": seed,
            "unscored_sections": UNSCORED_FILE,
            "group_family": (
                "a project's experiments, and what a third word adds; "
                "not a group by technique or by condition word"
            ),
            "ignored": (
                "the key declines to answer about documents, version chains and files saved again "
                "under a copy-of name or in another folder, about the files an archiving system "
                "replaced by links, and, but for the copy family, about folders copied into "
                "backup, in the families named"
            ),
            "system_files": (
                "files an operating system or an editor wrote (folder files, thumbnail caches, "
                "lock files) are byte-for-byte copies where they sit in a folder a person copied "
                "into backup/, and the key names them as copies there; anywhere else no two are "
                "alike, so the key names none as a copy"
            ),
            "undecidable": (
                "the undecidable and declined calls are one person's judgement of what the files "
                "alone can settle, not a measurement"
            ),
            **({"vocabulary": vocabulary.summary} if vocabulary is not None else {}),
        },
        words=vocabulary.family if vocabulary is not None else None,
    )
    # Through its own JSON, so the key returned is the key a reader of the file gets.
    return AnswerKey.from_json(key.to_json())


_PAIR_LINE = re.compile(r'\[\n\s+("(?:[^"\\\n]|\\.)*"),\n\s+("(?:[^"\\\n]|\\.)*")\n\s+\]')


def _key_text(key: AnswerKey) -> str:
    """The key as JSON text with each pair on one line: the same bytes for the same key."""
    return _PAIR_LINE.sub(r"[\1, \2]", key.dumps())


def _unscored(d: Drive, *, tier: str, seed: int) -> dict[str, Any]:
    sections: dict[str, Any] = {}
    noise: dict[str, list[str]] = {}
    for path, kind in sorted(d.kinds.items()):
        if kind.startswith(_NOISE_PREFIX):
            noise.setdefault(_NOISE_LABELS.get(kind, kind), []).append(path)
    leftovers = sorted(d.empty_folders)
    if leftovers:
        noise["empty folders"] = leftovers
    d.unscored["noise"] = [noise] if noise else []
    for name, sentence in UNSCORED_SECTIONS.items():
        entries = d.unscored.get(name, [])
        ordered = sorted(entries, key=lambda entry: json.dumps(entry, sort_keys=True))
        sections[name] = {"what": sentence, "entries": ordered}
    return {
        "format": UNSCORED_FORMAT,
        "format_version": 1,
        "describes": {
            "written_by": GENERATOR_NAME,
            "generator_version": GENERATOR_VERSION,
            "tier": tier,
            "seed": seed,
            "about": (
                "what a person who knew the laboratory would also say, "
                "and the scorer does not yet score"
            ),
        },
        "sections": sections,
    }


# ---------------------------------------------------------------------------
# The manifest, the pool on disk.
# ---------------------------------------------------------------------------


def _pool_digest(d: Drive) -> str:
    """One digest of every file's path, content and modification time (to the second)."""
    digest = hashlib.sha256()
    for path in sorted(d.files):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(d.files[path]).digest())
        digest.update(str(int(d.times[path].timestamp())).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _write_pool(pool: Path, d: Drive) -> tuple[bool, str]:
    """Write the files, then the folders and links; return whether links were made, and why not."""
    made: set[str] = set()

    def ensure(folder: str) -> None:
        if folder not in made:
            (pool / folder).mkdir(parents=True, exist_ok=True)
            made.add(folder)

    newest: dict[str, float] = {}
    pool.mkdir(parents=True)
    for path in sorted(d.files):
        folder = path.rpartition("/")[0]
        if folder:
            ensure(folder)
        target = pool / path
        target.write_bytes(d.files[path])
        stamp = d.times[path].timestamp()
        os.utime(target, (stamp, stamp))
        parent = folder
        while True:
            if newest.get(parent, 0.0) < stamp:
                newest[parent] = stamp
            if not parent:
                break
            parent = parent.rpartition("/")[0]
    for folder in sorted(d.empty_folders):
        ensure(folder)
        stamp = d.times[folder + "/"].timestamp()
        parent = folder
        while True:
            if newest.get(parent, 0.0) < stamp:
                newest[parent] = stamp
            if not parent:
                break
            parent = parent.rpartition("/")[0]
    made_links = True
    reason = ""
    for path in sorted(d.links):
        try:
            ensure(path.rpartition("/")[0])
            os.symlink(d.links[path], pool / path)
        except (OSError, NotImplementedError, AttributeError) as refused:
            made_links = False
            reason = (
                f"links not made: {type(refused).__name__}: "
                "the operating system refused to create a symbolic link"
            )
            break
        stamp = d.link_times[path].timestamp()
        with contextlib.suppress(OSError, NotImplementedError):
            os.utime(pool / path, (stamp, stamp), follow_symlinks=False)
        parent = path.rpartition("/")[0]
        while True:
            if newest.get(parent, 0.0) < stamp:
                newest[parent] = stamp
            if not parent:
                break
            parent = parent.rpartition("/")[0]
    for folder in sorted(newest, key=lambda name: (-name.count("/"), name)):
        os.utime(pool / folder if folder else pool, (newest[folder], newest[folder]))
    return made_links and bool(d.links), reason


def _manifest(
    d: Drive,
    *,
    tier: str,
    seed: int,
    links_made: bool,
    reason: str,
    mess: Mapping[str, int],
    vocabulary: Applied | None = None,
) -> dict[str, Any]:
    spec = TIERS[tier]
    kinds = Counter(d.kinds[path] for path in d.files)
    times = sorted(d.times[path] for path in d.files)
    members = [entry for entry in d.unscored.get("members", []) if isinstance(entry, dict)]
    experiments = len(d.values.get("experiment_folder", {}))
    if not d.links:
        links_note = "no links planned"
    elif links_made:
        links_note = "links made"
    else:
        links_note = reason or "links not made"
    return {
        "format": MANIFEST_FORMAT,
        "format_version": 1,
        "generator": {
            "name": GENERATOR_NAME,
            "version": GENERATOR_VERSION,
            "version_means": "raised only when the same tier and seed would write different files",
        },
        "package": {
            "name": "ingence",
            "version": __version__,
            "note": (
                "the package that ran the generator; it changes with every release and changes "
                "nothing in the pool or the key"
            ),
        },
        "tier": tier,
        "tier_purpose": spec.purpose,
        "seed": seed,
        "license": "CC0-1.0",
        "counts": {
            "files": len(d.files),
            "folders": len(d.folders | d.empty_folders),
            "empty_folders": len(d.empty_folders),
            "members": len(members),
            "experiment_folders": experiments,
            "by_kind": dict(sorted(kinds.items())),
        },
        "bytes": sum(len(data) for data in d.files.values()),
        "modified": {"first": times[0].isoformat(), "last": times[-1].isoformat()},
        "links": {"planned": len(d.links), "made": links_made, "note": links_note},
        "mess": dict(mess),
        **({"vocabulary": vocabulary.summary} if vocabulary is not None else {}),
        "pool_digest": {
            "algorithm": (
                "sha256 over each file's path, content digest and modification second, by path"
            ),
            "value": _pool_digest(d),
        },
        "written_beside_the_pool": [KEY_FILE, UNSCORED_FILE, MANIFEST_FILE, DATASHEET_FILE],
        "notes": [
            "The files shaped like a Zeiss CZI hold a header with invented values and nothing "
            "else; they are not readable CZI files.",
            "Every name, value and byte is invented; no file came from a real laboratory.",
            "The files shaped like Leica LIF, Olympus OIB and older Office files hold a header or "
            "an empty container only; the program that saves them cannot open them.",
        ],
    }


def generate_sample_lab(
    destination: Path,
    *,
    tier: str,
    seed: int,
    vocabulary: float = 0.0,
    vocabulary_seed: int | None = None,
    vocabulary_originals: bool = False,
) -> SampleLab:
    """Write a sample collection of ``tier`` (S, M or L) and its key, manifest and datasheet.

    The pool is ``destination/pool`` and everything else sits beside it, never inside. Nothing
    already there is written over: a destination that holds a pool, any of the files beside it,
    or anything else at all is refused. It names no project, so it has no root to check a key
    against; the key sits beside the pool, so a project that indexes ``pool`` never reads it.

    The vocabulary tier (:mod:`ingence.evaluation.sample_lab_vocabulary`) is off unless
    ``vocabulary`` is a share above 0. Each subject-matter word in an experiment folder is then
    written the way laboratories write it (a slip, initials, words run together, a code, a second
    spelling of one condition) with a chance of that share, drawn from ``vocabulary_seed`` (the
    collection's seed unless given); an experiment whose files are named from outside it (a
    reference or a copy of a text file crosses its boundary) is left alone. The key gains a words
    family. With it off, the files, the key, the manifest and the datasheet are the default
    sample's.

    ``vocabulary_originals`` is for the tier-off arm of a measurement: with the tier off, it adds
    to the key a words family of the original words alone (what each subject-matter word in the
    names means) and changes nothing else; the pool, the manifest and the datasheet stay the
    default sample's. It is ignored when the tier is on.
    """
    if tier not in TIERS:
        message = f"no tier {tier!r}; the tiers are {', '.join(TIERS)}"
        raise ValueError(message)
    pool = destination / POOL_DIR
    beside = [
        destination / name for name in (KEY_FILE, UNSCORED_FILE, MANIFEST_FILE, DATASHEET_FILE)
    ]
    if pool.exists() or any(path.exists() for path in beside):
        message = f"refusing to write a collection over {destination}: one is already there"
        raise FileExistsError(message)
    if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
        message = f"refusing to write a collection to {destination}: it is not a folder"
        raise FileExistsError(message)
    # The Finder writes `.DS_Store` into a folder when somebody opens it: that is not a file the
    # command's user put there.
    if destination.is_dir() and any(path.name != ".DS_Store" for path in destination.iterdir()):
        message = (
            f"refusing to write a collection into {destination}: it already holds files this "
            "command did not write. Give a folder that is empty or does not exist yet."
        )
        raise FileExistsError(message)
    if not 0.0 <= vocabulary <= 1.0:
        message = f"the vocabulary share is between 0 and 1, not {vocabulary}"
        raise ValueError(message)
    d = _build(tier, seed)
    applied = (
        apply_vocabulary(
            d,
            fraction=vocabulary,
            seed=seed if vocabulary_seed is None else vocabulary_seed,
            tier=tier,
        )
        if vocabulary
        else None
    )
    # With the tier off, only the key may carry the originals' words family; the manifest and the
    # datasheet say nothing of a tier that was not applied.
    key_words = applied if applied is not None or not vocabulary_originals else originals_only(d)
    key = _key(d, tier=tier, seed=seed, vocabulary=key_words)
    unscored = _unscored(d, tier=tier, seed=seed)
    destination.mkdir(parents=True, exist_ok=True)
    links_made, reason = _write_pool(pool, d)
    mess = {
        name: (
            sum(len(paths) for paths in section["entries"][0].values())
            if name == "noise" and section["entries"]
            else len(section["entries"])
        )
        for name, section in unscored["sections"].items()
    }
    manifest = _manifest(
        d,
        tier=tier,
        seed=seed,
        links_made=links_made,
        reason=reason,
        mess=mess,
        vocabulary=applied,
    )
    # Bytes, not text: no platform turns a line ending into its own.
    key_path, unscored_path, manifest_path, datasheet_path = beside
    key_path.write_bytes(_key_text(key).encode("utf-8"))
    unscored_path.write_bytes(
        (json.dumps(unscored, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
    )
    manifest_path.write_bytes(
        (json.dumps(manifest, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
    )
    datasheet_path.write_bytes(datasheet(manifest, UNSCORED_SECTIONS).encode("utf-8"))
    return SampleLab(
        pool=pool,
        key_path=key_path,
        unscored_path=unscored_path,
        manifest_path=manifest_path,
        datasheet_path=datasheet_path,
        key=key,
        manifest=manifest,
        files=tuple(sorted(d.files)),
    )


def describe(manifest: Mapping[str, Any]) -> str:
    """One line for a command to print."""
    counts = manifest["counts"]
    return (
        f"tier {manifest['tier']}, seed {manifest['seed']}: {counts['files']} files in "
        f"{counts['folders']} folders, {manifest['bytes']} bytes"
    )
