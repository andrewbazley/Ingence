"""A person's own graph layer, written to the Record through its one way in.

This is the Graph workspace's human layer, beyond display labels (``labels.py``). It holds what a
person says about the drawing, kept apart from what the engine observed, claimed and published.

- A group, which a person reads as a topic: files and folders that a person says belong together,
  under a name ("the brisa series"), on ``overlay:group:<group id>``. A topic may also hold
  experiments, lattice cores and lines of work. A lattice core is a set of words, followed by the
  words, so an experiment that comes to carry them joins. A line of work is added by its
  experiments, which are the stable anchor, and keeps its words so that a drifted line can be
  told. The target is the group's own. A topic is a group that a person reads under another word,
  and never a second feature.
- An exclusion: a file or folder that a person sets aside, with the reason in words ("scratch,
  not data"), on ``overlay:exclude:<entry id>``.
- A proposed relationship between two entries, on
  ``overlay:relate:<source>:<target>:<relation>``. The relation is one of these: one entry made
  from the other (``derives``), one naming the other (``references``), a copy (``copy_of``), the
  same experiment (``same_experiment``), or not related (``not_related``).

Each of these is a person's statement and never an assertion. It is one ``annotate`` event (what a
person wants remembered), with the names and paths of what it names as they were. Nothing in
inference, the review or the sheets reads these targets. ``record/targets.target_kind`` knows no
``overlay`` prefix, so none is a review target, and search indexes only notes on folders and
entries. Whether a published run later agrees is read beside the statement
(``api/v1/explorer/overlay``) and never written into it. A removal is one more event, so the
history stays whole, and a revert is one more event that restores what was said before.

Members can be added and removed one edit at a time. An edit of a topic that stands may name
``add`` (members, as a new topic's are named) and ``remove_members`` (the members as the overlay
served them: an entry's identifier, ``group:<words>`` for a core, ``line:<digest>`` for a line)
instead of its whole member list. Only a member newly added is asked of the run. The members
already held are carried over as they were, so a file since gone never blocks an add or a
removal, and the edit does not race another edit that was made between a read and a write. Either
way the event says the topic's whole member list, so a reader that replays the Record needs
nothing new.

A batch is checked as a whole first, as labels are. The whole batch is refused in words, before
anything is written, if it holds any of these: an entry the current run does not hold, an empty or
overlong name or reason, a control character, an unknown relation, a kind of member that the
module does not know, a topic edited that nobody made, or a target named twice.

A topic's payload says each member's kind. ``members`` holds entry identifiers (files, folders and
experiments' folders), so an older group's payload reads as it always did, and a move is
followed. ``experiments`` lists which of them were said as
experiments, ``cores`` the sets of words followed, and ``lines`` the lines of work added, each with
the experiments it was added by. The reading of what they resolve to in a run belongs to
``ingence.topics``, which computes it when it reads and never stores it.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from ingence.core.ids import PROJECT_SCOPE
from ingence.core.schema import RecordEventKind
from ingence.record.log import append_event, read_events
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from ingence.core.schema import RecordEvent

__all__ = [
    "CORE_TERM_LIMIT",
    "KINDS",
    "MEMBER_KINDS",
    "OVERLAY_PREFIX",
    "RELATIONS",
    "OverlayRefusedError",
    "PreparedOverlay",
    "Standing",
    "Topic",
    "TopicMember",
    "goal_key",
    "is_topic_edit",
    "overlay_target",
    "prepare_overlay",
    "standing_layer",
    "target_parts",
    "topic_events_exist",
    "topic_members",
    "topics_standing",
    "write_prepared",
]

OVERLAY_PREFIX = "overlay"
KINDS = ("group", "exclude", "relate")
#: What a person may call an edit of a group: a topic is a group read under another word, so the
#: edit's kind may say either and writes the same target.
GROUP_KIND_WORDS = ("group", "topic")
#: What a topic may hold: a file or a folder, an experiment, a lattice core followed by its
#: words, a line of work added by its experiments, or a goal (``ingence.goals``).
MEMBER_KINDS = ("entry", "experiment", "core", "line", "goal")
#: How a goal's identifier is spelled (``ingence.goals``).
_GOAL_ID = re.compile(r"^goal_[0-9a-z]{1,40}$")
#: What a person may say one entry is to another. ``not_related`` records that two entries a rule
#: or a name might join are, in the person's knowledge, unrelated.
RELATIONS = ("derives", "references", "copy_of", "same_experiment", "not_related")
#: A group's name, and a reason or note, in characters.
NAME_LIMIT = 120
NOTE_LIMIT = 400
#: Members one group may hold, and edits one batch may make.
MEMBER_LIMIT = 2000
BATCH_LIMIT = 500
#: The most words one core or one line may carry, and the most characters in one word.
CORE_TERM_LIMIT = 24
LINE_WORD_LIMIT = 24
WORD_LIMIT = 60


class OverlayRefusedError(ValueError):
    """A batch refused before anything was written, in words."""


def overlay_target(kind: str, *parts: str) -> str:
    return ":".join((OVERLAY_PREFIX, kind, *parts))


def target_parts(target: str) -> tuple[str, list[str]] | None:
    """(kind, parts) for an overlay target, or None for any other target."""
    prefix, _, rest = target.partition(":")
    if prefix != OVERLAY_PREFIX:
        return None
    kind, _, body = rest.partition(":")
    if kind not in KINDS or not body:
        return None
    return kind, body.split(":")


def _text(value: Any, what: str, *, limit: int, required: bool) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            message = f"{what} is empty"
            raise OverlayRefusedError(message)
        return None
    if not isinstance(value, str):
        message = f"{what} is not text"
        raise OverlayRefusedError(message)
    text = unicodedata.normalize("NFC", value).strip()
    if len(text) > limit:
        message = f"{what} is {len(text)} characters; {limit} at most"
        raise OverlayRefusedError(message)
    if any(unicodedata.category(character) == "Cc" for character in text):
        message = f"{what} holds a control character"
        raise OverlayRefusedError(message)
    return text


def _entry(value: Any, what: str) -> str:
    text = str(value or "").strip()
    if not text or ":" in text:
        message = f"{what} names no item"
        raise OverlayRefusedError(message)
    return text


@dataclass
class _Members:
    """The members one edit of a topic names, by kind, checked and in canonical order."""

    #: Entry identifiers: files and folders as they were, and experiments' folders.
    entries: set[str] = field(default_factory=set)
    #: Which of ``entries`` were said as experiments.
    experiments: set[str] = field(default_factory=set)
    #: Each core as its words, casefolded and sorted.
    cores: set[tuple[str, ...]] = field(default_factory=set)
    #: Each line as (its experiments, its words as said, its label as said).
    lines: dict[tuple[str, ...], tuple[tuple[str, ...], str | None]] = field(default_factory=dict)
    #: Each goal by its identifier. Followed by the goal's words, it resolves every run to the
    #: experiments in the goal's top tier (``ingence.goals``).
    goals: set[str] = field(default_factory=set)
    #: The lines an edit sent back by their served key and so left as they were: what they were
    #: added by is not asked of the run again (a line's experiment since gone does not stop a save).
    carried: set[tuple[str, ...]] = field(default_factory=set)

    def count(self) -> int:
        return len(self.entries) + len(self.cores) + len(self.lines) + len(self.goals)

    def entry_ids(self) -> list[str]:
        """Every entry identifier the run must hold: members, and what each line newly said was
        added by."""
        held = set(self.entries)
        for anchors in self.lines:
            if anchors not in self.carried:
                held.update(anchors)
        return sorted(held)


def _word(value: Any, what: str, *, phrase: bool = False, colon: bool = True) -> str:
    """One word of a core or of a line, casefolded.

    A word may be a phrase (``nuclear stain``, ``wild type``, a two-word condition). The
    Dictionary's words and the lattice's terms are often phrases, so a space inside one is kept
    as one space (``phrase``). A word that is empty, holds a control character or is too long is
    not a word. A core's word holds no colon (``colon`` false), because a core is written on
    ``group:<words>``, which a colon would break.
    """
    text = unicodedata.normalize("NFC", str(value if value is not None else "")).strip()
    if phrase:
        text = " ".join(text.split())
    if not text or (not phrase and any(character.isspace() for character in text)):
        message = f"{what} holds a word that is empty or has a space: {text!r}"
        raise OverlayRefusedError(message)
    if not colon and ":" in text:
        message = f"{what} holds a word with a colon: {text!r}"
        raise OverlayRefusedError(message)
    if len(text) > WORD_LIMIT:
        message = f"{what} holds a word of {len(text)} characters; {WORD_LIMIT} at most"
        raise OverlayRefusedError(message)
    if any(unicodedata.category(character) == "Cc" for character in text):
        message = f"{what} holds a word with a control character"
        raise OverlayRefusedError(message)
    return text.casefold()


def _word_list(
    value: Any,
    what: str,
    *,
    limit: int,
    required: bool,
    phrase: bool = False,
    colon: bool = True,
) -> tuple[str, ...]:
    if value is None and not required:
        return ()
    if not isinstance(value, (list, tuple)) or (required and not value):
        message = f"{what} is a list of words" + (" with at least one" if required else "")
        raise OverlayRefusedError(message)
    if len(value) > limit:
        message = f"{what} holds {len(value)} words; {limit} at most"
        raise OverlayRefusedError(message)
    return tuple(sorted({_word(item, what, phrase=phrase, colon=colon) for item in value}))


def _members_of(raw: Any, name: str, before: _Members | None = None) -> _Members:
    """Check a topic's members, whatever their kinds, as a whole. Any one that is not a member
    refuses the batch. A bare string is an entry.

    An edit of a topic that stands (``before``) may send its members back as the overlay served
    them (the Graph workspace does, as plain strings). A string equal to one of the topic's core
    keys (``group:<words>``) or line keys (``line:<digest>``) carries that member over as it was,
    and an identifier that was an experiment member stays an experiment. A key that is none of the
    topic's refuses the batch, as any string with a colon does.
    """
    from ingence.record.targets import group_target

    if not isinstance(raw, list) or not raw:
        message = f"the topic {name!r} names no member"
        raise OverlayRefusedError(message)
    if len(raw) > MEMBER_LIMIT:
        message = f"a topic holds at most {MEMBER_LIMIT} members"
        raise OverlayRefusedError(message)
    cores_by_key = {group_target(core): core for core in before.cores} if before else {}
    lines_by_key = {line_key(anchors): anchors for anchors in before.lines} if before else {}
    goals_by_key = {goal_key(goal): goal for goal in before.goals} if before else {}
    found = _Members()
    for member in raw:
        if isinstance(member, str):
            served = member.strip()
            if before is not None and served in cores_by_key:
                found.cores.add(cores_by_key[served])
                continue
            if before is not None and served in goals_by_key:
                found.goals.add(goals_by_key[served])
                continue
            if before is not None and served in lines_by_key:
                kept = lines_by_key[served]
                found.lines[kept] = before.lines[kept]
                found.carried.add(kept)
                continue
            entry = _entry(member, f"a member of {name!r}")
            found.entries.add(entry)
            if before is not None and entry in before.experiments:
                found.experiments.add(entry)
            continue
        if not isinstance(member, dict):
            message = f"a member of {name!r} is an item id or an object saying its kind"
            raise OverlayRefusedError(message)
        kind = str(member.get("kind") or "entry")
        what = f"a {kind} member of {name!r}"
        if kind == "entry":
            found.entries.add(_entry(member.get("id"), what))
        elif kind == "experiment":
            experiment = _entry(member.get("id"), what)
            found.entries.add(experiment)
            found.experiments.add(experiment)
        elif kind == "core":
            found.cores.add(
                _word_list(
                    member.get("terms"),
                    what,
                    limit=CORE_TERM_LIMIT,
                    required=True,
                    phrase=True,
                    colon=False,
                )
            )
        elif kind == "goal":
            goal_id = str(member.get("id") or "").strip()
            if not _GOAL_ID.match(goal_id):
                message = f"{what} names the goal by its identifier (id: goal_<n>)"
                raise OverlayRefusedError(message)
            found.goals.add(goal_id)
        elif kind == "line":
            anchors = member.get("experiments")
            if not isinstance(anchors, list) or not anchors:
                message = f"{what} names the experiments it was added by (experiments)"
                raise OverlayRefusedError(message)
            ids = tuple(sorted({_entry(anchor, what) for anchor in anchors}))
            words = _word_list(
                member.get("words"), what, limit=LINE_WORD_LIMIT, required=False, phrase=True
            )
            label = _text(member.get("label"), f"{what} label", limit=NAME_LIMIT, required=False)
            found.lines[ids] = (words, label)
        else:
            message = (
                f"{kind!r} is no kind of member of {name!r}: {', '.join(MEMBER_KINDS)} "
                "(the whole batch is refused, nothing was written)"
            )
            raise OverlayRefusedError(message)
    return found


def _changed_members(
    before: _Members, add: Any, drop: Any, name: str
) -> tuple[_Members, list[str]]:
    """A topic's members after members were added to it and removed from it, and the entries the
    run must hold (only the ones newly added).

    The members already held are carried over unchecked, so a file since gone does not stop an add
    or a removal. A member to remove is named as the overlay served it (an entry's identifier,
    ``group:<words>`` for a core, ``line:<digest>`` for a line). The function refuses the batch if
    a member to remove is none of the topic's. Members to add are checked as a new topic's are,
    and one already held is left as it was. The function removes first. A member removed and
    added again in one edit is held again as it was said: it was held, so the run is not asked
    about it again, and it keeps the name and path it had.
    """
    from ingence.record.targets import group_target

    found = _Members(
        entries=set(before.entries),
        experiments=set(before.experiments),
        cores=set(before.cores),
        lines=dict(before.lines),
        carried=set(before.lines),
        goals=set(before.goals),
    )
    if drop is not None:
        if not isinstance(drop, list) or not drop:
            message = f"remove_members from {name!r} is a list of the members to remove"
            raise OverlayRefusedError(message)
        core_keys = {group_target(core): core for core in found.cores}
        line_keys = {line_key(anchors): anchors for anchors in found.lines}
        goal_keys = {goal_key(goal): goal for goal in found.goals}
        for key in drop:
            text = str(key).strip()
            if text in found.entries:
                found.entries.discard(text)
                found.experiments.discard(text)
            elif text in core_keys:
                found.cores.discard(core_keys[text])
            elif text in goal_keys:
                found.goals.discard(goal_keys[text])
            elif text in line_keys:
                found.lines.pop(line_keys[text], None)
                found.carried.discard(line_keys[text])
            else:
                message = f"{text!r} is no member of the topic {name!r}"
                raise OverlayRefusedError(message)
    fresh: set[str] = set()
    if add is not None:
        parsed = _members_of(add, name, before)
        held = set(before.entries)
        for anchors in parsed.lines:
            if anchors in before.lines:
                parsed.carried.add(anchors)
        fresh = {entry for entry in parsed.entries if entry not in held}
        for anchors in parsed.lines:
            if anchors not in parsed.carried:
                fresh.update(anchors)
        found.entries |= parsed.entries
        found.experiments |= {e for e in parsed.experiments if e in found.entries}
        found.cores |= parsed.cores
        found.goals |= parsed.goals
        for anchors, said in parsed.lines.items():
            if anchors not in before.lines:
                found.lines[anchors] = said
    if not (found.entries or found.cores or found.lines or found.goals):
        message = f"the topic {name!r} would hold no member; withdraw the topic to take it away"
        raise OverlayRefusedError(message)
    return found, sorted(fresh)


def _new_group_id(actor: str, occurred_at: str, name: str, index: int) -> str:
    """A new group's identifier: its words, its moment and a random part, so two groups said
    with one name in one second are two statements, never versions of one."""
    said = f"{actor}\x1f{occurred_at}\x1f{name}\x1f{index}\x1f{secrets.token_hex(8)}"
    return f"hg{hashlib.sha256(said.encode()).hexdigest()[:14]}"


@dataclass(frozen=True)
class PreparedOverlay:
    """A checked batch: the events to append, and the run they were checked against."""

    run_id: str
    events: tuple[tuple[str, dict[str, Any]], ...]


def _decode_members(payload: Mapping[str, Any], moved: Mapping[str, str]) -> _Members:
    """The members a stored topic payload names, each entry read at its current place.

    An old group's payload holds ``members`` alone and reads as entries only.
    """

    def at(entry: Any) -> str:
        return moved.get(str(entry), str(entry))

    found = _Members()
    found.entries = {at(member) for member in payload.get("members") or ()}
    found.experiments = {at(member) for member in payload.get("experiments") or ()}
    found.cores = {
        tuple(sorted(str(term) for term in core)) for core in payload.get("cores") or () if core
    }
    found.goals = {str(goal) for goal in payload.get("goals") or () if goal}
    for line in payload.get("lines") or ():
        anchors = tuple(sorted({at(anchor) for anchor in line.get("experiments") or ()}))
        if anchors:
            label = line.get("label")
            found.lines[anchors] = (
                tuple(str(word) for word in line.get("words") or ()),
                None if label is None else str(label),
            )
    return found


def _rekey(found: Any, moved: Mapping[str, str]) -> dict[str, str]:
    """Words kept by entry (names, paths) at the entries' current places."""
    return {moved.get(str(key), str(key)): str(value) for key, value in (found or {}).items()}


