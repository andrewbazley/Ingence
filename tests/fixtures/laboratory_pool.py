"""A laboratory pool written from invented names, to show defeat conditions on names nobody owns.

Every case is the smallest pool that shows one behaviour. The author wrote its expectations
before the pool was first indexed. A generated pool can show that a condition does what its
sentence says on the cases written for it. It cannot show that a condition is right for a
laboratory, because it holds only the cases its author wrote. Nothing here claims otherwise.

The stems are invented, and the other words are the Dictionary's own. The stems bind to nothing in
the shipped Dictionary, which a test holds. Every other word in a name is a surface of a shipped
sense: a stage, a condition, a reagent, a file role, or the one generic word ``data``. Folders are
named from the stems alone. Nothing from anybody's research appears.

The pool is a tripwire and not a target. Where the engine and an expectation disagree, the
disagreement is the finding, and the pool is not changed to make them agree.

Expectations are complete for what they name. For every rule or condition that an expectation
names, a member that no ``Concludes`` or ``Retracts`` names is one that the rule concludes nothing
about or leaves standing. A class, a template or a candidate that is not listed is not formed.

- ``Concludes``: a classifying rule concludes exactly these senses about a member.
- ``Retracts``: a defeat condition, as ``CONDITIONS`` states it, retracts a rule's claim about
  a member.
- ``Groups``: a grouping rule, with the parameters stated, forms a class of exactly these
  members. Only classes of two or more are listed.
- ``SharesTemplate``: members of one folder whose names are equal once their counters are
  masked, under any counter or under zero-padded counters only.
- ``Proposes``: the projection rule, drawing pairs within an index, proposes a derivation,
  blocked where a constraint refuses it.

The candidates assume the following. Within ``same_name_family``, the family is keyed with stage
words left out and holds only members whose family claim stands. Within
``same_name_identity``, a name is keyed on every component but its extension and its stage
words, compared within its folder, and holds only members whose identity claim stands.
Within ``parent``, the files of one folder are paired. All three assume that
``program_from_extension`` retracts a program's stage and no other condition retracts a stage a
name states, that an acquisition format concludes the stage ``acquisition`` unless the name
states a later one, and that the source roles admit ``acquisition``, ``analysis`` and
``quantification``.

A symbolic link that the platform refuses to create, as Windows does without the privilege, is
left out. ``expectations_over`` leaves out what only that link could show.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from fixtures.instrument_files import nd2_file, tiff_file

if TYPE_CHECKING:
    from collections.abc import Collection

__all__ = [
    "CASES",
    "CONDITIONS",
    "STEMS",
    "Case",
    "Concludes",
    "Groups",
    "Member",
    "PoolWritten",
    "Proposes",
    "Retracts",
    "SharesTemplate",
    "expectations_over",
    "members",
    "named_members",
    "write_pool",
]

#: Invented, and a test holds that none binds to anything in the shipped Dictionary.
STEMS = ("brisa", "kelt", "mirdo", "pellan", "quolt", "saltra", "tamen", "trevik", "vorn", "zeph")

#: Every defeat condition an expectation names, each in one sentence. None is adopted as a rule.
#: A condition that is not yet a rule is named for what it reads.
CONDITIONS: dict[str, str] = {
    "program_from_extension": (
        "the file is a program: its extension binds a Python, shell, SQL or notebook format"
    ),
    "later_stage_from_name": "the member's own name states a stage past acquisition",
    "name_states_two_stages": "the name states two different stages",
    "format_contradicts_role": (
        "the format contradicts the role: a figure or a plot that is a table, or a table that "
        "is an image"
    ),
    "filesystem_noise_from_name": "the member is operating-system, editor or sync noise",
    "placeholder_from_entry_kind": "the member is a symbolic link or a stub",
    "emitted_name_from_siblings": (
        "three or more files in the member's folder, itself included, share its name but for "
        "a zero-padded counter"
    ),
}

Parameters = tuple[tuple[str, tuple[str, ...]], ...]

_STAGE = "stage_from_name"
_ROLE = "file_role_from_name"
_FAMILY = "same_name_family"
_IDENTITY = "same_name_identity"
_CONDITION = "same_experimental_condition"
_ACQUISITION = "acquisition_stage_from_format"
_PARENT = "parent"
_WITHOUT_STAGE_WORDS: Parameters = (("ignored_theme_kinds", ("stage",)),)
_CONDITION_AND_REAGENT_WORDS: Parameters = (("counted_theme_kinds", ("condition", "reagent")),)


@dataclass(frozen=True)
class Member:
    """One file of the pool, or a symbolic link to one, by its POSIX path under the pool."""

    path: str
    #: A link's target, relative to the link's own folder.
    link_to: str | None = None


@dataclass(frozen=True)
class Concludes:
    """A classifying rule concludes exactly these senses about a member."""

    rule: str
    member: str
    senses: tuple[str, ...]


@dataclass(frozen=True)
class Retracts:
    """A defeat condition, as ``CONDITIONS`` states it, retracts a rule's claim about a member."""

    condition: str
    rule: str
    member: str


