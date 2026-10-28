"""Reading an entry's name: what it says, where the parts sat, and what they mean.

Three records, deliberately, because the three are different kinds of fact.

The token is a :class:`Component`. The engine reads it literally off the filesystem, and it stays
at ``LITERAL``, which is honest.

Where it sat is a :class:`NameShape`. Content is conceptual and position is relational. Storing an
ordinal on a component would entangle them and force a second decision about whether that ordinal
belongs in a component's identity.

What it means is a :class:`DictionaryBinding`, at ``SEMANTIC``. The binding does not live on the
component, because a shared tier would make the engine assert that resolving ``md`` to a sense is as
certain as a ``stat`` call. One record cannot carry one tier for two facts at different distances
from the bytes, and a fact that was read and a conclusion drawn from it are never stored in the same
field.

The extension is split off before anything is tokenised. A slash cannot appear in a basename and
an extension always can, so the extension is the one part of a name whose meaning is not in doubt.
Read as an ordinary word, an extension made up 27 percent of every bound token on real laboratory
folders and bound wrongly more often than not: ``md`` to molecular dynamics, ``sbatch`` to a
scheduler, ``out`` to a stage. Split off, it binds only to a sense of kind ``format``. That is a
statement about what may bind, not a guess about what probably means what.

The module reads names only, not paths. A folder is an entry like any other and the containment
forest already links it to what it holds, so path context is reached by walking up rather than by
copying every ancestor's tokens onto every descendant. Inheriting context is then a rule,
inspectable and switchable, rather than storage nobody can undo.
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from ingence.core.ids import (
    PROJECT_SCOPE,
    Scope,
    ScopeKind,
    canonical_json,
    component_id,
    dictionary_binding_id,
    digest_text,
    name_shape_id,
    observation_id,
)
from ingence.core.schema import (
    MATCH_QUALITY_ORDER,
    BindingAlternative,
    BindingMethod,
    Component,
    DictionaryBinding,
    DictionaryOrigin,
    EntryType,
    MatchQuality,
    NameShape,
    NameSlot,
    Observation,
    SourceScope,
    TimeSource,
    ValueKind,
)
from ingence.core.tiers import ExtractionTier, ResolutionStatus
from ingence.dictionary.case_reading import CaseCounts, CaseReading, Reading, readings_for
from ingence.dictionary.file_types import EXTENSION_KINDS, file_type_scope
from ingence.dictionary.normalize import (
    NORMALIZER_NAME,
    NORMALIZER_VERSION,
    ROUND_LABELS,
    UNNAMED_YEARS,
    Atom,
    AtomKind,
)
from ingence.dictionary.store import PLURAL_MINIMUM, SCOPE_PRECEDENCE, origin_rank
from ingence.storage.layout import RUNS_DIR

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path

    from ingence.core.schema import Entry
    from ingence.dictionary.normalize import Normaliser
    from ingence.dictionary.store import DictionaryStore, Hit, Sense

__all__ = [
    "CASE_WHOLE_CONSTRAINT",
    "COUNT_LABEL_CONSTRAINT",
    "DATE_ORDER_KEY",
    "DOMAIN_PRIOR_CONSTRAINT",
    "DOMAIN_PROFILE_KEY",
    "DOMAIN_PROFILE_VERSION",
    "DOMAIN_SKEW_CONSTRAINT",
    "DOMAIN_TIE_CONSTRAINT",
    "DOMAIN_VOTING_KINDS",
    "EXTENSION_CONSTRAINT",
    "EXTENSION_KINDS",
    "IDENTIFIER_CONSTRAINT",
    "INDEX_LABEL_CONSTRAINT",
    "INDEX_LABEL_KINDS",
    "NUMBER_CONSTRAINT",
    "NUMBER_MINIMUM_QUALITY",
    "RESOLVER_NAME",
    "RESOLVER_VERSION",
    "SPELLING_CONSTRAINT",
    "UNBOUND_LABEL_CONSTRAINT",
    "UNIT_KINDS",
    "UNIVERSAL_SENSE_CONSTRAINT",
    "WELL_GRID_CONSTRAINT",
    "CaseBinder",
    "DomainBinder",
    "DomainDecision",
    "NameReading",
    "SettledDate",
    "date_order_observation",
    "domain_profile_observation",
    "event_time_from_atoms",
    "is_skew_constraint",
    "names_read_with",
    "observe_carried",
    "observe_series",
    "profile_in_words",
    "read_name",
    "sense_domains",
    "settled_short_date",
    "skeleton_of",
    "skew_constraint",
    "spelling_constraint",
    "spelling_read_as",
    "universal_default",
]

#: What a component records as the thing that resolved it. Part of every
#: binding's identity, so improving the resolver produces new bindings rather
#: than silently rewriting old ones.
RESOLVER_NAME = "ingence.dictionary"

#: Bumped whenever resolution could reach a different sense, or name different candidates, for
#: unchanged input and an unchanged Dictionary. The versions so far:
#:
#: - 1 left out whichever tied sense the lookup happened to hold first.
#: - 2 names every sense a conflicting binding could not choose between.
#: - 3 does not look a bare number up at all, and binds no unit to a one-character word.
#: - 4 looks a bare number up against its exact spelling alone.
#: - 5 tries an indexed atom's whole text before its label.
#: - 6 admits a count beside a replicate as a kind an index label may bind.
#: - 7 looks no identifier up and records that it declined.
#: - 8 reads a spelling that a capital divides whole or as its pieces, once per spelling for the
#:   whole run (:class:`CaseBinder`), and binds a compressed format's suffix to the format inside
#:   it.
#: - 9 reads a plural as its singular where the singular is a Dictionary word (``masks``,
#:   ``cells``; :meth:`~ingence.dictionary.store.DictionaryStore.look_up_plural`). It also reads a
#:   letter ``r`` and a number (``R1``, ``R2``), alone or counted, as an indexed label. The whole
#:   text of such a label matches both r squared and the second read of a sequencing pair. So
#:   version 9 binds the whole text only through a sense declared for the file's type (read one and
#:   read two, in a file of reads) or through a person's answer, never through a sense that the
#:   whole project holds. Version 9 also binds a term spelled in parts whose last word is a plural
#:   (``phase_plates``) as the term.
#: - 10 reads a spelling that two domains hold differently from the folder's own files
#:   (:class:`DomainBinder`). The domain whose files lead by enough wins (``name:domain_skew``).
#:   Otherwise a prior the person gave wins (``name:domain_prior``). Otherwise the universal sense
#:   is the default (``name:universal_sense``). Otherwise the tie stays. Version 10 also treats a
#:   magnification as microscopy's, so ``100x`` resolves where the files are a microscope's.
#:   Version 10 also binds a slip of a Dictionary spelling as that spelling, marked corrected
#:   (``name:spelling``), and leaves a well-shaped part unbound in a folder whose names form a
#:   well grid (``name:well_grid``).
RESOLVER_VERSION = "10"

#: The marker a piece records where its spelling was read whole: the binding is the whole
#: spelling's, looked up as it was written, and every piece of it carries the same one. The
#: spelling itself is on the slot (``NameSlot.case_whole``).
CASE_WHOLE_CONSTRAINT = "name:case_whole"

#: The marker of a word read as the core's meaning because the domains did not decide. A spelling
#: that one universal sense and one or more domain senses hold, where no folder profile leans far
#: enough one way, binds the universal sense, marked assumed (the constraint says so). The domain
#: senses stay as shadowed alternatives.
UNIVERSAL_SENSE_CONSTRAINT = "name:universal_sense"

#: The marker of a word read as one domain's sense because the folder's files lean that way
#: (:class:`DomainBinder`). The losers stay as shadowed alternatives. The marker cites the deciding
#: folder where one has an entry (``name:domain_skew:<entry id>``, :func:`skew_constraint`), as a
#: settled short date's observation cites its neighbours. A decision at the root's own level or
#: the project's cites nothing.
DOMAIN_SKEW_CONSTRAINT = "name:domain_skew"

#: The marker of a word read as one domain's sense because a person made that domain a prior
#: (``init --domain``, or a yes to "load the X pack"), where the folder's files did not decide.
DOMAIN_PRIOR_CONSTRAINT = "name:domain_prior"

#: The marker of a word read as the Dictionary spelling it is a slip of
#: (:meth:`~ingence.dictionary.store.DictionaryStore.correct`). The binding's quality is
#: ``corrected``, which a rule whose floor is exact or loose refuses by the order. The marker
#: carries the matched spelling (``name:spelling:hoechst``, :func:`spelling_constraint`), so the
#: finding reads "hoescht is read as hoechst" and an answer that the two are unrelated names them.
SPELLING_CONSTRAINT = "name:spelling"


def spelling_constraint(spelling: str) -> str:
    """The spelling marker carrying the Dictionary spelling the word was read as."""
    return f"{SPELLING_CONSTRAINT}:{spelling}"


def spelling_read_as(constraint: str | None) -> str | None:
    """The Dictionary spelling a spelling marker carries, or None for any other marker."""
    if constraint is None or not constraint.startswith(SPELLING_CONSTRAINT + ":"):
        return None
    return constraint[len(SPELLING_CONSTRAINT) + 1 :] or None


#: The marker of a well-shaped part left unbound in a folder whose names form a well grid. ``C1``
#: to ``C4`` are wells where the names run A to P across 1 to 48 in at least two rows and two
#: columns, never channels or passages. The sense that the whole text reached stays as a shadowed
#: alternative.
WELL_GRID_CONSTRAINT = "name:well_grid"

#: The marker of a tie between domains left standing and not asked about. The folder's files lean
#: neither way, no universal sense is among the contenders, and fewer files carry the word than
#: ``dictionary.domain_skew.question_above_files``. The binding is conflicting, and no question
#: follows.
DOMAIN_TIE_CONSTRAINT = "name:domain_tie"

#: The key of the observation a census stores a folder's domain profile under, beside the folder
#: entry. The profile holds how many of its files read as each domain's, which domain leads and
#: whether it decided the words shared between domains there (:class:`DomainBinder`,
#: :func:`profile_in_words`).
DOMAIN_PROFILE_KEY = "name.domain_profile"
DOMAIN_PROFILE_VERSION = "1"

#: The kind a one-character word never binds to. A lone letter in a name is an initial, a panel or
#: a channel far more often than a unit symbol, and the quantity reader already refuses to fold
#: one. Read as a word, ``S`` would bind siemens and ``h`` would bind an hour where nothing measured
#: anything.
UNIT_KINDS = frozenset({"unit"})

#: The weakest spelling a bare number may be matched by. A number means a surface only where a
#: sense is written as that number: the six laser lines are spelled ``405``, ``488``, ``561``,
#: ``594``, ``640`` and ``647``. Reaching anything else from a digit means stripping the
#: punctuation that made it an index.
NUMBER_MINIMUM_QUALITY = MatchQuality.EXACT

#: Recorded on a binding that was restricted, so that a token which abstained because a
#: constraint excluded its only sense is distinguishable from one nobody has ever heard of.
#: Without it the discovery surface offers to add a term the Dictionary has had all along.
#:
#: A marker is written ``<slot>:<restriction>``: the slot of the name whose position licensed the
#: restriction, then what it was restricted to. Anything asking where a binding was made reads the
#: slot back. A binding that carries no marker was looked up unrestricted, which only a name token
#: ever is.
EXTENSION_CONSTRAINT = "extension:format"

#: The same marker for a number, restricted by spelling rather than by kind. It names the ``name``
#: slot, because a number is one of the name's own tokens and not its extension. A digit that
#: matched nothing was examined under a restriction, and saying so is what separates it from a
#: number nobody has declared a sense for.
NUMBER_CONSTRAINT = "name:exact"

#: The marker an identifier records: it was never looked up. A hash, an accession or a digested
#: plasmid means nothing a Dictionary could hold. A fragment of one that happened to spell a unit
#: or a word would make the Review queue ask a person what a digest meant. The binding reads no
#: evidence, as for a number that was not looked up, and the marker says why. So a declined
#: identifier is never mistaken for a word nobody has taught the engine.
IDENTIFIER_CONSTRAINT = "name:identifier"

#: The marker an indexed atom's label records, when the label is what bound. The whole text is
#: tried first and unrestricted, because 91 shipped surfaces are index-shaped and every one of them
#: is a real reading: ``c1`` is a channel, ``p1`` a passage, ``r1`` a read, ``nd2`` a vendor
#: format. Only where the whole text reaches nothing is the label tried, and then only against the
#: kinds in :data:`INDEX_LABEL_KINDS`.
#:
#: The label is restricted because an unrestricted label would read the digit-and-letter rule
#: backwards. Measured against the shipped vocabulary, a bare label binds ``a`` to the ampere by
#: folding case, ``p`` to the p-value, ``s`` to the second and ``c`` to the Celsius degree. So
#: ``A1`` would be a current and ``p03`` a statistic. That is "a digit followed by one letter
#: became a quantity" with the digit and the letter the other way round. The rule for a restricted
#: slot applies unchanged: it records what the label may mean, not what it probably means.
#:
#: The restriction also keeps ``V1`` unbound. The engine is not entitled to decide that ``V`` means
#: version, variant, volume or replicate. It may only notice the shape and ask. Unrestricted, ``v``
#: binds the version sense outright and nothing would be left to ask about.
#:
#: Widening the restriction needs a measurement behind it. It has been widened once: ``count``
#: joined ``replicate`` on a measurement of a few ``n``-labelled atoms on real laboratory folders.
#: ``channel``, ``position`` and ``vessel`` are the three kinds a label plausibly carries next,
#: and each needs its own evidence.
#:
#: The marker names the ``name`` slot, on the same argument as for a bare number: a label on an
#: index is one of the name's own tokens and not its extension. The slot says where in the name a
#: binding was made, and there are two such places. What the binding was restricted to is the
#: second field, which is what the restriction differs in.
INDEX_LABEL_CONSTRAINT = "name:replicate"

#: The same marker where a count is what the label bound. ``N=3`` states how many replicates
#: something rests on, which is a cardinality, and an indexed label is an ordinal. They are two
#: readings of one shape and a rule must be able to tell which one reached it, so the kind that
#: bound is what the marker names.
COUNT_LABEL_CONSTRAINT = "name:count"

#: The marker a label records where it was examined under the restriction and nothing answered.
#: It names the restriction rather than a kind, because the restriction is every kind below and
#: naming one of them would say the others were never tried. The restriction covers two kinds
#: (the replicate and the count), so its marker and a kind's marker differ.
UNBOUND_LABEL_CONSTRAINT = "name:index_label"

#: Which marker a label binding records, by the kind that bound. The kinds a label may bind are
#: derived from it rather than written a second time, because two declarations of one set in two
#: places are two declarations that one edit can change.
INDEX_LABEL_CONSTRAINT_OF = {
    "replicate": INDEX_LABEL_CONSTRAINT,
    "count": COUNT_LABEL_CONSTRAINT,
}

#: The only kinds an indexed atom's label may bind to.
INDEX_LABEL_KINDS = frozenset(INDEX_LABEL_CONSTRAINT_OF)

#: Which constraint each restricted slot records where the slot alone decides it. A slot
#: absent from it is looked up without restriction and records none. An indexed atom is not
#: here, because whether it was restricted depends on which of its two lookups answered.
_CONSTRAINT_OF = {
    AtomKind.EXTENSION.value: EXTENSION_CONSTRAINT,
    AtomKind.NUMBER.value: NUMBER_CONSTRAINT,
    AtomKind.IDENTIFIER.value: IDENTIFIER_CONSTRAINT,
}

#: What an atom the Dictionary did not recognise contributes to a skeleton.
#: Uppercase, so a placeholder is distinguishable at a glance from a theme kind,
#: which is always lowercase.
_PLACEHOLDER = {
    AtomKind.WORD: "WORD",
    AtomKind.NUMBER: "NUMBER",
    AtomKind.DATE: "DATE",
    AtomKind.VERSION: "VERSION",
    AtomKind.INDEXED: "INDEXED",
    AtomKind.QUANTITY: "QUANTITY",
    AtomKind.EXTENSION: "EXTENSION",
    AtomKind.IDENTIFIER: "IDENTIFIER",
}

#: Match qualities in descending standing. Where one component was reached by
#: several spellings, the best-matched one is what the binding records, which is
#: the same preference the lookup itself applies across its three indexes.


@dataclass(frozen=True, slots=True)
class NameReading:
    """Everything one name yielded, in the three records it belongs in."""

    components: tuple[Component, ...]
    shape: NameShape | None
    bindings: tuple[DictionaryBinding, ...]
    #: A date the name carried, as nanoseconds at UTC midnight, where exactly one unambiguous date
    #: was written. This is a tier-3 clock. It survives copying, which makes it worth more than the
    #: filesystem's own.
    event_time_ns: int | None = None
    event_time_source: TimeSource = TimeSource.UNAVAILABLE
    #: Where a neighbour settled the name's short date: the date, its order and the neighbours that
    #: settled it, which the census records as the evidence a person sees.
    settled: SettledDate | None = None


@dataclass(frozen=True)
class SettledDate:
    """A short date two field orders allow, read one way by its neighbours.

    ``settled_by`` names each neighbour that settled it, as ``(neighbour, path)``: ``folder`` and
    the dated folder's relative path, or ``series`` and the sibling whose own reading ruled the
    other orders out.
    """

    date: str
    order: str
    settled_by: tuple[tuple[str, str], ...]

    def as_json(self) -> dict[str, object]:
        return {
            "date": self.date,
            "order": self.order,
            "settled_by": [{"neighbour": by, "path": path} for by, path in self.settled_by],
        }


#: The observation a census records on an entry whose short date a neighbour settled.
DATE_ORDER_KEY = "name.date_order"
#: Bumped whenever the settled reading would be recorded differently.
DATE_ORDER_VERSION = "1"


#: The instants a signed 64-bit count of nanoseconds holds, which is how the Index stores a time.
_EARLIEST_INSTANT_NS = -(1 << 63)
_LATEST_INSTANT_NS = (1 << 63) - 1


def event_time_from_atoms(
    atoms: Sequence[Atom], *, latest: date | None = None
) -> tuple[int | None, TimeSource]:
    """Return the event time a name carries, or nothing, with its source.

    The engine abstains unless the name carries exactly one unambiguous full date. One date may be
    written more than once, so the function counts the distinct dates. Three cases show that the
    abstention is earned rather than lazy:

    A name carrying two dates has not said which one is the event.
    ``2026-01-04_to_2026-01-08`` is a span and ``raw_2026-01-04_analysed_2026-02-01``
    names two different events; picking the first would be right sometimes and
    silently wrong the rest of the time, and temporal order is one of the two
    ground truths the whole design rests on.

    An ambiguous date is already refused upstream: ``01/02/2026`` yields both readings and no
    value, because a guess would be wrong for eleven months of every twelve.

    A year-month is not an instant. Reading ``2026-01`` as the first of January
    asserts a day nobody wrote, and the difference between "January" and "the
    first of January" is exactly the kind of invented precision a timeline makes
    invisible.

    Midnight UTC, because a date has no time zone and inventing one would make
    the same filename mean different instants on two machines, which would stop a
    conclusion from being made again from the same inputs.

    A date after the day the name was read is not a clock. A file cannot have been made after the
    run that finds it, so a name that spells a later day spells a code, a plan or a time of day read
    as a date (``12-19-31`` read as a day in the year 2031). The function allows one day's grace
    past the reading day, which is the run's UTC day (:data:`_NAME_CLOCK_GRACE`).

    A C-library time stamp's date wins. The stamp is the program's own write time. Where a name
    holds a stamp's date (:attr:`~ingence.dictionary.normalize.Atom.stamp`) and another date (a
    sample sheet's, a folder's), the stamp's date is the clock and the other stays a date in the
    name, never its clock. Two stamps on different days are two write times, and the name
    abstains. A stamp that no reading admits is not a stamp. A stamp dated after the reading day is
    set aside, in the same way as a stamp whose weekday disagrees with its date or whose year is
    outside the years the run reads. The name's other date may then be its clock.
    """
    dated = [
        atom
        for atom in atoms
        if atom.kind is AtomKind.DATE and not atom.ambiguous and len(atom.normalised) == 10
    ]
    stamped = [atom for atom in dated if atom.stamp]
    if stamped:
        admitted = [atom for atom in stamped if not _after_reading(atom.normalised, latest)]
        dated = admitted or [atom for atom in dated if not atom.stamp]
    # Count distinct dates, not date atoms. A name that writes its one date twice, once compact and
    # once with separators, has said which day it is. Two different days have not.
    if len({atom.normalised for atom in dated}) != 1:
        return None, TimeSource.UNAVAILABLE
    try:
        moment = datetime.strptime(dated[0].normalised, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        # A calendar-invalid date such as 2026-02-31 parses as a date atom and is
        # not one. Recorded as no clock rather than as a clock nobody can trust.
        return None, TimeSource.UNAVAILABLE
    if latest is not None and moment.date() > latest + _NAME_CLOCK_GRACE:
        return None, TimeSource.UNAVAILABLE
    # A clock after the date is the time of day: one distinct clock sets it, and with none the time
    # is midnight. Two different clocks leave the day and not the hour.
    clocks = {atom.clock for atom in atoms if atom.clock}
    if len(clocks) == 1:
        hours, minutes, seconds = (int(field) for field in clocks.pop().split(":"))
        moment = moment.replace(hour=hours, minute=minutes, second=seconds)
    event_ns = int(moment.timestamp() * 1_000_000_000)
    if not _EARLIEST_INSTANT_NS <= event_ns <= _LATEST_INSTANT_NS:
        # A date that no instant can hold is not a clock. The calendar runs from year 1 to 9999 and
        # a time in nanoseconds from 1677 to 2262, so a name that spells a date outside that span,
        # which is a code or a number far likelier than an event, would otherwise give a value the
        # Index cannot store.
        return None, TimeSource.UNAVAILABLE
    return event_ns, TimeSource.FILENAME


def _after_reading(written: str, latest: date | None) -> bool:
    """Whether a date written ``YYYY-MM-DD`` falls after the reading day and its grace."""
    if latest is None:
        return False
    try:
        day = datetime.strptime(written, "%Y-%m-%d").replace(tzinfo=UTC).date()
    except ValueError:
        return False
    return day > latest + _NAME_CLOCK_GRACE


#: How far after the reading day a name's date may fall and still be its file's clock: one day.
#: The reading day is the run's UTC day, and a laboratory east of UTC names a file on its own
#: calendar, a day ahead of it for part of every day.
_NAME_CLOCK_GRACE = timedelta(days=1)

#: How many runs back :func:`names_read_with` looks for the census that read a run's names.
_READING_DEPTH = 64


def names_read_with(project: Path, run_id: str | None) -> tuple[tuple[int, int], date | None]:
    """Return the years and the reading day that the names of a run were read with.

    A census or a rebind states its years on its run record (``date_years``) and reads its names on
    its own start day. A dive or an inference walks nothing and states none, so the function
    follows the run's parents back to the run that read the names. Anything that tokenises text to
    compare with those names (a search query) reads it the same way, because the years are not
    part of a component's version and nothing else would notice a difference. A project whose
    names were read before the years were stated gives the span every reading then used,
    :data:`UNNAMED_YEARS`, and no day.
    """
    import pyarrow.parquet as pq

    current = run_id
    for _ in range(_READING_DEPTH):
        if not current:
            break
        files = sorted(
            str(path) for path in (project / RUNS_DIR / current / "run").glob("*.parquet")
        )
        if not files:
            break
        # Read with pyarrow, which a page's threads may share, rather than duckdb's one default
        # connection, which they may not.
        rows = pq.read_table(files[0]).to_pylist()
        if not rows:
            break
        record = rows[0]
        years = json.loads(record["date_years_json"]) if record.get("date_years_json") else None
        if years:
            started = str(record.get("started_at") or "")
            try:
                day: date | None = date.fromisoformat(started[:10])
            except ValueError:
                day = None
            return (int(years[0]), int(years[1])), day
        current = record.get("parent_run_id")
    return UNNAMED_YEARS, None


def case_reading_read_with(project: Path, run_id: str | None) -> dict[str, Any] | None:
    """Return the case reading that a run's names were read with, or ``None``.

    The function finds it as :func:`names_read_with` finds the years: it follows the run's parents
    back to the census or rebind that read the names (the one stating its years), and reads that
    run's ``case_reading``. It returns ``None`` where that run states none (written before the
    field existed) or no run read names. The reading is then unknown, never assumed.
    """
    import pyarrow.parquet as pq

    current = run_id
    for _ in range(_READING_DEPTH):
        if not current:
            break
        files = sorted(
            str(path) for path in (project / RUNS_DIR / current / "run").glob("*.parquet")
        )
        if not files:
            break
        rows = pq.read_table(files[0]).to_pylist()
        if not rows:
            break
        record = rows[0]
        # A run that read no names stores the JSON ``null`` for its years, a non-empty string.
        if json.loads(record.get("date_years_json") or "null"):
            stated = json.loads(record.get("case_reading_json") or "null")
            return dict(stated) if stated else None
        current = record.get("parent_run_id")
    return None


def split_extension(entry: Entry, *, store: DictionaryStore | None = None) -> tuple[str, str]:
    """Return an entry's name without its extension, and the extension.

    The longest suffix that the Dictionary recognises wins, not simply the longest suffix. The
    census records the last two suffixes as a compound. That is right for ``archive.tar.gz`` and
    wrong for ``entry.schema.json``: the first is one format spelled in two parts, and the second
    is a naming convention wrapped around an ordinary JSON file. Asking which of the two is a known
    format separates them without anybody enumerating the conventions. Where neither is known, the
    final suffix wins, because that is what a suffix conventionally is.

    Both suffixes are stored casefolded and without the leading dot, and the name
    is sliced by length rather than by matching, so a name whose case differs from
    the stored suffix still splits correctly.
    """
    candidates = [suffix for suffix in (entry.compound_extension, entry.extension) if suffix]
    if store is not None:
        known = [
            suffix
            for suffix in candidates
            if store.look_up(suffix, permitted_kinds=EXTENSION_KINDS) is not None
            or compressed_format(suffix, store) is not None
        ]
        candidates = known or candidates[-1:]
    for suffix in candidates:
        if len(entry.name) > len(suffix) + 1:
            stem = entry.name[: -(len(suffix) + 1)]
            return stem, entry.name[len(stem) + 1 :]
    return entry.name, ""


def compressed_format(suffix: str, store: DictionaryStore) -> str | None:
    """The format a compressed file holds, where its suffix is a format then a compression.

    A compression suffix after a format's own suffix is that format, compressed. So ``.nii.gz``
    reads as a compressed ``nii`` file and not as gzip. The last part must be a spelling that the
    Dictionary marks as wrapping another format (``wraps: true``: ``gz``, ``bz2``), and the part
    before it must be a format that the Dictionary knows. A compound that the Dictionary declares
    whole (``tar.gz``, a compressed archive) is read as declared, before this is asked. Returns the
    inner format's spelling, or None.
    """
    inner, dot, outer = suffix.rpartition(".")
    if not dot or not inner or "." in inner:
        return None
    wrapper = store.look_up(outer, permitted_kinds=EXTENSION_KINDS)
    if wrapper is None or wrapper.sense.properties.get("wraps") is not True:
        return None
    if store.look_up(inner, permitted_kinds=EXTENSION_KINDS) is None:
        return None
    return inner


def _where(entry: Entry, extension: str, *, store: DictionaryStore) -> tuple[Scope, ...]:
    """Return the places this entry is in, besides the region it was asked about.

    Its own folder, where it is in one, and the kind of file it is, where the Dictionary
    knows the kind. A folder scope reaches inward (:func:`scope_applies`), so naming the
    entry's own folder admits every sense declared for it or for any folder above it.
    """
    places: list[Scope] = []
    folder = entry.relative_path.rsplit("/", 1)[0] if "/" in entry.relative_path else ""
    if folder:
        places.append(Scope(ScopeKind.FOLDER, folder))
    if extension:
        # A compressed file is of the type it holds (``S1_R1_001.fastq.gz``, a file of reads).
        inner = compressed_format(extension, store)
        of_type = file_type_scope(store, extension) or (
            file_type_scope(store, inner) if inner is not None else None
        )
        if of_type is not None:
            places.append(of_type)
    return tuple(places)


def _token_of(atom: Atom, theme_kind: str | None) -> str:
    """Return what this atom contributes to a skeleton.

    A recognised term becomes its theme kind, because what matters for grouping
    is the axis it sits on and not which word was chosen. A quantity keeps its
    dimension, because a dose series and a time series differ in exactly that.
    """
    if atom.kind is AtomKind.QUANTITY:
        return f"QUANTITY:{atom.dimension}" if atom.dimension else "QUANTITY"
    if theme_kind is not None:
        return theme_kind
    return _PLACEHOLDER[atom.kind]


def skeleton_of(name: str, atoms: tuple[Atom, ...], kinds: tuple[str | None, ...]) -> str:
    """Return the name with its content abstracted away and its layout kept.

    ``plate_A1_2026-01-04.tif`` becomes ``{vessel}_{INDEXED}_{DATE}.{format}``.
    Braces rather than bare tokens, because two adjacent atoms can be separated by
    nothing at all and ``WORDNUMBER`` would not say where one ended.

    Carries no gene, protein, clone or project code by construction, which is what
    makes a collection of these safe to keep as shape coverage for a pool whose
    contents are confidential.
    """
    parts: list[str] = []
    cursor = 0
    for atom, kind in zip(atoms, kinds, strict=True):
        parts.append(name[cursor : atom.start])
        parts.append("{" + _token_of(atom, kind) + "}")
        cursor = atom.start + len(atom.text)
    parts.append(name[cursor:])
    return "".join(parts)


def read_name(
    entry: Entry,
    *,
    run_id: str,
    normaliser: Normaliser,
    store: DictionaryStore,
    scope: Scope = PROJECT_SCOPE,
    source_scope: SourceScope = SourceScope.NAME,
    with_shape: bool = True,
    with_bindings: bool = True,
) -> NameReading:
    """Tokenise one entry's name into components, its shape, and its bindings.

    One function rather than three, because the shape references component
    identifiers and a binding references both, and computing them separately
    would tokenise every name three times.

    ``with_bindings=False`` records what the name says and nothing about what it
    means. That is the switch behind attenuating the Dictionary: the mechanical
    half of the work is cheap and certain, and the interpretive half can be
    deferred to a stage that has already tried higher-confidence evidence.
    """
    stem, extension = split_extension(entry, store=store)
    # Every lookup for this name admits the senses scoped to where this entry actually is: its own
    # folder, and the kind of file it is. An entry sits in both, and only the caller knows which.
    #
    # The cost is small. A lookup short-circuits the whole applicability test while every sense in
    # the vocabulary is project-scoped, which is true of the shipped one, so the cost on a pool
    # that nobody has taught is constructing two scopes per name. The binding still records the
    # region it was asked about, so no binding identifier moves. A sense that won on the folder or
    # the type says so in its own scope, which is the column that answers "why did this bind here
    # and not there".
    #
    # Without this, the Dictionary would store a folder-scoped answer and honour it, but the
    # census would never pass a folder scope, so "in this folder that word means this" would bind
    # nothing at all.
    asked: tuple[Scope, ...] = (scope, *_where(entry, extension, store=store))
    atoms = list(normaliser.atoms(stem))
    if extension:
        atoms.append(
            Atom(
                text=extension,
                kind=AtomKind.EXTENSION,
                normalised=extension.casefold(),
                start=len(entry.name) - len(extension),
            )
        )
    if not atoms:
        return NameReading(components=(), shape=None, bindings=())

    order: list[str] = []
    found: dict[str, Component] = {}
    hits: dict[str, list[tuple[Atom, Hit | None, str | None]]] = {}
    kinds: list[str | None] = []

    cases = normaliser.cases if with_bindings else None
    domains = normaliser.domains if with_bindings else None
    if cases is not None:
        cases.counts.observe(atoms)
    read: list[tuple[Atom, Hit | None]] = []
    for atom in atoms:
        hit, constraint = _look_up(
            atom,
            store=store,
            scope=asked,
            plural_minimum=normaliser.plural_minimum,
        )
        read.append((atom, hit))
        kinds.append(_kind_of(atom, hit))
        identifier = component_id(
            entry=entry.entry_id,
            component_type=atom.kind.value,
            normalized_value=atom.normalised,
            normalizer=NORMALIZER_NAME,
            normalizer_version=normaliser.version,
            source_scope=source_scope.value,
        )
        order.append(identifier)
        hits.setdefault(identifier, []).append((atom, hit, constraint))
        existing = found.get(identifier)
        if existing is not None:
            # Counted, not repeated. The first spelling in the name keeps the raw
            # value, which is deterministic because atoms arrive in order.
            found[identifier] = existing.model_copy(
                update={"occurrences": existing.occurrences + 1}
            )
            continue
        found[identifier] = Component(
            component_id=identifier,
            entry_id=entry.entry_id,
            run_id=run_id,
            component_type=atom.kind.value,
            normalized_value=atom.normalised,
            raw_value=atom.text,
            source_scope=source_scope,
            normalizer=NORMALIZER_NAME,
            normalizer_version=normaliser.version,
            # The name was read off the filesystem exactly as it is stored. What
            # it *means* is a separate record at a separate rung.
            extraction_tier=ExtractionTier.LITERAL,
            magnitude=atom.magnitude,
            dimension=atom.dimension,
            unit=atom.unit,
        )

    # The file's type and the words only one domain spells say what domain it belongs to. The
    # census counts them once per domain into the file's folder profile.
    voted = domains.observe(entry, read, store=store) if domains is not None else frozenset()
    template = tuple(atom.kind.value for atom in atoms)
    bound: list[DictionaryBinding] = []
    for component in found.values() if with_bindings else ():
        component_hits = hits[component.component_id]
        if cases is not None and all(atom.case_whole for atom, _, _ in component_hits):
            # Every writing of this component is a piece of a spelling that a capital divided. The
            # binding waits until the run's names have all been counted, then binds it whole or as
            # pieces.
            cases.defer(component, component_hits, asked=asked, scope=scope)
            continue
        why = domains.would_defer(component_hits, store=store) if domains is not None else None
        if domains is not None and why is not None:
            # A spelling that two domains hold differently waits until the run has read every
            # name, then binds from the folder's own files. A well-shaped part that the whole text
            # bound is unbound where the folder's names form a well grid.
            domains.defer(
                component,
                component_hits,
                asked=asked,
                scope=scope,
                entry=entry,
                shape=template,
                position=order.index(component.component_id),
                voted=voted,
                why=why,
                positions=tuple(
                    index for index, held in enumerate(order) if held == component.component_id
                ),
            )
            continue
        bound.append(
            _binding_for(component, run_id=run_id, hits=component_hits, scope=scope, store=store)
        )
    bindings = tuple(bound)
    shape = (
        _shape_for(
            entry,
            version=normaliser.version,
            run_id=run_id,
            atoms=tuple(atoms),
            kinds=tuple(kinds),
            identifiers=order,
            source_scope=source_scope,
        )
        if with_shape
        else None
    )
    event_ns, event_source = event_time_from_atoms(atoms, latest=normaliser.read_on)
    _note_folder_year(entry, atoms, normaliser)
    settled: SettledDate | None = None
    if event_ns is None and not any(
        atom.kind is AtomKind.DATE and not atom.ambiguous for atom in atoms
    ):
        # A short date that the name alone leaves open is read by its neighbours. The digits are
        # the name's own, so the clock's source is the name's.
        settled = settled_short_date(entry, stem, normaliser)
        if settled is not None:
            event_ns, event_source = event_time_from_atoms(
                (Atom(settled.date, AtomKind.DATE, settled.date, 0),), latest=normaliser.read_on
            )
            if event_ns is None:
                settled = None
    return NameReading(
        components=tuple(found.values()),
        shape=shape,
        bindings=bindings,
        event_time_ns=event_ns,
        event_time_source=event_source,
        settled=settled,
    )


def _note_folder_year(entry: Entry, atoms: Sequence[Atom], normaliser: Normaliser) -> None:
    """Remember the year a folder's own name dates it to, where its unambiguous dates agree."""
    if entry.entry_type is not EntryType.DIRECTORY:
        return
    years = {
        int(atom.normalised[:4])
        for atom in atoms
        if atom.kind is AtomKind.DATE and not atom.ambiguous
    }
    if len(years) == 1:
        normaliser.folder_years[(entry.root_id, entry.relative_path)] = years.pop()