def _encode_members(members: _Members) -> dict[str, Any]:
    """The payload fields that say a topic's members and their kinds, in canonical order. The
    fields of kinds a topic does not hold are left out, so an old group's payload is unchanged."""
    encoded: dict[str, Any] = {"members": sorted(members.entries)}
    if members.experiments:
        encoded["experiments"] = sorted(members.experiments)
    if members.cores:
        encoded["cores"] = [list(core) for core in sorted(members.cores)]
    if members.lines:
        encoded["lines"] = [
            {"experiments": list(anchors), "words": list(words), "label": label}
            for anchors, (words, label) in sorted(members.lines.items())
        ]
    if members.goals:
        encoded["goals"] = sorted(members.goals)
    return encoded


def _known_groups(project: Path) -> dict[str, dict[str, Any] | None]:
    """Every group anyone made here, to what it last said (None where it stands withdrawn), so an
    edit can carry over what it leaves out."""
    import json

    found: dict[str, dict[str, Any] | None] = {}
    for event in read_events(project):
        parsed = target_parts(event.target)
        if parsed is None or parsed[0] != "group" or event.kind is not RecordEventKind.ANNOTATE:
            continue
        payload = json.loads(event.payload or "{}")
        # The latest statement: what it said, or None where the group stands withdrawn.
        found[parsed[1][0]] = None if payload.get("removed") else payload
    return found


