"""A folder copied here whole, concluded as information.

People copy and paste whole analysis bundles all the time, for example when they move them from a
laptop to a server. A copy is not a reason for a large response or for the user's attention when
the engine can explain what happened. So the copy is a claim about the folder, which says when its
files arrived together. It is not a question. A claim is information that a page shows in one
sentence, and a finding is something that is asked. One narrower case is still asked: a copy that
puts a later stage before an earlier one. That case stays with the ``copied_in_bulk`` flag.

A folder was copied here whole where it directly holds ``minimum_files`` or more files with a birth
and a modification, at least ``minimum_share`` of them born after they were last modified (a copy
keeps the modification and is born when it lands), and those births lie within
``burst_window_seconds`` of one another. The defaults are the ones measured for the flag. The
value names the day the copy arrived and how many files it brought.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from ingence.core.ids import canonical_json
from ingence.core.schema import EntryType, RuleEffect
from ingence.infer.subjects import FolderContents
from ingence.rulebook.registry import Abstention, ClassKey, Concluded, Raised, Reads, declare

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ingence.rulebook.registry import Predicate

__all__ = ["CopiedInBulkFromContents"]


class CopiedInBulkFromContents:
    """A folder whose files arrived together, born after they were made: the copy's day."""

    name = "copied_in_bulk_from_contents"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the day the folder's files were copied in and how many, or None."""
        if not _copied(subject, parameters):
            return None
        assert isinstance(subject, FolderContents)
        first = subject.late_births_first_ns or 0
        day = dt.datetime.fromtimestamp(first / 1e9, tz=dt.UTC).strftime("%Y-%m-%d")
        value = canonical_json({"copied_on": day, "files": subject.born_late_here})
        return Concluded(values=(value,), inputs=(subject.entry.entry_id,))

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say which condition the folder's own files did not meet."""
        entry_id = getattr(getattr(subject, "entry", None), "entry_id", "")
        inputs = (entry_id,) if entry_id else ()
        if not isinstance(subject, FolderContents):
            return Abstention(inputs=inputs, reason="not a folder")
        return Abstention(
            inputs=inputs,
            reason=(
                f"{subject.born_late_here} of its {subject.dated_files_here} files with a birth "
                "were born after they were last modified, not enough of them or not together"
            ),
        )


def _copied(subject: object, parameters: Mapping[str, object]) -> bool:
    if not isinstance(subject, FolderContents) or subject.entry.entry_type != EntryType.DIRECTORY:
        return False
    minimum = int(str(parameters.get("minimum_files", 5)))
    share = float(str(parameters.get("minimum_share", 0.8)))
    window = int(str(parameters.get("burst_window_seconds", 600)))
    if subject.dated_files_here < minimum or subject.born_late_here < minimum:
        return False
    if subject.born_late_here < share * subject.dated_files_here:
        return False
    first, last = subject.late_births_first_ns, subject.late_births_last_ns
    return first is not None and last is not None and (last - first) <= window * 1_000_000_000


@declare(
    "copied_in_bulk_from_contents",
    version="1",
    summary="Classify a folder whose files arrived together as copied in, naming the day",
    effect=RuleEffect.CLASSIFIES,
    reads=Reads.FOLDER_CONTENTS,
    parameters=("burst_window_seconds", "minimum_files", "minimum_share"),
    support_families=("filesystem_fact",),
)
def _build_copied_in_bulk_from_contents() -> Predicate:
    return CopiedInBulkFromContents()


class CopiedInBulkByDeclaredClocks:
    """A folder whose files an archive says it wrote, or received, at one time.

    A listing's copy time is the time of a copy and never the file's time. An archive's
    ``Last-Modified`` on its own file is when the archive wrote its copy. An object store's upload
    time is when the file was uploaded. Neither says when the content was made, so no rule orders
    files by them. They do say what the copy rule infers from births after modifications: that the
    folder's files arrived together. Here the archive states it, so one file is enough to know that
    a copy happened. The parameters say how many of the folder's files must carry a declared copy
    time, and how close together, for the folder to have been copied in one event.

    The value names the day and the span, ``{"copied_on", "first", "last", "files"}``, so a person
    reading any time in the folder knows it is the archive's.
    """

    name = "copied_in_bulk_by_declared_clocks"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the day the archive's copies of the folder's files arrived, or None."""
        if not _declared_copy(subject, parameters):
            return None
        assert isinstance(subject, FolderContents)
        first = subject.declared_copies_first_ns or 0
        last = subject.declared_copies_last_ns or first

        def instant(nanoseconds: int) -> str:
            return dt.datetime.fromtimestamp(nanoseconds / 1e9, tz=dt.UTC).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )

        value = canonical_json(
            {
                "copied_on": instant(first)[:10],
                "first": instant(first),
                "last": instant(last),
                "files": subject.declared_copies_here,
            }
        )
        return Concluded(values=(value,), inputs=(subject.entry.entry_id,))

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say which condition the folder's own files did not meet."""
        entry_id = getattr(getattr(subject, "entry", None), "entry_id", "")
        inputs = (entry_id,) if entry_id else ()
        if not isinstance(subject, FolderContents):
            return Abstention(inputs=inputs, reason="not a folder")
        if not subject.declared_copies_here:
            return Abstention(
                inputs=inputs,
                reason="no file directly in it carries a copy time a listing declared",
            )
        return Abstention(
            inputs=inputs,
            reason=(
                f"{subject.declared_copies_here} of its {subject.files_here} files carry a "
                "declared copy time, not enough of them or not close enough together"
            ),
        )


