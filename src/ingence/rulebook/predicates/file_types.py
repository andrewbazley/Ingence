"""Predicates that read what a file's own bytes say it is and who wrote it.

Both read the observations a content pass left on one entry and nothing else: the type
detector's opinion of the bytes, and the TIFF reader's readings of the writer's tags. Each
turns what the file-type table says about those (``dictionary/file_types.yaml``: which format
a signature marks, which program a tag names and whether it acquires, analyses or draws
figures) into a classification, and abstains in words where the table says nothing.

The table is data and not the Dictionary. A predicate here reaches no project and no
vocabulary, only the shipped table, which the run's record names by its digest.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ingence.core.schema import Entry, FailureClass, Observation, RuleEffect
from ingence.core.tiers import ResolutionStatus
from ingence.dictionary.file_types import file_type_table
from ingence.rulebook.registry import (
    Abstention,
    Concluded,
    Predicate,
    PredicateNotEvaluableError,
    Reads,
    declare,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ["DerivedFromInnerFormat", "FormatFromContent", "WriterFromContentTags"]

#: Where detection writes each detector's opinion of the bytes.
_OPINION = "type.opinion."
#: What the TIFF reader's readings begin with.
_IMAGE = "image."

#: The parameter each kind of writer's conclusion is read from.
_CONCLUDES: dict[str, str] = {
    "acquisition": "acquisition_concludes",
    "analysis": "analysis_concludes",
    "figure": "figure_concludes",
}


def _observations(subject: object) -> tuple[Observation, ...]:
    held = getattr(subject, "observations", subject)
    if not isinstance(held, list | tuple):
        return ()
    return tuple(item for item in held if isinstance(item, Observation))


def _strings(parameters: Mapping[str, object], name: str) -> frozenset[str]:
    """A string list a rule declared, as nothing where it declares none."""
    declared = parameters.get(name)
    if declared is None:
        return frozenset()
    if isinstance(declared, str):
        return frozenset({declared})
    if isinstance(declared, list | tuple | set | frozenset):
        return frozenset(str(item) for item in declared if item)
    message = f"{name} is a list of strings, and this rule declares {declared!r}"
    raise PredicateNotEvaluableError(message)


class WriterFromContentTags:
    """A file is what the program named in its tags makes: recorded data, derived, or a figure.

    The rule makes an assumption and says so. The TIFF reader records the ``Software`` tag, the
    OME ``Creator`` and the vendor tags. None of those says which program wrote the file last.
    They say only which programs left a mark. The rule assumes that the program named is the one
    that made the file. It concludes the stage that the file-type table's writers list gives that
    program: acquisition software writes recorded data, analysis software writes something
    derived, and a drawing program writes a figure. Where the tags name two kinds of program, the
    rule abstains as conflicting and does not pick one. A stack that an acquisition program wrote
    carries its vendor tag beside an ``ImageJ=`` description. That description is a hyperstack
    convention and not a writer's mark, so the rule does not read the description kind. A file
    that an analysis program saved keeps the acquisition software's marks, and nothing in the tags
    says which program came last. A stage that the file's own name states defeats the rule, as the
    rule declares.
    """

    name = "writer_from_content_tags"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the stage or role the named writers give, or None where none is named."""
        found = self._named(_observations(subject), parameters)
        kinds = {kind for kind, _ in found}
        if len(kinds) != 1:
            return None
        (kind,) = kinds
        value = str(parameters.get(_CONCLUDES[kind]) or "")
        if not value:
            message = f"the writer rule concludes {_CONCLUDES[kind]}, and this rule names none"
            raise PredicateNotEvaluableError(message)
        return Concluded(
            values=(value,), inputs=tuple(sorted({identifier for _, identifier in found}))
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say whether no reader read the tags, no tag names a program, or none is a writer."""
        observations = _observations(subject)
        if not any(observation.key.startswith(_IMAGE) for observation in observations):
            return Abstention(
                status=ResolutionStatus.UNEXAMINED,
                reason="no content pass has read this file's tags",
            )
        keys = _strings(parameters, "tag_keys")
        tagged = tuple(
            observation.observation_id
            for observation in observations
            if observation.key in keys and observation.value_text
        )
        if not tagged:
            return Abstention(reason="the file's tags name no program")
        found = self._named(observations, parameters)
        kinds = {kind for kind, _ in found}
        if len(kinds) > 1:
            return Abstention(
                status=ResolutionStatus.EXAMINED_CONFLICTING,
                failure_class=FailureClass.INTERPRETATION,
                inputs=tuple(sorted({identifier for _, identifier in found})),
                reason=(
                    "the tags name programs of two kinds ("
                    + ", ".join(sorted(kinds))
                    + "), and nothing in them says which wrote the file"
                ),
            )
        return Abstention(
            inputs=tagged,
            reason="the program the tags name is not one the file-type table lists as a writer",
        )

    @staticmethod
    def _named(
        observations: Sequence[Observation], parameters: Mapping[str, object]
    ) -> list[tuple[str, str]]:
        """The kind of each writer the tags name, with the observation naming it."""
        keys = _strings(parameters, "tag_keys")
        if not keys:
            message = (
                "writer_from_content_tags reads the tags a rule names, and this rule names none"
            )
            raise PredicateNotEvaluableError(message)
        found: list[tuple[str, str]] = []
        for observation in observations:
            if observation.key not in keys or not observation.value_text:
                continue
            found.extend(
                (writer.kind, observation.observation_id)
                for writer in file_type_table().writers_named(observation.value_text)
            )
        return found


class FormatFromContent:
    """A file is of the format whose signature its bytes carry, whatever its name says.

    The rule reads the bytes, so a wrong extension does not decide the format.
    ``content_contradicts_extension`` retracts the format that an extension named. This rule says
    what the format is, from the detector's opinion and the table's row for that media type. A
    container's signature (HDF5, gzip, OLE storage) names no format of its own, because a vendor
    writes its own format on it. What a ZIP is used for is a laboratory's to say. So neither
    concludes anything here, which keeps this rule in step with the container reading.
    """

    name = "format_from_content"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the family the table gives the detector's media type, or None."""
        opinions = self._opinions(_observations(subject), parameters)
        containers = _strings(parameters, "container_media_types") | _strings(
            parameters, "unclear_media_types"
        )
        table = file_type_table()
        for observation in opinions:
            media_type = str(observation.value_text)
            if media_type in containers:
                continue
            family = table.family_for_media_type(media_type)
            if family is not None:
                return Concluded(values=(family,), inputs=(observation.observation_id,))
        return None

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say whether detection never ran, found no signature, found a container, or a format
        the table names no term for."""
        observations = _observations(subject)
        if not any(observation.key.startswith("type.") for observation in observations):
            return Abstention(
                status=ResolutionStatus.UNEXAMINED,
                reason="type detection has not read this file's content",
            )
        opinions = self._opinions(observations, parameters)
        if not opinions:
            return Abstention(reason="the bytes carry no signature the detector recognises")
        containers = _strings(parameters, "container_media_types") | _strings(
            parameters, "unclear_media_types"
        )
        inputs = tuple(observation.observation_id for observation in opinions)
        if all(observation.value_text in containers for observation in opinions):
            return Abstention(
                inputs=inputs,
                reason="the bytes carry a container's signature, which names no format of its own",
            )
        return Abstention(
            inputs=inputs,
            reason="the file-type table names no term for the format the bytes carry",
        )

    @staticmethod
    def _opinions(
        observations: Sequence[Observation], parameters: Mapping[str, object]
    ) -> list[Observation]:
        detector = str(parameters.get("detector") or "")
        if not detector:
            message = "format_from_content reads one detector's opinion, and this rule names none"
            raise PredicateNotEvaluableError(message)
        key = f"{_OPINION}{detector}"
        return sorted(
            (
                observation
                for observation in observations
                if observation.key == key and observation.value_text
            ),
            key=lambda observation: observation.observation_id,
        )


class DerivedFromInnerFormat:
    """A file named with another kind of file's extension before its own was exported from one.

    ``x.nd2.csv`` is a table written out of an image. The census keeps the two-part suffix
    (``nd2.csv``) where both halves are extensions, and the file-type table says what each names.
    The file sits at the stage that the rule names, exported from a file of the inner kind, when
    all of these hold.

    * Both halves are kinds of file that the table knows, and not two spellings of one
      (``tif.tiff``).
    * The pair is not a spelling of its own (``ome.tif`` and ``tar.gz`` are).
    * Neither half is a compression wrapped around the other (``csv.gz``).
    * The inner half is not also an ordinary word (``counts.raw.csv`` holds the stage word raw, not
      a source) and not a document's extension (``notes.txt.csv``).
    * The outer kind is not an instrument's own.

    A TIFF named for the CZI it came from is also an export, which the stage words call converted.
    The rule makes an assumption and says so. The inner extension could be a word that happens to
    spell a format. This is why the rule stands at the abductive tier, and why bytes that
    contradict the outer extension defeat it.
    """

    name = "derived_from_inner_format"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the stage an export sits at, or None where the name holds no such pair."""
        entry = subject if isinstance(subject, Entry) else getattr(subject, "entry", None)
        if entry is None or "." not in entry.compound_extension:
            return None
        inner, outer = entry.compound_extension.split(".", 1)
        table = file_type_table()
        if table.row_for(entry.compound_extension) is not None:
            return None
        inner_row, outer_row = table.row_for(inner), table.row_for(outer)
        if inner_row is None or outer_row is None:
            return None
        if inner_row.family is None or outer_row.family is None:
            return None
        if inner_row.wraps or outer_row.wraps or inner_row is outer_row:
            return None
        # An inner half that is an ordinary word too (raw, log, out, txt, tmp, fig) is a word
        # here, not a source's extension. An instrument's own format is never written out of
        # another file.
        if inner in inner_row.spelled_after_a_dot or inner_row.category == "document":
            return None
        if outer_row.category == "instrument":
            return None
        value = str(parameters.get("concludes") or "")
        if not value:
            message = "derived_from_inner_format concludes a stage, and this rule names none"
            raise PredicateNotEvaluableError(message)
        return value

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say why the name's suffix is not an export's: no pair, a spelling of its own, a
        compression, or a half the table does not know."""
        entry = subject if isinstance(subject, Entry) else getattr(subject, "entry", None)
        if entry is None or "." not in entry.compound_extension:
            return Abstention(reason="the name ends in one extension")
        table = file_type_table()
        if table.row_for(entry.compound_extension) is not None:
            return Abstention(reason="the two-part suffix is a format's own spelling")
        inner, outer = entry.compound_extension.split(".", 1)
        inner_row, outer_row = table.row_for(inner), table.row_for(outer)
        if inner_row is None or outer_row is None:
            return Abstention(reason="a half of the suffix names no kind of file the table knows")
        if inner_row.family is None or outer_row.family is None:
            return Abstention(reason="a half of the suffix names no kind of file the table knows")
        if inner_row.wraps or outer_row.wraps:
            return Abstention(reason="the suffix is a format with a compression after it")
        if inner_row is outer_row:
            return Abstention(reason="both halves are spellings of the same kind of file")
        if inner in inner_row.spelled_after_a_dot:
            return Abstention(reason="the inner half is an ordinary word as well as an extension")
        if inner_row.category == "document":
            return Abstention(
                reason="the inner half is a document, which nothing is written out of"
            )
        return Abstention(reason="an instrument's own format is not written out of another file")


@declare(
    "derived_from_inner_format",
    version="1",
    summary="Classify a file named with another kind's extension before its own as an export",
    effect=RuleEffect.CLASSIFIES,
    reads=Reads.ENTRY,
    parameters=("concludes",),
    support_families=("name_structure",),
)
def _build_derived_from_inner_format() -> Predicate:
    return DerivedFromInnerFormat()


@declare(
    "writer_from_content_tags",
    version="1",
    summary="Classify a file by the kind of program its own tags name as its writer",
    effect=RuleEffect.CLASSIFIES,
    reads=Reads.OBSERVATIONS,
    parameters=("acquisition_concludes", "analysis_concludes", "figure_concludes", "tag_keys"),
    support_families=("content_tags",),
)
def _build_writer_from_content_tags() -> Predicate:
    return WriterFromContentTags()


@declare(
    "format_from_content",
    version="1",
    summary="Classify a file by the format whose signature its bytes carry",
    effect=RuleEffect.CLASSIFIES,
    reads=Reads.OBSERVATIONS,
    parameters=("container_media_types", "detector", "unclear_media_types"),
    support_families=("type_detection",),
)
def _build_format_from_content() -> Predicate:
    return FormatFromContent()