def prepare_overlay(
    project: Path,
    *,
    actor: str,
    occurred_at: str,
    edits: Sequence[Mapping[str, Any]],
) -> PreparedOverlay:
    """Check every edit in the batch against the current run; return the events to write."""
    if not actor.strip():
        message = "a person's layer states who wrote it"
        raise OverlayRefusedError(message)
    if not edits:
        message = "the batch holds no edit"
        raise OverlayRefusedError(message)
    if len(edits) > BATCH_LIMIT:
        message = f"a batch holds at most {BATCH_LIMIT} edits; this one holds {len(edits)}"
        raise OverlayRefusedError(message)
    from ingence.record.log import moved_entries

    known_groups = _known_groups(project)
    moved = moved_entries(project)
    planned: list[tuple[str, dict[str, Any], list[str]]] = []
    seen: set[str] = set()
    for index, edit in enumerate(edits):
        kind = str(edit.get("kind") or "")
        removed = bool(edit.get("remove"))
        if kind in GROUP_KIND_WORDS:
            kind = "group"
            group_id = str(edit.get("id") or "").strip()
            if group_id and group_id not in known_groups:
                message = f"no topic {group_id} was made in this project; a new topic names no id"
                raise OverlayRefusedError(message)
            if removed:
                if not group_id:
                    message = "withdrawing a topic names it (id)"
                    raise OverlayRefusedError(message)
                if "members" in edit or "add" in edit or "remove_members" in edit:
                    message = "withdrawing a topic names no members"
                    raise OverlayRefusedError(message)
                target = overlay_target("group", group_id)
                payload: dict[str, Any] = {"overlay": "group", "removed": True}
                entries: list[str] = []
            else:
                before = known_groups.get(group_id) if group_id else None
                if group_id and before is None and ("members" not in edit or "name" not in edit):
                    # Edited after it was withdrawn: it says everything it holds again.
                    message = f"the topic {group_id} was withdrawn; bringing it back names it all"
                    raise OverlayRefusedError(message)
                changing = "add" in edit or "remove_members" in edit
                if changing and not group_id:
                    message = "adding members to a topic or removing them names the topic (id)"
                    raise OverlayRefusedError(message)
                if changing and "members" in edit:
                    message = (
                        f"the topic {group_id} is edited by its whole member list (members) or "
                        "by members added and removed (add, remove_members), not both"
                    )
                    raise OverlayRefusedError(message)
                # What an edit of a topic leaves out stays as it was (a rename names no member).
                name = _text(
                    edit["name"] if "name" in edit else (before or {}).get("name"),
                    "a topic's name",
                    limit=NAME_LIMIT,
                    required=True,
                )
                carried_names: dict[str, str] = {}
                carried_paths: dict[str, str] = {}
                if "members" in edit or before is None:
                    stood = _decode_members(before, moved) if before is not None else None
                    members = _members_of(edit.get("members"), str(name), stood)
                    entries = members.entry_ids()
                    if before is not None and members.carried:
                        # What a carried line was added by keeps the words it was said in.
                        anchors = {a for line in members.carried for a in line}
                        carried_names = {
                            k: v
                            for k, v in _rekey(before.get("names"), moved).items()
                            if k in anchors
                        }
                        carried_paths = {
                            k: v
                            for k, v in _rekey(before.get("paths"), moved).items()
                            if k in anchors
                        }
                elif changing:
                    # Members added and removed on a topic that stands: the ones already held are
                    # carried over as they were and unchecked; only a new one is asked of the run.
                    # The event still says the topic's whole member list.
                    stood = _decode_members(before, moved)
                    members, entries = _changed_members(
                        stood, edit.get("add"), edit.get("remove_members"), str(name)
                    )
                    kept = set(members.entries) | {a for line in members.lines for a in line}
                    carried_names = {
                        k: v for k, v in _rekey(before.get("names"), moved).items() if k in kept
                    }
                    carried_paths = {
                        k: v for k, v in _rekey(before.get("paths"), moved).items() if k in kept
                    }
                else:
                    # Members left out stay as they were, with the words they were said in; a
                    # member since moved away or gone must not stop a rename, so they are not
                    # checked against the run again. Their words are kept at their current
                    # places, so a lookup by where a member is now finds them.
                    members = _decode_members(before, moved)
                    entries = []
                    carried_names = _rekey(before.get("names"), moved)
                    carried_paths = _rekey(before.get("paths"), moved)
                group_id = group_id or _new_group_id(actor, occurred_at, str(name), index)
                target = overlay_target("group", group_id)
                payload = {
                    "overlay": "group",
                    "name": name,
                    **_encode_members(members),
                    "note": _text(
                        edit["note"] if "note" in edit else (before or {}).get("note"),
                        "a topic's note",
                        limit=NOTE_LIMIT,
                        required=False,
                    ),
                    **({"names": carried_names} if carried_names else {}),
                    **({"paths": carried_paths} if carried_paths else {}),
                }
        elif kind == "exclude":
            entry = _entry(edit.get("entry"), "an exclusion")
            target = overlay_target("exclude", entry)
            entries = [entry]
            if removed:
                payload = {"overlay": "exclude", "removed": True}
            else:
                payload = {
                    "overlay": "exclude",
                    "reason": _text(
                        edit.get("reason"), "an exclusion's reason", limit=NOTE_LIMIT, required=True
                    ),
                }
        elif kind == "relate":
            source = _entry(edit.get("source"), "a relation's source")
            other = _entry(edit.get("target"), "a relation's target")
            relation = str(edit.get("relation") or "")
            if relation not in RELATIONS:
                message = f"{relation!r} is no relation: {', '.join(RELATIONS)}"
                raise OverlayRefusedError(message)
            if source == other:
                message = "a relation joins two different items"
                raise OverlayRefusedError(message)
            target = overlay_target("relate", source, other, relation)
            entries = [source, other]
            payload = (
                {"overlay": "relate", "removed": True}
                if removed
                else {
                    "overlay": "relate",
                    "source": source,
                    "target": other,
                    "relation": relation,
                    "note": _text(
                        edit.get("note"), "a relation's note", limit=NOTE_LIMIT, required=False
                    ),
                }
            )
        else:
            message = f"{kind!r} is no kind of edit: topic (or group), exclude, relate"
            raise OverlayRefusedError(message)
        if target in seen:
            message = f"{target} is edited twice in one batch"
            raise OverlayRefusedError(message)
        seen.add(target)
        planned.append((target, payload, entries))

    with open_catalog(project) as catalog:
        row = catalog.query("SELECT run_id FROM run LIMIT 1").fetchone()
        if row is None or not row[0]:
            message = "the project has no published run to say anything about"
            raise OverlayRefusedError(message)
        run_id = str(row[0])
        wanted = sorted({entry for _, _, entries in planned for entry in entries})
        held = {
            str(entry): (str(name), str(path or ""))
            for entry, name, path in catalog.query(
                "SELECT entry_id, name, relative_path FROM entries WHERE NOT is_deleted "
                "AND list_contains(?, entry_id)",
                [wanted],
            ).fetchall()
        }
    missing = [entry for entry in wanted if entry not in held]
    if missing:
        message = f"the current run holds no live item {', '.join(missing[:5])}"
        raise OverlayRefusedError(message)
    events: list[tuple[str, dict[str, Any]]] = []
    for target, payload, entries in planned:
        if entries and not payload.get("removed"):
            # What it names, as it was when it was said: a later reader sees what a person saw.
            payload["names"] = {
                **payload.get("names", {}),
                **{entry: held[entry][0] for entry in entries},
            }
            payload["paths"] = {
                **payload.get("paths", {}),
                **{entry: held[entry][1] for entry in entries},
            }
        # Where it was said (not ``source``, which a relation uses for its first entry).
        payload["via"] = "graph workspace"
        events.append((target, {key: value for key, value in payload.items() if value is not None}))
    return PreparedOverlay(run_id=run_id, events=tuple(events))


