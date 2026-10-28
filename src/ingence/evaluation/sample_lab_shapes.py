"""What happens on a laboratory's drive over the years, one shape at a time.

Each function here adds files to a :class:`~ingence.evaluation.sample_lab_drive.Drive` the way a
laboratory's members would have, and records beside each file what a person who knew the laboratory
would say about it. The shapes:

1. :func:`experiment`: a dated folder of acquisitions, processed files and analysis, in the
   member's own naming habit, ending complete, abandoned (acquisitions only, never
   analyzed), partial (processed, never analyzed) or analysis only (the acquisitions are
   gone: a script still names them). Its conditions come from a design (a control and a treatment
   at some doses, or a control and a treatment over some times) with one to three replicates of
   each, and the design is written into the key;
2. :func:`borrowed_analysis`: analysis in another member's folder of data that lies in someone
   else's;
3. :func:`versions` and :func:`copy_of`: ``_final``, ``_new``, ``_edit``, ``_fixed``, ``_2`` and a
   date; a file saved again under a copy-of name or, more often, in another folder under its own
   name;
4. :func:`backup_tree`: part of a folder copied into ``backup/``, with a near-copy that differs in
   one byte;
5. :func:`moved_folder`, :func:`broken_link` and :func:`archive_links`: a folder moved with a readme
   still naming its old place, a link to somewhere that is gone, and files an archiving system
   replaced by links to another server;
6. :func:`python_package`, :func:`matlab_toolbox` and :func:`fiji_application`: a program installed
   inside a data folder;
7. noise: the files an operating system or an editor leaves, empty folders, an empty output among
   filled siblings.

What the key says is what the files say. Where a file's stage, or two files' relation, is in the
files (a format that is an instrument's, a word in a folder's name, the same stem in two names)
the key answers; where it is not, it marks the file or the pair undecidable (the right output is
abstention); where a person could reasonably say either, it leaves the file out of the score.
A shape is never changed because the engine and the key disagree.
"""

from __future__ import annotations

import datetime as dt
import posixpath
from dataclasses import dataclass, replace
from itertools import combinations, pairwise
from typing import TYPE_CHECKING

from ingence.evaluation.documents import (
    ab1_bytes,
    avi_bytes,
    czi_header_bytes,
    dna_bytes,
    docx_bytes,
    ds_store_bytes,
    fcs_bytes,
    gif_bytes,
    jar_bytes,
    jpeg_bytes,
    lif_header_bytes,
    mat_file_bytes,
    notebook_bytes,
    office_lock_bytes,
    ole_document_bytes,
    pdf_bytes,
    png_bytes,
    pptx_bytes,
    svg_bytes,
    thumbs_db_bytes,
    tiff_stack_bytes,
    xlsx_bytes,
    zip_bytes,
)
from ingence.evaluation.formats import nd2_bytes, tiff_bytes
from ingence.evaluation.sample_lab_people import (
    DATE_STYLES,
    MONTH_NAMES,
    Condition,
    Habit,
    Member,
    Project,
    date_text,
    design_words,
    replicate_parts,
)
from ingence.evaluation.sample_lab_words import words

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from ingence.evaluation.sample_lab_drive import Drive

__all__ = [
    "ARCHIVE_SERVER",
    "KINDS",
    "archive_links",
    "backup_tree",
    "borrowed_analysis",
    "broken_link",
    "copy_of",
    "experiment",
    "fiji_application",
    "matlab_toolbox",
    "moved_folder",
    "odds_and_ends",
    "python_package",
    "sequencing_runs",
    "shared_folder",
    "versions",
]

#: The instruments a member's experiments may come from, and how often a member's projects use each.
KINDS = ("nd2", "tiffstack", "camera", "plate", "blot")
KIND_WEIGHTS = (72, 12, 6, 6, 4)
#: Microscopes whose files are read by a few programs only. A visit to one now and then.
OTHER_FORMATS = ("lif", "czi", "oib")
OTHER_FORMAT_SHARE = 0.06
_OUTCOMES = ("complete", "abandoned", "partial", "analysis_only")
_OUTCOME_WEIGHTS = (55, 14, 19, 12)
#: How often a member slips from their habit for one folder.
SLIP = 0.18
_SEPARATORS = (" ", "_", "-")
#: The server an archiving system moved files to. It is not reachable from where the pool is read.
ARCHIVE_SERVER = "/archive-server/lab"
#: The experiments with data in another person's folder, and with analysis kept somewhere else.
DATA_ELSEWHERE_SHARE = 0.08
ANALYSIS_ELSEWHERE_SHARE = 0.10
#: Experiments with many more replicates than usual (time series, plate reads).

_COLUMNS = (
    ("frame", "area", "mean_intensity"),
    ("track", "speed", "length"),
    ("object", "area", "circularity"),
    ("well", "signal", "background"),
    ("band", "volume", "ratio"),
)

_PROCESSED_KINDS = {"nd2", "czi", "tiffstack", "lif", "oib"}
_DEFAULT_PREFIX = {
    "nd2": "Image",
    "czi": "Series",
    "lif": "Series",
    "oib": "Image",
    "tiffstack": "IMG_",
    "camera": "IMG_",
    "plate": "Export",
    "blot": "gel_",
}


def _at(day: dt.datetime, *, days: float = 0, hours: float = 0, minutes: float = 0) -> dt.datetime:
    return day + dt.timedelta(days=days, hours=hours, minutes=minutes)


def _capitalized(name: str) -> str:
    return name[:1].upper() + name[1:]


def _csv(d: Drive, columns: Sequence[str], rows: int | None = None, *, tab: bool = False) -> bytes:
    """A small table of invented numbers; no two tables share a byte sequence."""
    count = rows if rows is not None else d.random.randint(4, 8)
    mark = "\t" if tab else ","
    lines = [mark.join(columns)]
    for row in range(1, count + 1):
        values = [f"{d.random.uniform(0, 999):.3f}" for _ in columns[1:]]
        lines.append(mark.join([str(row), *values]))
    lines.append(f"# {d.token().hex()}")
    return ("\n".join(lines) + "\n").encode()


def _stamp_text(d: Drive) -> str:
    return d.token().hex()


# ---------------------------------------------------------------------------
# What a file's place says about its stage.
# ---------------------------------------------------------------------------


def _stage(d: Drive, path: str, stage: str | None, *, cue: bool) -> None:
    """Record a file's stage: an answer where the files say it, undecidable where they do not."""
    if stage is None:
        d.value("stage", path, None)
    elif cue:
        d.value("stage", path, stage)
    else:
        d.undecidable("stage", path)


def _dated(d: Drive, path: str, settled: str | None) -> None:
    """Record the date a file's name states, or that its spelling does not settle it."""
    if settled is not None:
        d.value("name_date", path, settled)
    else:
        d.undecidable("name_date", path)


@dataclass
class _Unit:
    """One condition's one replicate: its files, by stage."""

    condition: Condition
    index: int
    #: The name of its processed and analysis files (without a stage word or an extension).
    base: str
    #: The name of its acquisition (without an extension).
    raw_base: str
    raw: str | None = None
    processed: str | None = None
    results: str | None = None
    sidecar: str | None = None


@dataclass
class _Layout:
    """Where a stage's files go in one experiment folder, and whether the folder says the stage."""

    folder: str
    raw: tuple[str, bool]
    processed: tuple[str, bool]
    analysis: tuple[str, bool]


def _layout(folder: str, habit: Habit) -> _Layout:
    if habit.layout == "stages":
        return _Layout(
            folder,
            (f"{folder}/{habit.raw_folder[0]}", habit.raw_folder[1]),
            (f"{folder}/{habit.processed_folder[0]}", habit.processed_folder[1]),
            (f"{folder}/{habit.analysis_folder[0]}", habit.analysis_folder[1]),
        )
    if habit.layout == "images_and_rest":
        return _Layout(folder, (f"{folder}/images", False), (folder, False), (folder, False))
    return _Layout(folder, (folder, False), (folder, False), (folder, False))


# ---------------------------------------------------------------------------
# Contents of the text-like files.
# ---------------------------------------------------------------------------


