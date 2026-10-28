"""What a third party declared about a file's content, read as such.

``identical`` means proven byte equality, and a declared digest proves nothing the engine read.
An archive that publishes an MD5 for every file has made a statement about bytes this engine may
never see. The statement is usually right, and it is still somebody else's. So a declared digest
groups files under a rule of its own, one tier below an observed digest. The shipped
``identical_by_content_digest`` stays entailed and stays for digests the engine computed.
``identical_by_declared_digest`` is defeasible, and it names what retracts it:

- ``same_declared_digest`` keys a file by the digest its listing declares for one method
  (``declared.digest.<method>``, ``SourceScope.DECLARED``). A listing that declares two values for
  one method contradicts itself, and the file is conflicting, never grouped on either;
- ``declared_digest_contradicted`` concludes on a file whose own bytes, read by the engine under
  the same method (``identity.<method>``), give another value than the listing declared: the
  declaration was wrong for this copy, and the defeasible identity built on it is retracted.

Both read observations only, and neither names an archive.

A listed file can have an address that answered no file. The engine records such files as missing
or broken links and does not chase them down. ``broken_addresses_in_folder`` raises a folder that
holds files whose address a listing, or a remote reading, found answering "not found" or "moved",
or found to be an address that could not be read as one. It raises one finding for the folder,
with how many answered each way, and never one question per file. It concludes nothing about the
files. A broken address says nothing about whether a file exists elsewhere, and the words say so.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ingence.core.ids import canonical_json
from ingence.core.outcomes import SourceScope
from ingence.core.schema import EntryType, FailureClass, Observation, RuleEffect
from ingence.core.tiers import ResolutionStatus
from ingence.infer.subjects import FolderContents
from ingence.rulebook.registry import (
    Abstention,
    ClassKey,
    Concluded,
    PredicateNotEvaluableError,
    Raised,
    Reach,
    Reads,
    declare,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ingence.rulebook.registry import Predicate

__all__ = [
    "BrokenAddressesInFolder",
    "DeclaredAttributeToClarify",
    "DeclaredDigestContradicted",
    "SameDeclaredDigest",
    "attribute_value",
]

_DECLARED_DIGEST = "declared.digest."
_IDENTITY = "identity."


def _method(parameters: Mapping[str, object]) -> str:
    method = parameters.get("digest_algorithm")
    if not isinstance(method, str) or not method:
        message = "a declared digest is read for one method, and this rule names none"
        raise PredicateNotEvaluableError(message)
    return method


def _observations(subject: object) -> tuple[Observation, ...]:
    held = subject if isinstance(subject, (list, tuple)) else getattr(subject, "observations", ())
    return tuple(item for item in held if isinstance(item, Observation))


def _declared_values(subject: object, method: str) -> tuple[list[str], tuple[str, ...]]:
    """Every value the listing declared for this method, and the observations that said so."""
    values: list[str] = []
    inputs: list[str] = []
    for observation in _observations(subject):
        if (
            observation.key != f"{_DECLARED_DIGEST}{method}"
            or observation.source_scope is not SourceScope.DECLARED
        ):
            continue
        inputs.append(observation.observation_id)
        if observation.value_text:
            values.append(observation.value_text)
        elif observation.value_structure:
            values.extend(str(value) for value in json.loads(observation.value_structure)["values"])
    return sorted(set(values)), tuple(sorted(inputs))


class SameDeclaredDigest:
    """Two files whose listings declare one digest of their whole content, for one method."""

    name = "same_declared_digest"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> ClassKey | None:
        """Return the class of files declaring this digest, or None where none or two are."""
        method = _method(parameters)
        values, inputs = _declared_values(subject, method)
        if len(values) != 1:
            return None
        return ClassKey(
            key=canonical_json({"method": method, "declared": values[0]}), inputs=inputs
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say whether nothing was declared, or the listing declared two values."""
        method = _method(parameters)
        values, inputs = _declared_values(subject, method)
        if not values:
            return Abstention(
                status=ResolutionStatus.UNEXAMINED,
                reason=f"no listing declared a {method} digest for this file",
            )
        return Abstention(
            status=ResolutionStatus.EXAMINED_CONFLICTING,
            failure_class=FailureClass.INTERPRETATION,
            inputs=inputs,
            reason=f"its listing declared {len(values)} different {method} digests for it",
        )


