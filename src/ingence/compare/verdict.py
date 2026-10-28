"""Reading the counts: same files or not, organised the same or not, and how they got here.

``Thresholds`` holds every cut the verdict makes. Each cut is a parameter with its value shown
beside the verdict, and no default is measured. The verdict is a rendering for the History page and
a run-level notice, never a claim about any file. Nothing about any file changes because of it.

The module answers two questions separately: whether the organisation changed, and how the files
got here (the same drive from a different computer, or the same files on a different drive).

What changed in the organisation (the verdict):

========================  ===================================================================
``different_files``       too few files matched to call these the same files at all
``same_organisation``     every matched file kept its path: every earlier folder is where it
                          was, holding what it held
``files_renamed``         every earlier folder is where it was, but some files in them were
                          renamed: no folder moved
``relocated_whole``       paths changed, but one rewrite explains nearly every move and every
                          folder kept its files: the tree was nested, or a top folder renamed
``folders_renamed``       folders kept their files together, under new names or places
``partly_reorganised``    some folders split or merged; the rest intact (they are named)
``reorganised``           pairs of files that shared a folder mostly no longer do, or pairs
                          sharing a folder now mostly did not before (folders pooled), and
                          most files now sit among different files than before
========================  ===================================================================

No share of files makes a moved folder "the same organisation". One folder of ten renamed among
six hundred files is 98 percent of paths kept, and on a large census thousands of moved files are
a small share. A note about that folder has lost its place either way. Pairs alone do not make a
reorganisation either. A folder of a thousand files holds most of a census's pairs, so splitting
it in two among a hundred untouched folders halves the pairs while nine folders in ten are as they
were. ``reorganised_files`` asks how many files the change touched.

How the files got here (the context), from roots and birth times:

========================  ===================================================================
``same_tree``             one root, one place: the same drive, scanned again (whatever was moved
                          on it: a move within a volume keeps every birth)
``other_machine``         a different mount path or root, the same births: the same volume
                          seen from another computer (births survive a mount, not a copy)
``copied``                births later on one side, and later than each file's own modification:
                          a copy (``copied_in_bulk``'s signal), and which side looks the copy
``unclear``               the evidence points both ways or nowhere; said, not guessed
========================  ===================================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from ingence.compare.fingerprints import DEFAULT_MTIME_GRAIN_NS

if TYPE_CHECKING:
    import duckdb

    from ingence.compare.match import MatchSummary
    from ingence.compare.structure import Structure

__all__ = [
    "DEFAULT_THRESHOLDS",
    "FIRST_AND_SECOND",
    "HISTORY",
    "NAMED_IN_NOTICE",
    "NOTICED",
    "Context",
    "Sides",
    "Thresholds",
    "Verdict",
    "birth_evidence",
    "changed_folders",
    "notice",
    "read_context",
    "read_verdict",
    "where_it_went",
]


class Verdict(StrEnum):
    DIFFERENT_FILES = "different_files"
    SAME_ORGANISATION = "same_organisation"
    FILES_RENAMED = "files_renamed"
    RELOCATED_WHOLE = "relocated_whole"
    FOLDERS_RENAMED = "folders_renamed"
    PARTLY_REORGANISED = "partly_reorganised"
    REORGANISED = "reorganised"


class Context(StrEnum):
    SAME_TREE = "same_tree"
    OTHER_MACHINE = "other_machine"
    COPIED = "copied"
    UNCLEAR = "unclear"


#: The verdicts the History page opens on ("hey we noticed this"): a folder moved. The page also
#: opens on any verdict where a decision's folder is gone and its files were found elsewhere
#: (``report``). A file renamed inside a folder that stayed moves no decision's place.
NOTICED = frozenset(
    {
        Verdict.RELOCATED_WHOLE,
        Verdict.FOLDERS_RENAMED,
        Verdict.PARTLY_REORGANISED,
        Verdict.REORGANISED,
    }
)


@dataclass(frozen=True, slots=True)
class Thresholds:
    """Every cut the verdict makes. Every default is unmeasured."""

    #: Of the smaller side's files, the share that must match for "the same files".
    same_files: float = 0.5
    #: Of moved files, the share one rewrite must explain for "relocated whole".
    one_rewrite: float = 0.95
    #: Pairs still together over pairs together before (and now over before, reversed) for
    #: "folders kept their files".
    together: float = 0.95
    #: Of matched files, the share that must have moved for "relocated whole": one folder renamed
    #: among many is that folder renamed, not the tree moved.
    whole_tree_moved: float = 0.9
    #: Below this share of pairs still together (or of pairs together now that were before),
    #: "reorganised", where ``reorganised_files`` also holds.
    reorganised_below: float = 0.6
    #: More than this share of matched files must now sit among different files than before
    #: (their old folder split, pooled or joined) for "reorganised", which says "most files";
    #: below it the change is "partly reorganised" and the folders are named.
    reorganised_files: float = 0.5
    #: Of matched files with births on both sides, the share born later on one side (and after
    #: their own modification) for "copied", or born together for "another machine".
    copied_births: float = 0.8
    #: Of the larger side's files, the share that must be on the other side before the notice stops
    #: saying that only a part of it is (a backup of some of a folder is a copy of a part).
    whole_coverage: float = 0.98


#: The defaults, as one shared value (a frozen dataclass, so sharing it is safe).
DEFAULT_THRESHOLDS = Thresholds()


def _share(part: int, whole: int) -> float:
    return part / whole if whole else 1.0


def read_verdict(
    summary: MatchSummary, structure: Structure, *, thresholds: Thresholds = DEFAULT_THRESHOLDS
) -> tuple[Verdict, list[str]]:
    """The verdict and, beside it, each count it rested on in plain words."""
    because: list[str] = []
    smaller = min(summary.files.values()) if summary.files else 0
    because.append(
        f"{summary.matched_total:,} of the smaller side's {smaller:,} files matched a file "
        "on the other side"
    )
    if summary.matched_without_size:
        because.append(
            f"{summary.matched_without_size:,} of them matched by their place or their folder "
            "and name alone, without their sizes, because "
            + _no_sizes_named(summary.states_no_sizes, FIRST_AND_SECOND)
            + " (a weaker kind of match: it does not show the same bytes)"
        )
    if smaller == 0:
        # A side with no files has nothing to be the same as: a "backup" holding only shortcuts, or
        # nothing, is not a copy, and a share of nothing over nothing must not read as all of it.
        because.append("one side holds no files")
        return Verdict.DIFFERENT_FILES, because
    if _share(summary.matched_total, smaller) < thresholds.same_files:
        return Verdict.DIFFERENT_FILES, because

    because.append(
        f"{structure.paths_kept:,} of {structure.matched:,} matched files kept their path"
    )
    if structure.paths_kept == structure.matched:
        return Verdict.SAME_ORGANISATION, because
    because.append(
        f"{structure.folders_moved:,} of {len(structure.folders):,} earlier folders had files "
        "that are now in a folder of another path"
    )
    if not structure.folders_moved:
        because.append(
            f"{structure.files_renamed_in_place:,} files were renamed inside their folders"
        )
        return Verdict.FILES_RENAMED, because

    parent = structure.levels.get(1, {})
    before = int(parent.get("pairs_together_before", 0))
    still = int(parent.get("pairs_still_together", 0))
    now = int(parent.get("pairs_together_now", 0))
    kept = _share(still, before)
    fresh = _share(still, now)
    because.append(f"{still:,} of {before:,} pairs of files that shared a folder still do")
    because.append(f"{still:,} of {now:,} pairs that share a folder now shared one before")

    top, moved = structure.dominant_rewrite_share
    if structure.rewrites:
        old, new, _ = structure.rewrites[0]
        because.append(
            f"one rewrite, '{old or '(root)'}' to '{new or '(root)'}', "
            f"explains {top:,} of {moved:,} moves"
        )
    split = sum(1 for fate in structure.folders if fate.fate == "split")
    because.append(
        f"{split:,} of {len(structure.folders):,} old folders were split; "
        f"{len(structure.merged):,} new folders merged several"
    )
    # "Every folder kept its files" is what the notice says of these two verdicts, so it is what
    # they require. The pair share alone would let three files leaving one folder of twelve (95
    # percent of pairs still together) read as "the whole tree moved as one piece".
    if (
        kept >= thresholds.together
        and fresh >= thresholds.together
        and not split
        and not (structure.merged)
    ):
        if (
            _share(top, moved) >= thresholds.one_rewrite
            and _share(moved, structure.matched) >= thresholds.whole_tree_moved
        ):
            return Verdict.RELOCATED_WHOLE, because
        return Verdict.FOLDERS_RENAMED, because
    # Both directions are read, because a reorganisation can destroy grouping two ways: scattering
    # a folder's files (pairs no longer together) or pooling many folders into one (pairs together
    # now that never were). When every experiment is pooled into one folder by file type, every
    # old pair stays together and many new ones are made. A one-directional reading would call
    # that "partly reorganised".
    because.append(
        f"{structure.files_regrouped:,} of {structure.matched:,} matched files now sit among "
        "different files than before"
    )
    if (
        min(kept, fresh) < thresholds.reorganised_below
        and _share(structure.files_regrouped, structure.matched) > thresholds.reorganised_files
    ):
        return Verdict.REORGANISED, because
    return Verdict.PARTLY_REORGANISED, because


def birth_evidence(
    connection: duckdb.DuckDBPyConnection, *, grain_ns: int = DEFAULT_MTIME_GRAIN_NS
) -> dict[str, int]:
    """Over matched files with births on both sides: same birth, later on the right, later on the
    left, and later that is also after that side's own modification (a copy's signature).

    Two births closer than ``grain_ns`` are the same moment: the grain modification times are
    compared at (``fingerprints``), because a copy between filesystems rounds a birth as it rounds
    a modification time."""
    row = connection.execute(
        """
        SELECT
            count(*) FILTER (WHERE l.created_ns IS NOT NULL AND r.created_ns IS NOT NULL) AS both,
            count(*) FILTER (WHERE abs(r.created_ns - l.created_ns) < ?) AS same,
            count(*) FILTER (WHERE r.created_ns - l.created_ns >= ?) AS later,
            count(*) FILTER (WHERE l.created_ns - r.created_ns >= ?) AS earlier,
            count(*) FILTER (WHERE r.created_ns - l.created_ns >= ?
                               AND r.created_ns > r.modified_ns) AS copied,
            count(*) FILTER (WHERE l.created_ns - r.created_ns >= ?
                               AND l.created_ns > l.modified_ns) AS copied_left
        FROM matched m
        JOIN l_entries l ON l.entry_id = m.left_id
        JOIN r_entries r ON r.entry_id = m.right_id
        """,
        [grain_ns] * 5,
    ).fetchone()
    names = ("both", "same", "later", "earlier", "copied", "copied_left")
    return {name: int(value or 0) for name, value in zip(names, row or (), strict=False)}


def read_context(
    roots: dict[str, Any],
    births: dict[str, int],
    structure: Structure,
    *,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> tuple[Context, list[str], str | None]:
    """How the files got here, from the roots' identities and the matched files' births.

    ``roots`` holds ``same_root_id`` and ``same_mount_path`` (booleans read from each run's
    ``roots`` table) and ``same_place`` (False where the two sides are different folders of one
    census). The third value is the side that looks like the copy, ``"left"`` or ``"right"``,
    when the context is ``copied``.
    """
    because: list[str] = []
    both = births.get("both", 0)
    if both:
        because.append(
            f"of {both:,} matched files with a birth time on both sides, "
            f"{births['same']:,} were born at the same moment, {births['later']:,} later on the "
            f"second side ({births['copied']:,} of those after their own last modification) and "
            f"{births['earlier']:,} later on the first "
            f"({births.get('copied_left', 0):,} of those after their own last modification)"
        )
    # One root read again at one place is the same drive whatever was moved on it: a move within a
    # volume keeps every birth, so births cannot tell "moved" from "the same" and are not asked.
    if roots.get("same_root_id") and roots.get("same_place", True):
        return Context.SAME_TREE, because, None
    if both:
        if _share(births["copied"], both) >= thresholds.copied_births:
            return Context.COPIED, because, "right"
        if _share(births.get("copied_left", 0), both) >= thresholds.copied_births:
            return Context.COPIED, because, "left"
        elsewhere = not roots.get("same_root_id") or not roots.get("same_mount_path")
        if elsewhere and _share(births["same"], both) >= thresholds.copied_births:
            if not roots.get("same_mount_path"):
                because.append("the root is mounted at a different path")
            return Context.OTHER_MACHINE, because, None
    return Context.UNCLEAR, because, None


#: What a person reads first, by verdict: one sentence, no percentages, no verbs of alarm. The
#: notice is a flag ("we noticed this") and never a roadblock. It uses plain words: no "tier",
#: "rewrite" or "pair", which are jargon.
_NOTICE: dict[Verdict, str] = {
    Verdict.DIFFERENT_FILES: (
        "These look like different files: only {matched} of the {smaller} files in {small} "
        "could be matched to one in {other}."
    ),
    Verdict.SAME_ORGANISATION: "Nothing was reorganised: the files are where they were.",
    Verdict.FILES_RENAMED: "Some files were renamed inside their folders; no folder moved.",
    Verdict.RELOCATED_WHOLE: (
        "The whole tree moved as one piece: {rewrite}, and every folder kept its files."
    ),
    Verdict.FOLDERS_RENAMED: "Some folders were renamed or moved; each kept all of its files.",
    Verdict.PARTLY_REORGANISED: (
        "We noticed some reorganising: files were split up or joined together in {split} "
        "{folders} ({named}); the rest kept their files."
    ),
    Verdict.REORGANISED: (
        "We noticed a reorganisation: most files now sit in folders with different files "
        "than before."
    ),
}

_CONTEXT: dict[Context, str] = {
    Context.SAME_TREE: "",
    Context.OTHER_MACHINE: " The same files, seen from another computer or mount point.",
    Context.COPIED: " {Copy} looks like a copy: its files were created after they were last "
    "changed.",
    Context.UNCLEAR: "",
}


@dataclass(frozen=True, slots=True)
class Sides:
    """How the notice names the two sides it compares, so no sentence says "side".

    ``history`` is one project's reading against an earlier reading of it (the History page):
    the notice then says how many files are new, changed and gone, where between two folders or
    two projects it says how much of one the other holds.
    """

    first: str = "the first reading"
    second: str = "the second reading"
    history: bool = False


#: The History page's two sides: the earlier reading of a project's folders and the later one.
HISTORY = Sides("the earlier reading", "the later reading", history=True)

#: Two readings that are not one project's (two projects, or two copies of the files).
FIRST_AND_SECOND = Sides()

#: How many folders the "partly reorganised" sentence names before saying how many more.
NAMED_IN_NOTICE = 3


def _quoted(folder: str) -> str:
    return f"'{folder}'" if folder else "the top folder"


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def changed_folders(structure: Structure) -> list[str]:
    """The folders a partial reorganisation touched, as read: each earlier folder whose files were
    split up, largest first, then each later folder that joined several, most joined first."""
    split = sorted(
        (fate for fate in structure.folders if fate.fate == "split"),
        key=lambda fate: (-fate.files, fate.old_folder),
    )
    named = [fate.old_folder for fate in split]
    named += [folder for folder, _ in structure.merged]
    return list(dict.fromkeys(named))


def where_it_went(structure: Structure) -> str:
    """Where a tree moved whole now sits, said as a place and never as a rename of the top."""
    if not structure.rewrites:
        return "everything moved to one new place"
    old, new, _ = structure.rewrites[0]
    if new and not old:
        return f"everything now sits inside '{new}'"
    if old and not new:
        return f"what was inside '{old}' now sits at the top"
    if old and new and old != new:
        return f"everything in '{old}' is now in '{new}'"
    return "everything moved to one new place"


def _counted(parts: list[tuple[int, str, str]]) -> str:
    """ "12 files are new, 5 were changed and 3 are gone": the noun once, on the first count."""
    said = [
        f"{count:,} {('file ' if count == 1 else 'files ') if not index else ''}"
        f"{one if count == 1 else many}"
        for index, (count, one, many) in enumerate(part for part in parts if part[0])
    ]
    return said[0] if len(said) == 1 else ", ".join(said[:-1]) + " and " + said[-1]


def _no_sizes_named(states_no_sizes: dict[str, bool], sides: Sides) -> str:
    """Which reading states no sizes, as a clause: "the later reading states no file sizes"."""
    left, right = states_no_sizes.get("l", False), states_no_sizes.get("r", False)
    if left and right:
        return f"neither {sides.first} nor {sides.second} states file sizes"
    return f"{sides.first if left else sides.second} states no file sizes"


def _sizes_sentence(summary: MatchSummary, sides: Sides) -> str:
    """Where a reading states no sizes: how many files were matched without them, said as likely
    the same files and never as the same bytes."""
    if not any(summary.states_no_sizes.values()):
        return ""
    named = _capital(_no_sizes_named(summary.states_no_sizes, sides))
    count = summary.matched_without_size
    if not count:
        return (
            f" {named}, so its files could be matched only by their place or their folder and name."
        )
    if count == 1:
        return (
            f" {named}, so 1 file was matched by its place or its folder and name alone: likely "
            "the same file, though nothing here shows it holds the same bytes."
        )
    return (
        f" {named}, so {count:,} files were matched by their place or their folder and name "
        "alone: likely the same files, though nothing here shows they hold the same bytes."
    )


def _files_sentence(
    verdict: Verdict, summary: MatchSummary, sides: Sides, thresholds: Thresholds
) -> str:
    """How the two sides' files differ beyond the moves: new, changed and gone on the History
    page; elsewhere, how much of one side the other holds, where it holds less than nearly all.
    Files the other side holds as identical copies that could not be told apart are never said
    as missing."""
    if min(summary.files.values(), default=0) == 0:
        return ""
    if sides.history:
        parts = [
            (summary.absent.get("r", 0), "is new", "are new"),
            (summary.changed.get("l", 0), "was changed", "were changed"),
            (summary.absent.get("l", 0), "is gone", "are gone"),
        ]
        if not any(count for count, _, _ in parts):
            return ""
        return f" Since {sides.first}, {_counted(parts)}."
    if verdict is Verdict.DIFFERENT_FILES:
        return ""
    names = {"l": (sides.first, sides.second), "r": (sides.second, sides.first)}
    side = min(("l", "r"), key=lambda each: (summary.present(each) / summary.files[each], each))
    present, files = summary.present(side), summary.files[side]
    if present / files >= thresholds.whole_coverage:
        return ""
    name, other = names[side]
    return f" Only {present:,} of the {files:,} files in {name} are also in {other}."


def notice(
    verdict: Verdict,
    context: Context,
    *,
    structure: Structure,
    summary: MatchSummary | None = None,
    sides: Sides = FIRST_AND_SECOND,
    copy_side: str | None = None,
    followed: int = 0,
    scattered: int = 0,
    mixed: int = 0,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    named_in_notice: int = NAMED_IN_NOTICE,
) -> str:
    """The comparison in a few plain sentences, as the History page opens with it.

    Says how many of a person's own notes and answers were written about a folder whose files have
    since gone together to one place (``followed``), and how many were about a folder whose files
    scattered, or went to a place that now mixes many (``scattered``, ``mixed``). This is
    information and never a request that waits. The page shows it while the difference stands.
    The notice does not say that the engine applied anything. It only says where the files went.
    """
    touched = changed_folders(structure)
    split = len(touched)
    listed = ", ".join(_quoted(folder) for folder in touched[:named_in_notice])
    if split > named_in_notice:
        listed += f" and {split - named_in_notice:,} more"
    files = summary.files if summary is not None else {}
    said = _NOTICE[verdict]
    small, other = sides.first, sides.second
    if files and files.get("r", 0) < files.get("l", 0):
        small, other = sides.second, sides.first
    if verdict is Verdict.DIFFERENT_FILES and files and min(files.values()) == 0:
        empty = [name for key, name in (("l", sides.first), ("r", sides.second)) if not files[key]]
        said = (
            f"{_capital(empty[0])} holds no files, so there is nothing to match."
            if len(empty) == 1
            else "Neither holds any files, so there is nothing to match."
        )
    elif verdict is Verdict.DIFFERENT_FILES and summary is None:
        said = "These look like different files."
    elif verdict is Verdict.DIFFERENT_FILES and summary is not None and not summary.matched_total:
        said = (
            "These look like different files: the one file in {small} has no match in {other}."
            if min(files.values()) == 1
            else "These look like different files: none of the {smaller} files in {small} "
            "could be matched to one in {other}."
        )
    if verdict is Verdict.SAME_ORGANISATION and context in (Context.COPIED, Context.OTHER_MACHINE):
        said = f"{_capital(sides.first)} and {sides.second} are laid out the same way."
    text = said.format(
        rewrite=where_it_went(structure),
        split=split,
        folders="folder" if split == 1 else "folders",
        named=listed,
        matched=f"{summary.matched_total if summary is not None else 0:,}",
        smaller=f"{min(files.values(), default=0):,}",
        small=small,
        other=other,
    ) + _CONTEXT[context].format(
        Copy=_capital(sides.first if copy_side == "left" else sides.second)
    )
    if summary is not None:
        text += _sizes_sentence(summary, sides)
        text += _files_sentence(verdict, summary, sides, thresholds)
    if followed:
        text += (
            f" {followed:,} of your notes and answers {'was' if followed == 1 else 'were'} "
            f"written about {'a folder' if followed == 1 else 'folders'} whose files "
            "have moved together to one new place."
        )
    unplaced = scattered + mixed
    if unplaced:
        text += (
            f" {unplaced:,} of your notes and answers {'was' if unplaced == 1 else 'were'} "
            f"about {'a folder' if unplaced == 1 else 'folders'} whose files went to several "
            "places or now sit among other files; nothing waits on that."
        )
    return text