def _parent_path(relative: str) -> str:
    """The relative path of the folder holding an entry, ``.`` for the root's own children."""
    return relative.rsplit("/", 1)[0] if "/" in relative else "."


def observe_series(
    entries: Iterable[Entry], *, normaliser: Normaliser, store: DictionaryStore
) -> None:
    """Settle each folder's series of short dates by the one field order it allows.

    A walk and a rebind alike call this function with a folder's listing before any name in it is
    read, so both read the same siblings. A series is the siblings in one folder whose names each
    write one short date with one separator. Each name allows the orders that a calendar and the
    years allow it (:meth:`~ingence.dictionary.normalize.Normaliser.short_date_orders`). Where
    every sibling allows one order in common and only one, and some sibling rules another out, the
    series is read in that order. A settled name's evidence is, for each other order it allows,
    the first other sibling by path that does not (:func:`settled_short_date`). A series that is
    consistent in two orders, or in none (two conventions in one folder), settles nothing.

    A sibling none of whose readings could be a clock is not a member of the series. A name whose
    every reading falls after the reading day (plus the day's grace that a name clock has) is a code
    or a plan, and rules nothing out for its neighbours.

    One sibling decides a folder. A single name whose first field is above 12 settles every
    ambiguous sibling day first, so one mistyped name misreads the others with it.
    ``dictionary.date_series: off`` switches the series off (a folder's date still settles).
    """
    if normaliser.date_series == "off":
        return
    latest = normaliser.read_on + _NAME_CLOCK_GRACE if normaliser.read_on is not None else None
    members: dict[tuple[str, str, str], list[tuple[str, frozenset[str]]]] = {}
    for entry in entries:
        if entry.relative_path == ".":
            continue
        stem, _ = split_extension(entry, store=store)
        dates = normaliser.short_dates(stem)
        if len(dates) != 1:
            continue
        joiner, pairs = dates[0]
        if latest is not None and all(date.fromisoformat(day) > latest for _, day in pairs):
            continue
        key = (entry.root_id, _parent_path(entry.relative_path), joiner)
        members.setdefault(key, []).append(
            (entry.relative_path, frozenset(order for order, _ in pairs))
        )
    for key, series in sorted(members.items()):
        allowed = frozenset.intersection(*(orders for _, orders in series))
        if len(allowed) != 1:
            continue
        ruled_out = frozenset.union(*(orders for _, orders in series)) - allowed
        if not ruled_out:
            continue
        normaliser.series_orders[key] = (next(iter(allowed)), tuple(sorted(series)))