class DeclaredDigestContradicted:
    """A file whose own bytes, read by the engine, disagree with the digest its listing declared."""

    name = "declared_digest_contradicted"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Conclude where an observed digest of the declared method differs from the declaration."""
        method = _method(parameters)
        declared, declared_inputs = _declared_values(subject, method)
        observed = [
            observation
            for observation in _observations(subject)
            if observation.key == f"{_IDENTITY}{method}"
            and observation.source_scope is not SourceScope.DECLARED
            and observation.value_text
        ]
        if not declared or not observed:
            return None
        read = sorted({str(observation.value_text) for observation in observed})
        if set(read) <= set(declared):
            return None
        value = canonical_json({"method": method, "declared": declared, "read": read})
        return Concluded(
            values=(value,),
            inputs=tuple(sorted({*declared_inputs, *(item.observation_id for item in observed)})),
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say whether a declaration or a reading is missing, or the two agree."""
        method = _method(parameters)
        declared, inputs = _declared_values(subject, method)
        if not declared:
            return Abstention(reason=f"no listing declared a {method} digest for this file")
        observed = any(
            observation.key == f"{_IDENTITY}{method}" for observation in _observations(subject)
        )
        if not observed:
            return Abstention(
                status=ResolutionStatus.UNEXAMINED,
                inputs=inputs,
                reason=f"the engine has not read this file's bytes under {method}",
            )
        return Abstention(inputs=inputs, reason=f"the {method} it read is the one declared")


@declare(
    "same_declared_digest",
    version="1",
    summary="Group files whose listings declare one whole-content digest for a method",
    effect=RuleEffect.GROUPS,
    reads=Reads.OBSERVATIONS,
    parameters=("digest_algorithm",),
)
def _build_same_declared_digest() -> Predicate:
    return SameDeclaredDigest()


@declare(
    "declared_digest_contradicted",
    version="1",
    summary="Conclude where the engine's own digest of a file disagrees with its declared one",
    effect=RuleEffect.CLASSIFIES,
    reads=Reads.OBSERVATIONS,
    parameters=("digest_algorithm",),
    support_families=("declared_fact",),
)
def _build_declared_digest_contradicted() -> Predicate:
    return DeclaredDigestContradicted()


_DECLARED_ATTRIBUTE = "declared.attribute."


def attribute_value(name: str) -> str:
    """The question one attribute's name raises, the same in every run and for every file."""
    return f"what the attribute {name} says"