def _script_text(
    language: str, reads: Sequence[str], writes: Sequence[str], token: str, *, where: str
) -> bytes:
    """A script naming, by path from its own folder, the files it reads and the files it writes."""
    read = [posixpath.relpath(path, where) for path in reads]
    wrote = [posixpath.relpath(path, where) for path in writes]
    tables = (".csv", ".xlsx", ".txt")
    if language == "matlab":
        lines = ["% summarize one experiment's files", "data = {};"]
        for name in read:
            reader = "readtable" if name.endswith(tables) else "bfopen"
            lines.append(f"data{{end+1}} = {reader}('{name}');")
        for name in wrote:
            lines.append(
                f"writetable(S, '{name}');" if name.endswith(tables) else f"saveas(gcf, '{name}');"
            )
        lines.append(f"% {token}")
    elif language == "fiji":
        # As a recorded macro is: one block for each image (open, process, save, close), each
        # saving the file made from the one it opened. ``reads`` and ``writes`` are in step.
        lines = ["// process one experiment's images"]
        for source, target in zip(read, wrote, strict=True):
            lines += [
                f'open("{source}");',
                'run("Z Project...", "projection=[Max Intensity]");',
                f'saveAs("Tiff", "{target}");',
                "close();",
                "close();",
            ]
        lines.append(f"// {token}")
    else:
        readers = {
            "csv": ("pd", "pd.read_csv"),
            "txt": ("pd", "pd.read_table"),
            "xlsx": ("pd", "pd.read_excel"),
            "mat": ("scipy.io", "scipy.io.loadmat"),
            "tif": ("tifffile", "tifffile.imread"),
            "nd2": ("nd2", "nd2.imread"),
            "czi": ("czifile", "czifile.imread"),
            "lif": ("readlif", "readlif.LifFile"),
            "oib": ("oiffile", "oiffile.imread"),
        }
        used = [readers.get(name.rpartition(".")[2], readers["txt"]) for name in read]
        modules = sorted({module for module, _ in used} - {"pd"})
        lines = ["import pandas as pd", *(f"import {module}" for module in modules)]
        lines += ["", "data = {}"]
        lines.extend(
            f'data["{name}"] = {call}("{name}")' for name, (_, call) in zip(read, used, strict=True)
        )
        lines.append("summary = pd.DataFrame({key: [len(value)] for key, value in data.items()})")
        for name in wrote:
            if name.endswith(".csv"):
                lines.append(f'summary.to_csv("{name}")')
            elif name.endswith(".xlsx"):
                lines.append(f'summary.to_excel("{name}")')
            else:
                lines.append(f'summary.plot().figure.savefig("{name}")')
        lines.append(f"# {token}")
    return ("\n".join(lines) + "\n").encode()


def _readme_text(d: Drive, title: str, lines: Sequence[str], *, quality: bool = False) -> bytes:
    body = [title, "", *lines]
    if quality:
        body.append(d.random.choice(["good run", "cells looked unwell on day two", "not good"]))
    body += ["", f"({_stamp_text(d)})"]
    return ("\n".join(body) + "\n").encode()


def _sidecar_bytes(d: Drive, form: str, name: str, moment: dt.datetime) -> bytes:
    """The record an instrument writes beside an acquisition: an XML or a JSON file."""
    if form == "xml":
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<Acquisition name="{name}" date="{moment:%Y-%m-%d}">\n'
            f'  <Objective magnification="{d.random.choice([20, 40, 60, 63, 100])}"/>\n'
            f'  <Exposure ms="{d.random.randint(10, 500)}"/>\n'
            f'  <Serial value="{d.token().hex()}"/>\n'
            "</Acquisition>\n"
        ).encode()
    return (
        "{\n"
        f'  "name": "{name}",\n'
        f'  "date": "{moment:%Y-%m-%d}",\n'
        f'  "exposure_ms": {d.random.randint(10, 500)},\n'
        f'  "serial": "{d.token().hex()}"\n'
        "}\n"
    ).encode()


# ---------------------------------------------------------------------------
# One experiment.
# ---------------------------------------------------------------------------


def _folder_path(
    m: Member, project: Project, day: dt.datetime, name: str, batch: str | None = None
) -> str:
    head = m.folder
    if m.old_folder and m.old_before and day < m.old_before:
        head = f"{head}/{m.old_folder}"
    if m.instrument_folder:
        head = f"{head}/{m.instrument_folder}"
    year = m.year_folder(day.year)
    if batch:
        head = f"{head}/{batch}"
    if m.top == "years":
        return f"{head}/{year}/{name}"
    if m.top == "projects":
        return f"{head}/{project.label}/{name}"
    if m.top == "project_year":
        return f"{head}/{project.label}/{year}/{name}"
    if m.top == "year_project":
        return f"{head}/{year}/{project.label}/{name}"
    return f"{head}/{name}"


def _treated(project: Project, drug: str, dose: str, time: str | None = None) -> Condition:
    parts = (drug, dose, *((project.vehicle,) if project.vehicle else ()))
    levels = (("treatment", drug), ("dose", dose))
    if time:
        return Condition((*parts, time), (*levels, ("time", time)))
    return Condition(parts, levels)


def _control(project: Project, time: str | None = None) -> Condition:
    levels = (("treatment", "control"),)
    if time:
        return Condition((project.control, time), (*levels, ("time", time)))
    return Condition((project.control,), levels)


def _replicates(d: Drive, base: int) -> int:
    """The replicates of one condition: mostly the experiment's number, now and then one fewer."""
    if base > 1 and d.random.random() < 0.3:
        return base - 1
    return base


def _design(d: Drive, project: Project, *, big: bool) -> list[tuple[Condition, int]]:
    """The conditions of one experiment, each with its replicates.

    A design is a control and a treatment (one dose, or several), two treatments, a control and a
    treatment over some times, or a single condition. The replicates of the cells differ a little.
    """
    rng = d.random
    drug = rng.choice(project.extras)
    doses = project.doses[drug]
    if big:
        return [(_control(project), 25), (_treated(project, drug, doses[0]), 25)]
    base = rng.choices([1, 2, 3], [15, 40, 45])[0]
    if rng.random() < d.busy_share:
        base = rng.randint(4, 12)
    kind = rng.choices(["pair", "dose", "time", "single", "two"], [25, 25, 25, 8, 17])[0]
    cells: list[Condition]
    if kind == "dose":
        cells = [
            _control(project),
            *(_treated(project, drug, dose) for dose in rng.sample(doses, min(len(doses), 3))),
        ]
    elif kind == "time":
        times = sorted(rng.sample(project.times, min(len(project.times), rng.randint(2, 3))))
        dose = rng.choice(doses)
        cells = [
            *(_control(project, time) for time in times),
            *(_treated(project, drug, dose, time) for time in times),
        ]
    elif kind == "two" and len(project.extras) > 1:
        other = rng.choice([x for x in project.extras if x != drug])
        cells = [
            _control(project),
            _treated(project, drug, doses[0]),
            _treated(project, other, project.doses[other][0]),
        ]
    elif kind == "single":
        cells = [rng.choice([_control(project), _treated(project, drug, doses[0])])]
    else:
        cells = [_control(project), _treated(project, drug, rng.choice(doses))]
    return [(cell, _replicates(d, base)) for cell in cells]


def _raw_bytes(d: Drive, kind: str, moment: dt.datetime, plate_form: str) -> tuple[bytes, str, str]:
    """The bytes of one acquisition, its extension, and the label the manifest counts it under."""
    if kind == "nd2":
        return nd2_bytes(moment, pixels=d.token()), "nd2", "nd2"
    if kind == "czi":
        return czi_header_bytes(d.token()), "czi", "czi_shaped_header"
    if kind == "lif":
        return lif_header_bytes(d.token()), "lif", "lif_shaped_header"
    if kind == "oib":
        return ole_document_bytes("OibInfo.txt", d.token()), "oib", "oib_shaped_container"
    if kind == "tiffstack":
        pages = [d.token() * 2 for _ in range(d.random.randint(2, 4))]
        text = f"ImageJ=1.53\nimages={len(pages)}\nslices={len(pages)}\n"
        return tiff_stack_bytes(pages, description=text), "tif", "tiff_stack"
    if kind == "plate":
        columns = ["well", "signal", "background"]
        if plate_form == "xlsx":
            rows: list[list[str | float | int]] = [list(columns)]
            rows += [
                [f"A{n:02d}", f"{d.random.uniform(0, 999):.3f}", f"{d.random.uniform(0, 99):.3f}"]
                for n in range(1, 5)
            ]
            rows.append([d.token().hex(), 0, 0])
            data = xlsx_bytes(rows, sheet="plate", creator="reader", moment=moment)
            return data, "xlsx", "xlsx_plate_export"
        table = _csv(d, columns, 4, tab=plate_form == "txt")
        return table, plate_form, "plate_table"
    return tiff_bytes(d.token() * 2), "tif", "tiff_image"