@dataclass(frozen=True)
class Groups:
    """A grouping rule, with the parameters stated, forms a class of exactly these members."""

    rule: str
    members: frozenset[str]
    parameters: Parameters = ()


@dataclass(frozen=True)
class SharesTemplate:
    """Members of one folder whose names are equal once their counters are masked."""

    members: frozenset[str]
    zero_padded_only: bool


@dataclass(frozen=True)
class Proposes:
    """``projection_derives_from_acquisition`` proposes that ``target`` derives from ``source``.

    ``within`` is the index the pair is drawn within. ``blocked_by`` names the constraint that
    refuses the candidate, which is what makes its tier ``blocked`` rather than
    ``circumstantial``.
    """

    within: str
    source: str
    target: str
    blocked_by: str | None = None

    @property
    def tier(self) -> str:
        """The decision tier the candidate is expected at."""
        return "circumstantial" if self.blocked_by is None else "blocked"


Expectation = Concludes | Retracts | Groups | SharesTemplate | Proposes


@dataclass(frozen=True)
class Case:
    """The smallest pool showing one behaviour, and what the rules should make of it."""

    name: str
    shows: str
    members: tuple[Member, ...]
    expected: tuple[Expectation, ...]


@dataclass(frozen=True)
class PoolWritten:
    """Where the pool was written, what was written, and which links the platform refused."""

    root: Path
    written: tuple[str, ...]
    refused_links: tuple[str, ...]


def _files(*paths: str) -> tuple[Member, ...]:
    return tuple(Member(path) for path in paths)


def _stage(member: str, *senses: str) -> Concludes:
    return Concludes(_STAGE, member, tuple(sorted(senses)))


def _role(member: str, sense: str) -> Concludes:
    return Concludes(_ROLE, member, (sense,))


def _in_every_index(source: str, target: str) -> tuple[Proposes, Proposes, Proposes]:
    return (
        Proposes(_FAMILY, source, target),
        Proposes(_IDENTITY, source, target),
        Proposes(_PARENT, source, target),
    )


def _chain() -> Case:
    raw = "vorn-kelt/vorn_kelt_raw.tif"
    processed = "vorn-kelt/vorn_kelt_processed.tif"
    analysis = "vorn-kelt/vorn_kelt_analysis.csv"
    return Case(
        name="chain",
        shows="raw, processed and analysis files sharing their stems: the positive case",
        members=_files(raw, processed, analysis),
        expected=(
            _stage(raw, "raw"),
            _stage(processed, "processed"),
            _stage(analysis, "analysis"),
            Groups(_FAMILY, frozenset({raw, processed, analysis}), _WITHOUT_STAGE_WORDS),
            *_in_every_index(raw, processed),
            *_in_every_index(processed, analysis),
        ),
    )