class DeclaredAttributeToClarify:
    """An attribute a listing states about a file: asked about by name, never packed in.

    The engine holds no archive-specific terminology. It stays adaptable across archives, and a
    person clarifies terms and choices that validate or change its interpretations. So each
    attribute that a listing declared on a file (``declared.attribute.<name>``) raises one value
    per attribute name. The Review queue gathers every file that states the attribute into one
    question, which a person answers once. The reason shows the listing's own values. Nothing is
    concluded from them, and no vocabulary holds them.
    """

    name = "declared_attribute_to_clarify"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Raise each attribute the file's listing declared, by its name, citing the statement."""
        del parameters
        stated = sorted(
            (observation.key.removeprefix(_DECLARED_ATTRIBUTE), observation)
            for observation in _observations(subject)
            if observation.key.startswith(_DECLARED_ATTRIBUTE)
            and observation.source_scope is SourceScope.DECLARED
        )
        if not stated:
            return None
        said = [
            f"{name}: {observation.value_text or observation.value_structure or ''}"[:120]
            for name, observation in stated
        ]
        return Raised(
            values=tuple(attribute_value(name) for name, _ in stated),
            inputs=tuple(observation.observation_id for _, observation in stated),
            reason="its listing states " + "; ".join(said),
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say that no listing declared an attribute of this file."""
        return Abstention(reason="no listing declared an attribute of this file")


@declare(
    "declared_attribute_to_clarify",
    version="1",
    summary="Raise each attribute a listing declares on a file, by name, for a person to clarify",
    effect=RuleEffect.FLAGS,
    reads=Reads.OBSERVATIONS,
    parameters=(),
    support_families=("declared_fact",),
)
def _build_declared_attribute_to_clarify() -> Predicate:
    return DeclaredAttributeToClarify()


#: How each state is said of a file's address, one file and several: what a finding reads.
_ANSWERED = {
    "not_found": ("answered not found", "answered not found"),
    "moved": ("answered that it had moved", "answered that they had moved"),
}
#: How a file whose address could not be read as one is said: alone, and counted among others.
_UNREADABLE = "with an address that could not be read as one"
_UNREADABLE_COUNTED = "had an address that could not be read as one"
#: The value a finding raises for each state, short and plain.
_RAISED = {
    "not_found": "address not found",
    "moved": "address moved",
    "malformed": "address unreadable",
}


def _broken_sentence(states: tuple[tuple[str, int], ...], total: int) -> str:
    """The finding's words: how many files, how their addresses answered, and what is not said."""
    many = total != 1
    files = f"{total} file{'s' if many else ''}"
    whose = "addresses" if many else "address"
    if len(states) == 1:
        state, _ = states[0]
        said = (
            f"{'each ' if many else ''}{_UNREADABLE}"
            if state == "malformed"
            else f"whose {whose} {_ANSWERED.get(state, ('answered no file',) * 2)[many]}"
        )
    else:
        parts = [
            f"{count} {_UNREADABLE_COUNTED}"
            if state == "malformed"
            else f"{count} {_ANSWERED.get(state, ('answered no file',) * 2)[count != 1]}"
            for state, count in states
        ]
        said = f"whose {whose} are broken: {', '.join(parts[:-1])} and {parts[-1]}"
    return (
        f"The archive lists {files} directly in this folder {said}. "
        f"{'They were' if many else 'It was'} not read, and a broken address does not say "
        "whether a file exists elsewhere."
    )


class BrokenAddressesInFolder:
    """A folder holding files whose addresses answered no file: one finding, with how many."""

    name = "broken_addresses_in_folder"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Raise the folder where enough of its own files' addresses answered no file."""
        if not isinstance(subject, FolderContents):
            return None
        if subject.entry.entry_type is not EntryType.DIRECTORY:
            return None
        minimum = int(str(parameters.get("minimum_files", 1)))
        if subject.broken_addresses_here < max(1, minimum):
            return None
        states = subject.broken_address_states
        return Raised(
            values=tuple(sorted(_RAISED.get(state, "address broken") for state, _ in states)),
            inputs=(subject.entry.entry_id,),
            reason=_broken_sentence(states, subject.broken_addresses_here),
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say how many of the folder's own files' addresses answered no file."""
        held = subject.broken_addresses_here if isinstance(subject, FolderContents) else 0
        return Abstention(
            reason=(
                f"{held} of the files directly in it have an address found broken, fewer than "
                f"the {parameters.get('minimum_files', 1)} the rule asks for"
            )
        )


@declare(
    "broken_addresses_in_folder",
    version="1",
    summary="Raise a folder holding files whose addresses answered not found or moved, once",
    effect=RuleEffect.FLAGS,
    reads=Reads.FOLDER_CONTENTS,
    parameters=("minimum_files",),
    support_families=("declared_fact",),
    # Counted over the pool first, from the observations, and attached to each folder.
    reach=Reach.ATTACHED,
)
def _build_broken_addresses_in_folder() -> Predicate:
    return BrokenAddressesInFolder()