def _extra_words(project: Project, habit: Habit) -> list[str]:
    """The words beyond the cell line and the protein that a member's file names hold."""
    words = [word for word in (project.techniques[0], project.stain) if word]
    return words[: habit.file_words]


def _unit_names(
    d: Drive,
    habit: Habit,
    project: Project,
    kind: str,
    cells: list[tuple[Condition, int]],
    day: dt.datetime,
    start: int,
) -> tuple[list[_Unit], str | None]:
    """The units of an experiment with the names of their acquisitions and their later files.

    Returns the units, and the ISO date a name states if the raw names carry the date and the
    spelling settles it (``""`` if they carry it and it does not settle it).
    """
    rng = d.random
    sep = habit.sep
    extra_words = _extra_words(project, habit)
    prefix = [*(project.core if habit.long_names else project.core[:1]), *extra_words]
    sloppy = rng.random() < 0.3
    written, settled = date_text(day, habit.date_style)
    dated = habit.raw_naming == "descriptive" and habit.raw_dated
    units: list[_Unit] = []
    serial = start if habit.raw_naming == "instrument" else 0
    for condition, count in cells:
        for number in range(1, count + 1):
            serial += 1
            variant = rng.randint(0, 2) if sloppy and number > 1 else 0
            numbering = replicate_parts(habit.replicate, number, serial, variant)
            member_base = sep.join([*prefix, *condition.parts, *numbering])
            if habit.raw_naming == "instrument":
                raw_base = (
                    f"{_DEFAULT_PREFIX[kind]} {serial}"
                    if kind in {"czi", "lif"}
                    else f"{_DEFAULT_PREFIX[kind]}{serial:04d}"
                )
            elif habit.raw_naming == "descriptive":
                head = [written] if dated else []
                raw_base = "_".join(
                    [
                        *head,
                        project.core[0],
                        *extra_words,
                        "-".join(condition.parts),
                        f"{serial:03d}",
                    ]
                )
            else:
                raw_base = member_base
            base = raw_base if habit.names_follow else member_base
            units.append(_Unit(condition, number, base, raw_base))
    return units, (settled or "") if dated else None


def experiment(
    d: Drive,
    project: Project,
    day: dt.datetime,
    *,
    others: Sequence[Member] = (),
    big: bool = False,
    symbol: str | None = None,
) -> None:
    """Write one dated experiment of ``project`` on ``day``, in its member's habit of the year."""
    rng = d.random
    m = project.owner
    habit = m.habit_on(day.year)
    if rng.random() < SLIP:
        habit = replace(
            habit,
            date_style=rng.choices(DATE_STYLES, [15, 10, 25, 12, 15, 10, 13])[0],
            sep=rng.choice(_SEPARATORS),
            date_first=rng.random() < 0.5,
            layout=habit.layout if rng.random() < 0.85 else rng.choice(["stages", "flat"]),
        )
    sep = habit.sep
    outcome = "complete" if big else rng.choices(_OUTCOMES, _OUTCOME_WEIGHTS)[0]
    kind = project.kind
    if not big and rng.random() < OTHER_FORMAT_SHARE:
        kind = rng.choice(OTHER_FORMATS)
    # -- the folder -------------------------------------------------------------------------------
    parts_of_name = list(project.core)
    if rng.random() < 0.6:
        parts_of_name.append(rng.choice(project.techniques))
    extra = None
    if project.extras and rng.random() < 0.35:
        extra = rng.choice(project.extras)
        parts_of_name.append(extra)
    if project.stain and rng.random() < 0.15:
        parts_of_name.append(project.stain)
    if rng.random() < 0.25:
        parts_of_name.append(rng.choice(design_words()))
    if symbol:
        parts_of_name.append(symbol)
    written, settled = date_text(day, habit.date_style)
    parts = [written, *parts_of_name] if habit.date_first else [*parts_of_name, written]
    name = sep.join(parts)
    # Now and then a month's folder holds the experiments of its weeks.
    batch = f"{MONTH_NAMES[day.month - 1][:3]} {day.year}" if rng.random() < 0.15 else None
    folder = _folder_path(m, project, day, name, batch)
    attempt = 1
    while d.taken(folder):
        attempt += 1
        folder = _folder_path(m, project, day, f"{name}{sep}{attempt}", batch)
    if attempt > 1:
        settled = settled if habit.date_first else None
    d.value("experiment_folder", folder, "experiment")
    _dated(d, folder, settled)
    project.experiments.append(folder)
    if extra is not None:
        project.subgroups.setdefault(extra, []).append(folder)
    layout = _layout(folder, habit)
    # -- where the data and the analysis are kept -------------------------------------------------
    elsewhere: Member | None = None
    if (
        others
        and not big
        and outcome in {"complete", "partial"}
        and rng.random() < DATA_ELSEWHERE_SHARE
    ):
        candidate = rng.choice(list(others))
        place = f"{candidate.folder}/{posixpath.basename(folder)}/raw"
        if not d.taken(place) and not d.taken(posixpath.dirname(place)):
            elsewhere = candidate
            layout.raw = (place, True)
    if (
        habit.layout == "stages"
        and outcome == "complete"
        and not big
        and rng.random() < ANALYSIS_ELSEWHERE_SHARE
    ):
        place = f"{m.folder}/analysis/{posixpath.basename(folder)}"
        if not d.taken(place):
            layout.analysis = (place, True)
            d.note("analysis_elsewhere", {"experiment": folder, "analysis": place})
    # -- the units --------------------------------------------------------------------------------
    cells = _design(d, project, big=big)
    units, raw_date = _unit_names(d, habit, project, kind, cells, day, rng.randint(1, 2500))
    # The same experiment done again: one project, one set of name words,
    # one instrument format, another day. The key's repeats family is built from these. Not
    # the conditions too: the generator draws a design afresh each time, so no two experiments
    # share one exactly, and a key asking for that names no repeat on any tier or seed.
    d.signatures[folder] = (
        (project.label, " ".join(sorted(parts_of_name)), kind),
        day.date().isoformat(),
    )
    sidecar_form = project.sidecar if kind in {"nd2", "czi", "lif", "oib", "tiffstack"} else None
    make_raw = outcome in {"complete", "abandoned", "partial"}
    make_processed = (
        not big
        and kind in _PROCESSED_KINDS
        and (
            outcome in {"complete", "partial"}
            or (outcome == "analysis_only" and rng.random() < 0.5)
        )
    )
    if make_processed and rng.random() < 0.15:
        make_processed = False
    make_results = outcome in {"complete", "analysis_only"}
    when = _at(day, hours=9, minutes=rng.randint(0, 59))
    pending_missing: list[str] = []
    all_raw: list[str] = []
    mat_results = m.scripting in {"matlab", "both"} and rng.random() < 0.4
    # -- the files, unit by unit ------------------------------------------------------------------
    for position, unit in enumerate(units):
        # Stamped before the bytes are built: a date inside a file is never later than its time.
        moment = d.stamp(_at(when, minutes=4 * position), layout.folder)
        data, extension, raw_label = _raw_bytes(d, kind, moment, project.plate_form)
        raw_name = unit.raw_base
        if habit.raw_word and not layout.raw[1]:
            raw_name = f"{raw_name}{sep}raw"
        raw_path = f"{layout.raw[0]}/{raw_name}.{extension}"
        if outcome == "analysis_only":
            pending_missing.append(raw_path)
        elif make_raw:
            if d.taken(raw_path):
                raw_path = f"{layout.raw[0]}/{raw_name}{sep}2.{extension}"
            d.file(raw_path, data, moment, kind=raw_label)
            unit.raw = raw_path
            all_raw.append(raw_path)
            raw_cue = kind in {"nd2", "czi", "lif", "oib"} or layout.raw[1] or habit.raw_word
            _stage(d, raw_path, "raw", cue=raw_cue)
            if raw_date is not None:
                _dated(d, raw_path, raw_date or None)
            if sidecar_form:
                side = f"{layout.raw[0]}/{raw_name}.{sidecar_form}"
                if not d.taken(side):
                    d.file(
                        side,
                        _sidecar_bytes(d, sidecar_form, raw_name, moment),
                        moment,
                        kind=f"{sidecar_form}_sidecar",
                    )
                    unit.sidecar = side
                    _stage(d, side, "raw", cue=raw_cue)
                    d.decline(side, "replicate", "derivation", "reference")
        if make_processed:
            suffix = (
                sep + sep.join([*habit.detail, habit.process_word])
                if (habit.layout != "stages" or rng.random() < 0.6)
                else ""
            )
            proc_path = f"{layout.processed[0]}/{unit.base}{suffix}.tif"
            if d.taken(proc_path):
                proc_path = f"{layout.processed[0]}/{unit.base}{sep}{habit.process_word}{sep}2.tif"
            image = tiff_bytes(d.token() * 2)
            d.file(proc_path, image, _at(moment, hours=rng.randint(1, 30)), kind="tiff_image")
            unit.processed = proc_path
            _stage(d, proc_path, "processed", cue=layout.processed[1] or bool(suffix))
            if raw_date is not None and habit.names_follow:
                _dated(d, proc_path, raw_date or None)
        if make_results:
            suffix = (
                sep + habit.results_word if (habit.layout != "stages" or rng.random() < 0.6) else ""
            )
            # Made from the processed file if there is one, so never older than what it came from.
            made_from = d.times[unit.processed] if unit.processed else moment
            res_moment = _at(made_from, days=rng.randint(0, 20), hours=3)
            if mat_results:
                res_path = f"{layout.analysis[0]}/{unit.base}{suffix}.mat"
                table = mat_file_bytes(d.stamp(res_moment, res_path), "MACI64") + d.token()
                label = "mat_file"
            else:
                extension_out = "txt" if habit.results_text else "csv"
                res_path = f"{layout.analysis[0]}/{unit.base}{suffix}.{extension_out}"
                table = _csv(d, _COLUMNS[position % len(_COLUMNS)], tab=extension_out == "txt")
                label = "txt_table" if extension_out == "txt" else "csv"
            if d.taken(res_path):
                res_path = f"{res_path.rpartition('.')[0]}{sep}2.{res_path.rpartition('.')[2]}"
            d.file(res_path, table, res_moment, kind=label)
            unit.results = res_path
            _stage(d, res_path, "analysis", cue=layout.analysis[1] or bool(suffix))
            if raw_date is not None and habit.names_follow:
                _dated(d, res_path, raw_date or None)
    # -- the chain the folder's own files reached -------------------------------------------------
    reached = [
        stage_name
        for stage_name, held in (
            ("raw", [unit.raw for unit in units if unit.raw]),
            ("processed", [unit.processed for unit in units if unit.processed]),
            ("analysis", [unit.results for unit in units if unit.results]),
        )
        if any(path.startswith(f"{folder}/") for path in held)
    ]
    d.value("workflow", folder, ">".join(reached) if reached else None)
    # -- empty output among filled siblings -------------------------------------------------------
    results = [unit.results for unit in units if unit.results]
    if len(results) >= 3 and rng.random() < 0.08 and not mat_results:
        victim = rng.choice(results)
        d.files[victim] = b""
        d.kinds[victim] = "csv_empty"
        d.value("empty_file", victim, "empty_output")
        d.decline(victim, "replicate")
    # -- relations --------------------------------------------------------------------------------
    named_after_raw = (
        habit.names_follow
        or habit.raw_naming == "renamed"
        or (habit.raw_naming == "descriptive" and habit.replicate == "counter")
    )
    for unit in units:
        chain = [path for path in (unit.raw, unit.processed, unit.results) if path]
        for source, target in pairwise(chain):
            if source == unit.raw and not named_after_raw:
                d.pair_undecidable("derivation", source, target)
            else:
                d.pair("derivation", source, target)
    says_condition = {
        "raw": habit.raw_naming != "instrument",
        "processed": (habit.raw_naming != "instrument") if habit.names_follow else True,
        "results": (habit.raw_naming != "instrument") if habit.names_follow else True,
    }
    for stage_name in ("raw", "processed", "results"):
        found = [getattr(unit, stage_name) for unit in units if getattr(unit, stage_name)]
        if not says_condition[stage_name]:
            # Named by the instrument, no file says which condition or replicate it is: any two of
            # an experiment's files of one stage might be replicates, and the files cannot settle
            # it.
            for first, second in combinations(sorted(found), 2):
                d.pair_undecidable("replicate", first, second)
            continue
        for condition in {unit.condition for unit in units}:
            members = tuple(
                getattr(unit, stage_name)
                for unit in units
                if unit.condition == condition and getattr(unit, stage_name)
            )
            if len(members) < 2:
                continue
            d.group("replicate", members)
    if outcome == "analysis_only":
        d.note(
            "analysis_without_raw",
            {"folder": folder, "missing": sorted(pending_missing), "named_by": []},
        )
    elif outcome == "abandoned":
        d.note("abandoned_experiments", folder)
        if layout.processed[0] != folder and rng.random() < 0.5:
            d.folder(layout.processed[0], _at(when, hours=1))
        if (
            layout.analysis[0] != folder
            and layout.analysis[0].startswith(folder)
            and rng.random() < 0.5
        ):
            d.folder(layout.analysis[0], _at(when, hours=1))
    elif outcome == "complete":
        d.note("complete_experiments", folder)
    else:
        d.note("partial_experiments", folder)
    if elsewhere is not None:
        d.decline(posixpath.dirname(layout.raw[0]), "experiment_folder", "program")
        d.note(
            "data_in_another_members_folder",
            {
                "folder": folder,
                "data_in": posixpath.dirname(layout.raw[0]),
                "kept_by": elsewhere.name,
            },
        )
    # -- the design, written from the generator's own choices -------------------------------------
    d.note(
        "designs",
        {
            "folder": folder,
            "outcome": outcome,
            "cells": [
                {
                    "levels": [list(level) for level in condition.levels],
                    "replicates": count,
                    "raw_files": sum(
                        1 for unit in units if unit.condition == condition and unit.raw
                    ),
                }
                for condition, count in cells
            ],
        },
    )
    # -- what is written beside them --------------------------------------------------------------
    _extras(d, m, project, habit, layout, units, folder, outcome, day, pending_missing, all_raw)
    _noise(d, m, layout, when)
    if folder not in d.folders and folder not in d.empty_folders:
        # Its data is in another member's folder and nothing else was kept here: the folder stays.
        d.folder(folder, _at(when, hours=1))