def _declared_copy(subject: object, parameters: Mapping[str, object]) -> bool:
    if not isinstance(subject, FolderContents) or subject.entry.entry_type != EntryType.DIRECTORY:
        return False
    minimum = int(str(parameters.get("minimum_files", 1)))
    share = float(str(parameters.get("minimum_share", 0.8)))
    window = int(str(parameters.get("burst_window_seconds", 86_400)))
    if subject.declared_copies_here < minimum or not subject.files_here:
        return False
    if subject.declared_copies_here < share * subject.files_here:
        return False
    first, last = subject.declared_copies_first_ns, subject.declared_copies_last_ns
    return first is not None and last is not None and (last - first) <= window * 1_000_000_000


@declare(
    "copied_in_bulk_by_declared_clocks",
    version="1",
    summary="Classify a folder whose files a listing says were copied in together, naming the day",
    effect=RuleEffect.CLASSIFIES,
    reads=Reads.FOLDER_CONTENTS,
    parameters=("burst_window_seconds", "minimum_files", "minimum_share"),
    support_families=("declared_fact",),
)
def _build_copied_in_bulk_by_declared_clocks() -> Predicate:
    return CopiedInBulkByDeclaredClocks()


class ArchiveLinkNamesAFile:
    """An archive's link and the real file it stands for, keyed by that file."""

    name = "archive_link_names_a_file"
    version = "2"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the real file as the class key, for the link and for the file alike.

        Version 2 cites a listing's declared size of the file behind the link where it agreed. The
        citation sits beside the entry. It is the statement the match rests on, with its source.
        """
        del parameters
        copy_of = getattr(subject, "copy_of", None)
        entry = getattr(subject, "entry", None)
        if not copy_of or entry is None:
            return None
        evidence = tuple(getattr(subject, "evidence", ()))
        return ClassKey(key=str(copy_of), inputs=(entry.entry_id, *evidence))

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say that no archive link names this entry, or no single file answers the link.

        Where a listing's declared size refused the file of the link's name, say so and cite the
        statement. A declared size decides nothing silently.
        """
        refused = str(getattr(subject, "refused", "") or "")
        entry = getattr(subject, "entry", None)
        if refused and entry is not None:
            return Abstention(
                inputs=(entry.entry_id, *tuple(getattr(subject, "evidence", ()))),
                reason=refused,
            )
        return Abstention(
            reason="no archive link names it, or no one file of the link's name and path is held"
        )


@declare(
    "archive_link_names_a_file",
    version="2",
    summary="Group an archive's link with the real file of its name and path, as its copy",
    effect=RuleEffect.GROUPS,
    reads=Reads.ARCHIVED_COPY,
    parameters=(),
    support_families=("name_structure",),
)
def _build_archive_link_names_a_file() -> Predicate:
    return ArchiveLinkNamesAFile()


class DeclaredCopyWindowExceeded:
    """A folder whose declared copies are related but span more than the window.

    The copy window for declared copies, which relate to each other, can be per day. If that
    fails to hold, the engine flags it so that a person can adjust the window. So where enough of
    a folder's files carry a copy time that a listing declared, but those times span more than
    ``burst_window_seconds``, the folder is raised. Enough means the same ``minimum_files`` and
    ``minimum_share`` that the copy rule reads. The rule raises one question for each such folder,
    with the span in the reason.
    """

    name = "declared_copy_window_exceeded"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Raise the folder where its declared copies span more than the window."""
        if (
            not isinstance(subject, FolderContents)
            or subject.entry.entry_type != EntryType.DIRECTORY
        ):
            return None
        minimum = int(str(parameters.get("minimum_files", 1)))
        share = float(str(parameters.get("minimum_share", 0.8)))
        window = int(str(parameters.get("burst_window_seconds", 86_400)))
        if subject.declared_copies_here < max(minimum, 2) or not subject.files_here:
            return None
        if subject.declared_copies_here < share * subject.files_here:
            return None
        first, last = subject.declared_copies_first_ns, subject.declared_copies_last_ns
        if first is None or last is None or (last - first) <= window * 1_000_000_000:
            return None

        def instant(nanoseconds: int) -> str:
            return dt.datetime.fromtimestamp(nanoseconds / 1e9, tz=dt.UTC).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )

        span = (last - first) / 1e9
        return Raised(
            values=("declared copies span more than the window",),
            inputs=(subject.entry.entry_id,),
            reason=(
                f"{subject.declared_copies_here} of its {subject.files_here} files carry a copy "
                f"time its listing declared, from {instant(first)} to {instant(last)}, "
                f"{span / 86_400:.1f} days where the window is {window / 86_400:g}"
            ),
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say that the folder's declared copies fit the window, or there are too few."""
        return Abstention(reason="its declared copies fit the window, or too few are declared")


@declare(
    "declared_copy_window_exceeded",
    version="1",
    summary="Raise a folder whose declared copy times span more than the copy window",
    effect=RuleEffect.FLAGS,
    reads=Reads.FOLDER_CONTENTS,
    parameters=("burst_window_seconds", "minimum_files", "minimum_share"),
    support_families=("declared_fact",),
)
def _build_declared_copy_window_exceeded() -> Predicate:
    return DeclaredCopyWindowExceeded()