def settled_short_date(entry: Entry, stem: str, normaliser: Normaliser) -> SettledDate | None:
    """Return a short date that two field orders allow, settled by its neighbours, or None.

    A short date is read by its neighbours by default, and a folder's date is date information
    that belongs in a file's date. Two neighbours are read:

    - Its folder: the nearest folder above the entry whose own name dates it to one year settles
      the order where exactly one of the name's dates falls in that year. So a folder settles where
      the year could lead or trail, never a day and a month that swap.
    - Its series: the order that its folder's siblings settle (:func:`observe_series`).

    Where both settle, they must agree. Two neighbours that say two dates are a conflict, and the
    name has no clock. Only a name that holds one short date, which read alone gives two dates, is
    settled.
    """
    if entry.relative_path == ".":
        return None
    dates = normaliser.short_dates(stem)
    if len(dates) != 1:
        return None
    joiner, pairs = dates[0]
    orders = dict(pairs)
    if len(set(orders.values())) < 2:
        return None
    said: dict[str, list[tuple[str, str]]] = {}
    order_of: dict[str, str] = {}
    folder = _folder_settles(entry, orders, normaliser)
    if folder is not None:
        reading, path = folder
        said.setdefault(reading, []).append(("folder", path))
        order_of[reading] = " or ".join(
            sorted(order for order, value in orders.items() if value == reading)
        )
    series = normaliser.series_orders.get(
        (entry.root_id, _parent_path(entry.relative_path), joiner)
    )
    if series is not None and series[0] in orders:
        order, members = series
        reading = orders[order]
        # Settle per name, and never against itself: for each other order this name allows, find
        # the first other sibling by path that does not allow it.
        evidence = sorted(
            {
                min(
                    path
                    for path, allows in members
                    if path != entry.relative_path and other not in allows
                )
                for other in orders
                if other != order
            }
        )
        said.setdefault(reading, []).extend(("series", path) for path in evidence)
        order_of[reading] = order
    if len(said) != 1:
        return None
    ((reading, settled_by),) = said.items()
    return SettledDate(date=reading, order=order_of[reading], settled_by=tuple(settled_by))