def _extras(
    d: Drive,
    m: Member,
    project: Project,
    habit: Habit,
    layout: _Layout,
    units: list[_Unit],
    folder: str,
    outcome: str,
    day: dt.datetime,
    missing: list[str],
    raw_paths: list[str],
) -> None:
    rng = d.random
    sep = habit.sep
    results = [unit.results for unit in units if unit.results]
    processed = [unit.processed for unit in units if unit.processed]
    stem = sep.join([*project.core, *_extra_words(project, habit)])
    after = _at(day, days=rng.randint(3, 40), hours=14)
    if results:
        # Whatever is made from the tables is made after the last of them.
        after = max(after, _at(max(d.times[path] for path in results), hours=1))
    after = d.stamp(after, folder)  # the date inside what is made then is capped like its time
    made: list[str] = []
    # a summary workbook of every table
    summary = None
    if results and rng.random() < 0.2:
        rows: list[list[str | float | int]] = [["file", "area", "signal"]]
        rows.extend(
            [posixpath.basename(path), round(rng.uniform(0, 99), 3), round(rng.uniform(0, 99), 3)]
            for path in results
        )
        rows.append([d.token().hex(), 0, 0])
        extension = "xls" if after.year <= 2016 and rng.random() < 0.5 else "xlsx"
        path = f"{layout.analysis[0]}/{stem}{sep}summary.{extension}"
        data = (
            xlsx_bytes(rows, sheet="summary", creator=m.name, moment=after)
            if extension == "xlsx"
            else ole_document_bytes("Workbook", d.token())
        )
        summary = d.file(path, data, after, kind=extension)
        # A workbook built from the analysis tables, in the analysis folder, is analysis: the key
        # says so where the folder says it, and calls the stage undecidable where nothing does.
        _stage(d, summary, "analysis", cue=layout.analysis[1])
        d.never_copied.add(summary)  # no copy-of name for it
        if extension == "xlsx":
            for path in results:
                d.pair("derivation", path, summary)
        else:
            d.decline(summary, "derivation", "reference", "replicate")
        made.append(summary)
        _lock(d, m, summary, after)
    figure = None
    if results and rng.random() < 0.55:
        figure = d.file(
            f"{layout.analysis[0]}/{stem}{sep}figure.png",
            png_bytes(d.token() * 2),
            _at(after, hours=1),
            kind="png",
        )
        _stage(d, figure, "analysis", cue=layout.analysis[1])
        made.append(figure)
    # a script or a notebook that reads the tables and writes the figure
    script = None
    if (
        results
        and (figure or summary)
        and m.scripting not in {"none", "fiji"}
        and rng.random() < 0.5
    ):
        language = (
            "matlab"
            if m.scripting == "matlab" or (m.scripting == "both" and rng.random() < 0.5)
            else "python"
        )
        writes = [path for path in (figure, summary) if path and path.endswith((".png", ".xlsx"))]
        where = layout.analysis[0] if rng.random() < 0.5 else folder
        notebook = language == "python" and rng.random() < 0.3
        token = _stamp_text(d)
        extension = "m" if language == "matlab" else ("ipynb" if notebook else "py")
        verb = "quant" if language == "matlab" else "analyze"
        script = f"{where}/{verb}{sep}{stem}.{extension}"
        if d.taken(script):
            script = f"{where}/{verb}{sep}{stem}{sep}2.{extension}"
        reads = [*missing, *results]
        if notebook:
            text = _script_text("python", reads, writes, token, where=where).decode()
            data = notebook_bytes([("markdown", f"# {stem}\n"), ("code", text)])
            label = "notebook"
        else:
            data = _script_text(language, reads, writes, token, where=where)
            label = "matlab_script" if language == "matlab" else "python_script"
        d.file(script, data, _at(after, hours=2), kind=label)
        d.decline(script, "stage")
        for path in (*results, *writes):
            d.pair("reference", script, path)
        for source in results:
            for target in writes:
                d.pair("derivation", source, target)
        made.append(script)
    elif figure is not None:
        # No script says what the figure was made from. The tables beside it are the likely
        # source, and the files do not settle it: the right output is abstention, not silence.
        for source in results:
            d.pair_undecidable("derivation", source, figure)
    # a Fiji macro that opens the acquisitions and saves the processed images
    if processed and m.scripting in {"fiji", "both"} and rng.random() < 0.5:
        both = [(unit.raw, unit.processed) for unit in units if unit.raw and unit.processed]
        raws = [raw for raw, _ in both if raw]
        made_from = [target for _, target in both if target]
        if raws:
            where = layout.processed[0] if rng.random() < 0.5 else folder
            macro = f"{where}/process{sep}{stem}.ijm"
            if not d.taken(macro):
                d.file(
                    macro,
                    _script_text("fiji", raws, made_from, _stamp_text(d), where=where),
                    _at(day, hours=8),
                    kind="fiji_macro",
                )
                d.decline(macro, "stage")
                for path in (*raws, *made_from):
                    d.pair("reference", macro, path)
                # The macro says which image each processed file was made from.
                for raw, target in both:
                    if raw and target:
                        d.undecidable_pairs.get("derivation", set()).discard((raw, target))
                        d.pair("derivation", raw, target)
                made.append(macro)
    # a note
    note: str | None = None
    if outcome == "analysis_only" or rng.random() < m.tidy:
        names = [path for path in (raw_paths[:1] + processed[:1] + results[:1]) if path]
        lines = [
            f"Experiment of {day.day} {MONTH_NAMES[day.month - 1]} {day.year}.",
            "Replicates: "
            + str(len([u for u in units if u.condition == units[0].condition]))
            + " per condition.",
        ]
        lines.extend(f"See {posixpath.relpath(path, folder)}." for path in names)
        for path in missing[:2]:
            lines.append(f"The images were {posixpath.relpath(path, folder)}.")
        note_name = rng.choice(["README.txt", "notes.txt", "readme.txt", "lab notes.txt"])
        if not d.taken(f"{folder}/{note_name}"):
            note = f"{folder}/{note_name}"
            d.file(
                note,
                _readme_text(d, f"{stem} {day.year}", lines, quality=rng.random() < 0.25),
                _at(max(d.times[path] for path in names), hours=1) if names else _at(day, hours=20),
                kind="text_note",
            )
            for path in names:
                d.pair("reference", note, path)
            made.append(note)
    # a document or two
    if outcome in {"complete", "analysis_only"} and rng.random() < 0.12:
        report = _document(d, m, f"{folder}/{stem}{sep}report", "docx", after, stem)
        made.append(report)
    if outcome == "complete" and rng.random() < 0.08:
        deck = _document(d, m, f"{folder}/{stem}{sep}figures", "pptx", _at(after, days=5), stem)
        made.append(deck)
    if outcome in {"complete", "partial"} and rng.random() < 0.5:
        pdf = _document(d, m, f"{folder}/{stem}{sep}summary", "pdf", _at(after, days=2), stem)
        made.append(pdf)
    # frames exported as pictures, and the tables zipped for a colleague
    if outcome == "complete" and rng.random() < 0.25:
        for number in range(1, rng.randint(2, 5)):
            form = rng.choice(["jpg", "gif"])
            frame = f"{layout.analysis[0]}/{stem}{sep}frame{number:03d}.{form}"
            if not d.taken(frame):
                data = jpeg_bytes(d.token()) if form == "jpg" else gif_bytes(d.token())
                d.file(frame, data, _at(after, hours=1, minutes=number), kind=form)
                d.decline(frame, "stage", "replicate", "derivation", "reference")
    if outcome == "complete" and results and rng.random() < 0.55:
        archive = f"{layout.analysis[0]}/{stem}{sep}{habit.results_word}.zip"
        if not d.taken(archive):
            parts: dict[str, str | bytes] = {
                posixpath.basename(path): d.files[path]
                for path in results[:6]
                if d.files[path] and path.endswith(".csv")
            }
            parts[f"{stem}.txt"] = f"{stem} tables ({_stamp_text(d)})\n"
            d.file(
                archive,
                zip_bytes(parts, d.stamp(_at(after, days=1), archive)),
                _at(after, days=1),
                kind="zip",
            )
            d.decline(archive, "stage", "replicate", "derivation", "reference")
    # a table renamed and kept beside the work, or a stray export saved under the wrong extension
    full = [path for path in results if d.files[path] and path.endswith((".csv", ".txt"))]
    if full and rng.random() < 0.05:
        source = rng.choice(full)
        kept = f"{posixpath.dirname(source)}/data for figure {rng.randint(1, 6)}.csv"
        if not d.taken(kept):
            copy_of_path(d, source, kept, _at(after, days=1), declined=("derivation", "reference"))
    if full and rng.random() < 0.05:
        # An output written twice: the same name and bytes in the experiment's folder and in the
        # folder it was made in.
        source = rng.choice(full)
        twice = f"{folder}/{posixpath.basename(source)}"
        if posixpath.dirname(source) != folder and not d.taken(twice):
            copy_of_path(d, source, twice, _at(after, days=2), declined=("derivation", "reference"))
            d.note("outputs_written_twice", [source, twice])
    if rng.random() < 0.03:
        wrong = f"{layout.analysis[0]}/{stem}{sep}export.csv"
        if not d.taken(wrong):
            d.file(wrong, tiff_bytes(d.token() * 2), after, kind="tiff_named_csv")
            d.value("contradicted_extension", wrong, "image/tiff")
            d.decline(wrong, "stage")
    if rng.random() < 0.02:
        wrong = f"{layout.analysis[0]}/{stem}{sep}panel.jpg"
        if not d.taken(wrong):
            d.file(wrong, png_bytes(d.token() * 2), after, kind="png_named_jpg")
            d.value("contradicted_extension", wrong, "image/png")
            d.decline(wrong, "stage")
    if outcome == "analysis_only":
        namers = [path for path in (script, note) if path]
        for entry in d.unscored.get("analysis_without_raw", []):
            if isinstance(entry, dict) and entry["folder"] == folder:
                entry["named_by"] = namers
    # a version chain on one of the documents
    for path in made:
        extension = path.rsplit(".", 1)[-1]
        if extension in {"docx", "pptx", "xlsx", "py", "m", "ijm"} and rng.random() < 0.15:
            versions(d, m, path, _at(after, days=rng.randint(7, 90)))
            break