def write_prepared(
    project: Path, prepared: PreparedOverlay, *, actor: str, occurred_at: str
) -> list[RecordEvent]:
    """Append a checked batch, one annotation per edit, in the batch's order."""
    return [
        append_event(
            project,
            kind=RecordEventKind.ANNOTATE,
            actor=actor,
            occurred_at=occurred_at,
            target=target,
            against_run_id=prepared.run_id,
            payload=payload,
            scope=PROJECT_SCOPE,
        )
        for target, payload in prepared.events
    ]


@dataclass(frozen=True)
class Standing:
    """A person's groups and exclusions in force now, members read at their current places."""

    #: (group id, name, members): a topic's entries (files, folders, experiments' folders).
    groups: tuple[tuple[str, str, tuple[str, ...]], ...]
    excluded: tuple[str, ...]
    #: (target, source, other end, relation): what a person proposed, read at current places.
    relations: tuple[tuple[str, str, str, str], ...] = ()


def standing_layer(project: Path) -> Standing:
    """The groups, exclusions and relations standing in the Record (each target's latest event,
    not removed)."""
    import json

    from ingence.record.log import moved_entries

    moved = moved_entries(project)
    latest: dict[str, tuple[str, dict[str, Any]]] = {}
    for event in read_events(project):
        parsed = target_parts(event.target)
        if parsed is None:
            continue
        if event.kind is not RecordEventKind.ANNOTATE:
            continue
        latest[event.target] = (parsed[0], json.loads(event.payload or "{}"))
    groups: list[tuple[str, str, tuple[str, ...]]] = []
    excluded: list[str] = []
    relations: list[tuple[str, str, str, str]] = []
    for target, (kind, payload) in sorted(latest.items()):
        if payload.get("removed"):
            continue
        if kind == "group":
            members = tuple(moved.get(str(m), str(m)) for m in payload.get("members") or ())
            groups.append((target.split(":")[2], str(payload.get("name") or ""), members))
        elif kind == "exclude":
            excluded.append(target.split(":", 2)[2])
        else:
            # The target is read at the entries' current places (``at_new_places``).
            source, other, relation = target.split(":")[2:5]
            relations.append((target, source, other, relation))
    return Standing(groups=tuple(groups), excluded=tuple(excluded), relations=tuple(relations))