def _folder_settles(
    entry: Entry, orders: Mapping[str, str], normaliser: Normaliser
) -> tuple[str, str] | None:
    """The date the nearest dated folder above an entry settles, with the folder's path."""
    path = entry.relative_path
    while "/" in path:
        path = path.rsplit("/", 1)[0]
        year = normaliser.folder_years.get((entry.root_id, path))
        if year is None:
            continue
        agreeing = {reading for reading in orders.values() if int(reading[:4]) == year}
        return (agreeing.pop(), path) if len(agreeing) == 1 else None
    return None


def date_order_observation(entry: Entry, settled: SettledDate, *, run_id: str) -> Observation:
    """Return the evidence a person sees for a settled short date.

    The evidence holds the date, the order and the neighbours. It is a census reading of the name
    among its neighbours (a rule of thumb over names, so heuristic). The census records it beside
    the entry's clock rather than in it, because the clock's column holds a time and its source and
    nothing about why.
    """
    return Observation(
        observation_id=observation_id(
            entry=entry.entry_id,
            observer="census",
            observer_version=DATE_ORDER_VERSION,
            key=DATE_ORDER_KEY,
        ),
        entry_id=entry.entry_id,
        run_id=run_id,
        observer="census",
        observer_version=DATE_ORDER_VERSION,
        observed_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        key=DATE_ORDER_KEY,
        value_kind=ValueKind.STRUCTURE,
        value_structure=canonical_json(settled.as_json()),
        extraction_tier=ExtractionTier.HEURISTIC,
        source_scope=SourceScope.NAME,
    )


def observe_carried(
    entries: Iterable[Entry], *, normaliser: Normaliser, store: DictionaryStore
) -> None:
    """Read the names a partial rebind carries for what a run's other names are read by.

    Each folder's year (the neighbour that dates a short date) and each spelling's counts (the
    case reading), so a partial rebind reads its re-read names as a whole rebind would.
    """
    for entry in entries:
        stem, _ = split_extension(entry, store=store)
        atoms = normaliser.atoms(stem)
        _note_folder_year(entry, atoms, normaliser)
        if normaliser.cases is not None:
            normaliser.cases.counts.observe(atoms)


