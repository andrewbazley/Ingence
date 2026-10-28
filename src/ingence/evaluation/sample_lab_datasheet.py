# ruff: noqa: E501
# The datasheet is markdown prose in a template; its lines are written for a reader of the rendered text.
"""The datasheet written beside every synthetic sample-laboratory collection.

Its questions are those of "Datasheets for Datasets" (Gebru et al., 2021), in the order the paper
gives them: motivation, composition, collection process, preprocessing, uses, distribution and
maintenance. It is rendered from the collection's own manifest, so every figure in it is the figure
of the collection it sits beside, and no tier is described with another tier's numbers.

It is written in plain words for a person who may never have heard of the engine, and it states what
the collection is not: it is not real data, and how real a drive it looks is one person's model of a
drive.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["RELEASE", "Release", "datasheet"]


# ---------------------------------------------------------------------------------------------
# THE VALUES THAT CHANGE FROM ONE RELEASE TO THE NEXT: edit them here, and nowhere else.
#
# Every datasheet this module renders takes these. The angle-bracket fields are left for the person
# who archives a collection (a contact, where errata are posted, a DOI): until they are filled in,
# every generated datasheet carries them as they stand. The engine runs state which collections the
# engine has read and whether each was scored against the key written with it; raise the date
# whenever the runs are repeated, and add a collection only after the engine has read it.
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Release:
    """What a datasheet says that is true of a release and not of a collection."""

    author: str = "The Ingence project"
    funder: str = "The Astera Institute"
    #: (tier, seed, scored against the key written with it) for each collection the engine has read.
    engine_runs: tuple[tuple[str, int, bool], ...] = (("S", 1, True),)
    engine_runs_on: str = "13 October 2026"
    archived: str = "<tiers and seeds archived>"
    uses_list: str = "<where uses are listed>"
    archive_record: str = "<DOI and date of distribution, once archived>"
    contact: str = "hello@ingence.org"
    errata: str = "<where corrections to the key are posted>"
    older_versions: str = "<how older versions are kept, and for how long>"
    issue_tracker: str = "<issue tracker address>"


RELEASE = Release()

_KIND_WORDS = {
    "nd2": "Nikon ND2 acquisitions (built from the format's structure, invented pixels)",
    "czi_shaped_header": "files shaped like Zeiss CZI acquisitions (a header only; not readable as CZI)",
    "lif_shaped_header": "files shaped like Leica LIF acquisitions (a header only; not readable as LIF)",
    "oib_shaped_container": "files shaped like Olympus OIB acquisitions (an empty container; not readable as OIB)",
    "plate_table": "plate reader exports (CSV and tab-separated text)",
    "txt_table": "tab-separated text tables",
    "xml_sidecar": "XML records an instrument writes beside an acquisition",
    "json_sidecar": "JSON records an instrument writes beside an acquisition",
    "jpg": "JPEG pictures (a header and no scan data; not viewable)",
    "gif": "GIF pictures",
    "zip": "ZIP archives of tables",
    "doc": "older Word files (an empty container; not readable by Word)",
    "xls": "older Excel files (an empty container; not readable by Excel)",
    "ppt": "older PowerPoint files (an empty container; not readable by PowerPoint)",
    "fiji_macro": "Fiji macros (never run)",
    "program_jar": "Java archives of a Fiji folder (a manifest only)",
    "ab1_trace": "sequencing traces (a header only)",
    "sequence_text": "sequence text files",
    "plasmid_map": "plasmid map files (a header only)",
    "avi": "video files (a header and no frame)",
    "svg": "vector figures",
    "pzfx": "Prism graph files (a header only)",
    "fcs": "flow cytometry files (a header and no event)",
    "tiff_stack": "multi-page TIFF stacks",
    "tiff_image": "single-page TIFF images",
    "xlsx_plate_export": "spreadsheet exports shaped like a plate reader's",
    "csv": "CSV tables",
    "csv_empty": "empty CSV outputs",
    "xlsx": "XLSX workbooks",
    "python_script": "Python scripts (never run)",
    "matlab_script": "MATLAB scripts (never run)",
    "notebook": "Jupyter notebooks (never executed, no outputs)",
    "pdf": "PDF documents",
    "docx": "DOCX documents",
    "pptx": "PPTX presentations",
    "png": "PNG figures",
    "text_note": "readme and notes files",
    "program_file": "packaging and license files of installed programs",
    "mat_file": "MATLAB data file headers",
    "noise_ds_store": "Finder folder files (.DS_Store)",
    "noise_thumbs_db": "Windows thumbnail caches (Thumbs.db)",
    "noise_office_lock": "Office editor lock files (names beginning ~$)",
    "tiff_named_csv": "TIFF images saved under a .csv name",
    "png_named_jpg": "PNG images saved under a .jpg name",
}


def _table(counts: Mapping[str, int]) -> str:
    lines = ["| Kind of file | Files |", "|---|---|"]
    for kind, number in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| {_KIND_WORDS.get(kind, kind)} | {number:,} |")
    return "\n".join(lines)


def datasheet(
    manifest: Mapping[str, Any], sections: Mapping[str, str], release: Release = RELEASE
) -> str:
    """Render the datasheet of the collection a manifest describes."""
    counts = manifest["counts"]
    tier = manifest["tier"]
    seed = manifest["seed"]
    generator = manifest["generator"]
    links = manifest["links"]
    first = str(manifest["modified"]["first"])[:10]
    last = str(manifest["modified"]["last"])[:10]
    mess = manifest["mess"]
    unscored = "\n".join(
        f"- **{name.replace('_', ' ').replace('members folder', 'member' + chr(39) + 's folder')}** "
        f"({mess.get(name, 0):,} here): {what}"
        for name, what in sections.items()
    )
    planned = int(links["planned"])
    noun = "broken symbolic link" if planned == 1 else "broken symbolic links"
    if not planned:
        links_line = "No symbolic links were planned."
    elif links["made"]:
        links_line = f"{planned} {noun} {'was' if planned == 1 else 'were'} planned and made."
    else:
        links_line = (
            f"{planned} {noun} {'was' if planned == 1 else 'were'} planned; on the machine that "
            f"generated this copy they were not made ({links['note']}), and the manifest says so."
        )
    empty = int(counts["empty_folders"])
    empty_line = f"{empty:,} of them {'is' if empty == 1 else 'are'} empty"
    read = {(name, number): scored for name, number, scored in release.engine_runs}
    single = len(read) == 1
    if (tier, seed) not in read:
        used = "This collection is not one of them: the engine has not read it."
    else:
        used = "This collection is that one." if single else "This collection is one of them."
    runs = " and ".join(f"tier {name}, seed {number}" for name, number in read)
    only_read = [
        f"tier {name}, seed {number}" for (name, number), scored in read.items() if not scored
    ]
    if not only_read:
        scoring = (
            "It was scored against the key written with it."
            if single
            else "Each was scored against the key written with it."
        )
    else:
        scoring = (
            " and ".join(only_read)
            + (" was" if len(only_read) == 1 else " were")
            + " not scored against the key written with it; the others were."
        )
    unread_tiers = [name for name in ("M", "L") if name not in {key[0] for key in read}]
    names = {"M": "middle", "L": "large"}
    unread = (
        "The "
        + " and ".join(names[name] for name in unread_tiers)
        + " tier"
        + ("s have" if len(unread_tiers) > 1 else " has")
        + " not been read by the engine."
        if unread_tiers
        else "Every tier has been read by the engine."
    )
    rewritten = manifest.get("vocabulary")
    vocabulary_note = (
        ""
        if rewritten is None
        else (
            "\n\n**Was the vocabulary tier applied?** Yes. "
            "In an experiment folder that can be rewritten, each subject-matter word had a "
            f"chance of {rewritten['fraction']} of being written the way laboratories write it "
            "(a slip, initials, two words run together, a code, a second spelling of one "
            f"condition, a change of case or separator), drawn with seed {rewritten['seed']}. "
            f"{rewritten['experiments_left_alone']:,} of {rewritten['experiments']:,} experiment "
            "folders cannot be rewritten, because a reference or a copy of a text file crosses "
            f"their boundary, and are left as they were. {rewritten['rewrites']:,} words in "
            f"{rewritten['experiments_rewritten']:,} folders were rewritten. Every spelling is "
            "made from a word of the collection's word list. The key carries a words family "
            "that says what each spelling means and which spellings are one thing."
        )
    )
    return f"""# Datasheet: a sample collection, tier {tier}, seed {seed}