@dataclass(frozen=True)
class TopicMember:
    """One member of a topic, as it was said."""

    kind: Literal["entry", "experiment", "core", "line", "goal"]
    #: The member's identifier within the topic: an entry's own for an entry and an experiment,
    #: ``group:<words>`` (the core's Record target) for a core, ``line:<digest>`` for a line,
    #: ``goal:<id>`` for a goal.
    key: str
    #: The entry (a file, a folder, an experiment's folder), at its current place.
    entry: str | None = None
    #: A core's words, casefolded and sorted.
    terms: tuple[str, ...] = ()
    #: The experiments a line was added by, at their current places, and its words and label as
    #: they were when it was added.
    anchors: tuple[str, ...] = ()
    words: tuple[str, ...] = ()
    label: str | None = None
    #: A goal's identifier, for a goal member.
    goal_id: str | None = None


@dataclass(frozen=True)
class Topic:
    """A person's topic standing in the Record: a group, read under the word a person uses."""

    topic_id: str
    #: The Record target it is written on: ``overlay:group:<topic id>``.
    target: str
    name: str
    note: str | None
    members: tuple[TopicMember, ...]
    #: Who said it first and when, and who said it last and when.
    made_by: str
    made_at: str
    edited_by: str
    edited_at: str
    event_id: str
    sequence: int
    #: The run its latest statement was written against.
    against_run_id: str | None
    #: How many earlier statements the target holds (edits and a withdrawal and its undoing).
    earlier: int
    #: The names and paths the entries had when they were said, by the identifier said.
    names: Mapping[str, str] = field(default_factory=dict)
    paths: Mapping[str, str] = field(default_factory=dict)