def _look_up(
    atom: Atom,
    *,
    store: DictionaryStore,
    scope: Scope | Sequence[Scope],
    plural_minimum: int = PLURAL_MINIMUM,
) -> tuple[Hit | None, str | None]:
    """Resolve one atom, and say which restriction, if any, reached it.

    Returns the hit beside the constraint marker the binding records. So the one place a
    restriction is applied is the one place it is described. Reading the marker off the atom's kind
    afterwards is right only while every restricted slot is decided by its kind alone, and an
    indexed atom is not: it has two lookups and only the second is restricted.

    A bare number binds only a surface spelled exactly as it is. Matched loosely, a digit would
    reach a sense only by losing the punctuation around it, and would tie a copy marker with one
    read of a sequencing pair. Refusing a number altogether would put the six laser lines out of
    reach of every name that writes one. Matching as written admits those six and nothing else,
    because no shipped surface is a single digit. A one-character word binds no unit, and keeps
    every other reading it has.

    An indexed atom's label is tried only where its whole text reached nothing, and then only
    against the kinds in :data:`INDEX_LABEL_KINDS`. ``rep1`` is a replicate. ``c1`` is a channel
    and stays one, because the whole text answered first.

    The marker names the kind that bound, because two kinds are admitted and a rule that reads the
    marker must be able to tell a cardinality from an ordinal. Where the label answered nothing,
    the marker names the restriction instead, because there is no kind to name and no honest way
    to name one of two.
    """
    if atom.kind is AtomKind.EXTENSION:
        whole = store.look_up(atom.text, scope=scope, permitted_kinds=EXTENSION_KINDS)
        inner = compressed_format(atom.text, store) if whole is None else None
        if inner is not None:
            whole = store.look_up(inner, scope=scope, permitted_kinds=EXTENSION_KINDS)
        return whole, EXTENSION_CONSTRAINT
    if atom.kind is AtomKind.QUANTITY:
        return None, None  # Units resolve through the normaliser's own table.
    if atom.kind is AtomKind.IDENTIFIER:
        return None, IDENTIFIER_CONSTRAINT
    if atom.kind is AtomKind.NUMBER:
        return (
            store.look_up(atom.text, scope=scope, minimum_quality=NUMBER_MINIMUM_QUALITY),
            NUMBER_CONSTRAINT,
        )
    if atom.kind is AtomKind.INDEXED:
        whole = store.look_up(atom.text, scope=scope)
        if (
            whole is not None
            and atom.label in ROUND_LABELS
            and whole.sense.origin is not DictionaryOrigin.USER
            and whole.sense.scope.kind is ScopeKind.PROJECT
        ):
            # A letter ``r`` and a number is a label for a round or a replicate that a laboratory
            # counts. It does not bind through a sense that the whole project holds. Its whole
            # text is a reading only where a sense is declared for this type of file (the
            # sequencing pack's read one and read two, in a file of reads) or a person said what
            # it means.
            whole = None
        if whole is not None or not atom.label:
            return whole, None
        label = store.look_up(atom.label, scope=scope, permitted_kinds=INDEX_LABEL_KINDS)
        if label is None:
            return None, UNBOUND_LABEL_CONSTRAINT
        return label, INDEX_LABEL_CONSTRAINT_OF.get(
            label.sense.theme_kind, UNBOUND_LABEL_CONSTRAINT
        )
    if atom.kind is AtomKind.WORD and len(atom.text) == 1:
        return store.look_up(atom.text, scope=scope, excluded_kinds=UNIT_KINDS), None
    found = store.look_up(atom.text, scope=scope)
    if found is None and atom.kind is AtomKind.WORD:
        # A plural reads as its singular where the singular is a word. The word is a free word of
        # the name, so there is no marker. The match quality says the name wrote another spelling.
        found = store.look_up_plural(atom.text, scope=scope, minimum=plural_minimum)
    if found is None and atom.kind is AtomKind.WORD and " " in atom.normalised:
        # A term spelled in parts, with its last word in the plural: the reader read it as the term
        # (``phase_plates`` as ``phase plate``), which the name did not spell.
        found = store.look_up(atom.normalised, scope=scope)
        if found is not None and found.sense.theme_kind in store.plural_excluded_kinds():
            # A plural never folds onto a doubt word, a date, a status or a unit, written in
            # parts or not (``do_not_uses``).
            found = None
        if found is not None:
            found = replace(found, token=atom.text, quality=MatchQuality.LOOSE)
    if found is None and atom.kind is AtomKind.WORD and " " not in atom.normalised:
        # A slip of a Dictionary spelling reads as that spelling: a word of letters only, bound to
        # a unique candidate near enough, marked corrected.
        found = store.correct(atom.text, scope=scope)
        if found is not None:
            matched = store.corrected_to.get(atom.text.casefold(), "")
            return found, spelling_constraint(matched) if matched else SPELLING_CONSTRAINT
    return found, None


def sense_domains(sense: Sense) -> frozenset[str]:
    """The domains a sense belongs to: its section's, or the ones its ``domains`` property lists.

    A universal sense (no domain) returns the empty set. A sense may list several domains in its
    properties (a dye two fields use, a magnification), so the schema does not change.
    """
    listed = sense.properties.get("domains")
    found: set[str] = set()
    if sense.domain:
        found.add(sense.domain)
    if isinstance(listed, (list, tuple)):
        found.update(str(item) for item in listed if item)
    return frozenset(found)


def universal_default(contenders: Mapping[str, Sense]) -> Sense | None:
    """The one universal sense among tied contenders that differ in domain, or None.

    The universal sense is the resolver's default. Where a spelling ties between exactly one sense
    of no domain and senses of one or more domains, and nothing else decided it, the resolver reads
    the universal sense and marks it assumed. Two universal senses, or senses of domains alone,
    stay a tie.
    """
    universal = [sense for sense in contenders.values() if not sense_domains(sense)]
    if len(universal) != 1 or len(contenders) < 2:
        return None
    return universal[0]


def skew_constraint(entry_id: str | None) -> str:
    """The skew marker citing the deciding folder's entry, or the bare marker where none has one."""
    return f"{DOMAIN_SKEW_CONSTRAINT}:{entry_id}" if entry_id else DOMAIN_SKEW_CONSTRAINT


def is_skew_constraint(constraint: str | None) -> bool:
    """Whether a binding's marker is the skew's, citing a folder or not."""
    return constraint is not None and (
        constraint == DOMAIN_SKEW_CONSTRAINT or constraint.startswith(DOMAIN_SKEW_CONSTRAINT + ":")
    )


#: The kinds of word that say which domain a file belongs to: a technique, an acquisition, an
#: instrument or a channel. A stain, a dye or a fluorescent label also counts (:func:`_label_like`).
#: Never a subject, a culture, a sample, a condition or a format: a cell, a line, a strain or a
#: probe is a bench word every field writes beside its own files.
DOMAIN_VOTING_KINDS = frozenset({"technique", "acquisition", "instrument", "channel"})
_LABEL_WORDS = ("stain", "dye", "fluor", "label")


def _label_like(sense: Sense) -> bool:
    """Whether a reagent sense is a stain, a dye or a fluorescent label, by its term."""
    return sense.theme_kind == "reagent" and any(word in sense.term for word in _LABEL_WORDS)


def _votes_by_word(sense: Sense) -> bool:
    """Whether a word bound to this sense says which domain its file belongs to."""
    return sense.theme_kind in DOMAIN_VOTING_KINDS or _label_like(sense)


#: The three kinds of evidence a profile counts, in the order they decide.
_VOTE_KINDS = ("by_type", "by_word", "by_neighbour")


def profile_in_words(profile: Mapping[str, Any]) -> str:
    """A folder's domain profile as a sentence a person reads without the glossary: the files
    reading as each domain's by their type, by their words and by their neighbours."""
    voting = int(profile.get("voting") or 0)
    files = int(profile.get("files") or voting)
    if not voting:
        return "no file in this folder reads as any domain's"
    said: list[str] = []
    for kind, how in (
        ("by_type", "by their type"),
        ("by_word", "by their words"),
        ("by_neighbour", "by their neighbours"),
    ):
        counted = sorted(
            ((str(name), int(count)) for name, count in dict(profile.get(kind) or {}).items()),
            key=lambda item: (-item[1], item[0]),
        )
        if counted:
            said.append(f"{how}: " + ", ".join(f"{count} {name}" for name, count in counted[:4]))
    return f"{voting} of {files} files read as a domain's; " + "; ".join(said)


def _kind_of(atom: Atom, hit: Hit | None) -> str | None:
    """Return the theme kind a skeleton should show for this atom."""
    if atom.kind is AtomKind.QUANTITY:
        return "unit" if atom.unit else None
    if hit is None or hit.ambiguous:
        return None
    return hit.sense.theme_kind


def _alternative(sense: Sense, *, shadowed: bool) -> BindingAlternative:
    """Return one sense that was not chosen, with the precedence in force."""
    return BindingAlternative(
        term_id=sense.as_record().term_id,
        theme_kind=sense.theme_kind,
        origin=sense.origin,
        origin_rank=origin_rank(sense),
        scope=sense.scope,
        shadowed=shadowed,
    )


def _binding_for(
    component: Component,
    *,
    run_id: str,
    hits: list[tuple[Atom, Hit | None, str | None]],
    scope: Scope,
    store: DictionaryStore,
    decided: DomainDecision | None = None,
) -> DictionaryBinding:
    """Return what the Dictionary made of one component.

    One binding per component even where the component occurred several times and
    was spelled differently each time. Where those spellings reach one sense, the
    binding records the best-matched quality. Where they reach different senses,
    that disagreement is the finding, and the binding says so rather than keeping
    whichever arrived first.

    ``decided`` is the :class:`DomainBinder`'s reading of a tie between domains: the sense it
    settled on with the marker that says why, or no sense and the marker of a tie left standing.
    When it is given, this function does not apply the universal default, because the binder has
    already walked the whole ladder.
    """
    quantity = component.component_type == AtomKind.QUANTITY.value
    # From the lookup that ran rather than from the atom's kind: an indexed atom is
    # restricted only when its label answered, and its kind cannot say whether it did.
    constraint = hits[0][2] if hits else _CONSTRAINT_OF.get(component.component_type)
    shared = _shared_fields(component, run_id=run_id, scope=scope, constraint=constraint)

    if quantity:
        return _unit_binding(hits[0][0], store=store, shared=shared, decided=decided)

    present = [hit for _, hit, _ in hits if hit is not None]
    if not present:
        return DictionaryBinding(**shared, resolution_status=ResolutionStatus.EXAMINED_NO_EVIDENCE)

    # A declared ambiguity, or two spellings of one component reaching two
    # senses. Either way the engine cannot say which was meant, and choosing
    # would invent a fact.
    contenders = {hit.sense.term: hit.sense for hit in present}
    for hit in present:
        contenders.update({sense.term: sense for sense in hit.alternatives})
    settled: tuple[Sense, str] | None = None
    if len(contenders) > 1 and decided is not None:
        if decided.sense is not None and decided.sense.term in contenders:
            settled = (contenders[decided.sense.term], decided.constraint)
        elif decided.constraint:
            shared["constraint"] = decided.constraint
    elif len(contenders) > 1 and (universal := universal_default(contenders)) is not None:
        # The universal sense is the default: read where the domains did not decide, marked
        # assumed, with the domain senses kept as shadowed alternatives.
        settled = (universal, UNIVERSAL_SENSE_CONSTRAINT)
    if settled is not None:
        chosen, marker = settled
        best = min(
            (hit for hit in present if hit.sense.term == chosen.term),
            key=lambda hit: MATCH_QUALITY_ORDER.index(hit.quality),
            default=min(present, key=lambda hit: MATCH_QUALITY_ORDER.index(hit.quality)),
        )
        shadowed = tuple(
            _alternative(sense, shadowed=True)
            for _, sense in sorted(contenders.items())
            if sense.term != chosen.term
        )
        return _resolved(
            {**shared, "constraint": marker},
            chosen,
            quality=best.quality,
            store=store,
            shadowed=shadowed,
            assumed=True,
        )
    if len(contenders) > 1:
        # Every contender, the lookup's first sense included. A tie has no
        # winner: a hit holds one tied sense only because it must hold one, and
        # which sense that is follows the order the sections loaded in. Leaving
        # it out named half of a two-way conflict, and always the same half.
        alternatives = tuple(
            _alternative(sense, shadowed=False) for _, sense in sorted(contenders.items())
        )
        kinds = {sense.theme_kind for sense in contenders.values()}
        return DictionaryBinding(
            **shared,
            resolution_status=ResolutionStatus.EXAMINED_CONFLICTING,
            # Where every candidate sits on one axis the axis is settled even
            # though the value is not, and saying so is more than a bare null.
            theme_kind=kinds.pop() if len(kinds) == 1 else None,
            alternative_count=len(alternatives),
            alternatives=alternatives,
        )

    best = min(present, key=lambda hit: MATCH_QUALITY_ORDER.index(hit.quality))
    shadowed = tuple(_alternative(sense, shadowed=True) for sense in best.shadowed)
    return _resolved(shared, best.sense, quality=best.quality, store=store, shadowed=shadowed)