This datasheet answers the questions of "Datasheets for Datasets" (Gebru et al., 2021, Communications of
the ACM 64(12): 86-92), in the order the paper gives them. It describes one generated collection:
tier **{tier}**, seed **{seed}**, written by `{generator["name"]}` version {generator["version"]}. Its figures are this
collection's own. Fields in angle brackets are to be filled in before the collection is archived.

**In short.** A shared drive of an invented research laboratory over several years, with several
invented people, written by a program from a seed. Every name, value and byte is invented. It is meant
for trying software that reads untidy folders, for tutorials, for tests and for benchmarks. It is not real
data, and nothing in it is a measurement.

## Motivation

**For what purpose was the collection created?** Real laboratory drives cannot be shared: they hold
people's names, unpublished work and files nobody can vouch for. Tools that read such drives (to find
which experiment a file belongs to, what produced it, what repeats it and what is missing) need
something free to try, to test and to measure against. This collection is that: the shapes of a real
drive, with nothing real in it, and a key that says what a person who knew the laboratory would say about
it.

**Who created it, and on whose behalf?** {release.author} made the generator and
chose the tiers and seeds.

**Who funded it?** {release.funder} funded the work.

## Composition

**What do the instances represent?** Files and folders on a drive, and symbolic links. The data
pool, in the folder `pool/`, holds {counts["files"]:,} files in {counts["folders"]:,} folders ({empty_line}),
{manifest["bytes"]:,} bytes in all, for {counts["members"]} invented members and
{counts["experiment_folders"]:,} experiment folders. Every file is small: the bytes follow each format's
structure, and the pixels and numbers are invented.