def _programs() -> Case:
    raw = "brisa-tamen/brisa_tamen_raw.tif"
    processed = "brisa-tamen/brisa_tamen_processed.tif"
    script = "brisa-tamen/brisa_tamen_processed.py"
    shell_script = "brisa-tamen/brisa_tamen_analysis.sh"
    notebook = "brisa-tamen/brisa_tamen_quantification.ipynb"
    query = "brisa-tamen/brisa_tamen_summary.sql"
    programs = (script, shell_script, notebook, query)
    return Case(
        name="programs",
        shows=(
            "a script, a shell script, a notebook and a query named for stages, beside data "
            "sharing their stems"
        ),
        members=_files(raw, processed, *programs),
        expected=(
            _stage(raw, "raw"),
            _stage(processed, "processed"),
            _stage(script, "processed"),
            _stage(shell_script, "analysis"),
            _stage(notebook, "quantification"),
            _stage(query, "summary"),
            *(Retracts("program_from_extension", _STAGE, program) for program in programs),
            Groups(_FAMILY, frozenset({processed, script})),
            Groups(_CONDITION, frozenset({processed, script})),
            Groups(_FAMILY, frozenset({raw, processed, *programs}), _WITHOUT_STAGE_WORDS),
            *_in_every_index(raw, processed),
        ),
    )


def _two_stages() -> Case:
    raw = "quolt-zeph/quolt_zeph_raw.tif"
    both = "quolt-zeph/quolt_zeph_raw_processed.tif"
    return Case(
        name="two stages",
        shows="a name stating two stages, beside a name stating the first of them",
        members=_files(raw, both),
        expected=(
            _stage(raw, "raw"),
            _stage(both, "raw", "processed"),
            Retracts("name_states_two_stages", _STAGE, both),
            Groups(_FAMILY, frozenset({raw, both}), _WITHOUT_STAGE_WORDS),
            *_in_every_index(raw, both),
        ),
    )


def _counters() -> Case:
    frames = tuple(f"zeph-mirdo/zeph_mirdo_raw_{number:04d}.tif" for number in (1, 2, 3))
    from_frames = "zeph-mirdo/zeph_mirdo_processed.tif"
    replicates = tuple(f"mirdo-saltra/mirdo_saltra_raw_rep{number}.tif" for number in (1, 2, 3))
    from_replicates = "mirdo-saltra/mirdo_saltra_processed.tif"
    return Case(
        name="counters",
        shows=(
            "three zero-padded frames beside a processed file, and three hand-numbered "
            "replicates beside another"
        ),
        members=_files(*frames, from_frames, *replicates, from_replicates),
        expected=(
            *(_stage(raw, "raw") for raw in (*frames, *replicates)),
            _stage(from_frames, "processed"),
            _stage(from_replicates, "processed"),
            Groups(_FAMILY, frozenset(frames)),
            Groups(_FAMILY, frozenset(replicates)),
            Groups(_CONDITION, frozenset(frames)),
            Groups(_CONDITION, frozenset(replicates)),
            Groups(_FAMILY, frozenset({*frames, from_frames}), _WITHOUT_STAGE_WORDS),
            Groups(_FAMILY, frozenset({*replicates, from_replicates}), _WITHOUT_STAGE_WORDS),
            SharesTemplate(frozenset(frames), zero_padded_only=False),
            SharesTemplate(frozenset(frames), zero_padded_only=True),
            SharesTemplate(frozenset(replicates), zero_padded_only=False),
            *(Retracts("emitted_name_from_siblings", _FAMILY, frame) for frame in frames),
            *(Proposes(_FAMILY, replicate, from_replicates) for replicate in replicates),
            *(Proposes(_PARENT, frame, from_frames) for frame in frames),
            *(Proposes(_PARENT, replicate, from_replicates) for replicate in replicates),
        ),
    )