def _shared_fields(
    component: Component, *, run_id: str, scope: Scope, constraint: str | None
) -> dict[str, object]:
    """The fields every binding of one component carries, whatever it resolved to."""
    quantity = component.component_type == AtomKind.QUANTITY.value
    return {
        "binding_id": dictionary_binding_id(
            component=component.component_id,
            scope=scope,
            resolver=RESOLVER_NAME,
            resolver_version=RESOLVER_VERSION,
        ),
        "component_id": component.component_id,
        "entry_id": component.entry_id,
        "run_id": run_id,
        "source_scope": component.source_scope,
        "resolver": RESOLVER_NAME,
        "resolver_version": RESOLVER_VERSION,
        "method": BindingMethod.UNIT_TABLE if quantity else BindingMethod.SURFACE,
        "lookup_scope": scope,
        "constraint": constraint,
    }


def _unit_binding(
    atom: Atom,
    *,
    store: DictionaryStore,
    shared: dict[str, object],
    decided: DomainDecision | None = None,
) -> DictionaryBinding:
    """Return the binding for a quantity, whose unit the normaliser already resolved.

    A unit that the section files declared confusable stays confusable here. The normaliser
    resolves a unit through its own table rather than through the surface lookup, so a declared
    ambiguity reaches this function on the atom rather than on a hit. Reading only the resolved
    term would silently promote ``63x`` from "sixty-three fold or sixty-three times magnification"
    to whichever of the two the table happened to hold.
    """
    sense = store.sense_for_term(atom.unit) if atom.unit else None
    if sense is None:
        return DictionaryBinding(**shared, resolution_status=ResolutionStatus.EXAMINED_NO_EVIDENCE)
    if atom.ambiguous:
        # The unit the table holds is one of the declared readings rather than
        # the answer, so it is named beside the others and not left out.
        contenders = {sense.term: sense}
        for term in atom.alternatives:
            other = store.sense_for_term(term)
            if other is not None:
                contenders[other.term] = other
        # The domains decided it: ``100x`` is a magnification where the folder's files are a
        # microscope's, and a fold by default elsewhere. The loser is shadowed.
        if decided is not None and decided.sense is not None and decided.sense.term in contenders:
            chosen = contenders[decided.sense.term]
            shadowed = tuple(
                _alternative(other, shadowed=True)
                for _, other in sorted(contenders.items())
                if other.term != chosen.term
            )
            return _resolved(
                {**shared, "constraint": decided.constraint},
                chosen,
                quality=MatchQuality.EXACT,
                store=store,
                shadowed=shadowed,
                assumed=True,
            )
        if decided is not None and decided.constraint:
            shared = {**shared, "constraint": decided.constraint}
        others = tuple(
            _alternative(other, shadowed=False) for _, other in sorted(contenders.items())
        )
        kinds = {other.theme_kind for other in contenders.values()}
        return DictionaryBinding(
            **shared,
            resolution_status=ResolutionStatus.EXAMINED_CONFLICTING,
            theme_kind=kinds.pop() if len(kinds) == 1 else None,
            alternative_count=len(others),
            alternatives=others,
        )
    return _resolved(shared, sense, quality=MatchQuality.EXACT, store=store, shadowed=())


def _resolved(
    shared: dict[str, object],
    sense: Sense,
    *,
    quality: MatchQuality,
    store: DictionaryStore,
    shadowed: tuple[BindingAlternative, ...],
    assumed: bool = False,
) -> DictionaryBinding:
    """Return a binding that reached one sense, with the sense's standing on it.

    ``assumed`` says the resolver took the reading by a rule of thumb rather than by the spelling
    alone: a universal sense read by default, or a domain sense read from a folder's files. A
    resolved binding is a semantic reading whatever decided it (the schema holds that), so the mark
    of an assumption is the binding's constraint (:data:`UNIVERSAL_SENSE_CONSTRAINT`,
    :data:`DOMAIN_SKEW_CONSTRAINT`), which a page lists the assumptions by.
    """
    del assumed
    return DictionaryBinding(
        **shared,
        resolution_status=ResolutionStatus.RESOLVED,
        term_id=store.term_id(sense),
        term=sense.term,
        theme_kind=sense.theme_kind,
        quality=quality,
        sense_origin=sense.origin,
        sense_origin_rank=origin_rank(sense),
        sense_scope=sense.scope,
        sense_scope_rank=SCOPE_PRECEDENCE.get(sense.scope.kind, 0),
        sense_status=sense.status,
        sense_digest=sense.digest,
        alternative_count=len(shadowed),
        alternatives=shadowed,
    )


def _shape_for(
    entry: Entry,
    *,
    version: str = NORMALIZER_VERSION,
    run_id: str,
    atoms: tuple[Atom, ...],
    kinds: tuple[str | None, ...],
    identifiers: list[str],
    source_scope: SourceScope,
) -> NameShape:
    """Build the layout record for one name.

    Each slot addresses its own occurrence, not its value. The end offset is what makes
    ``name[start:end]`` this occurrence's exact text. Components are deduplicated by normalised
    value and keep only the first spelling, so without the end offset the second of two equivalent
    occurrences is unrecoverable.

    The group index is the separator-delimited fragment the slot came from. A fragment ends where a
    separator appears between two atoms, so two atoms written flush against one another belong to
    one group and two divided by an underscore do not. That is a fact about the layout rather than
    a claim about the grammar, which is the only kind of thing this record may carry.

    A hyphen after a quantity does not end its fragment: it first denotes a related modifier.
    ``3mM-Calcium`` is a quantity on a subject, and the two slots share a group. Anywhere else a
    hyphen divides, as a space does.
    """
    slots: list[NameSlot] = []
    cursor = 0
    group = 0
    for ordinal, (atom, kind, identifier) in enumerate(zip(atoms, kinds, identifiers, strict=True)):
        separator = entry.name[cursor : atom.start]
        modifies = (
            separator == "-"
            and ordinal > 0
            and atoms[ordinal - 1].kind is AtomKind.QUANTITY
            and atom.kind is AtomKind.WORD
        )
        if ordinal and separator and not modifies:
            group += 1
        slots.append(
            NameSlot(
                ordinal=ordinal,
                component_id=identifier,
                component_type=atom.kind.value,
                theme_kind=kind,
                separator=separator,
                start=atom.start,
                end=atom.start + len(atom.text),
                group=group,
                case_whole=atom.case_whole,
            )
        )
        cursor = atom.start + len(atom.text)
    skeleton = skeleton_of(entry.name, atoms, kinds)
    return NameShape(
        name_shape_id=name_shape_id(
            entry=entry.entry_id,
            source_scope=source_scope.value,
            normalizer=NORMALIZER_NAME,
            normalizer_version=version,
        ),
        entry_id=entry.entry_id,
        run_id=run_id,
        source_scope=source_scope,
        normalizer=NORMALIZER_NAME,
        normalizer_version=version,
        skeleton=skeleton,
        skeleton_hash=digest_text(skeleton),
        slot_count=len(slots),
        slots=tuple(slots),
    )


@dataclass(frozen=True, slots=True)
class _Deferred:
    """A component whose every writing is a piece of a divided spelling, awaiting its reading."""

    component: Component
    hits: tuple[tuple[Atom, Hit | None, str | None], ...]
    asked: tuple[Scope, ...]
    scope: Scope


class CaseBinder:
    """A run's reading of the spellings a capital divides, whole or as pieces.

    The binder is made once per run and carried by the run's normaliser (``Normaliser.cases``),
    which every walker already holds. A name read on any thread counts its words here and leaves
    the pieces of a divided spelling to be bound when the run has read every name. :meth:`bind`
    then decides each spelling once (:mod:`ingence.dictionary.case_reading`) and binds each
    deferred piece. A spelling read as pieces binds as it would have bound without the binder. A
    spelling read whole binds as the whole spelling, marked :data:`CASE_WHOLE_CONSTRAINT`. A
    component's identity never moves. Only its binding does, and a binding is the Dictionary's
    reading, reproducible from the Index, the Dictionary and the Record.
    """

    def __init__(
        self,
        store: DictionaryStore,
        *,
        answers: Mapping[str, Reading] | None = None,
        whole_over_alone: float = 0.5,
        unknown_whole: str = "pieces",
    ) -> None:
        self.store = store
        self.answers = dict(answers or {})
        self.whole_over_alone = whole_over_alone
        #: Where a spelling read whole has no sense in the Dictionary
        #: (``dictionary.case_reading.unknown_whole``). The default, ``pieces``, binds a piece the
        #: Dictionary knows with its own sense and a piece it does not as the unknown whole, so a
        #: spelling with no known piece stays one word. ``whole`` binds every piece as the unknown
        #: whole, each keeping the sense it has alone as a shadowed alternative. A person's answer
        #: that the spelling is one word reads as ``whole`` whatever this says, because an answer
        #: wins over a default.
        self.unknown_whole = unknown_whole
        self.counts = CaseCounts()
        self._deferred: list[_Deferred] = []
        self._lock = threading.Lock()
        self._readings: dict[str, CaseReading] | None = None

    def defer(
        self,
        component: Component,
        hits: Sequence[tuple[Atom, Hit | None, str | None]],
        *,
        asked: tuple[Scope, ...],
        scope: Scope,
    ) -> None:
        """Hold one component's binding until the run's names have all been counted."""
        with self._lock:
            self._deferred.append(_Deferred(component, tuple(hits), asked, scope))

    def readings(self) -> dict[str, CaseReading]:
        """Every divided spelling's reading, by casefolded spelling, decided once."""
        if self._readings is None:
            evidence = self.counts.evidence()
            self._readings = readings_for(
                evidence,
                knows_whole={
                    folded: self.store.look_up(counted.written, excluded_kinds=UNIT_KINDS)
                    is not None
                    for folded, counted in evidence.items()
                },
                answers=self.answers,
                whole_over_alone=self.whole_over_alone,
            )
        return self._readings

    def summary(self, *, asked_shown: int = 20) -> dict[str, Any]:
        """How many spellings each tier read each way, and the first spellings to ask about.

        For the run's journal: the reading a person may want to check, most names first.
        """
        readings = self.readings()
        tiers = Counter(f"{reading.tier}:{reading.reading.value}" for reading in readings.values())
        evidence = self.counts.evidence()
        asked = sorted(
            (folded for folded, reading in readings.items() if reading.ask),
            key=lambda folded: (-evidence[folded].whole_names, folded),
        )
        return {
            "spellings": len(readings),
            "by_tier": dict(sorted(tiers.items())),
            "asked": len(asked),
            "asked_first": [readings[folded].written for folded in asked[:asked_shown]],
        }

    def bind(self, *, run_id: str) -> list[DictionaryBinding]:
        """The deferred pieces' bindings, in a canonical order."""
        readings = self.readings()
        found: list[DictionaryBinding] = []
        with self._lock:
            deferred = sorted(self._deferred, key=lambda held: held.component.component_id)
        for held in deferred:
            hits: list[tuple[Atom, Hit | None, str | None]] = []
            alone: list[Hit] = []
            for atom, hit, constraint in held.hits:
                whole = atom.case_whole or ""
                reading = readings.get(whole.casefold())
                if reading is not None and reading.reading is Reading.WHOLE:
                    read = self.store.look_up(whole, scope=held.asked, excluded_kinds=UNIT_KINDS)
                    # A person's "one word" wins over the setting. Only a spelling that the counts
                    # read whole keeps its known piece's own sense.
                    if (
                        read is None
                        and hit is not None
                        and self.unknown_whole == "pieces"
                        and reading.tier != "answered"
                    ):
                        hits.append((atom, hit, constraint))
                        continue
                    if read is None and hit is not None:
                        alone.append(hit)
                    hits.append((atom, read, CASE_WHOLE_CONSTRAINT))
                else:
                    hits.append((atom, hit, constraint))
            binding = _binding_for(
                held.component, run_id=run_id, hits=hits, scope=held.scope, store=self.store
            )
            if alone and binding.resolution_status is ResolutionStatus.EXAMINED_NO_EVIDENCE:
                # More evidence never lowers a conclusion's standing silently. Read into an
                # unknown whole, a piece keeps the sense it has alone beside the binding, shadowed.
                senses = {hit.sense.term: hit.sense for hit in alone}
                kept = tuple(
                    _alternative(sense, shadowed=True) for _, sense in sorted(senses.items())
                )
                binding = binding.model_copy(
                    update={"alternatives": kept, "alternative_count": len(kept)}
                )
            found.append(binding)
        return found