def line_key(anchors: Sequence[str]) -> str:
    """A line member's identifier within its topic: a digest of the experiments it was added by."""
    digest = hashlib.sha256("\x1f".join(sorted(anchors)).encode()).hexdigest()[:12]
    return f"line:{digest}"


def goal_key(goal_id: str) -> str:
    """A goal member's key within its topic: ``goal:<id>``."""
    return f"goal:{goal_id}"


def member_list(members: _Members) -> tuple[TopicMember, ...]:
    """A topic's members as typed records: entries (and experiments), then cores, then lines,
    then goals."""
    from ingence.record.targets import group_target

    listed: list[TopicMember] = []
    for entry in sorted(members.entries):
        kind: Literal["entry", "experiment"] = (
            "experiment" if entry in members.experiments else "entry"
        )
        listed.append(TopicMember(kind=kind, key=entry, entry=entry))
    for core in sorted(members.cores):
        listed.append(TopicMember(kind="core", key=group_target(core), terms=core))
    for anchors, (words, label) in sorted(members.lines.items()):
        listed.append(
            TopicMember(
                kind="line", key=line_key(anchors), anchors=anchors, words=words, label=label
            )
        )
    for goal_id in sorted(members.goals):
        listed.append(TopicMember(kind="goal", key=goal_key(goal_id), goal_id=goal_id))
    return tuple(listed)