def _noise_and_a_link() -> Case:
    raw = "saltra-trevik/saltra_trevik_raw.csv"
    processed = "saltra-trevik/saltra_trevik_processed.csv"
    lock_file = "saltra-trevik/~$saltra_trevik_processed.csv"
    link = "pellan/saltra_trevik_raw.csv"
    return Case(
        name="noise and a link",
        shows=(
            "an editor's lock file, which the Dictionary files as an editor backup, and a "
            "symbolic link, each sharing a family's words"
        ),
        members=(
            Member(raw),
            Member(processed),
            Member(lock_file),
            Member(link, link_to="../saltra-trevik/saltra_trevik_raw.csv"),
        ),
        expected=(
            _stage(raw, "raw"),
            _stage(processed, "processed"),
            _stage(lock_file, "processed"),
            _stage(link, "raw"),
            Retracts("filesystem_noise_from_name", _FAMILY, lock_file),
            Retracts("filesystem_noise_from_name", _IDENTITY, lock_file),
            Retracts("placeholder_from_entry_kind", _FAMILY, link),
            Groups(_FAMILY, frozenset({raw, link})),
            Groups(_FAMILY, frozenset({processed, lock_file})),
            Groups(_CONDITION, frozenset({raw, link})),
            Groups(_CONDITION, frozenset({processed, lock_file})),
            Groups(_FAMILY, frozenset({raw, processed, lock_file, link}), _WITHOUT_STAGE_WORDS),
            *_in_every_index(raw, processed),
            Proposes(_PARENT, raw, lock_file),
        ),
    )


def _conditions() -> Case:
    control = ("vorn/pellan_vorn_control.csv", "vorn/tamen_zeph_control.csv")
    treated = ("vorn/kelt_saltra_treated_10nM.csv", "vorn/quolt_trevik_treated_10nM.csv")
    untreated = "vorn/brisa_mirdo_untreated.csv"
    mock = "vorn/pellan_zeph_mock.csv"
    stained = ("vorn/brisa_saltra_dapi.tif", "vorn/kelt_trevik_dapi.tif")
    inhibited = ("vorn/quolt_vorn_medium_inhibitor.csv", "vorn/mirdo_tamen_medium_inhibitor.csv")
    vehicle = "vorn/pellan_saltra_vehicle.csv"
    agonist = "vorn/kelt_zeph_agonist.csv"
    generic = ("vorn/mirdo_vorn_data_01.csv", "vorn/mirdo_vorn_data_02.csv")
    return Case(
        name="conditions",
        shows=(
            "files named for a condition or a reagent, and files sharing only their stems and a "
            "generic word; two control words spelled differently stay apart, because the key "
            "compares words and not senses"
        ),
        members=_files(
            *control, *treated, untreated, mock, *stained, *inhibited, vehicle, agonist, *generic
        ),
        expected=(
            Groups(_CONDITION, frozenset(control), _CONDITION_AND_REAGENT_WORDS),
            Groups(_CONDITION, frozenset(treated), _CONDITION_AND_REAGENT_WORDS),
            Groups(_CONDITION, frozenset(stained), _CONDITION_AND_REAGENT_WORDS),
            Groups(_CONDITION, frozenset(inhibited), _CONDITION_AND_REAGENT_WORDS),
            Groups(_CONDITION, frozenset(generic)),
            Groups(_FAMILY, frozenset(generic)),
            Groups(_FAMILY, frozenset(generic), _WITHOUT_STAGE_WORDS),
            SharesTemplate(frozenset(generic), zero_padded_only=False),
            SharesTemplate(frozenset(generic), zero_padded_only=True),
        ),
    )