# --- a spelling two domains hold differently, read from the folder's own files ---


@dataclass(frozen=True, slots=True)
class DomainDecision:
    """How the :class:`DomainBinder` settled one tie between domains: a sense and the marker that
    says why, or no sense and the marker of a tie left standing (``""`` for a plain tie)."""

    sense: Sense | None
    constraint: str


@dataclass(frozen=True, slots=True)
class _DomainDeferred:
    """A component whose candidates differ in domain, awaiting the folder's profile."""

    component: Component
    hits: tuple[tuple[Atom, Hit | None, str | None], ...]
    asked: tuple[Scope, ...]
    scope: Scope
    #: The folder the file is in: its root and the path of its parent (``""`` at the root).
    folder: tuple[str, str]
    #: The name's template and the place of this component in it, for the slot neighbours.
    shape: tuple[str, ...]
    position: int
    #: The domains the file had already voted from its type and its unambiguous words.
    voted: frozenset[str]
    #: Why it waits: ``domain`` (its candidates differ in domain) or ``well`` (a well-shaped part
    #: the whole text bound, to be unbound where the folder's names form a well grid).
    why: str = "domain"
    #: The places in the template each of :attr:`hits` was written at, in the same order, so a
    #: well-shaped part is judged by its own slot (``C01_c1``: the well's, then the channel's).
    positions: tuple[int, ...] = ()


#: A well-shaped part: one letter A to P and a number 1 to 48.
_WELL_LETTERS = frozenset("abcdefghijklmnop")
_WELL_COLUMNS = 48


def _well_of(atom: Atom) -> tuple[str, int] | None:
    """The row and column a well-shaped indexed atom names, or None."""
    if atom.kind is not AtomKind.INDEXED or not atom.label or atom.index is None:
        return None
    letter = atom.label.casefold()
    if len(letter) != 1 or letter not in _WELL_LETTERS or not 1 <= atom.index <= _WELL_COLUMNS:
        return None
    return letter, atom.index


#: A folder's key in the binder: the root it is under and its relative path (``""`` for the root).
_FolderKey = tuple[str, str]


def _ancestors(folder: _FolderKey) -> list[_FolderKey]:
    """The folder, then each folder above it up to the root's own level, nearest first."""
    root_id, path = folder
    found = [(root_id, path)]
    while path:
        path = path.rsplit("/", 1)[0] if "/" in path else ""
        found.append((root_id, path))
    return found


def domain_profile_observation(
    entry_id: str, profile: Mapping[str, Any], *, run_id: str
) -> Observation:
    """Return the evidence a person sees for a folder's domain reading.

    The evidence is the profile, beside the folder. It is a census reading of the names and types in
    a folder (a rule of thumb, so heuristic). It is stored as an observation on the folder's own
    entry and raised by the ``folder_domain_profile`` rule.
    """
    return Observation(
        observation_id=observation_id(
            entry=entry_id,
            observer="census",
            observer_version=DOMAIN_PROFILE_VERSION,
            key=DOMAIN_PROFILE_KEY,
        ),
        entry_id=entry_id,
        run_id=run_id,
        observer="census",
        observer_version=DOMAIN_PROFILE_VERSION,
        observed_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        key=DOMAIN_PROFILE_KEY,
        value_kind=ValueKind.STRUCTURE,
        value_structure=canonical_json(dict(profile)),
        extraction_tier=ExtractionTier.HEURISTIC,
        source_scope=SourceScope.NAME,
    )