#: The extensions of an older Office file and the stream its container holds.
_OLD_OFFICE = {"docx": ("doc", "WordDocument"), "pptx": ("ppt", "PowerPoint Document")}


def _document(
    d: Drive, m: Member, stem_path: str, extension: str, moment: dt.datetime, subject: str
) -> str:
    """A report, a figure deck or a summary page: a document the key declines to relate."""
    old = extension in _OLD_OFFICE and moment.year <= 2016 and d.random.random() < 0.5
    if old:
        extension, stream = _OLD_OFFICE[extension]
    path = f"{stem_path}.{extension}"
    moment = d.stamp(moment, path)  # before the bytes: the date inside is capped like the file's
    token = _stamp_text(d)
    title = posixpath.basename(stem_path)
    creator = m.name
    if old:
        data = ole_document_bytes(stream, d.token())
    elif extension == "docx":
        data = docx_bytes(
            [f"{subject}: second pass.", f"Wells three to nine. ({token})"],
            title=title,
            creator=creator,
            moment=moment,
        )
    elif extension == "pptx":
        data = pptx_bytes(
            [f"{subject} slide one", f"{subject} slide two ({token})"],
            title=title,
            creator=creator,
            moment=moment,
        )
    else:
        data = pdf_bytes([f"{subject} summary", f"values {token}"], title=title, moment=moment)
    if d.taken(path):
        path = f"{stem_path} 2.{extension}"
    d.file(path, data, moment, kind=extension)
    d.decline(path, "stage", "replicate", "derivation", "reference")
    _lock(d, m, path, moment)
    return path