def _file_roles() -> Case:
    figure_script = "quolt-pellan/quolt_pellan_figure.py"
    figure_table = "quolt-pellan/quolt_pellan_figure.csv"
    plot_image = "quolt-pellan/quolt_pellan_plot.tif"
    plot_notebook = "quolt-pellan/quolt_pellan_plot.ipynb"
    table = "quolt-pellan/quolt_pellan_table.csv"
    table_image = "quolt-pellan/quolt_pellan_table.tif"
    pairs = ((figure_script, figure_table), (plot_image, plot_notebook), (table, table_image))
    return Case(
        name="file roles",
        shows="figure, plot and table names on programs, images and tables",
        members=_files(figure_script, figure_table, plot_image, plot_notebook, table, table_image),
        expected=(
            _role(figure_script, "figure"),
            _role(figure_table, "figure"),
            _role(plot_image, "plot"),
            _role(plot_notebook, "plot"),
            _role(table, "table"),
            _role(table_image, "table"),
            Retracts("program_from_extension", _ROLE, figure_script),
            Retracts("program_from_extension", _ROLE, plot_notebook),
            Retracts("format_contradicts_role", _ROLE, figure_table),
            Retracts("format_contradicts_role", _ROLE, table_image),
            *(Groups(_FAMILY, frozenset(pair)) for pair in pairs),
            *(Groups(_CONDITION, frozenset(pair)) for pair in pairs),
            *(Groups(_FAMILY, frozenset(pair), _WITHOUT_STAGE_WORDS) for pair in pairs),
        ),
    )


def _analysis_steps() -> Case:
    analysis = "tamen-trevik/tamen_trevik_analysis.csv"
    quantification = "tamen-trevik/tamen_trevik_quantification.csv"
    summary = "tamen-trevik/tamen_trevik_summary.csv"
    return Case(
        name="analysis steps",
        shows=(
            "analysis, quantification and summary members of one family: two of the projection "
            "rule's own steps, starting from stages that revision 1's source roles leave out"
        ),
        members=_files(analysis, quantification, summary),
        expected=(
            _stage(analysis, "analysis"),
            _stage(quantification, "quantification"),
            _stage(summary, "summary"),
            Groups(_FAMILY, frozenset({analysis, quantification, summary}), _WITHOUT_STAGE_WORDS),
            *_in_every_index(analysis, quantification),
            *_in_every_index(quantification, summary),
        ),
    )


def _conflicting_conditions() -> Case:
    raw = "brisa-kelt/brisa_kelt_sham_raw.tif"
    processed = "brisa-kelt/brisa_kelt_dosed_processed.tif"
    return Case(
        name="conflicting conditions",
        shows=(
            "two files of one folder on a declared step, whose condition words name different "
            "senses, so no family holds both and only the folder pairs them"
        ),
        members=_files(raw, processed),
        expected=(
            _stage(raw, "raw"),
            _stage(processed, "processed"),
            Proposes(_PARENT, raw, processed, "identity_conflict_blocks_a_derivation"),
        ),
    )


def _one_rung() -> Case:
    first = "saltra-zeph/saltra_zeph_processed.tif"
    second = "saltra-zeph/saltra_zeph_processed_02.tif"
    return Case(
        name="one rung",
        shows="two processed members of one family, which no declared step joins",
        members=_files(first, second),
        expected=(
            _stage(first, "processed"),
            _stage(second, "processed"),
            Groups(_FAMILY, frozenset({first, second})),
            Groups(_CONDITION, frozenset({first, second})),
            Groups(_FAMILY, frozenset({first, second}), _WITHOUT_STAGE_WORDS),
        ),
    )


def _acquisition() -> Case:
    acquired = "trevik-brisa/trevik_brisa.nd2"
    processed = "trevik-brisa/trevik_brisa_processed.tif"
    processed_in_format = "trevik-brisa/trevik_brisa_processed.nd2"
    return Case(
        name="acquisition",
        shows=(
            "a file in an instrument's own format naming no stage, beside a processed file and a "
            "processed file saved in the instrument's format, all sharing their stems"
        ),
        members=_files(acquired, processed, processed_in_format),
        expected=(
            Concludes(_ACQUISITION, acquired, ("acquisition",)),
            Concludes(_ACQUISITION, processed_in_format, ("acquisition",)),
            Retracts("later_stage_from_name", _ACQUISITION, processed_in_format),
            _stage(processed, "processed"),
            _stage(processed_in_format, "processed"),
            Groups(_FAMILY, frozenset({processed, processed_in_format})),
            Groups(_CONDITION, frozenset({processed, processed_in_format})),
            Groups(
                _FAMILY,
                frozenset({acquired, processed, processed_in_format}),
                _WITHOUT_STAGE_WORDS,
            ),
            *_in_every_index(acquired, processed),
            *_in_every_index(acquired, processed_in_format),
        ),
    )