def is_topic_edit(event: RecordEvent) -> bool:
    """Whether a Record event is a statement about a person's topic (a group of the layer).

    A topic is an annotation: it decides nothing and no run reads it, so a count of answers (the
    run manifest's, the Changes timeline's) leaves these out and counts them as edits.
    """
    parsed = target_parts(event.target)
    return parsed is not None and parsed[0] == "group"


def topic_events_exist(project: Path) -> bool:
    """Whether the Record holds any statement about a topic, withdrawn or not."""
    return any(is_topic_edit(event) for event in read_events(project, withdrawn=True))


def topic_members(payload: Mapping[str, Any], moved: Mapping[str, str]) -> tuple[TopicMember, ...]:
    """The typed members a stored group payload names, each entry at its current place (an old
    group's payload gives entries only)."""
    return member_list(_decode_members(payload, moved))


def topics_standing(project: Path, *, up_to: int | None = None) -> tuple[Topic, ...]:
    """Every topic standing now (each group's latest statement, not withdrawn), in the order they
    were first made. Members are read at their current places, and nothing is resolved here: what
    a topic's members come to in a run is the work of ``ingence.topics``.

    ``up_to`` reads the Record as it stood at a run's watermark (a run's manifest says what the
    topics were when that run was made). Otherwise the function reads the Record as it stands."""
    import json

    from ingence.record.log import moved_entries, read_at

    moved = moved_entries(project)
    first: dict[str, Any] = {}
    latest: dict[str, tuple[Any, dict[str, Any]]] = {}
    counts: dict[str, int] = {}
    with read_at(project, up_to):
        events = read_events(project)
    for event in events:
        parsed = target_parts(event.target)
        if parsed is None or parsed[0] != "group" or event.kind is not RecordEventKind.ANNOTATE:
            continue
        first.setdefault(event.target, event)
        counts[event.target] = counts.get(event.target, -1) + 1
        latest[event.target] = (event, json.loads(event.payload or "{}"))
    found: list[Topic] = []
    for target, (event, payload) in latest.items():
        if payload.get("removed"):
            continue
        made = first[target]
        found.append(
            Topic(
                topic_id=target.split(":")[2],
                target=target,
                name=str(payload.get("name") or ""),
                note=payload.get("note") or None,
                members=member_list(_decode_members(payload, moved)),
                made_by=made.actor,
                made_at=made.occurred_at,
                edited_by=event.actor,
                edited_at=event.occurred_at,
                event_id=event.event_id,
                sequence=int(event.sequence),
                against_run_id=str(event.against_run_id) if event.against_run_id else None,
                earlier=counts[target],
                names=_rekey(payload.get("names"), moved),
                paths=_rekey(payload.get("paths"), moved),
            )
        )
    return tuple(sorted(found, key=lambda topic: (first[topic.target].sequence, topic.topic_id)))