def _lock(d: Drive, m: Member, path: str, moment: dt.datetime) -> None:
    """The lock file an Office editor sometimes leaves beside a document on a Windows machine."""
    if (
        m.system == "windows"
        and path.rsplit(".", 1)[-1] in {"docx", "xlsx", "pptx"}
        and d.random.random() < 0.06
    ):
        folder, _, name = path.rpartition("/")
        lock = f"{folder}/~${name[2:] if len(name) > 2 else name}"
        if not d.taken(lock):
            d.file(
                lock,
                office_lock_bytes(m.given, d.token()),
                moment,
                kind="noise_office_lock",
            )
            d.decline(lock, "stage")


def _noise(d: Drive, m: Member, layout: _Layout, when: dt.datetime) -> None:
    """What an operating system leaves in the folders it visited, and a folder nobody filled."""
    rng = d.random
    folders = sorted({layout.folder, layout.raw[0], layout.processed[0], layout.analysis[0]})
    for place in folders:
        if place not in d.folders:
            continue
        if m.system == "mac" and rng.random() < 0.4:
            path = f"{place}/.DS_Store"
            if not d.taken(path):
                d.file(path, ds_store_bytes(d.token()), _at(when, hours=2), kind="noise_ds_store")
        elif m.system == "windows" and rng.random() < 0.12:
            path = f"{place}/Thumbs.db"
            if not d.taken(path):
                d.file(path, thumbs_db_bytes(d.token()), _at(when, hours=2), kind="noise_thumbs_db")
    if rng.random() < 0.04:
        leftover = rng.choice(["New folder", "Untitled Folder", "tmp", "New folder (2)"])
        path = f"{layout.folder}/{leftover}"
        if not d.taken(path):
            d.folder(path, _at(when, hours=3))


# ---------------------------------------------------------------------------
# Analysis in somebody else's folder.
# ---------------------------------------------------------------------------


def borrowed_analysis(
    d: Drive, project: Project, owner_folder: str, sources: Sequence[str], day: dt.datetime
) -> None:
    """A member's script and tables made from files that lie in another member's folder."""
    m = project.owner
    habit = m.habit_on(day.year)
    written, settled = date_text(day, habit.date_style)
    stem = habit.sep.join(project.core)
    name = (
        habit.sep.join([written, "reanalysis", stem])
        if habit.date_first
        else habit.sep.join(["reanalysis", stem, written])
    )
    folder = _folder_path(m, project, day, name)
    if d.taken(folder):
        return
    d.value("experiment_folder", folder, "experiment")
    _dated(d, folder, settled)
    chosen = list(sources[:3])
    table = d.file(
        f"{folder}/{stem}{habit.sep}{habit.results_word}.csv",
        _csv(d, _COLUMNS[0]),
        _at(day, hours=15),
        kind="csv",
    )
    figure = d.file(
        f"{folder}/{stem}{habit.sep}figure.png",
        png_bytes(d.token() * 2),
        _at(day, hours=16),
        kind="png",
    )
    d.value("stage", table, "analysis")
    d.decline(figure, "stage")
    language = "matlab" if m.scripting == "matlab" else "python"
    script = f"{folder}/analyze{habit.sep}{stem}.{'m' if language == 'matlab' else 'py'}"
    d.file(
        script,
        _script_text(language, chosen, [table, figure], _stamp_text(d), where=folder),
        _at(day, hours=14),
        kind="matlab_script" if language == "matlab" else "python_script",
    )
    d.decline(script, "stage")
    for path in chosen:
        d.pair("reference", script, path)
        d.pair("derivation", path, table)
        d.pair("derivation", path, figure)
    d.pair("reference", script, table)
    d.pair("reference", script, figure)
    d.note(
        "analysis_in_another_members_folder",
        {"folder": folder, "data_in": owner_folder, "reads": sorted(chosen)},
    )


# ---------------------------------------------------------------------------
# Versions and copies.
# ---------------------------------------------------------------------------

#: The marks a person adds to a name when a file is saved again, in the order they usually come.
_VERSION_MARKERS = ("_2", "_new", "_edit", "_fixed", "_final", "_date")


def _revised(
    d: Drive, m: Member, path: str, original: bytes, number: int, moment: dt.datetime
) -> bytes:
    """Another revision of a file: its text changed (a script or a table) or rebuilt (a package)."""
    extension = path.rsplit(".", 1)[-1]
    moment = d.stamp(moment, path)  # before the bytes: the date inside is capped like the file's
    if extension in {"py", "m"}:
        return original + f"\n# revision {number} {_stamp_text(d)}\n".encode()
    if extension == "ijm":
        return original + f"\n// revision {number} {_stamp_text(d)}\n".encode()
    if extension == "docx":
        return docx_bytes(
            [f"Revision {number}.", _stamp_text(d)],
            title="revised",
            creator=m.name,
            moment=moment,
        )
    if extension == "pptx":
        return pptx_bytes(
            [f"Revision {number}", _stamp_text(d)],
            title="revised",
            creator=m.name,
            moment=moment,
        )
    rows: list[list[str | float | int]] = [
        ["file", "area"],
        ["revised", number],
        [_stamp_text(d), 0],
    ]
    return xlsx_bytes(rows, sheet="summary", creator=m.name, moment=moment)


def versions(d: Drive, m: Member, path: str, moment: dt.datetime) -> None:
    """Write ``_2``, ``_new``, ``_edit``, ``_fixed``, ``_final`` and a date beside a file.

    One to three of the marks, earliest first, joined with an underscore or, now and then, a
    space. The files differ in content: each is another revision, never a copy."""
    rng = d.random
    spaced = rng.random() < 0.4
    count = rng.randint(1, 3)
    chosen = sorted(rng.sample(_VERSION_MARKERS, count), key=_VERSION_MARKERS.index)
    folder, _, name = path.rpartition("/")
    stem, _, extension = name.rpartition(".")
    chain = [path]
    original = d.files[path]
    for number, mark in enumerate(chosen, start=2):
        later = _at(moment, days=3 * number)
        word = f"{later:%Y%m%d}" if mark == "_date" else mark.lstrip("_")
        target = f"{folder}/{stem}{' ' if spaced else '_'}{word}.{extension}"
        if d.taken(target):
            break
        d.file(target, _revised(d, m, path, original, number, later), later, kind=d.kinds[path])
        chain.append(target)
    if len(chain) > 1:
        d.decline(chain, "stage", "replicate", "derivation", "reference")
        d.note("version_chains", chain)


_COPY_PATTERNS = {
    "mac": "{stem} copy.{extension}",
    "windows": "{stem} - Copy.{extension}",
    "linux": "Copy of {stem}.{extension}",
}


def _carry_values(d: Drive, source: str, target: str) -> None:
    """A copy holds its source's bytes, so what they say of their extension it says too."""
    for family in ("contradicted_extension", "empty_file"):
        said = d.values.get(family, {}).get(source)
        if said is not None:
            d.value(family, target, said)


def copy_of_path(
    d: Drive, source: str, target: str, moment: dt.datetime, *, declined: Iterable[str] = ()
) -> None:
    """Write ``target`` as a byte-for-byte copy of ``source``, and say so in the key."""
    if not d.files[source]:
        message = "an empty file is no copy"
        raise ValueError(message)
    d.file(target, d.files[source], moment, kind=d.kinds[source], copy_of=source)
    d.decline(target, "stage", *declined)
    _carry_values(d, source, target)