{_table(counts["by_kind"])}

{links_line}

**Is it all possible instances, or a sample? Are there recommended splits?** It is the whole of what the
generator writes for this tier and seed, and it is not a sample of any real drive: it is one draw from the
generator's model of a drive. The seed picks the draw, and the tier picks the size: S for tutorials,
documentation examples and tests, M for demonstrations and screenshots, L for benchmarks of speed and
memory. There are no training, validation or test splits, because nothing here is meant for learning. If a
held-out check is wanted, generate the same tier with another seed; the seed is the only split. Which
tiers and seeds are archived is stated in the archive record: {release.archived}.

**What does the drive look like?** Each member keeps a top-level folder named for them, active over their
own span of years (several overlap). A member who left the laboratory has a folder in `Lab Alumni`: it stops
early and holds an old-files folder and a handover note, and nothing in it is dated, or authored, later.
Experiments are dated folders in each member's own naming habit, which changes over the years and slips
from folder to folder. The date is the day of the acquisition, written in one of seven ways
(`12Oct2026`, `Oct-12-2026`, `20261012`, `10-12-2026`, `2026_10_12`, `10-12-26`, `12-10-26`), first or last
in the name, with spaces, underscores or hyphens. Inside are raw acquisitions, processed images and analysis
tables, in folders named for their stage or all in one. A replicate is labeled `rep2`, `R2`, `02` or with
the instrument's counter (`_002`), and the labels in one experiment may differ slightly. A raw file keeps the
name its instrument gave it (`Image0012`), or a name that says the sample and the condition followed by the
instrument's counter (`20261012_HeLa_nocodazole-10uM_001`). An experiment has a design (a control and a
treatment at some doses, or over some times) with one to three replicates of each condition, and a few
experiments have many more. Some experiments have their data in another member's folder or their analysis in
a folder of the member's own. The collection is built to hold experiments that were abandoned, analysis whose
acquisitions are gone, files saved again with `_2`, `_new`, `_edit`, `_fixed`, `_final` or a date, files
saved again in other folders under their own names or under a copy-of name, folders copied in part into
a backup folder, a readme that names files by a path from before a folder moved, links that point at nothing,
raw files an archiving system replaced by links to another server, programs installed inside data folders
(a Python package, a MATLAB toolbox, a copy of Fiji), and the files an operating system or an editor leaves.
How many of each this collection holds is counted in the list below.

**Is there a label or target?** Yes: the answer key, in `answer_key.json`, in the format the Ingence
evaluation reads (which stage a file is at; which folders are experiments; which files are replicates,
copies or in one group; which file was made from which; which file names which; which folders are
programs; which files are empty; which names state a date). Where the files alone cannot settle an
answer the key says so, and an abstention is the right output. Where a person could reasonably say either
the key leaves the file out of the score. What the scorer does not yet score is in
`answer_key_unscored.json`:

{unscored}

The key is never inside the data pool, and the relationships between instances (copies, replicates,
which file was made from which, which folders belong together) are made explicit in it.

**Is any information missing?** By design: acquisitions are gone from some experiments, some analyses were
never run, and some names refer to files that are not there (the counts above say how many here). The files
shaped like a Zeiss CZI, a Leica LIF or an Olympus OIB, and the files shaped like older Office files, hold only
a header or an empty container. They are not readable by the programs that save such files, and no image can
be opened from them. A JPEG, an AB1 trace, a plasmid map, a video, a Prism file and a flow cytometry file hold
the start of their format and no data.

**Are there errors, sources of noise or redundancies?** Noise and redundancy are the point: copies, near-copies,
saved-again files, empty files and files an operating system left. The key can also be wrong. It is the
generator's record of what it wrote, together with one person's judgement of what the files alone can
settle. Its "undecidable" calls (a six-digit date, a file named by its instrument, a stage no name or
folder gives) and its "declined" calls (documents, version chains, files saved again under a copy-of name, folders copied into
backup) are that person's judgement, not measurements; another reader could draw the lines
elsewhere. Where the key and a program disagree, either may be the one in error. Mistakes found in the key
are errata (see Maintenance).