class DomainBinder:
    """A run's reading of the spellings two domains hold differently.

    The binder is made once per run and carried by the run's normaliser (``Normaliser.domains``),
    as the :class:`CaseBinder` is. A name read on any thread counts its file's evidence here and
    leaves a component whose candidates differ in domain to be bound when the run has read every
    name.

    There are three kinds of evidence. Each file is counted once per domain, and tied words never
    vote. (a) The file's type (an ND2 image is microscopy's,
    :func:`~ingence.dictionary.file_types.extension_domains`). (b) A word that only one domain
    spells, bound without a tie (a nuclear stain, a blot). (c) At the end, a tied word that
    stands in a name template whose other words at that place share one domain. A folder's profile
    is its own files' votes and its subfolders'.

    The scope is the nearest folder with at least ``minimum_files`` voting files, then the root,
    then the project. The decision follows a ladder. The leading domain wins where its share of
    the voting files is at least ``minimum_share`` and at least ``minimum_ratio`` times the
    runner-up's (:data:`DOMAIN_SKEW_CONSTRAINT`). Otherwise a prior the person gave wins, where
    exactly one contender is that domain's (:data:`DOMAIN_PRIOR_CONSTRAINT`). Otherwise the
    universal sense wins, marked assumed (:data:`UNIVERSAL_SENSE_CONSTRAINT`). Otherwise the tie
    stays. The tie is a question only where more than ``question_above_files`` files carry the
    word (:data:`DOMAIN_TIE_CONSTRAINT` otherwise). The binder keeps the losers as shadowed
    alternatives. A component's identity never moves. Only its binding does.
    """

    def __init__(
        self,
        store: DictionaryStore,
        *,
        minimum_files: int = 20,
        minimum_share: float = 0.6,
        minimum_ratio: float = 3.0,
        question_above_files: int = 20,
        priors: frozenset[str] = frozenset(),
        extension_domains: Mapping[str, frozenset[str]] | None = None,
    ) -> None:
        self.store = store
        self.minimum_files = max(1, int(minimum_files))
        self.minimum_share = float(minimum_share)
        self.minimum_ratio = max(1.0, float(minimum_ratio))
        self.question_above_files = max(0, int(question_above_files))
        self.priors = frozenset(priors)
        self.extension_domains: Mapping[str, frozenset[str]] = (
            dict(extension_domains) if extension_domains is not None else {}
        )
        self._lock = threading.Lock()
        self._deferred: list[_DomainDeferred] = []
        #: Each folder's own files' votes by kind (type, word, neighbour), files read, files
        #: that voted, and files that voted by each kind.
        self._votes: dict[str, dict[_FolderKey, Counter[str]]] = {kind: {} for kind in _VOTE_KINDS}
        self._files: Counter[_FolderKey] = Counter()
        self._voting: Counter[_FolderKey] = Counter()
        self._voting_by: dict[str, Counter[_FolderKey]] = {kind: Counter() for kind in _VOTE_KINDS}
        #: The folder entries read, so a profile can be stored beside the folder.
        self._folders: dict[_FolderKey, str] = {}
        #: Per folder, template and place: the words bound to one domain there, by domain.
        self._neighbours: dict[tuple[_FolderKey, tuple[str, ...], int], dict[str, set[str]]] = {}
        #: How many files carry each deferred spelling.
        self._word_files: Counter[str] = Counter()
        #: Per folder, template and place, the rows and columns that the well-shaped parts written
        #: there cover. A well slot spans rows and columns, and a channel slot beside it does not
        #: (``A01_c1``), so the two are told apart by their place in the template.
        self._grid: dict[tuple[_FolderKey, tuple[str, ...], int], tuple[set[str], set[int]]] = {}
        self._profiles: dict[_FolderKey, dict[str, Any]] | None = None
        self._project: dict[str, Any] | None = None
        self.outcomes: Counter[str] = Counter()

    # -- during the walk ---------------------------------------------------

    @staticmethod
    def folder_of(entry: Entry) -> _FolderKey:
        """The folder an entry sits in: its root and its parent's path, ``""`` at the root."""
        relative = entry.relative_path
        return (entry.root_id, relative.rsplit("/", 1)[0] if "/" in relative else "")

    @staticmethod
    def contenders(
        hits: Sequence[tuple[Atom, Hit | None, str | None]], *, store: DictionaryStore
    ) -> dict[str, Sense]:
        """Every sense a component's readings stand between, by term; one or none where no tie."""
        found: dict[str, Sense] = {}
        for atom, hit, _ in hits:
            if atom.kind is AtomKind.QUANTITY:
                if atom.ambiguous and atom.unit:
                    for term in (atom.unit, *atom.alternatives):
                        sense = store.sense_for_term(term)
                        if sense is not None:
                            found[sense.term] = sense
                continue
            if hit is None:
                continue
            found[hit.sense.term] = hit.sense
            for sense in hit.alternatives:
                found[sense.term] = sense
        return found

    def would_defer(
        self, hits: Sequence[tuple[Atom, Hit | None, str | None]], *, store: DictionaryStore
    ) -> str | None:
        """Why a component waits for the whole run, or None: ``domain`` where its candidates
        differ in domain, ``well`` where a well-shaped part bound through its whole text."""
        if any(hit is not None and _well_of(atom) is not None for atom, hit, _ in hits):
            return "well"
        contenders = self.contenders(hits, store=store)
        if len(contenders) < 2:
            return None
        return (
            "domain" if len({sense_domains(sense) for sense in contenders.values()}) > 1 else None
        )

    def is_well_grid(self, folder: _FolderKey, shape: tuple[str, ...], position: int) -> bool:
        """Whether the parts written at this place of this template in this folder form a well
        grid: at least two columns, and rows that are contiguous letters or at least three."""
        held = self._grid.get((folder, shape, position))
        if held is None or len(held[0]) < 2 or len(held[1]) < 2:
            return False
        rows = sorted(held[0])
        contiguous = ord(rows[-1]) - ord(rows[0]) == len(rows) - 1
        return contiguous or len(rows) >= 3

    def observe(
        self, entry: Entry, read: Sequence[tuple[Atom, Hit | None]], *, store: DictionaryStore
    ) -> frozenset[str]:
        """Count one name's evidence into its folder, and return the domains the file voted."""
        if entry.entry_type is EntryType.DIRECTORY:
            with self._lock:
                self._folders[(entry.root_id, entry.relative_path)] = entry.entry_id
            return frozenset()
        folder = self.folder_of(entry)
        typed: set[str] = set()
        worded: set[str] = set()
        neighbours: list[tuple[int, str, str]] = []
        wells: list[tuple[int, str, int]] = []
        shape = tuple(atom.kind.value for atom, _ in read)
        # (a) The file's type: its extension, and the leading part of a compound extension
        # (``.nd2.csv`` is an export of an ND2 image: microscopy's), where the table knows it.
        if entry.compound_extension:
            typed.update(self.extension_domains.get(entry.compound_extension.casefold(), ()))
            leading = entry.compound_extension.casefold().split(".", 1)[0]
            typed.update(self.extension_domains.get(leading, ()))
        for position, (atom, hit) in enumerate(read):
            well = _well_of(atom)
            if well is not None:
                wells.append((position, *well))
            if atom.kind is AtomKind.EXTENSION:
                typed.update(self.extension_domains.get(atom.text.casefold(), ()))
                if hit is not None and not hit.ambiguous:
                    typed.update(sense_domains(hit.sense))
                continue
            if hit is None or hit.ambiguous or not _votes_by_word(hit.sense):
                continue
            domains = sense_domains(hit.sense)
            if len(domains) == 1 and atom.kind is AtomKind.WORD:
                neighbours.append((position, next(iter(domains)), atom.normalised))
            worded.update(domains)
        # A file whose type belongs to a domain votes by its type alone. Its words vote only where
        # the type belongs to none.
        voted = typed or worded
        kind = "by_type" if typed else "by_word"
        with self._lock:
            self._files[folder] += 1
            if voted:
                self._voting[folder] += 1
                self._voting_by[kind][folder] += 1
                counter = self._votes[kind].setdefault(folder, Counter())
                for domain in voted:
                    counter[domain] += 1
            for position, domain, word in neighbours:
                place = self._neighbours.setdefault((folder, shape, position), {})
                place.setdefault(domain, set()).add(word)
            for position, letter, column in wells:
                rows, columns = self._grid.setdefault((folder, shape, position), (set(), set()))
                rows.add(letter)
                columns.add(column)
        return frozenset(voted)

    def defer(
        self,
        component: Component,
        hits: Sequence[tuple[Atom, Hit | None, str | None]],
        *,
        asked: tuple[Scope, ...],
        scope: Scope,
        entry: Entry,
        shape: tuple[str, ...],
        position: int,
        voted: frozenset[str],
        why: str = "domain",
        positions: tuple[int, ...] = (),
    ) -> None:
        """Hold one component's binding until the run's names have all been read."""
        held = _DomainDeferred(
            component,
            tuple(hits),
            asked,
            scope,
            self.folder_of(entry),
            shape,
            position,
            voted,
            why,
            positions or (position,),
        )
        with self._lock:
            self._deferred.append(held)
            self._word_files[component.normalized_value.casefold()] += 1

    # -- once every name is read ------------------------------------------

    def _neighbour_vote(self, held: _DomainDeferred) -> str | None:
        """The one domain the other words at this place of the template share, or None."""
        place = self._neighbours.get((held.folder, held.shape, held.position))
        if not place or len(place) != 1:
            return None
        domain, words = next(iter(place.items()))
        return domain if len(words) >= 2 else None

    def _leader_of(self, votes: Mapping[str, int], voting: int) -> str | None:
        """The domain leading one kind's count by enough, or None: at least ``minimum_files``
        files voted by this kind, the leader holds ``minimum_share`` of them and at least
        ``minimum_ratio`` times the runner-up's."""
        if not votes or voting < self.minimum_files:
            return None
        ranked = sorted(votes.items(), key=lambda item: (-item[1], item[0]))
        leader, count = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else 0
        if count / voting >= self.minimum_share and count >= self.minimum_ratio * runner_up:
            return leader
        return None

    def decide_profile(self, profile: Mapping[str, Any]) -> tuple[str | None, str | None]:
        """The domain a profile decides and the kind of evidence that decided it, read
        lexicographically: the files' types first, then their words, then their
        neighbours; None where no kind leads by enough."""
        voting_by = profile.get("voting_by") or {}
        for kind in _VOTE_KINDS:
            leader = self._leader_of(profile.get(kind) or {}, int(voting_by.get(kind, 0)))
            if leader is not None:
                return leader, kind
        return None, None

    def profiles(self) -> dict[_FolderKey, dict[str, Any]]:
        """Every folder's cumulative profile (its own files and its subfolders'), decided once."""
        if self._profiles is not None:
            return self._profiles
        with self._lock:
            # (c) The slot neighbours, last: a tied word among words of one domain votes for it,
            # in a file whose type and words said nothing, once per file and domain.
            seen: set[tuple[str, str]] = set()
            for held in self._deferred:
                domain = self._neighbour_vote(held)
                if domain is None or held.voted or (held.component.entry_id, domain) in seen:
                    continue
                seen.add((held.component.entry_id, domain))
                counter = self._votes["by_neighbour"].setdefault(held.folder, Counter())
                counter[domain] += 1
                if not any(
                    (held.component.entry_id, other) in seen for other in counter if other != domain
                ):
                    self._voting[held.folder] += 1
                    self._voting_by["by_neighbour"][held.folder] += 1
            votes: dict[str, dict[_FolderKey, Counter[str]]] = {kind: {} for kind in _VOTE_KINDS}
            files: Counter[_FolderKey] = Counter()
            voting: Counter[_FolderKey] = Counter()
            voting_by: dict[str, Counter[_FolderKey]] = {kind: Counter() for kind in _VOTE_KINDS}
            folders = set(self._files)
            for kind in _VOTE_KINDS:
                folders |= set(self._votes[kind])
            for folder in folders:
                for above in _ancestors(folder):
                    files[above] += self._files[folder]
                    voting[above] += self._voting[folder]
                    for kind in _VOTE_KINDS:
                        counter = votes[kind].setdefault(above, Counter())
                        counter.update(self._votes[kind].get(folder, Counter()))
                        voting_by[kind][above] += self._voting_by[kind][folder]
            found: dict[_FolderKey, dict[str, Any]] = {}
            for folder in {key for kind in _VOTE_KINDS for key in votes[kind]}:
                found[folder] = {
                    "files": files[folder],
                    "voting": voting[folder],
                    "voting_by": {kind: voting_by[kind][folder] for kind in _VOTE_KINDS},
                    **{kind: dict(sorted(votes[kind][folder].items())) for kind in _VOTE_KINDS},
                }
            whole = {
                kind: sum((counter for counter in self._votes[kind].values()), Counter())
                for kind in _VOTE_KINDS
            }
            self._project = {
                "files": sum(self._files.values()),
                "voting": sum(self._voting.values()),
                "voting_by": {kind: sum(self._voting_by[kind].values()) for kind in _VOTE_KINDS},
                **{kind: dict(sorted(whole[kind].items())) for kind in _VOTE_KINDS},
            }
            self._profiles = found
        return found

    def deciding_profile(
        self, folder: _FolderKey
    ) -> tuple[dict[str, Any] | None, _FolderKey | None]:
        """The profile that decides a word in this folder, with the folder it is: the nearest
        folder with enough voting files, then the root, then the project (no folder); None where
        none has enough."""
        profiles = self.profiles()
        for above in _ancestors(folder):
            held = profiles.get(above)
            if held is not None and held["voting"] >= self.minimum_files:
                return held, above
        if self._project is not None and self._project["voting"] >= self.minimum_files:
            return self._project, None
        return None, None

    def decide(self, held: _DomainDeferred) -> DomainDecision:
        """Walk the ladder for one deferred component."""
        contenders = self.contenders(held.hits, store=self.store)
        profile, where = self.deciding_profile(held.folder)
        leader, _ = self.decide_profile(profile) if profile is not None else (None, None)
        if leader is not None:
            of_leader = [sense for sense in contenders.values() if leader in sense_domains(sense)]
            if len(of_leader) == 1:
                self.outcomes["skew"] += 1
                cited = self._folders.get(where) if where is not None else None
                return DomainDecision(of_leader[0], skew_constraint(cited))
        if self.priors:
            of_prior = [
                sense
                for sense in contenders.values()
                if sense_domains(sense) and sense_domains(sense) <= self.priors
            ]
            if len(of_prior) == 1:
                self.outcomes["prior"] += 1
                return DomainDecision(of_prior[0], DOMAIN_PRIOR_CONSTRAINT)
        universal = universal_default(contenders)
        if universal is not None:
            self.outcomes["universal"] += 1
            return DomainDecision(universal, UNIVERSAL_SENSE_CONSTRAINT)
        word = held.component.normalized_value.casefold()
        if self._word_files[word] > self.question_above_files:
            self.outcomes["tie_asked"] += 1
            return DomainDecision(None, "")
        self.outcomes["tie_kept"] += 1
        return DomainDecision(None, DOMAIN_TIE_CONSTRAINT)

    def bind(self, *, run_id: str) -> list[DictionaryBinding]:
        """The deferred components' bindings, in a canonical order."""
        self.profiles()
        with self._lock:
            deferred = sorted(self._deferred, key=lambda held: held.component.component_id)
        found: list[DictionaryBinding] = []
        for held in deferred:
            if held.why == "well":
                found.append(self._well_binding(held, run_id=run_id))
                continue
            found.append(
                _binding_for(
                    held.component,
                    run_id=run_id,
                    hits=list(held.hits),
                    scope=held.scope,
                    store=self.store,
                    decided=self.decide(held),
                )
            )
        return found

    def _well_binding(self, held: _DomainDeferred, *, run_id: str) -> DictionaryBinding:
        """A well-shaped part: unbound where every writing of it that bound a sense sits in a
        well-grid slot, else bound as it would have been (``C01_c1`` keeps its channel)."""
        bound_slots = [
            position
            for (atom, hit, _), position in zip(held.hits, held.positions, strict=False)
            if hit is not None and _well_of(atom) is not None
        ]
        if not bound_slots or not all(
            self.is_well_grid(held.folder, held.shape, position) for position in bound_slots
        ):
            self.outcomes["well_bound"] += 1
            return _binding_for(
                held.component,
                run_id=run_id,
                hits=list(held.hits),
                scope=held.scope,
                store=self.store,
            )
        self.outcomes["well_unbound"] += 1
        senses = {
            sense.term: sense
            for _, hit, _ in held.hits
            if hit is not None
            for sense in (hit.sense, *hit.alternatives, *hit.shadowed)
        }
        kept = tuple(_alternative(sense, shadowed=True) for _, sense in sorted(senses.items()))
        return DictionaryBinding(
            **_shared_fields(
                held.component, run_id=run_id, scope=held.scope, constraint=WELL_GRID_CONSTRAINT
            ),
            resolution_status=ResolutionStatus.EXAMINED_NO_EVIDENCE,
            alternative_count=len(kept),
            alternatives=kept,
        )

    def observations(self, *, run_id: str) -> list[Observation]:
        """One profile observation per folder entry whose files read as some domain's."""
        profiles = self.profiles()
        found: list[Observation] = []
        for folder, entry_id in sorted(self._folders.items(), key=lambda item: item[1]):
            profile = profiles.get(folder)
            if profile is None or not profile["voting"]:
                continue
            found.append(domain_profile_observation(entry_id, profile, run_id=run_id))
        return found

    def summary(self) -> dict[str, Any]:
        """What the run read: deferred spellings by outcome, folders profiled, the project."""
        profiles = self.profiles()
        return {
            "deferred": len(self._deferred),
            "outcomes": dict(sorted(self.outcomes.items())),
            "folders_profiled": sum(1 for profile in profiles.values() if profile["voting"]),
            "folders_decided": sum(
                1 for profile in profiles.values() if self.decide_profile(profile)[0]
            ),
            "project": self._project,
        }