def copy_of(
    d: Drive, m: Member, source: str, moment: dt.datetime, *, folders: Sequence[str] = ()
) -> None:
    """A file saved again: usually into another folder under its own name, now and then as the
    member's system names a copy (``x copy.csv``, ``Copy of x.csv``) beside the original."""
    folder, _, name = source.rpartition("/")
    if not d.files[source]:
        return
    if folders and d.random.random() < 0.75:
        place = d.random.choice(list(folders))
        target = f"{place}/{name}"
        if d.taken(target) or place == folder:
            return
        copy_of_path(d, source, target, moment, declined=("replicate", "derivation", "reference"))
        d.note("random_duplicates", [source, target])
        return
    stem, _, extension = name.rpartition(".")
    target = f"{folder}/{_COPY_PATTERNS[m.system].format(stem=stem, extension=extension)}"
    if d.taken(target):
        return
    copy_of_path(d, source, target, moment, declined=("replicate", "derivation", "reference"))
    d.note("copies_by_name", [source, target])


def backup_tree(
    d: Drive, m: Member, folder: str, moment: dt.datetime, *, near_copy: bool = False
) -> str | None:
    """Copy part of an experiment folder into the member's ``backup/``, now and then with a
    near-copy.

    A backup is partial: each file is kept with a chance of about one half to nine tenths. The key
    declines to answer about everything inside the copy except that its files are copies of the
    originals (the copy family). A near-copy differs from its original in one digit.
    """
    rng = d.random
    name = folder.rsplit("/", 1)[-1]
    head = m.folder
    target = f"{head}/backup/{name}"
    if d.taken(target):
        return None
    sources = sorted(path for path in d.files if path.startswith(f"{folder}/"))
    if not sources:
        return None
    keep = rng.uniform(0.5, 0.9)
    near = None
    texty = [path for path in sources if path.endswith((".csv", ".txt")) and d.files[path]]
    if texty and (near_copy or rng.random() < 0.5):
        near = rng.choice(texty)
        # A file saved under a .csv name that holds no digit has none to change: take another.
        if not any(48 <= byte <= 57 for byte in d.files[near]):
            with_digits = [p for p in texty if any(48 <= byte <= 57 for byte in d.files[p])]
            near = with_digits[0] if with_digits else None
    chosen = [path for path in sources if path == near or rng.random() < keep]
    if not chosen:
        chosen = sources[:1]
    kept: list[str] = []
    for path in chosen:
        relative = path[len(folder) + 1 :]
        new = f"{target}/{relative}"
        data = d.files[path]
        stamp = d.times[path] if rng.random() < 0.6 else _at(moment, days=rng.randint(0, 20))
        if not data:
            d.file(new, data, stamp, kind=d.kinds[path])
            d.value("empty_file", new, d.values.get("empty_file", {}).get(path, "empty_output"))
        elif path == near:
            digits = [i for i, byte in enumerate(data) if 48 <= byte <= 57]
            spot = digits[len(digits) // 2]
            changed = bytes(data[:spot] + bytes((48 + (data[spot] - 47) % 10,)) + data[spot + 1 :])
            d.file(new, changed, stamp, kind=d.kinds[path])
            _carry_values(d, path, new)
            d.note("near_copies", [path, new])
        else:
            d.file(new, data, stamp, kind=d.kinds[path], copy_of=path)
            _carry_values(d, path, new)
        d.decline(new, "stage", "replicate", "derivation", "reference")
        kept.append(new)
    inside = [target, *(f for f in d.folders if f.startswith(f"{target}/"))]
    d.decline(inside, "experiment_folder", "program")
    d.note(
        "backup_folder_copies",
        {"original": folder, "copy": target, "files": len(kept), "of": len(sources)},
    )
    return target


# ---------------------------------------------------------------------------
# Links, moves.
# ---------------------------------------------------------------------------


def broken_link(d: Drive, m: Member, label: str, gone: str, moment: dt.datetime) -> bool:
    """A symbolic link to a folder that has since been renamed away: it points at nothing.

    Returns whether it was planned: a name already taken in the member's folder plans none."""
    names = ("latest", "current data", f"{label} link", f"go to {label}")
    link = f"{m.folder}/{d.random.choice(names)}"
    if d.taken(link) or link in d.links:
        return False
    d.link(link, gone, moment)
    d.note("broken_links", {"link": link, "target": gone})
    return True


def archive_links(d: Drive, experiments: Sequence[str], count: int) -> int:
    """Replace raw files by links to another server, oldest experiments first, until ``count``.

    An archiving system replaced the older files with links, the raw data first. Each link keeps
    the file's name and time and holds a path on the archive server, which does not exist where
    the pool is read, so it is a link that points at nothing. Returns how many were made.
    """
    made = 0
    by_experiment: dict[str, list[str]] = {}
    for path in sorted(d.files):
        if d.kinds[path] in _RAW_KINDS and d.values.get("stage", {}).get(path) == "raw":
            for folder in experiments:
                if path.startswith(f"{folder}/"):
                    by_experiment.setdefault(folder, []).append(path)
                    break
    oldest = sorted(
        by_experiment, key=lambda folder: min(d.times[p] for p in by_experiment[folder])
    )
    for folder in oldest:
        for path in by_experiment[folder]:
            if made >= count:
                return made
            if "/backup/" in path:
                continue
            kind = d.kinds[path]
            d.archive(path, f"{ARCHIVE_SERVER}/{path}")
            d.note(
                "archive_links", {"link": path, "target": f"{ARCHIVE_SERVER}/{path}", "was": kind}
            )
            made += 1
    return made


_RAW_KINDS = {
    "nd2",
    "czi_shaped_header",
    "lif_shaped_header",
    "oib_shaped_container",
    "tiff_stack",
    "tiff_image",
    "plate_table",
    "xlsx_plate_export",
}


def moved_folder(d: Drive, m: Member, project: Project, day: dt.datetime) -> None:
    """A readme naming a folder's ``data/`` files, which now stand in an archive folder."""
    label = project.label
    stem = "_".join(project.core)
    where = f"{m.folder}/{label} notes"
    if d.taken(where):
        return
    archive = f"{m.folder}/{d.random.choice(['archive', 'old', 'done'])}/{label}"
    if d.taken(archive):
        return
    readme = d.file(
        f"{where}/README.txt",
        (
            f"The raw counts are data/{stem}_counts.csv and the fits data/{stem}_fits.csv.\n"
            f"Everything lived on S:\\lab drive\\{m.name}\\{label}\\data\\ before the move.\n"
            f"({_stamp_text(d)})\n"
        ).encode(),
        day,
        kind="text_note",
    )
    moved = []
    for word in ("counts", "fits"):
        target = d.file(
            f"{archive}/data/{stem}_{word}.csv",
            _csv(d, ("cell", word)),
            _at(day, days=90),
            kind="csv",
        )
        d.pair_undecidable("reference", readme, target)
        d.decline(target, "stage")
        moved.append(target)
    d.note(
        "moved_folders",
        {
            "readme": readme,
            "names": [f"data/{stem}_counts.csv", f"data/{stem}_fits.csv"],
            "now_at": sorted(moved),
        },
    )


# ---------------------------------------------------------------------------
# Programs.
# ---------------------------------------------------------------------------


def _beneath(d: Drive, paths: Sequence[str], root: str) -> None:
    """Beneath a program every reading defers: no stage, nothing an empty file means."""
    for path in paths:
        d.value("stage", path, None)
        d.decline(path, "empty_file")
    d.value("program", root, "program")
    for folder in {path.rpartition("/")[0] for path in paths}:
        if folder != root:
            d.decline(folder, "program", "experiment_folder")


def python_package(d: Drive, root: str, name: str, moment: dt.datetime) -> None:
    """A Python package as the Dictionary declares one: packaging, modules, tests, sample data."""
    text = _stamp_text(d)
    paths = [
        d.file(
            f"{root}/pyproject.toml",
            f'[project]\nname = "{name}"\nversion = "0.3.1"\n# {text}\n'.encode(),
            moment,
            kind="program_file",
        ),
        d.file(
            f"{root}/{name}_io.py",
            f"def read(path):\n    return path  # {text}\n".encode(),
            moment,
            kind="python_script",
        ),
        d.file(
            f"{root}/{name}_plot.py",
            f"def plot(values):\n    return values  # {text}\n".encode(),
            moment,
            kind="python_script",
        ),
        d.file(
            f"{root}/tests/test_{name}.py",
            f"def test_{name}():\n    assert True  # {text}\n".encode(),
            moment,
            kind="python_script",
        ),
        d.file(f"{root}/data/{name}_sample_raw.csv", _csv(d, ("x", "y")), moment, kind="csv"),
        d.file(f"{root}/data/{name}_001.tif", tiff_bytes(d.token() * 2), moment, kind="tiff_image"),
        d.file(f"{root}/data/{name}_002.tif", tiff_bytes(d.token() * 2), moment, kind="tiff_image"),
        d.file(f"{root}/data/{name}_results.csv", _csv(d, ("x", "fit")), moment, kind="csv"),
    ]
    _beneath(d, paths, root)
    d.note("programs", {"folder": root, "kind": "Python package"})


def matlab_toolbox(d: Drive, root: str, name: str, moment: dt.datetime) -> None:
    """A MATLAB toolbox: its contents file beside functions, and a variables file."""
    moment = d.stamp(moment, root)  # the date in the variables file is capped like its time
    text = _stamp_text(d)
    paths = [
        d.file(
            f"{root}/Contents.m",
            f"% {name} toolbox\n% version 1.2\n% {text}\n".encode(),
            moment,
            kind="matlab_script",
        ),
        d.file(
            f"{root}/{name}_fit.m",
            f"function out = {name}_fit(x)\nout = x; % {text}\nend\n".encode(),
            moment,
            kind="matlab_script",
        ),
        d.file(
            f"{root}/{name}_load.m",
            f"function out = {name}_load(p)\nout = p; % {text}\nend\n".encode(),
            moment,
            kind="matlab_script",
        ),
        d.file(
            f"{root}/{name}_defaults.mat",
            mat_file_bytes(moment, "GLNXA64") + d.token(),
            moment,
            kind="mat_file",
        ),
    ]
    _beneath(d, paths, root)
    d.note("programs", {"folder": root, "kind": "MATLAB toolbox"})


_FIJI_PLUGINS = ("Cell_Counter", "Colocalization_Threshold", "Analyze_Particles", "Stack_Sorter")
_FIJI_LUTS = ("Fire", "Grays", "Green", "Red")


def fiji_application(d: Drive, root: str, moment: dt.datetime) -> None:
    """A copy of Fiji: its jars, plugins, lookup tables and macros side by side, as it installs.

    The jars are archives that hold a manifest only; the program would not start from them."""
    moment = d.stamp(moment, root)
    text = _stamp_text(d)
    paths = [
        d.file(f"{root}/jars/ij-1.54.jar", jar_bytes("ij", moment), moment, kind="program_jar"),
        d.file(
            f"{root}/jars/commons-math3-3.6.1.jar",
            jar_bytes("commons-math3", moment) + d.token(),
            moment,
            kind="program_jar",
        ),
    ]
    paths += [
        d.file(
            f"{root}/plugins/{plugin}.jar",
            jar_bytes(plugin, moment) + d.token(),
            moment,
            kind="program_jar",
        )
        for plugin in _FIJI_PLUGINS
    ]
    paths += [
        d.file(f"{root}/luts/{lut}.lut", bytes(range(256)) + d.token(), moment, kind="program_file")
        for lut in _FIJI_LUTS
    ]
    paths += [
        d.file(
            f"{root}/macros/StartupMacros.ijm",
            f'macro "Startup" {{ print("ready"); }} // {text}\n'.encode(),
            moment,
            kind="fiji_macro",
        ),
        d.file(
            f"{root}/macros/Montage.ijm",
            f'run("Make Montage...", "columns=2 rows=2"); // {text}\n'.encode(),
            moment,
            kind="fiji_macro",
        ),
    ]
    _beneath(d, paths, root)
    d.note("programs", {"folder": root, "kind": "Fiji application"})


# ---------------------------------------------------------------------------
# The shared folder, sequencing, and what is left in the corners.
# ---------------------------------------------------------------------------


def shared_folder(
    d: Drive, members: Sequence[Member], topics: Sequence[str], first: int, last: int, count: int
) -> None:
    """Protocols, meeting slides and an instrument log that belong to nobody in particular."""
    rng = d.random
    for number in range(count):
        year = rng.randint(first, last)
        day = dt.datetime(year, rng.randint(1, 12), rng.randint(1, 28), 11, tzinfo=dt.UTC)
        topic = topics[number % len(topics)]
        # Never a member who had not come yet or had left: a departed member writes nothing later.
        here = [member for member in members if member.first <= year <= member.last]
        author = rng.choice(here)
        choice = rng.choice(["protocol", "slides", "log"])
        if choice == "protocol":
            _document(
                d,
                author,
                f"shared/protocols/{topic} protocol {number}",
                rng.choice(["docx", "pdf"]),
                day,
                topic,
            )
        elif choice == "slides":
            _document(d, author, f"shared/lab meeting/{day:%Y%m%d} {topic}", "pptx", day, topic)
        else:
            path = f"shared/instrument logs/{topic} log {year} {number}.csv"
            d.file(path, _csv(d, ("time", "temperature", "count")), day, kind="csv")
            d.decline(path, "stage")


def sequencing_runs(d: Drive, member: Member, constructs: Sequence[str], count: int) -> None:
    """Sanger traces and the sequences read from them, one folder for each run of plasmids.

    Each construct has a forward and a reverse trace (``.ab1``) and the sequence read from the two
    (``.seq``, a text file), and a few have a plasmid map (``.dna``)."""
    rng = d.random
    made = 0
    run = 0
    while made < count:
        run += 1
        year = rng.randint(max(member.first, 2014), member.last)
        day = dt.datetime(year, rng.randint(1, 12), rng.randint(1, 28), 15, tzinfo=dt.UTC)
        folder = f"{member.folder}/sequencing/{day:%Y%m%d} run {run}"
        if d.taken(folder):
            continue
        for construct in rng.sample(list(constructs), min(len(constructs), rng.randint(3, 8))):
            traces = []
            for direction in ("forward", "reverse"):
                trace = d.file(
                    f"{folder}/{construct} {direction}.ab1",
                    ab1_bytes(d.token()),
                    _at(day, minutes=made),
                    kind="ab1_trace",
                )
                d.decline(trace, "stage", "replicate", "derivation", "reference")
                traces.append(trace)
                made += 1
            bases = "".join(rng.choice("ACGT") for _ in range(60))
            sequence = d.file(
                f"{folder}/{construct}.seq",
                f">{construct}\n{bases}\n# {_stamp_text(d)}\n".encode(),
                _at(day, hours=1),
                kind="sequence_text",
            )
            d.decline(sequence, "stage", "replicate", "derivation", "reference")
            made += 1
            if rng.random() < 0.3:
                plasmid = d.file(
                    f"{folder}/{construct} plasmid.dna",
                    dna_bytes(d.token()),
                    _at(day, hours=2),
                    kind="plasmid_map",
                )
                d.decline(plasmid, "stage", "replicate", "derivation", "reference")
                made += 1


def odds_and_ends(d: Drive, members: Sequence[Member], topics: Sequence[str], count: int) -> None:
    """A handful of files of kinds a laboratory keeps but seldom: video, vector graphics, Prism
    graphs, flow cytometry. Each sits in a member's own folder."""
    rng = d.random
    kinds = ("avi", "svg", "pzfx", "fcs")
    for number in range(count):
        member = members[number % len(members)]
        year = rng.randint(member.first, member.last)
        day = dt.datetime(year, rng.randint(1, 12), rng.randint(1, 28), 16, tzinfo=dt.UTC)
        form = kinds[number % len(kinds)]
        topic = topics[number % len(topics)]
        path = f"{member.folder}/misc/{topic} {number + 1}.{form}"
        if d.taken(path):
            continue
        token = d.token()
        if form == "avi":
            data = avi_bytes(token)
        elif form == "svg":
            data = svg_bytes(topic, token)
        elif form == "pzfx":
            data = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                f'<GraphPadPrismFile PrismXMLVersion="5.00"><!-- {token.hex()} -->'
                "</GraphPadPrismFile>\n"
            ).encode()
        else:
            data = fcs_bytes(token)
        d.file(path, data, day, kind=form)
        d.decline(path, "stage", "replicate", "derivation", "reference")


def pick_words() -> dict[str, tuple[str, ...]]:
    """The vocabulary lists the shapes draw from."""
    vocabulary = words()
    return {
        "cell_lines": vocabulary.biology["cell_lines"],
        "targets": (*vocabulary.biology["proteins_and_genes"], *vocabulary.dictionary["tags"]),
        "drugs": (*vocabulary.biology["drugs"][1:], *vocabulary.dictionary["treatments"][:2]),
    }