**Is it self-contained?** Yes. Nothing in it links to anything outside it, and it needs no other file to
be read. Its key, manifest and this datasheet sit beside the pool. It can be regenerated from the Ingence
package, the tier and the seed.

**Does it contain confidential information, offensive content, or anything about real people?** No. Members'
names are ordinary first and family names put together at random from short lists, and a real person may
share one by chance; no file holds a real person's name, a real count, a real date of an experiment or a
real organism; nobody can be identified from it, because there are no people in it. The cell lines, genes,
proteins, stains, drugs and techniques are common biology words, and the other words are the shipped
Dictionary's or plain English. The dates run from {first} to {last} and are drawn from the seed. No file, text or
image of any vendor is in it. A format's name (ND2, CZI, TIFF, DOCX) says what a file's structure follows;
the Office files name LibreOffice as the program that made them, the MATLAB files name a MATLAB platform,
and the plasmid-map files start with the signature that SnapGene's format uses.

## Collection process

**How was it acquired?** It was generated. A seeded random stream chose the members, their naming
habits, their projects and the order of events; each file's bytes were then built from its format's
structure with invented values, and each file's modification time was set from the seed, in a fixed range
of years ending no later than 2025. The key is a record of what the generator
did and of what a person would say from the files.

**What is the time frame?** The files claim modification times from {first} to {last}; the collection was
generated in a moment.

**Was there an ethical review?** None was needed: it holds no data about people or animals.

## Preprocessing, cleaning and labeling

None. The generator writes the final bytes. There is no raw version from which this one was cleaned. The
software that writes it is the Ingence package (`python -m ingence.evaluation sample-lab --tier {tier} --seed {seed} <folder>`).{vocabulary_note}

**Does the same tier and seed always write the same collection?** The generator is built to write the same
files, byte for byte, with the same names and modification times, for the same tier and seed. That has been
tested on one Mac (macOS, with Python 3.12, from different working folders, time zones and
locales) and has not been tested on Windows or Linux. On a FAT or exFAT drive, and after zipping, file
times are rounded to two seconds. A zip file also stores times with no time zone, so files can shift by hours
when it is unpacked in another zone, and symbolic links may not survive it: archive the collection as a tar
file, which keeps the exact times and the links. The manifest's digest uses whole seconds, so compare after
unpacking with that in mind. The times a filesystem keeps for itself, such as creation time, are its own.

## Uses

**Has it been used already?** Yes, a little. The Ingence engine read {runs} of this
generator version on {release.engine_runs_on}, while the engine was in development, to see that it reads such a
drive. {scoring} {used} This datasheet gives no figures from those
runs: a collection is a tripwire, never a target, and no rule is tuned to make these numbers look better.
{unread} Other uses, as they are reported, will be listed in the
archive record: {release.uses_list}.

**What is it for?** Trying software on a drive that was never tidied; tutorials and documentation examples (tier S);
demonstrations and screenshots (tier M); and benchmarks of speed and memory (tier L); testing that a tool
makes no change to what it reads; teaching what a laboratory's folders can look like.

**What is it not fit for?**

- Any scientific conclusion. There is no experiment, no organism, no result and no measurement here.
- Judging how well a tool will do on a real laboratory. How real this drive looks is one person's model of
  a drive, built from how drives tend to be named and arranged. A real laboratory will differ, and a tool
  that does well here may do worse there.
- Image analysis. The pixels are invented, and the files shaped like another microscope's cannot be opened.
- Training or testing anything that is meant to learn people's behavior. The people are not people.

## Distribution

**How is it distributed, and under what license?** The collection is dedicated to the public domain under
CC0 1.0 Universal, with no intellectual-property or export restrictions known. It is intended to be archived
on Zenodo: {release.archive_record}. It can also be regenerated exactly from the
generator and the seed (see the limits above).

## Maintenance

**Who maintains it, and how can they be reached?** The Ingence project. Contact: {release.contact}.

**Will it be updated, and how are errors corrected?** The tier and the seed fix the collection. A change
that would write anything different raises the generator's version, which this datasheet and the manifest
state (`{generator["name"]}`, version {generator["version"]}), and a collection of another version is another
collection. The engine's own version is recorded apart from it in the manifest and does not change the
pool. Errata: {release.errata}.

**Do older versions stay available? Can others contribute?** {release.older_versions}. There is no retention limit tied to people, since there are none in it. Problems and suggestions
can be sent through the project's issue tracker: {release.issue_tracker}.
"""