CASES: tuple[Case, ...] = (
    _chain(),
    _programs(),
    _two_stages(),
    _counters(),
    _noise_and_a_link(),
    _conditions(),
    _file_roles(),
    _analysis_steps(),
    _conflicting_conditions(),
    _one_rung(),
    _acquisition(),
)


def members() -> tuple[Member, ...]:
    """Return every member of the pool, case by case, in the order they are written."""
    return tuple(member for case in CASES for member in case.members)


def named_members(expectation: Expectation) -> frozenset[str]:
    """Return every member an expectation names."""
    if isinstance(expectation, (Concludes, Retracts)):
        return frozenset({expectation.member})
    if isinstance(expectation, (Groups, SharesTemplate)):
        return expectation.members
    return frozenset({expectation.source, expectation.target})


def expectations_over(written: Collection[str]) -> tuple[Expectation, ...]:
    """Return every expectation, over only the members that were written.

    A class or a template that lost a member keeps the rest, where two or more remain. Any
    other expectation naming a member that was not written is left out whole.
    """
    present = frozenset(written)
    kept: list[Expectation] = []
    for case in CASES:
        for expectation in case.expected:
            if isinstance(expectation, Groups):
                remaining = expectation.members & present
                if len(remaining) >= 2:
                    kept.append(Groups(expectation.rule, remaining, expectation.parameters))
            elif isinstance(expectation, SharesTemplate):
                remaining = expectation.members & present
                if len(remaining) >= 2:
                    kept.append(SharesTemplate(remaining, expectation.zero_padded_only))
            elif named_members(expectation) <= present:
                kept.append(expectation)
    return tuple(kept)


def write_pool(root: Path) -> PoolWritten:
    """Write every member under ``root``, which must be absent or empty.

    The function never writes over anything. A pool written into a folder that already holds
    files would mix the generated names with whatever was there, and every figure measured on it
    would describe both. So a folder that is not empty is refused before anything is written.
    """
    if root.exists() and any(root.iterdir()):
        message = f"{root} is not empty, and a pool is never written over anything"
        raise FileExistsError(message)
    written: list[str] = []
    refused: list[str] = []
    for ordinal, member in enumerate(members(), start=1):
        target = root.joinpath(*PurePosixPath(member.path).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        if member.link_to is None:
            target.write_bytes(_content(member.path, ordinal))
            written.append(member.path)
            continue
        try:
            os.symlink(os.path.join(*PurePosixPath(member.link_to).parts), target)
        except (OSError, NotImplementedError):
            refused.append(member.path)
            continue
        written.append(member.path)
    return PoolWritten(root=root, written=tuple(written), refused_links=tuple(refused))


def _content(path: str, ordinal: int) -> bytes:
    """Return a member's bytes: the smallest file of its format, unlike every other member's."""
    suffix = PurePosixPath(path).suffix
    if suffix == ".tif":
        return tiff_file(description=str(ordinal))
    if suffix == ".nd2":
        return nd2_file(julian_day=2460000.5 + ordinal / 1000, frames=1)
    if suffix == ".csv":
        return f"a,b\n{ordinal},{ordinal}\n".encode()
    if suffix == ".py":
        return f"print({ordinal})\n".encode()
    if suffix == ".sh":
        return f"#!/bin/sh\necho {ordinal}\n".encode()
    if suffix == ".sql":
        return f"SELECT {ordinal};\n".encode()
    if suffix == ".ipynb":
        cell = {"cell_type": "markdown", "metadata": {}, "source": [str(ordinal)]}
        notebook = {"cells": [cell], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
        return json.dumps(notebook, indent=1, sort_keys=True).encode()
    message = f"no content is written for {suffix!r}"
    raise ValueError(message)
