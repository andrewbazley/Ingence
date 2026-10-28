"""Morphology and normalisation: turning a written name into comparable atoms.

The mechanical half of synonym resolution, and the half that needs no user input,
which is why it runs first and exhaustively. ``500nM``, ``500 nM``, ``0.5uM`` and
``500 NANOMOLAR`` are one quantity written four ways, and nothing downstream can
group them until something here says so.

There is one normaliser, not two. Two normalisers with different rules would give two answers to
one question. This normaliser is strict: it does not perform a conversion that the engine cannot
justify.

A name must split into atoms, not shatter. ``SubX800ngmL2.5h`` is three things: a label, a mass
concentration and a duration. A tokeniser that split at every letter-digit boundary would produce
``SubX``, ``800``, ``ngmL``, ``2``, ``5``, ``h`` and destroy both quantities. So the normaliser
recognises quantity spans and holds them together before any other splitting happens.

Case is preserved until it is safe to drop it. ``nM`` and ``nm`` differ by nine orders of
magnitude, so unit matching tries the token as written before it tries folding case. Words fold
freely. Symbols do not.

Ambiguity is reported, not resolved. ``01/02/2026`` is the first of February or the second of
January and nothing in the string decides. The atom records both readings and says it is
ambiguous, because guessing would silently corrupt every ordering built on it.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, replace
from enum import StrEnum
from itertools import pairwise
from math import isfinite
from typing import TYPE_CHECKING, Any

from ingence.dictionary.file_types import identifiers as _archive_identifiers
from ingence.dictionary.signs import trailing_sign_spans

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import date

    from ingence.core.settings import DictionarySettings
    from ingence.dictionary.discover import CaseBinder, DomainBinder
    from ingence.dictionary.store import DictionaryStore

#: One unit's conversion data, as the section files carry it. Heterogeneous by
#: nature: a term and a base are names, a factor and an offset are numbers, and a
#: declared ambiguity is a list of terms.
UnitData = dict[str, object]

__all__ = [
    "AMBIGUOUS_DATE_JOINER",
    "CASE_PIECE_MINIMUM",
    "DATE_SERIES",
    "DAY_FIRST",
    "DEFAULT_DATE_SERIES",
    "DEFAULT_GLUED_HALF_MINIMUM",
    "DEFAULT_GLUED_RUNS",
    "DEFAULT_GLUED_WORDS",
    "DEFAULT_KEY_VALUE_PAIRS",
    "DEFAULT_SIX_DIGIT_DATES",
    "GLUED_RUNS",
    "GLUED_WORDS",
    "MONTH_FIRST",
    "NORMALIZER_NAME",
    "NORMALIZER_VERSION",
    "ROUND_LABELS",
    "SIX_DIGIT_DATES",
    "UNNAMED_YEARS",
    "YEAR_FIRST",
    "Atom",
    "AtomKind",
    "Normaliser",
    "UnitData",
    "glued_division",
    "is_month_sense",
    "reading_options",
    "split_indexed",
    "split_words",
]

#: What a component records as the thing that produced it. Part of every
#: component's identity, so that improving the normaliser produces new components
#: rather than silently rewriting old ones.
NORMALIZER_NAME = "ingence.normalize"

#: Bumped whenever tokenisation could produce different output for unchanged input. A component
#: carries this version in its identity. So a bump means a re-scan produces new components rather
#: than silently rewriting old ones, and a past run stays replayable against the reading it
#: actually made. The versions so far:
#:
#: - 1 is the first version recorded on a component. It covers bucketed lookup with a linear
#:   fallback, a computed rather than assumed consumed length, and compound units that survive a
#:   separator.
#: - 2 narrows what is read as a quantity. A one-character unit symbol is not reached by folding
#:   case, and a magnitude that is not finite is not a quantity at all.
#: - 3 admits ``=`` between an indexed atom's label and its number, so a name that states a count
#:   tokenises as one atom rather than as a letter and a number that no longer know about each
#:   other. It re-derives every component identity on every tree, which costs once per project on
#:   the next index.
#: - 4 reads an identifier as one atom of its own kind: a build hash, a hexadecimal digest, a UUID,
#:   a DOI's prefix, an accession or article identifier, a plasmid digested by an enzyme. It also
#:   makes a one-character unit symbol a quantity only where it ends its chunk: ``20x`` and
#:   ``2.5h`` are quantities, and the ``d`` inside ``h7302d9a`` and the ``x`` inside
#:   ``4417xBglII`` are not. On real laboratory folders, half of the ambiguous-term questions
#:   were fragments of this kind, so a person was asked what a hash meant.
#: - 5 reads case boundaries and compounds. A case boundary divides a run of letters only where
#:   both sides are words. A piece shorter than :data:`CASE_PIECE_MINIMUM` letters belongs to its
#:   neighbour, so ``VoRn``, ``ZibKT`` and ``mRNA`` are one term each and ``rawFRAP`` is still two.
#:   A term that the Dictionary spells in parts is read as one where the name joins the parts with
#:   an underscore, a hyphen or a space, or writes them flush. Underscores read as spaces, and a
#:   hyphen joins before it divides.
#: - 6 reads a glued run as one word, so a strain's tail is never read apart from its name. A run
#:   of letters and digits with no separator, which version 5 would cut into several atoms, is one
#:   word where it begins with a letter (a strain, a construct, a mutation, a formula: ``vorn3d``,
#:   ``Q3XY``, ``B7KL2``, ``N2O4``, ``R412W``). The reader reads and asks about it whole. A counter
#:   after a word the Dictionary knows keeps its split (``sample1``), and a run beginning with a
#:   number is still a quantity and what it quantifies (``3mMvorn``). What a whole run is, is the
#:   person's to say: nothing is built in for any one of them. A dot between two letters, or a
#:   letter and a digit, divides such a run (only a decimal point joins), so a name that carries
#:   another file's name with its extension is read as parts. A setting controls the reading
#:   (:data:`GLUED_RUNS`).
#: - 7 reads an escaped space as a space. A name that a web page or a program wrote through
#:   percent-encoding (``kelt%2010min``) is read as the name with its spaces (``kelt 10min``).
#:   Version 6 read it as one glued word that bound nothing. The name's bytes are not touched: every
#:   atom's text and position are the name's own (:func:`_escaped_reading`). Version 7 also reads
#:   a date that a name spells as a date only where a calendar and the plausible span hold it. The
#:   year must be inside the plausible span (a setting). The day and the month must be ones the
#:   calendar holds, whichever way round they are written. One separator must stand between day,
#:   month and year. Anything else date-shaped is read as the numbers it is (``kelt_4217-09-13``, a
#:   sample's number; ``6-2-013``, a counter; ``7-35-26``, a time of day).
#: - 8 reads four more classes together, so a pool is re-read once for all of them. A day, a
#:   month's word and a year is a date where the Dictionary's month senses name the word
#:   (``17Jun2031``, ``3-Mar-2031``, ``09Feb2031``). Each was three atoms before: a number, a word
#:   the Dictionary read as a month, and a number, with no date. A time of day after its date is a
#:   time, not a second date (``10-28-2031_7-11-03_PM``, ``2031-03-04 at 12.02.31``). A run of
#:   three numbers of two digits or fewer that could be hours, minutes and seconds, written
#:   directly after a date or followed by the half of the day, is read as its numbers. A span of
#:   two dates keeps both. A trailing sign stays with its word (``Ca2+``, ``Fe3+``, ``Xyz+``): a
#:   ``+`` after a letter or a digit and before a separator or the end, so the Dictionary's
#:   ``ca2+`` is met exactly. ``Xy+Zw`` still divides, and a trailing minus stays a divider. The
#:   fourth class is in the event clock and not the tokeniser: a name that writes its one date
#:   twice has that date (:func:`ingence.dictionary.discover.event_time_from_atoms`).
#: - 9 limits dates to the years that a person names before a run. A year is a date only among the
#:   years the person says the data spans (``dictionary.years``, by default ending at the reading
#:   run's own year). The limit includes the six-digit year, month and day (``230512``). A number
#:   outside the years is a number. A short date that two field orders allow (the year first or
#:   last: ``22-06-07``) is not a date on its own, because reading one order is a guess. A day and a
#:   month that could swap under one year stay one ambiguous date. A calendar holds each month's own
#:   length, so the thirty-first of November is not a reading. A month-word date may have a
#:   separator on one side of the word and none on the other (``9 Mar2031``, ``9Mar 2031``). A
#:   key-value label (``run-02``) is one indexed atom, as ``run02`` is, where its key is written in
#:   lower case and the name writes two or more such pairs, as a key-value convention does
#:   (``Vorn-2``, ``WT-1`` and a lone ``kelt-2`` stay a word and its number). A list of numbers is
#:   not a date (``1-2-15-16``), and neither is a version after its marker (``v1.2.13``). A trailing
#:   minus is a sign after a word that the Dictionary marks as bearing one (``signed: true``:
#:   ``GFP-``), and a divider everywhere else. A run of letters divided at a capital marks every
#:   piece with the whole spelling (:attr:`Atom.case_whole`), so the binder can read the
#:   laboratory's glued words whole. No cut is made inside an acronym. Capitals followed by
#:   lower-case letters divide before the last capital only where the Dictionary knows the
#:   capitalised word that begins there (``SubXTreated``). They divide after the capitals only where
#:   the Dictionary knows the lower-case word that follows (``ZIBdata``) or the capitals are a
#:   two-letter tag it knows (``WTvorn``). They divide nowhere otherwise (``ZIBylate``). A
#:   two-letter tag that the Dictionary knows is never glued onto its neighbour. From version 9, the
#:   years are not part of a component's version. A run states its years on its run record
#:   (``RunRecord.date_years``), and a run of digits that the years read differently is a different
#:   component by its kind and value already.
#: - 10 reads two more classes. A folder's series of names settles a short date that two field
#:   orders allow. Version 9 left such a date for a neighbour, and read only the dated folder.
#:   The siblings in one folder that write a short date with one separator form a series. A series
#:   allows one field order only when, for example, one sibling has a first field above 12 (day
#:   first), a second field above 12 (month first), or a year that can only lead. Each sibling
#:   that the name alone leaves open is then dated in that order. The reading names the sibling
#:   that settled it (:meth:`Normaliser.short_date_orders`,
#:   :func:`ingence.dictionary.discover.observe_series`). A series consistent in two orders or in
#:   none settles nothing, and a folder's date and a series that disagree settle nothing: the
#:   atoms are unchanged, and the name's clock is what moves. A day and a month that could swap
#:   name both readings in their value (``2031-03-04 or 2031-04-03``). Version 9 wrote the
#:   month-first reading, which every reader of the component took as the day, so two spellings of
#:   one swap were grouped as replicates of one day.
#: - 11 reads three more date and clock shapes found on real names. A date after numbers written
#:   with its separator is the date its bare form is (``scan_7_12_09_2031`` as ``scan_12_09_2031``).
#:   The last three fields of a run of numbers joined by one separator are a date where they are a
#:   day and a month, either order, and a four-digit year inside the years. The numbers before them
#:   are numbers, as after a letter-led token (``pQ7_12_09_2031``). An ISO date is never cut to make
#:   one (``2024-11-30-2020`` is a date and a counter). A run ending in a two-digit year stays the
#:   list that version 9 read (``1-2-15-16``, a well's ``A01_16_2_01``). The rightmost complete date
#:   wins where the run's head could start one (``3_12_09_2031``: the head is its numbers). A
#:   C-library time stamp is its own date. It is what ``ctime`` and ``asctime`` write, with spaces
#:   and colons replaced as a name must (``Tue_Mar__9_14-07-45_2031``). The weekday, the month's
#:   word and the day are one date with the year written after the clock, where the Dictionary's
#:   weekday and month senses name the words and the calendar gives that day that weekday. Its clock
#:   is a time and never a short date, whether or not the stamp's date holds. A clock written with
#:   dots after its date is a time (``2031-03-04 at 10.41.27``), as version 8 read a clock that a
#:   calendar could also read. A clock is a clock where it is written as one, whether or not it
#:   could be a date, and a run that the reader takes for a clock is never offered to a folder's
#:   series as a short date. Nothing else with dots moves (``v1.2.3``, ``12.5.2``, ``7.4``).
#: - 12 reads a C-library time stamp as the program's own write time. A stamp's date is its name's
#:   clock where the name holds another date too (a sample sheet's, a folder's). That other date
#:   stays a date in the name and never its clock (:attr:`Atom.stamp`; version 11 gave such a name
#:   no clock). A stamp whose weekday disagrees with its calendar is still no stamp. A stamp's
#:   weekday and month words are the C library's, so a person who inactivates a weekday or a month
#:   as a word of their names leaves a stamp reading as one. A word that the Dictionary knows,
#:   written in capitals, divides from the word glued after it (``GFPKelt``, ``DAPIzeph``), as a
#:   known word after capitals always did, so a glued spelling keeps its known half whichever side
#:   it is written on (:func:`split_words`). The exception is before an ending that the Dictionary
#:   lists as forming one new word (``word_endings.yaml``: ``ATPase``, ``SUMOylation``). A
#:   two-letter tag never divides from inside a longer run of capitals (``GTPase``, ``HEKcells``).
#: - 13 divides a run of letters glued from two words that the Dictionary knows
#:   (``channelhoechst``), only where the whole is not a Dictionary word, each half is at least a
#:   setting's letters long and one division is plainly the best. The pieces carry the whole
#:   spelling (:attr:`Atom.case_whole`), so the run's case reading and a person's "one word" decide
#:   it exactly as they decide a spelling that a capital divides (:func:`glued_division`). A term
#:   that the Dictionary spells in parts binds across a dot too (``E. coli``, ``E.coli``), as it
#:   does across an underscore, a hyphen, a space or nothing, and only with its words adjacent and
#:   in order. A term spelled in parts also binds with its last word in the plural
#:   (``phase plates``), and a run of joiners reads as one separator (``single__particle``). A
#:   letter ``r`` and a number written alone is an indexed label, never a version (``zeph_R2``). A
#:   laboratory writes a round or a replicate this way. Read as a version, as ``v2`` is, ``R2``
#:   matched both the core's r squared and the sequencing pack's read two. A version keeps its
#:   longer markers (``rev2``, ``v2``) and its dotted numbers (``r1.2``).
#: - 14 reads three more shapes. A unit matched by folding and followed by a lower-case letter does
#:   not stand (``2Plate`` is a number and a word, not two picolitres and ``ate``; ``2pL`` and
#:   ``2PL`` stand, matched as written). A clock after a date is a time on the name's event time
#:   (``2031-03-04_101522``): the hours, minutes and seconds ride on the first of the clock's
#:   numbers (:attr:`Atom.clock`), and the event time is that moment, not midnight. A quantity
#:   written with a space between its number and its unit is a quantity (``500 mM``, ``20 min``),
#:   where the unit is at least two letters and matched whole.
NORMALIZER_VERSION = "14"

#: How a separator-free run of letters and digits is read, where version 5's reading cuts it into
#: several atoms. ``letter_led``, the default, reads a run beginning with a letter as one word.
#: ``whole`` reads every such run as one word, which also reads ``3mMvorn`` and ``20min003``
#: whole. ``split`` is version 5's reading. Measured on two laboratory censuses: the default changes
#: a sixth of the atoms of the names in one and half of those in the other, and the literal reading
#: changes two thirds, most of it numbers glued to words.
GLUED_RUNS = ("letter_led", "whole", "split")
DEFAULT_GLUED_RUNS = "letter_led"

#: Whether a run of letters glued from two Dictionary words divides. ``divide``, the default, reads
#: ``channelhoechst`` as ``channel`` and ``hoechst``, each piece carrying the whole spelling for
#: the case reading to settle (:func:`glued_division`). ``whole`` reads such a run as one word, as
#: version 12 did. The version of every component records the setting wherever it is not the
#: default.
GLUED_WORDS = ("divide", "whole")
DEFAULT_GLUED_WORDS = "divide"

#: The shortest half a glued run divides into: four letters. Measured on the shipped Dictionary:
#: of its 2,448 single words, read with the whole-word guard switched off, 82 divide into two words
#: at three letters, 46 at four and 22 at five. At three, the halves include ``mode|led`` and
#: ``pre|view``. At four, the wrong divisions left are compounds that a person reads either way
#: (``micro|scope``, ``photo|graph``). Five loses ``dapi``, ``mask``, ``cell`` and ``data`` as
#: halves. With the guard, none of the 2,448 divides. The divider also makes no cut after a listed
#: prefix: over a general English list's 176,810 words of eight letters or more, divisions fell
#: from 172 to 85, the rest mostly compounds (``headline``).
DEFAULT_GLUED_HALF_MINIMUM = 4

#: The fewest letters a word read as a plural has, by default: the Dictionary's own
#: (:data:`ingence.dictionary.store.PLURAL_MINIMUM`, where the reason is written).
DEFAULT_PLURAL_MINIMUM = 4

#: How six digits standing alone are read. ``year_first``, the default, reads ``YYMMDD`` as a date
#: inside the years. ``none`` reads six digits as the number they are. The reader never reads them
#: day first: a day-first code would read as another real date, and nothing would mark it. The
#: version of every component records the setting wherever it is not the default.
SIX_DIGIT_DATES = ("year_first", "none")
DEFAULT_SIX_DIGIT_DATES = "year_first"

#: Whether a folder's series of short dates settles their order: ``settle``, the default, or
#: ``off``. One sibling can decide a folder, so a person whose folders mix conventions switches it
#: off. The version of every component records the setting wherever it is not the default.
DATE_SERIES = ("settle", "off")
DEFAULT_DATE_SERIES = "settle"

#: The years a normaliser reads dates in when no run names them (a query, a test, a tool), the
#: first and the last inclusive. Every census and rebind names its own years instead
#: (``dictionary.years``: by default ending at the reading run's own year), so this span is never a
#: run's. It is the plausible span, measured on laboratory censuses: every date a person or a
#: program wrote into a name fell inside it.
UNNAMED_YEARS = (1990, 2035)

#: A dot that divides a glued run: one not between two digits, which is a decimal point.
_DIVIDING_DOT = re.compile(r"(?<!\d)\.|\.(?!\d)")

#: The escape that marks a name as written through percent-encoding: a space, as a web address, a
#: download or a program's output file writes one (RFC 3986's ``%`` and two hexadecimal digits).
_ESCAPED_SPACE = "%20"

#: What each escape reads as in a name written through percent-encoding: the escaped space, and
#: the escaped percent sign an encoder writes for a literal one, so a percentage in an encoded name
#: (``0.4%25``) is still a percentage. No other escape is read. Measured on laboratory censuses:
#: the only escapes an encoder wrote were these two, and every other percent followed by two
#: hexadecimal digits was a percentage glued to what followed it (``4%2Dzib``, ``7%FAvorn``,
#: ``60%031``). The characters an encoder leaves as they are (``-``, ``_``, ``.``, ``~``) are never
#: escaped by one, so reading their escapes would read only those percentages wrongly.
_ESCAPE_READINGS = {"%20": " ", "%25": "%"}

#: A percent sign that begins no escape. An encoder writes a literal percent sign as ``%25``, so a
#: name holding a bare one was not written by an encoder, and nothing in it is read as an escape.
_BARE_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")

#: A run of digits with an optional decimal part and an optional exponent.
_NUMBER = re.compile(r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")

#: Boundaries inside a run of letters: camel case, and an acronym meeting a word.
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

#: The shortest piece a case boundary may leave on either side of it and still divide a run of
#: letters. A capital inside a word marks a word boundary where it joins two words (``rawFRAP``,
#: ``RNASeq``) and is part of the word where one side is a letter or two (a cell line, a construct,
#: a chemical formula, a prefix: ``VoRn``, ``ZibKT``, ``NaOH``, ``mRNA``). Length is the only thing
#: a run of letters says about which, and three is the shortest word a stage, a technique or a
#: treatment is written with.
CASE_PIECE_MINIMUM = 3

#: A Dictionary surface spelled in parts: two or more runs of letters and digits joined by an
#: underscore, a hyphen or a space. The tokeniser divides a name at exactly those characters, so
#: without special handling no such surface could be reached from a name at all.
_SURFACE_IN_PARTS = re.compile(r"[0-9A-Za-z]+(?:[ _-][0-9A-Za-z]+)+")

#: What may join the parts of a term spelled in parts, where a name writes it: an underscore, a
#: hyphen or a space, or nothing at all (``WesternBlot``). A dot also joins them, as an
#: abbreviated genus is written (``E. coli``, ``E.coli``). A run of these joiners is read as one
#: separator (``single__particle``, ``single - particle``).
_PART_JOINER = r"[ _.\-]*"


def _part_or_its_plural(part: str) -> str:
    """A compound's last part as a pattern admitting its plural (``phase plates``)."""
    if not part.isalpha():
        return re.escape(part)
    if len(part) > 1 and part.endswith("y") and part[-2] not in "aeiou":
        return re.escape(part[:-1]) + "(?:y|ies)"
    return re.escape(part) + "(?:e?s)?"


#: The characters :data:`_PART_JOINER` may write between a compound's parts, read out of a match.
_PART_JOINERS = re.compile(r"[ _.\-]+")

#: Unambiguous date forms. Anything where day and month could swap is handled separately and
#: reported as ambiguous rather than guessed.
_ISO_DATE = re.compile(r"^(\d{4})[-_.]?(\d{2})[-_.]?(\d{2})$")
_YEAR_MONTH = re.compile(r"^(\d{4})[-_.](\d{2})$")
#: One separator between day, month and year: a person writes ``2-17-26`` or ``2.17.26``, and
#: ``6_2-17`` is a label's number beside the start of a date, or a pH beside a time (``6.5-40``).
#: Written with two separators, the run is read as the numbers it holds. The pattern is
#: :data:`_SHORT_DATE`, below, which reads the year first too.

#: A day, a month's word and a year, written together: ``17Jun2031``, ``3-Mar-2031``,
#: ``09 Feb 2031``. The month word alternation is built from the Dictionary's month senses when the
#: normaliser is made, so no month is named here. The same separator stands either side of the word,
#: or one on one side and none on the other (``9 Mar2031`` and ``9Mar 2031``). Two different
#: separators (``17-Jun_2031``) stay the numbers and word they are. The year has four digits: a
#: two-digit year after a month's word is a counter as often as a year, and is left to the numbers
#: it is. The day is group 1, the month's word group 3 or 4, the year group 5.
_MONTH_WORD_DATE = (
    r"(?<![0-9A-Za-z])(\d{{1,2}})(?:([ _.\-])({months})\2?|({months})[ _.\-]?)(\d{{4}})"
    r"(?![0-9A-Za-z])"
)

#: A run that could be a time of day written with one separator: hours, minutes and seconds of two
#: digits or fewer. Where it is written as a clock is written (:data:`_CLOCK_BEFORE`,
#: :data:`_CLOCK_AFTER`) it is a time, and is read as the numbers it holds rather than as a second,
#: short-year date.
_TIME_SHAPED = re.compile(r"^(\d{1,2})([-_./])(\d{1,2})\2(\d{2})$|^(\d{2})()(\d{2})(\d{2})$")
_TIME_LIMITS = (23, 59, 59)

#: How a clock is written beside its date. This is a grammar and not a vocabulary, as a version
#: marker's is (:data:`_VERSION`): directly after the date, with only separators between them or the
#: word a clock is read with (``2031-03-04_7-11-03``, ``2031-03-04 at 12.02.31``). A clock may also
#: be followed by the half of the day (``7-11-03_PM``). A second date written further on is not a
#: time: a span (``1-4-31_to_1-8-31``) keeps both its dates, and its name has no clock.
_CLOCK_BEFORE = re.compile(r"[\s_.,;:()\-]*(?:at[\s_.,;:()\-]+)?", re.IGNORECASE)
_CLOCK_AFTER = re.compile(r"[\s_.,;:()\-]*[ap]m(?![0-9A-Za-z])", re.IGNORECASE)

#: A month's canonical value in the Dictionary: two digits, ``01`` to ``12``.
_MONTH_NUMBER = re.compile(r"^(0[1-9]|1[0-2])$")

#: A C-library time stamp: what ``ctime`` and ``asctime`` write, ``Tue Mar  9 14:07:45 2031``,
#: with the spaces a space, an underscore or a hyphen (one kind throughout, the space-padded day's
#: two written as two) and the colons whatever a name holds instead. The weekday and month word
#: alternations are built from the Dictionary's senses when the normaliser is made, so no word is
#: named here.
_TIME_STAMP = (
    r"(?<![0-9A-Za-z])(?P<weekday>{weekdays})(?P<space>[ _\-])(?P<month>{months})"
    r"(?P=space){{1,2}}(?P<day>\d{{1,2}})(?P=space)"
    r"(?P<clock>\d{{1,2}}(?P<colon>[-:._])\d{{2}}(?P=colon)\d{{2}})"
    r"(?P=space)(?P<year>\d{{4}})(?![0-9A-Za-z])"
)

#: How a clock that no calendar reads is written: hours, then minutes and seconds of two digits
#: each, as a screenshot and every clock write them, with nothing glued after it (checked where it
#: is read), so ``1.5.10x`` stays a decimal and a quantity.
_WRITTEN_CLOCK = re.compile(r"\d{1,2}([-_./])\d{2}\1\d{2}")

#: A run of three or more numbers joined by one separator, not continuing a number on its left:
#: where its last three fields are a date after numbers (:meth:`Normaliser._dates_after_numbers`).
_NUMBER_RUN = re.compile(r"(?<!\d)\d+(?P<joiner>[-_./])\d+(?:(?P=joiner)\d+)+(?!\d)")

#: The width of a year a date after numbers must be written with: a two-digit one at a run's end
#: is a list's last number as often as a year (``1-2-15-16``).
_YEAR_AFTER_NUMBERS = 4

#: How many fields a short date is written with: a day, a month and a year.
_DATE_FIELDS = 3

#: A date anywhere in a name, including the separators that would otherwise
#: split it. Matched before anything else, because those separators are exactly
#: the characters that divide atoms.
# Digit lookarounds rather than word boundaries. An underscore is a word character, so \b finds
# no boundary between "04" and "_", and a date followed by an underscore would go unmatched: the
# single most common shape in a filename.
_DATE_IN_TEXT = re.compile(
    r"(?<!\d)(?:\d{4}[-_.]\d{2}[-_.]\d{2}|\d{8}|\d{6}"
    r"|\d{1,2}(?P<joiner>[-_./])\d{1,2}(?P=joiner)\d{1,4})(?!\d)"
)

#: Six digits as a year, a month and a day: a date only where the year, in this century, falls
#: inside the years and a calendar holds the month and the day. The reader never reads them day
#: first: a six-digit code sorts, and a day-first reading would be a second field order that no
#: name can settle alone.
_SIX_DIGIT_DATE = re.compile(r"^(\d{2})(\d{2})(\d{2})$")

#: A short date: three numbers joined by one separator, in every field order a person writes: the
#: year last (``3-17-15``, ``17.3.2015``) or, with all three written in two digits, first
#: (``15-03-17``). Which orders a calendar and the years allow decides the reading
#: (:meth:`Normaliser._short_date`).
_SHORT_DATE = re.compile(r"^(\d{1,2})([-_./])(\d{1,2})\2(\d{1,4})$")

#: The field orders a short date is read in, named so that a folder's series can be settled by
#: order. They are ``day_first`` (day, month, year), ``month_first`` (month, day, year) and
#: ``year_first`` (year, month, day).
DAY_FIRST = "day_first"
#: What joins an ambiguous date's readings in its value, in plain words: a component reading
#: ``2031-03-04 or 2031-04-03`` is not a day, and no reader takes it for one.
AMBIGUOUS_DATE_JOINER = " or "
MONTH_FIRST = "month_first"
YEAR_FIRST = "year_first"

#: A version marker directly before a run of numbers: ``v1.2.13`` is a version, not a date.
_VERSION_MARKER_BEFORE = re.compile(r"(?<![A-Za-z])(?:v|ver|version|rev|revision|r|b|build)$", re.I)

#: A key and its value joined by a hyphen (BIDS writes ``sub-01``, ``run-02``): one to four
#: letters, a hyphen and one to four digits, standing alone between separators and not continuing
#: as a list or a decimal. Read as the one indexed atom ``run02`` already is.
_KEY_VALUE = re.compile(r"(?<![0-9A-Za-z])([A-Za-z]{1,4})-(\d{1,4})(?![0-9A-Za-z])(?![-.]\d)")

#: A trailing minus after a word, followed by a separator, a dot or the end. It is a sign only
#: where the Dictionary marks the word before it as bearing one (``signed: true``).
_TRAILING_MINUS = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]*?)-(?=$|[\s_,;:()\[\]{}/\\|@#&'\".])"
)

#: How many key-value pairs a name must write before each is read as one: two, measured on two
#: laboratory censuses (a lone lower-case pair was a reagent or a day and its number thousands of
#: times). A setting (``dictionary.key_value_pairs``). The version of every component records it
#: wherever it is not the default.
DEFAULT_KEY_VALUE_PAIRS = 2

#: A capital-led run of capitals meeting lower-case letters: where an acronym meets a word.
_ACRONYM_MEETS_WORD = re.compile(r"[A-Z]{2,}(?=[a-z])")

#: A lower-case letter or a digit before a capital: where one word ends and the next begins.
_WORD_MEETS_CAPITAL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

#: Characters that may join the parts of a compound unit. A person writes a mass
#: concentration as ``ug/mL``, and a filename cannot contain a slash: the kernel
#: reads it as a path separator, and macOS stores a slash typed in Finder as a
#: colon on disk. So the same unit reaches a name as ``ug_mL``, ``ug-mL``,
#: ``ug:mL``, ``ug.mL`` or ``ugmL``, and only the last of those matches a surface
#: today. Squashing these out of both sides of a comparison is what lets one
#: written surface stand for all of them.
_UNIT_JOINERS = " /\\-_:."

#: A number followed by unit fragments joined by those characters. Matched
#: against the whole name before anything is split, for the same reason dates
#: are: the characters that join a compound unit are the characters that divide
#: atoms, so splitting first destroys the unit.
_COMPOUND_QUANTITY = re.compile(
    r"(?<![0-9A-Za-z])(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)([^\W\d_]+(?:[ /\\\-_:.][^\W\d_]+)+)"
)

#: Six digits with nothing glued to either side: a clock, where they are shaped as hours, minutes
#: and seconds and written directly after a date.
_SIX_DIGITS = re.compile(r"(?<![0-9A-Za-z])\d{6}(?![0-9A-Za-z])")

#: A number, one space and a unit of two to six letters, with nothing glued to either side:
#: ``500 mM``, ``20 min``. The unit is checked against the table where it is read.
_SPACED_QUANTITY = re.compile(r"(?<![0-9A-Za-z.])(\d+(?:\.\d+)?) ([^\W\d_]{2,6})(?![0-9A-Za-z])")

#: Words a spaced quantity never takes as its unit: the halves of the day and English words that
#: are also unit symbols (``in`` an inch, ``am`` an attometre, ``us`` a microsecond, ``mo`` a
#: month), so "3 pm" and "2 in" stay a number and a word.
_SPACED_NEVER = frozenset(
    {
        "am", "pm", "in", "at", "as", "an", "no", "on", "or", "of", "to", "up", "so", "by",
        "it", "is", "be", "we", "me", "he", "do", "go", "if", "us", "my", "mo", "the", "and",
        "for", "per", "not", "all", "any", "are", "was", "has", "had", "its", "one", "two",
        "ten", "bar", "bit", "cal", "deg", "day", "fig",
    }
)  # fmt: skip

#: A version marker followed by a number: v1, v2.3, rev4, r1.2.
_VERSION = re.compile(r"^(v|ver|version|rev|revision|r|b|build)[-_.]?(\d+(?:\.\d+)*)$", re.I)

#: The labels a round or a replicate shares with a version marker: a letter ``r`` and a number
#: written alone (``R2``) is an indexed label, never a version, because a laboratory writes a round
#: or a replicate this way. Read as a version, as ``v2`` is, ``R2`` matches both the core's r
#: squared and the sequencing pack's read two. Only a dotted number after it is a version
#: (``r1.2``).
ROUND_LABELS = frozenset({"r"})


def _versioned(match: re.Match[str] | None) -> re.Match[str] | None:
    """A version marker's match, where it is one (never ``r`` and a plain number)."""
    if match is None:
        return None
    if match.group(1).casefold() in ROUND_LABELS and "." not in match.group(2):
        return None
    return match


#: A replicate or index marker followed by a number: rep2, r3, n1, well A1.
#:
#: ``=`` joins a label to its number, so ``N=3``, ``n=3`` and ``(N=3)`` are the one indexed atom
#: ``N3`` already was. A person stating a count writes the equals sign, and split at it the
#: statement becomes a lone letter beside a lone number, which is two atoms neither of which says
#: anything.
#:
#: ``n_3`` is deliberately not joined. ``_`` is a separator, removed before a chunk reaches this
#: pattern, so admitting it here would mean admitting it among the separators in
#: :meth:`Normaliser._scan`, and then ``rep_2`` and ``plate_1`` become indexed atoms too. That
#: would change how every name in every pool is cut.
_INDEXED = re.compile(r"^([a-z]{1,4})[-_.=]?(\d{1,4})$", re.I)

#: A normalised indexed atom, read back into the label and index it was written from.
#: The normaliser writes one as its casefolded label followed by its index with leading
#: zeros dropped, and a component keeps neither of the two apart from the other.
_NORMALISED_INDEX = re.compile(r"^([a-z]{1,4})(\d{1,4})$")

# ---------------------------------------------------------------------------
# Identifiers: runs that name one thing and mean nothing a Dictionary could hold.
#
# Each shape is written over its identifier's own grammar, never over a name that revealed
# it, and each errs towards the ordinary reading: a run that is not plainly an identifier is
# read as words and numbers.
# ---------------------------------------------------------------------------

#: A universally unique identifier, eight, four, four, four and twelve hexadecimal digits.
#: Reserved before dates are, because its first group can be eight decimal digits, which is
#: exactly what a compact date looks like.
_UUID = re.compile(
    r"(?<![0-9A-Za-z])[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{12}(?![0-9A-Za-z])"
)

#: A DOI's prefix: the directory indicator ``10``, a dot, and a registrant code of four to
#: nine digits, followed by the character that begins the suffix. A name cannot hold the
#: slash, so the suffix follows whatever a person put in its place, and read as a number the
#: prefix is a decimal nobody wrote.
_DOI_PREFIX = re.compile(r"(?<![0-9A-Za-z.])10\.\d{4,9}(?=[_\-:. ])")

#: A public archive's accession: the patterns the file-type table lists
#: (``dictionary/file_types.yaml``, ``identifiers``), each matched as a whole run of the name, so
#: an accession is one identifier whatever its length. A hyphen inside one (``EMPIAR-10001``,
#: ``S-BIAD123``) is the identifier's own and divides nothing. The reader reads it before any
#: date, as it reads a UUID, and never looks it up or asks about it as a word.
_ARCHIVE_ACCESSIONS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(rf"(?<![0-9A-Za-z]){identifier.pattern}(?![0-9A-Za-z])")
    for identifier in _archive_identifiers()
)

#: A package build's hash, ``h`` and seven hexadecimal digits ending a chunk, as a conda build
#: string writes one after the interpreter it was built for (``py37h6115d3f``). Only the hash
#: is the identifier. What precedes it is read as words and numbers.
_BUILD_HASH = re.compile(r"h[0-9a-f]{7}$")

#: A hexadecimal digest, a commit or a manuscript number: seven or more hexadecimal digits,
#: the whole chunk, letters and digits alternating at least twice. A run of one kind, or of
#: two blocks, is a number with a suffix or a word, and is left to the ordinary reading.
_HEX_RUN = re.compile(r"^[0-9a-f]{7,}$|^[0-9A-F]{7,}$")

#: How often letters and digits must alternate in a hexadecimal run before it is a digest.
_HEX_ALTERNATIONS = 2

#: An accession or an article identifier: a short run of letters, then eight or more digits
#: and check characters, at least seven of them digits and at most two an ``X``, the whole
#: chunk. That is the shape of a publisher's item identifier and of an archive's accession,
#: and it is too long for a counter anybody typed.
_ACCESSION = re.compile(r"^[A-Za-z]{1,6}([0-9Xx]{8,})$")
_ACCESSION_DIGITS = 7
_ACCESSION_CHECKS = 2

#: A plasmid digested by a restriction enzyme: a construct's letters and number, ``x``, and
#: the enzyme as enzymes are written -- a capital and two or three letters of genus and
#: species, an optional strain letter, a Roman numeral (``EcoRI``, ``BglII``, ``HindIII``).
_PLASMID_BY_ENZYME = re.compile(r"^[A-Za-z]{0,6}\d{1,6}x[A-Z][a-z]{2,3}[A-Za-z]?[IVX]{1,4}$")


def reading_options(dictionary: DictionarySettings) -> dict[str, Any]:
    """The settings a project's names are read with, as :class:`Normaliser`'s keyword arguments.

    One place, so a census, a query and ``ingence fix`` construct the one reader, and name the one
    version, for the same settings.
    """
    return {
        "glued_runs": dictionary.glued_runs,
        "six_digit_dates": dictionary.six_digit_dates,
        "key_value_pairs": dictionary.key_value_pairs,
        "date_series": dictionary.date_series,
        "glued_words": dictionary.glued_words.reading,
        "glued_half_minimum": dictionary.glued_words.half_minimum,
        "plural_minimum": dictionary.plural_minimum,
    }


def _alternations(text: str) -> int:
    """How many times a run changes between letters and digits."""
    kinds = [character.isdigit() for character in text]
    return sum(1 for before, after in pairwise(kinds) if before != after)


def _identifier_span(chunk: str) -> tuple[int, int] | None:
    """Return where an identifier lies in one separator-free chunk, or None.

    The whole chunk for a digest, an accession or a plasmid digested by an enzyme. The tail for a
    build hash, whose head is read as words and numbers. Every shape errs towards the ordinary
    reading, because an identifier read as words costs a question and words read as an identifier
    cost a meaning.
    """
    if _PLASMID_BY_ENZYME.match(chunk):
        return 0, len(chunk)
    accession = _ACCESSION.match(chunk)
    if accession:
        tail = accession.group(1)
        digits = sum(character.isdigit() for character in tail)
        if digits >= _ACCESSION_DIGITS and len(tail) - digits <= _ACCESSION_CHECKS:
            return 0, len(chunk)
    if (
        _HEX_RUN.match(chunk)
        and any(character.isdigit() for character in chunk)
        and _alternations(chunk) >= _HEX_ALTERNATIONS
    ):
        return 0, len(chunk)
    build = _BUILD_HASH.search(chunk)
    if (
        build
        and any(character.isdigit() for character in build.group()[1:])
        and any(character.isalpha() for character in build.group()[1:])
    ):
        return build.start(), len(chunk)
    return None


class AtomKind(StrEnum):
    """What one atom of a name turned out to be."""

    #: A word, possibly recognised by the Dictionary.
    WORD = "word"
    #: A number with a unit, converted to the dimension's base.
    QUANTITY = "quantity"
    #: A bare number with no unit. Kept, because it is usually an index.
    NUMBER = "number"
    #: A date, normalised to ISO where that is unambiguous.
    DATE = "date"
    #: A version marker with its number.
    VERSION = "version"
    #: A short label with a trailing number: a replicate, a well, a position.
    INDEXED = "indexed"
    #: The filename's extension, split off before anything else is tokenised.
    #: Not produced by :meth:`Normaliser.atoms`, which never sees it: the caller
    #: removes it first, because an extension is a fact about the file's type and
    #: reading it as an ordinary word is what let ``md`` mean molecular dynamics.
    EXTENSION = "extension"
    #: A run that names one thing and means nothing a Dictionary could hold: a hash, a UUID, an
    #: accession, a DOI's prefix, a digested plasmid. The reader never looks it up and never asks
    #: about it: the binder records that it declined, and no person is asked what a digest means.
    IDENTIFIER = "identifier"


@dataclass(frozen=True, slots=True)
class Atom:
    """One comparable piece of a name."""

    text: str
    kind: AtomKind
    #: The form two differently-written versions of this atom share. Comparing
    #: normalised forms is the whole point. Comparing ``text`` is what the engine is trying to stop
    #: people having to do.
    normalised: str
    start: int

    # Quantities only.
    magnitude: float | None = None
    unit: str | None = None
    dimension: str | None = None
    #: The magnitude converted into the dimension's base unit, which is what
    #: makes 500 nM and 0.5 uM compare equal.
    base_value: float | None = None

    # Indexed atoms and versions.
    label: str | None = None
    index: int | None = None

    #: True where more than one reading is defensible. A consumer must treat an
    #: ambiguous atom as evidence of *something*, never as a settled value.
    ambiguous: bool = False
    #: The readings that were not chosen, so nothing is lost by not guessing.
    alternatives: tuple[str, ...] = ()
    #: The whole spelling a capital divided, on every piece of such a run and on nothing else:
    #: ``zibKelt`` read as ``zib`` and ``Kelt`` gives both ``zibKelt``. What the laboratory means by
    #: the glued spelling is the binder's to read, from the whole run.
    case_whole: str | None = None
    #: A C-library time stamp's date, on that date atom and on nothing else: the program's own
    #: write time, which the name's clock takes over any other date it holds
    #: (:func:`ingence.dictionary.discover.event_time_from_atoms`).
    stamp: bool = False
    #: A clock's time of day, ``HH:MM:SS``, on the first of a clock's reserved numbers and on
    #: nothing else: a time written after a date, which the name's event time takes
    #: (:func:`ingence.dictionary.discover.event_time_from_atoms`).
    clock: str | None = None


def _squash(text: str) -> str:
    """Return text with unit joiners removed and case folded.

    The comparison form for a compound unit, and only for a compound unit. A
    surface with no joiner in it gains nothing here and is deliberately left
    alone, which is what keeps this from loosening 336 of the 358 surfaces to buy
    the 22 that need it.
    """
    return "".join(character for character in text if character not in _UNIT_JOINERS).casefold()


@dataclass(frozen=True, slots=True)
class _UnitSurface:
    """One unit spelling, with its comparison forms precomputed.

    Built once at construction and only read afterwards, which matters because
    the census walks on up to eight threads and shares one normaliser.
    """

    surface: str
    #: ``surface.casefold()``, computed here rather than inside the match loop.
    folded: str
    #: The joiner-free fold, for compound surfaces only. Empty for the rest, and
    #: an empty value means this surface is never compared that way.
    squashed: str
    data: UnitData


def _as_float(value: object) -> float:
    """Coerce a value from a section file to a float.

    Section files are written by people, so a factor may arrive as a number or
    as a string. Coercing here rather than at load time keeps the loader free of
    per-consumer knowledge, which is what lets one file serve the Dictionary and
    the normaliser.
    """
    return float(value) if isinstance(value, (int, float, str)) else 0.0


def split_words(
    text: str,
    *,
    minimum: int = CASE_PIECE_MINIMUM,
    knows: Callable[[str], bool] | None = None,
    endings: frozenset[str] = frozenset(),
) -> list[str]:
    """Split a run of letters at camel-case and acronym boundaries that divide two words.

    ``RNASeq`` becomes ``RNA``, ``Seq`` and ``SubXTreated`` becomes ``SubX``, ``Treated``. A piece
    shorter than ``minimum`` belongs to its neighbour, the one before it where there is one, so
    ``VoRn``, ``ZibKT`` and ``mRNA`` stay whole. The function is not applied to anything containing
    digits, since a run of letters and digits is a quantity or an index until proved otherwise.

    With the Dictionary's knowledge (``knows``), no cut is made inside an acronym. Where capitals
    meet lower-case letters, the run divides before its last capital only where the Dictionary
    knows the capitalised word that begins there (``SubXTreated``). It divides after the capitals
    only where the Dictionary knows the lower-case word that follows (``ZIBdata``) or the capitals
    are a two-letter tag it knows (``WTvorn``). It divides nowhere otherwise (``ZIBylate``, where
    the reading without ``knows`` cut ``ZIB`` and ``ylate`` apart as ``ZI`` and ``Bylate``). A
    two-letter tag that the Dictionary knows is never glued onto its neighbour, nor its neighbour
    onto it. A word that the Dictionary knows, written in capitals, divides from the word after it.
    It divides after the capitals where they are the known word (``DAPIzeph``), and before the last
    capital where the capitals before it are (``GFPKelt``). A tail shorter than a word stays on it
    (``GFPs``), and so does an ending that the Dictionary lists as forming a new word (``endings``:
    ``ATPase``, ``SUMOylation``). A two-letter tag divides only as the whole run of capitals, never
    from inside a longer one (``GTPase`` is not ``GT``). Without ``knows``, every such run divides
    before its last capital.
    """
    if knows is None:
        cuts = [match.start() for match in _CAMEL.finditer(text)]
    else:
        cuts = [match.start() for match in _WORD_MEETS_CAPITAL.finditer(text)]
        for acronym in _ACRONYM_MEETS_WORD.finditer(text):
            capitals_end = acronym.end()
            word_meets = _WORD_MEETS_CAPITAL.search(text, capitals_end)
            word_end = word_meets.start() if word_meets else len(text)
            titled, lower = text[capitals_end - 1 : word_end], text[capitals_end:word_end]
            capitals, before_last = acronym.group(), acronym.group()[:-1]
            if len(titled) >= minimum and knows(titled):
                cuts.append(capitals_end - 1)
            # The run divides after the capitals where the lower-case word is known or they are a
            # two-letter tag. It never divides where the lower case is an ending that forms one new
            # word with them (``ATPase``). A known word in capitals divides from the word after it:
            # after the capitals (``DAPI`` before ``zorl``), or before the last (``GFP`` before
            # ``Kelt``).
            elif (len(lower) >= minimum and knows(lower)) or _is_tag(capitals, knows):
                cuts.append(capitals_end)
            elif lower in endings:
                continue
            elif len(capitals) >= minimum and knows(capitals):
                cuts.append(capitals_end)
            elif len(before_last) >= minimum and knows(before_last):
                cuts.append(capitals_end - 1)
    pieces: list[str] = []
    start = 0
    for cut in sorted(set(cuts)):
        if 0 < cut < len(text):
            pieces.append(text[start:cut])
            start = cut
    pieces.append(text[start:])

    def short(piece: str) -> bool:
        return len(piece) < minimum and not (knows is not None and _is_tag(piece, knows))

    merged: list[str] = []
    for piece in (part for part in pieces if part):
        if merged and (short(piece) or short(merged[-1])):
            merged[-1] += piece
        else:
            merged.append(piece)
    return merged


def glued_division(
    word: str,
    *,
    knows: Callable[[str], bool],
    minimum: int = DEFAULT_GLUED_HALF_MINIMUM,
    prefixes: frozenset[str] = frozenset(),
) -> tuple[str, str] | None:
    """Return where a run of letters glued from two Dictionary words divides, or None.

    ``channelhoechst`` divides into ``channel`` and ``hoechst``. The run divides only where all
    three hold. The whole is not a Dictionary word (a word the Dictionary spells whole is never
    cut). Each half is a Dictionary word at least ``minimum`` letters long. One division is plainly
    the best: of the divisions into two known words, the one whose shorter half is longest
    (``keltz`` and ``orbvan`` over ``kelt`` and ``zorbvan``). Where two are equally good, the run is
    not divided, and is asked about whole as an unknown word. A cut whose left half is a prefix that
    the Dictionary lists as forming one word with what follows (``prefixes``: ``micro``, ``meta``,
    ``trans``) is never made (``Micrographs``, ``metaphase``, ``transform``). Without that rule, 172
    of a general English list's 176,810 words of eight letters or more divide, most of them at such
    a prefix. With it, 85 divide. A longer left half that begins with a prefix still divides
    (``microscope`` and a word glued after it). Whether the laboratory means the glued spelling as
    one word is the case reading's to say, from the pieces' whole spelling
    (:attr:`Atom.case_whole`), never this function's.
    """
    if len(word) < 2 * minimum or knows(word):
        return None
    divisions = [
        cut
        for cut in range(minimum, len(word) - minimum + 1)
        if word[:cut].casefold() not in prefixes and knows(word[:cut]) and knows(word[cut:])
    ]
    if not divisions:
        return None
    shorter = [min(cut, len(word) - cut) for cut in divisions]
    best = max(shorter)
    if shorter.count(best) > 1:
        return None
    cut = divisions[shorter.index(best)]
    return word[:cut], word[cut:]


#: How many capitals a tag the Dictionary knows is written with, where it stands apart from a
#: neighbour shorter than a word would: ``WT`` and ``KO``, a condition's abbreviation.
_TAG_LENGTH = 2


def _is_tag(piece: str, knows: Callable[[str], bool]) -> bool:
    """Whether a piece is a two-capital tag the Dictionary knows."""
    return len(piece) == _TAG_LENGTH and piece.isalpha() and piece.isupper() and knows(piece)


class Normaliser:
    """Turns names into atoms, using the Dictionary's units where it has them."""

    def __init__(
        self,
        store: DictionaryStore | None = None,
        *,
        glued_runs: str = DEFAULT_GLUED_RUNS,
        years: tuple[int, int] = UNNAMED_YEARS,
        read_on: date | None = None,
        six_digit_dates: str = DEFAULT_SIX_DIGIT_DATES,
        key_value_pairs: int = DEFAULT_KEY_VALUE_PAIRS,
        date_series: str = DEFAULT_DATE_SERIES,
        glued_words: str = DEFAULT_GLUED_WORDS,
        glued_half_minimum: int = DEFAULT_GLUED_HALF_MINIMUM,
        plural_minimum: int = DEFAULT_PLURAL_MINIMUM,
    ) -> None:
        #: The fewest letters a word read as a plural has: the binder's, carried here with the
        #: run's other reading settings (``dictionary.plural_minimum``).
        self.plural_minimum = plural_minimum
        if glued_words not in GLUED_WORDS:
            allowed = ", ".join(GLUED_WORDS)
            message = f"glued_words is one of {allowed}, and {glued_words!r} was given"
            raise ValueError(message)
        if glued_half_minimum < 1:
            message = f"glued_half_minimum is one or more, and {glued_half_minimum!r} was given"
            raise ValueError(message)
        #: Whether a run glued from two Dictionary words divides, and its halves' least length.
        self._glued_words = glued_words
        self._glued_half_minimum = glued_half_minimum
        if date_series not in DATE_SERIES:
            allowed = ", ".join(DATE_SERIES)
            message = f"date_series is one of {allowed}, and {date_series!r} was given"
            raise ValueError(message)
        #: Whether a folder's series of short dates settles their order.
        self.date_series = date_series
        if key_value_pairs < 1:
            message = f"key_value_pairs is one or more, and {key_value_pairs!r} was given"
            raise ValueError(message)
        #: How many lower-case key-value pairs a name must write before each is read as one.
        self._key_value_pairs = key_value_pairs
        if glued_runs not in GLUED_RUNS:
            allowed = ", ".join(GLUED_RUNS)
            message = f"glued_runs is one of {allowed}, and {glued_runs!r} was given"
            raise ValueError(message)
        if six_digit_dates not in SIX_DIGIT_DATES:
            allowed = ", ".join(SIX_DIGIT_DATES)
            message = f"six_digit_dates is one of {allowed}, and {six_digit_dates!r} was given"
            raise ValueError(message)
        self._six_digit = six_digit_dates
        first_year, last_year = years
        if first_year > last_year:
            message = (
                "years is a first and a last year, the first no later than the last, "
                f"and {tuple(years)!r} was given"
            )
            raise ValueError(message)
        self._store = store
        self._glued = glued_runs
        #: The years a date a name spells may fall in, first and last inclusive.
        self.years = (first_year, last_year)
        #: The day the reading run started, where a run reads the names: a date a name spells after
        #: it is not a clock (:func:`ingence.dictionary.discover.event_time_from_atoms`).
        self.read_on = read_on
        #: The version every component read by this normaliser records: the module's, and the
        #: reading of glued runs where a setting chose another, so two projects that cut names
        #: differently never share a component identity. The years are not in it: a run states them
        #: on its run record, and a run of digits they read differently is a different component by
        #: its kind and value.
        chosen = [glued_runs] if glued_runs != DEFAULT_GLUED_RUNS else []
        if six_digit_dates != DEFAULT_SIX_DIGIT_DATES:
            chosen.append(f"six_digit_{six_digit_dates}")
        if key_value_pairs != DEFAULT_KEY_VALUE_PAIRS:
            chosen.append(f"key_value_pairs_{key_value_pairs}")
        if date_series != DEFAULT_DATE_SERIES:
            chosen.append(f"date_series_{date_series}")
        if glued_words != DEFAULT_GLUED_WORDS:
            chosen.append(f"glued_words_{glued_words}")
        if glued_half_minimum != DEFAULT_GLUED_HALF_MINIMUM:
            chosen.append(f"glued_half_minimum_{glued_half_minimum}")
        if plural_minimum != DEFAULT_PLURAL_MINIMUM:
            chosen.append(f"plural_minimum_{plural_minimum}")
        self.version = "-".join((NORMALIZER_VERSION, *chosen))
        #: The run's reading of spellings a capital divides, where a run reads names: set by the
        #: census, which made this normaliser, so every walker carries it.
        self.cases: CaseBinder | None = None
        #: The run's reading of spellings two domains share, where a run reads names: set by the
        #: census beside :attr:`cases`, so every walker carries it.
        self.domains: DomainBinder | None = None
        #: The year each folder's own name dates it to, by relative path, where the name holds
        #: unambiguous dates of one year: the neighbour that settles a child's short date whose
        #: year could lead or trail (:func:`ingence.dictionary.discover.read_name`). Filled as a
        #: run reads names, a folder always before what it holds.
        self.folder_years: dict[tuple[str, str], int] = {}
        #: The field order each folder's series of short dates settles, by the root, the folder's
        #: relative path and the separator the series is written with: the order, and each sibling
        #: of the series with the orders it allows, from which a settled name's evidence is the
        #: siblings ruling out an order it allows. Filled as a run lists each folder, before any
        #: name in it is read (:func:`ingence.dictionary.discover.observe_series`).
        self.series_orders: dict[
            tuple[str, str, str], tuple[str, tuple[tuple[str, frozenset[str]], ...]]
        ] = {}
        #: What the Dictionary knows, asked once per spelling (the case and key readings).
        self._known: dict[str, bool] = {}
        #: The endings that form one new word with the capitals before them, the Dictionary's list
        #: (``word_endings.yaml``): ``ATPase`` is never ``ATP`` and a piece.
        self._endings: frozenset[str] = store.word_endings() if store is not None else frozenset()
        #: The prefixes that form one word with what follows them, the Dictionary's list
        #: (``word_endings.yaml``): no run is cut after one.
        self._prefixes: frozenset[str] = store.word_prefixes() if store is not None else frozenset()
        #: The words the Dictionary marks as bearing a sign (``signed: true``), casefolded: a
        #: trailing minus after one of them is its sign, and a divider after anything else.
        self._signed: frozenset[str] = frozenset(
            surface.casefold()
            for sense in (store.senses() if store is not None else ())
            if sense.properties.get("signed") is True
            for surface in sense.surfaces
        )
        # Unit surfaces sorted longest first, so "ngml" wins over "ng" and a
        # compound unit is never split into a prefix that happens to match.
        self._units: list[_UnitSurface] = []
        if store is not None:
            for sense in store.senses():
                if "dimension" not in sense.properties:
                    continue
                data: UnitData = {
                    "term": sense.term,
                    # The sense's scope travels with the unit so a quantity can
                    # name the Dictionary term it resolved to. A unit is resolved
                    # by the table here rather than by a lookup, so without this
                    # the only way back to the term identifier would be a second
                    # lookup that could land on a different sense.
                    "scope": sense.scope,
                    "dimension": sense.properties["dimension"],
                    "factor": float(sense.properties.get("factor", 1.0)),
                    "offset": float(sense.properties.get("offset", 0.0)),
                    "base": sense.properties.get("base"),
                    "ambiguous_with": tuple(sense.properties.get("ambiguous_with", ()) or ()),
                }
                for surface in sense.surfaces:
                    joined = any(character in _UNIT_JOINERS for character in surface)
                    self._units.append(
                        _UnitSurface(
                            surface=surface,
                            folded=surface.casefold(),
                            squashed=_squash(surface) if joined else "",
                            data=data,
                        )
                    )
            self._units.sort(key=lambda unit: (-len(unit.surface), unit.surface))

        # A term that the Dictionary spells in parts: its parts, keyed by how they read with nothing
        # between them, and one pattern finding any of them in a name, longest first so a longer
        # term is never read as a shorter one inside it. A unit is the unit table's and is left to
        # it.
        self._compounds: dict[str, str] = {}
        if store is not None:
            for sense in store.senses():
                if "dimension" in sense.properties:
                    continue
                for surface in sense.surfaces:
                    if _SURFACE_IN_PARTS.fullmatch(surface):
                        parts = re.split(r"[ _-]", surface.casefold())
                        self._compounds.setdefault("".join(parts), " ".join(parts))
        self._compound_pattern = (
            re.compile(
                r"(?<![0-9A-Za-z])(?:"
                + "|".join(
                    _PART_JOINER.join(
                        [
                            *(re.escape(part) for part in spelled.split(" ")[:-1]),
                            _part_or_its_plural(spelled.split(" ")[-1]),
                        ]
                    )
                    for spelled in sorted(
                        set(self._compounds.values()), key=lambda value: (-len(value), value)
                    )
                )
                + r")(?![0-9A-Za-z])",
                re.IGNORECASE,
            )
            if self._compounds
            else None
        )

        # The Dictionary's month senses: a sense of the date theme kind whose canonical value is a
        # month's number, and every surface it spells. A day, one of these words and a year written
        # together is a date (:data:`_MONTH_WORD_DATE`).
        self._months: dict[str, int] = {}
        if store is not None:
            for sense in store.senses():
                if not is_month_sense(sense.theme_kind, sense.canonical):
                    continue
                for surface in sense.surfaces:
                    if surface.isalpha():
                        self._months.setdefault(surface.casefold(), int(sense.canonical))
        self._month_date_pattern = (
            re.compile(
                _MONTH_WORD_DATE.format(
                    months="|".join(
                        re.escape(word) for word in sorted(self._months, key=lambda w: (-len(w), w))
                    )
                ),
                re.IGNORECASE,
            )
            if self._months
            else None
        )

        # The Dictionary's weekday senses: a sense of the date theme kind carrying its ISO weekday
        # (``weekday: 1`` for Monday), every surface it spells and the abbreviation a stamp writes
        # (``stamp_abbreviation: mon``), a property rather than a surface so that it binds nowhere
        # alone. A C-library time stamp begins with one (:data:`_TIME_STAMP`). A stamp's weekday and
        # month words are read as the vocabulary composed them before a person's inactivations.
        # They are the C library's, so a weekday or a month inactivated as a laboratory's word
        # still reads inside a stamp.
        self._weekdays: dict[str, int] = {}
        self._stamp_months: dict[str, int] = {}
        if store is not None:
            for sense in store.senses_before_inactivation():
                if is_month_sense(sense.theme_kind, sense.canonical):
                    for surface in sense.surfaces:
                        if surface.isalpha():
                            self._stamp_months.setdefault(surface.casefold(), int(sense.canonical))
                    continue
                weekday = sense.properties.get("weekday")
                if sense.theme_kind != "date" or not isinstance(weekday, int):
                    continue
                abbreviation = sense.properties.get("stamp_abbreviation")
                spelled = (*sense.surfaces, *([abbreviation] if abbreviation else []))
                for surface in (str(word) for word in spelled):
                    if surface.isalpha():
                        self._weekdays.setdefault(surface.casefold(), weekday)
        self._stamp_pattern = (
            re.compile(
                _TIME_STAMP.format(
                    weekdays=_alternation(self._weekdays), months=_alternation(self._stamp_months)
                ),
                re.IGNORECASE,
            )
            if self._weekdays and self._stamp_months
            else None
        )

        # First-character buckets: a filter, never the authority. A surface is
        # registered under every first character an input could reach it by, and
        # a lookup probes every first character its input could be spelled with.
        # Both halves are needed. Registering "mL" only under "m" while probing
        # only the raw first character loses every uppercase input, and "500ML"
        # is an ordinary filename. The micro sign is the same failure in a less
        # obvious dress: U+00B5 folds to U+03BC, a different codepoint, and
        # units.yaml carries both spellings for most units but not all.
        #: The longest surface, which bounds how much of an input can matter.
        self._longest = max((len(unit.surface) for unit in self._units), default=0)
        #: How many lookups the buckets missed and the full scan had to answer.
        #: Reported rather than assumed, because the fallback's cost is entirely
        #: a fact about the corpus: a laboratory's names needed few such lookups,
        #: a pool of quantity-dense names more than one a name.
        self._fallbacks = 0
        self._buckets: dict[str, list[_UnitSurface]] = {}
        for unit in self._units:
            keys = {form[0] for form in (unit.surface, unit.folded, unit.squashed) if form}
            for key in keys:
                self._buckets.setdefault(key, []).append(unit)

    # -- units ------------------------------------------------------------

    def match_unit(self, text: str) -> tuple[str, UnitData] | None:
        """Return the longest unit whose surface begins this text.

        Longest wins, and an exact match wins a tie. Both halves matter and they pull against each
        other. Preferring exact matches outright would stop at ``ng`` in ``ngmL`` and read a mass
        where a mass concentration was written, because the compound surface differs in case.
        Preferring length outright would let ``nm`` outrank ``nM`` and turn a concentration into a
        length. Longest first, exact on a tie, does both correctly.

        The buckets are a fast path and never the authority. They narrow 358 candidates to a few
        dozen, and where they find nothing the full list is scanned anyway. A filter that is only
        ever allowed to save work cannot change an answer, which is what makes the optimisation
        safe to trust. A test asserts that the two agree across every surface and the adversarial
        corpus, which establishes it rather than assuming it.
        """
        if not text:
            return None
        # Only the opening characters can carry a unit, and a fold can expand a
        # character into two. Trimming here rather than in every comparison makes
        # both the probe and the full scan work on a bounded string regardless of
        # how long the rest of the name is.
        text = text[: self._longest + 2]
        found = self._best_unit(text, self._candidates(text))
        if found is not None:
            return found
        self._fallbacks += 1
        return self._best_unit(text, self._units)

    def _candidates(self, text: str) -> list[_UnitSurface]:
        """Return the surfaces a lookup on this text could possibly reach."""
        if not text:
            return []
        squashed = _squash(text)
        keys = {text[0], text.casefold()[0]}
        if squashed:
            keys.add(squashed[0])
        if len(keys) == 1:
            return self._buckets.get(next(iter(keys)), [])
        # Two or three buckets. Merged in the list's own longest-first order
        # rather than concatenated, because the ranking below reads them in order
        # and a concatenation would put a short surface from the first bucket
        # ahead of a long one from the second.
        merged = [unit for key in keys for unit in self._buckets.get(key, [])]
        merged.sort(key=lambda unit: (-len(unit.surface), unit.surface))
        return merged

    def _best_unit(
        self, text: str, candidates: Sequence[_UnitSurface]
    ) -> tuple[str, UnitData] | None:
        """Rank one candidate list under the longest-wins contract.

        Three qualities, in descending standing: matched as written, matched
        ignoring case, matched ignoring the joiners a filename cannot keep. The
        ordering mirrors the Dictionary's own exact, folded and loose qualities,
        and for the same reason: matching as written is not a guess and each step
        away from it is.
        """
        lowered = text.casefold()
        squashed = _squash(text)
        best: _UnitSurface | None = None
        best_quality = 3
        best_length = 0
        for unit in candidates:
            length = len(unit.surface)
            if best is not None and length < best_length:
                # Candidates arrive longest first, so nothing from here on can
                # win on length and the ties at the current length are settled.
                # Without this the loop scans all 358 surfaces on every call that
                # finds no compound match, which is almost every call.
                break
            if text.startswith(unit.surface):
                quality = 0
            elif length == 1:
                # A one-character symbol is never reached by folding. Words fold freely and symbols
                # must not: folded, ``A`` is ``a`` and ``F`` is ``f``, so any digit followed by one
                # letter becomes a quantity. Running the classifying rules over real laboratory
                # folders showed the cost: 31 of 31 quantities were spurious, and one UUID alone
                # produced six of them: two temperatures, two currents, a duration and a size.
                #
                # The fold stays for everything longer, which is where it earns its place: a
                # compound surface differing only in case is a real spelling of a real unit, and
                # refusing it would read a mass where a mass concentration was written.
                continue
            elif lowered.startswith(unit.folded):
                quality = 1
            elif unit.squashed and squashed.startswith(unit.squashed):
                quality = 2
            else:
                continue
            if best is None or quality < best_quality:
                best, best_quality, best_length = unit, quality, length
                if quality == 0:
                    # Matched as written at the longest length still available.
                    # Nothing later can beat that on either axis.
                    break
        if best is None:
            return None
        return (best.surface, best.data)

    def consumed(self, text: str, surface: str) -> int:
        """Return how many characters of ``text`` a matched surface actually used.

        Never assume that the count is ``len(surface)``. ``casefold`` is not length-preserving:
        ``ß`` folds to ``ss`` and ``İ`` folds to two characters, so a folded match can consume fewer
        characters of the original than the surface has. Joiner-insensitive matching moves the
        length the other way, since ``ugmL`` is four characters and matched the five-character
        ``ug/mL``. Both produce a slice that is off by a character or two, and a slice that is off
        is a quantity carrying the wrong text.

        Computed rather than corrected: grow a prefix until it compares equal to
        the surface under the loosest comparison that could have matched.
        """
        if text.startswith(surface):
            return len(surface)
        folded = surface.casefold()
        squashed = _squash(surface)
        limit = min(len(text), len(surface) + 2)
        for length in range(1, limit + 1):
            prefix = text[:length]
            if prefix.casefold() == folded:
                return length
            if squashed and _squash(prefix) == squashed:
                return length
        return min(len(surface), len(text))

    def to_base(self, magnitude: float, unit: UnitData) -> float:
        """Convert a magnitude into its dimension's base unit.

        The offset exists for temperature and nothing else, and is applied after
        scaling, which is the order that makes Celsius and Fahrenheit both work.
        """
        factor = unit["factor"]
        offset = unit.get("offset", 0.0)
        return magnitude * _as_float(factor) + _as_float(offset)

    # -- tokenising -------------------------------------------------------

    def atoms(self, name: str) -> tuple[Atom, ...]:
        """Split a name into comparable atoms.

        Dates are found before anything is split, because they contain the very
        characters that separate atoms. ``2026-01-04`` split on its hyphens is
        three unrelated numbers, and no amount of later reassembly recovers the
        date reliably.

        Compound units are found the same way and for the same reason. A mass
        concentration is written ``ug/mL``, a filename cannot hold a slash, and
        what reaches a name is ``ug_mL`` or ``ug-mL``: the joiner is one of the
        characters that divides atoms, so splitting first reads a mass where a
        mass concentration was written.

        A name written through percent-encoding is read with its escapes read. The atoms are those
        of the name with each escaped space a space, and each atom carries the name's own text and
        position, escapes included, so ``name[start:end]`` is still exactly what the name holds.
        """
        escaped = _escaped_reading(name)
        if escaped is None:
            return self._atoms(name)
        reading, starts, ends = escaped
        return tuple(
            replace(
                atom,
                text=name[starts[atom.start] : ends[atom.start + len(atom.text) - 1]],
                start=starts[atom.start],
            )
            for atom in self._atoms(reading)
        )

    def _atoms(self, name: str) -> tuple[Atom, ...]:
        """Split a name that holds no escape to be read into comparable atoms."""
        reserved = self._reserved(name)

        found: list[Atom] = []
        cursor = 0
        for start, end, atom in reserved:
            if start < cursor:
                continue
            found.extend(self._scan(name[cursor:start], cursor))
            found.append(atom)
            cursor = end
        found.extend(self._scan(name[cursor:], cursor))
        return tuple(sorted(found, key=lambda item: item.start))

    def _reserved(
        self, name: str, found_clocks: list[tuple[int, int]] | None = None
    ) -> list[tuple[int, int, Atom]]:
        """Return the spans that must survive splitting, in order and disjoint.

        Identifiers that cross a separator are claimed first (a UUID's hyphens, the dot and the
        character after a DOI's prefix), then dates, and a compound unit overlapping either is
        dropped. A UUID's first group can be eight digits that read as a date, so the order is the
        identifier's. A date carries no letters and a compound unit requires them, so that contest
        is not live, but the code states its ordering rather than leaving it to the order the loops
        happen to run in.
        """
        taken: list[tuple[int, int, Atom]] = []
        for pattern in (_UUID, _DOI_PREFIX, *_ARCHIVE_ACCESSIONS):
            for match in pattern.finditer(name):
                if any(match.start() < end and start < match.end() for start, end, _ in taken):
                    continue
                taken.append(
                    (match.start(), match.end(), _identifier(match.group(), match.start()))
                )

        dates: list[tuple[int, int, Atom]] = list(taken)
        # A C-library time stamp comes before any short date, because its clock is one.
        for (first, last), spans, clock in self._time_stamps(name):
            if any(first < end and start < last for start, end, _ in taken):
                continue
            dates.extend(spans)
            if found_clocks is not None:
                found_clocks.append(clock)
        stamped = list(dates)
        # Runs shaped as a time of day that no calendar reads: a clock where written as one.
        clocks: list[tuple[int, int]] = []
        # A list of numbers, or a version after its marker, is not a date: ``1-2-15-16`` holds no
        # date, and ``v1.2.13`` is a version. A date after numbers is one.
        for match in self._date_runs(name):
            if any(match.start() < end and start < match.end() for start, end, _ in stamped):
                continue
            atom = self._whole_chunk(match.group(), match.start())
            if atom is not None and atom.kind is AtomKind.DATE:
                dates.append((match.start(), match.end(), atom))
            elif (
                match.group("joiner")
                and _time_of_day(match.group())
                and _WRITTEN_CLOCK.fullmatch(match.group())
                and not name[match.end() : match.end() + 1].isalnum()
            ):
                clocks.append((match.start(), match.end()))

        if self._month_date_pattern is not None:
            for match in self._month_date_pattern.finditer(name):
                if any(match.start() < end and start < match.end() for start, end, _ in dates):
                    continue
                atom = self._month_word_date(match)
                if atom is not None:
                    dates.append((match.start(), match.end(), atom))
        # Six digits shaped as a time of day are a clock where written as one after its date
        # (``2031-03-04_101522``), tested below with the other candidates.
        for match in _SIX_DIGITS.finditer(name):
            if any(match.start() < end and start < match.end() for start, end, _ in dates):
                continue
            if _time_of_day(match.group()):
                clocks.append((match.start(), match.end()))

        # A time of day after its date is a time: a run that could be hours, minutes and seconds,
        # written as a clock is written after a date (:data:`_CLOCK_BEFORE`, :data:`_CLOCK_AFTER`),
        # is left as the numbers it holds. A run that no calendar reads is a time too where it is
        # written as a clock (``at 10.41.27``), so a clock joined by dots is not read as a decimal
        # and a number.
        candidates: list[tuple[int, int, Atom | None]] = [
            *dates,
            *((start, end, None) for start, end in clocks),
        ]
        candidates.sort(key=lambda span: (span[0], -span[1]))
        kept: list[tuple[int, int, Atom]] = []
        for start, end, atom in candidates:
            if (atom is None or atom.kind is AtomKind.DATE) and _written_as_a_clock(
                name, kept, start, end
            ):
                # Reserved as its three numbers, so a run joined by dots is not read as a date by
                # the chunk it would otherwise fall to.
                kept.extend(_clock_numbers(name, start, end))
                if found_clocks is not None:
                    found_clocks.append((start, end))
                continue
            if atom is not None:
                kept.append((start, end, atom))
        dates = kept

        # A trailing sign stays with its word, found for the reason dates are: the plus divides
        # atoms. A trailing minus does where the word bears a sign.
        signs = list(trailing_sign_spans(name))
        if self._signed:
            signs.extend(
                (match.start(), match.end(), match.group().casefold())
                for match in _TRAILING_MINUS.finditer(name)
                if match.group(1).casefold() in self._signed
            )
        for start, end, signed in sorted(signs):
            if any(start < taken_end and taken_start < end for taken_start, taken_end, _ in dates):
                continue
            dates.append(
                (
                    start,
                    end,
                    Atom(text=name[start:end], kind=AtomKind.WORD, normalised=signed, start=start),
                )
            )

        # A key and its value joined by a hyphen is one indexed atom, found here because the hyphen
        # divides atoms, where the name writes two or more such pairs. A name keyed throughout
        # (``sub-01_ses-02_run-03``) says it follows a key-value convention, and one pair alone is
        # as often a reagent and its replicate. Measured on two laboratory censuses: thousands of
        # such pairs, each losing its word's reading.
        pairs = [
            (match, atom)
            for match in _KEY_VALUE.finditer(name)
            if not any(match.start() < end and start < match.end() for start, end, _ in dates)
            and (atom := self._key_value(match)) is not None
        ]
        if len(pairs) >= self._key_value_pairs:
            dates.extend((match.start(), match.end(), atom) for match, atom in pairs)

        # A term the Dictionary spells in parts, after dates and before compound units: the
        # Dictionary naming the compound is the strongest reading a joiner can have.
        if self._compound_pattern is not None:
            for match in self._compound_pattern.finditer(name):
                if any(match.start() < end and start < match.end() for start, end, _ in dates):
                    continue
                dates.append((match.start(), match.end(), self._compound(match)))

        spans = list(dates)
        for match in _COMPOUND_QUANTITY.finditer(name):
            atom = self._compound_quantity(match)
            if atom is None:
                continue
            start, end = atom.start, atom.start + len(atom.text)
            if any(start < taken_end and taken_start < end for taken_start, taken_end, _ in dates):
                continue
            spans.append((start, end, atom))
        # A quantity written with a space between its number and its unit (``500 mM``, ``20 min``):
        # the unit is at least two letters, matched whole, as written or ignoring case, and a word
        # of letters alone follows the space.
        for match in _SPACED_QUANTITY.finditer(name):
            atom = self._spaced_quantity(match)
            if atom is None:
                continue
            start, end = atom.start, atom.start + len(atom.text)
            if any(start < taken_end and taken_start < end for taken_start, taken_end, _ in spans):
                continue
            spans.append((start, end, atom))

        spans.sort(key=lambda span: (span[0], -span[1]))
        accepted: list[tuple[int, int, Atom]] = []
        reached = -1
        for start, end, atom in spans:
            if start < reached:
                continue
            accepted.append((start, end, atom))
            reached = end
        return accepted

    def _time_stamps(
        self, name: str
    ) -> list[tuple[tuple[int, int], list[tuple[int, int, Atom]], tuple[int, int]]]:
        """Each C-library time stamp in a name: its span, its reserved atoms and its clock.

        The clock is a time wherever the stamp is written. Its three numbers are reserved, so no
        calendar reads them as a short date. The date is the weekday, the month's word and the day
        read with the year after the clock: one date atom from the weekday to the day. The calendar
        must hold the day, the year must be inside the years and the calendar must give that day
        that weekday. A stamp is written by a clock that knows the weekday, so one that disagrees
        was not, and is left to its words and numbers. The year stays the number it is written
        as.
        """
        if self._stamp_pattern is None:
            return []
        found = []
        for match in self._stamp_pattern.finditer(name):
            clock = (match.start("clock"), match.end("clock"))
            # The stamp's date is the name's clock at midnight. The time of day rides on a clock
            # written after a plain date, not on a stamp's.
            spans = [
                (start, end, replace(atom, clock=None))
                for start, end, atom in _clock_numbers(name, *clock)
            ]
            month = self._stamp_months[match.group("month").casefold()]
            day, year = int(match.group("day")), int(match.group("year"))
            if (
                _calendar_holds(month, day, year)
                and self._plausible(year)
                and calendar.weekday(year, month, day) + 1
                == self._weekdays[match.group("weekday").casefold()]
            ):
                spans.insert(
                    0,
                    (
                        match.start(),
                        match.end("day"),
                        Atom(
                            text=name[match.start() : match.end("day")],
                            kind=AtomKind.DATE,
                            normalised=f"{year}-{month:02d}-{day:02d}",
                            start=match.start(),
                            stamp=True,
                        ),
                    ),
                )
            found.append(((match.start(), match.end()), spans, clock))
        return found

    def _dates_after_numbers(self, name: str) -> list[re.Match[str]]:
        """Each date written at the end of a run of numbers joined by its separator.

        ``scan_7_12_09_2031`` is a counter and a date: the run's last three fields are read as the
        bare form ``12_09_2031`` is, where they are a day and a month and a four-digit year the
        reader accepts, and the numbers before them are numbers. The rightmost complete date wins,
        and what precedes it is read again the same way, so two dates written back to back are both
        read and a head that could start a date with a two-digit year (``3_12_09``) is its numbers.
        Where the run's first field ends a letter-led token (``pQ7_12_09_2031``), that field is the
        token's, and three fields after it are the date the bare form is, with the same four-digit
        year. A run ending in a two-digit year stays a list (``1-2-15-16``, ``A01_16_2_01``), and a
        version marker before the run makes it a version. No candidate cuts into an ISO date's year,
        month and day (:func:`_iso_shapes`): ``2024-11-30-2020`` is a date and a counter, never
        2020-11-30.
        """
        found: list[re.Match[str]] = []
        for run in _NUMBER_RUN.finditer(name):
            if _VERSION_MARKER_BEFORE.search(name[: run.start()]):
                continue
            fields = [
                (run.start() + field.start(), run.start() + field.end())
                for field in re.finditer(r"\d+", run.group())
            ]
            # An ISO date is never cut: a candidate reaching into a year, a month and a day written
            # in that order is not a date after numbers (``2024-11-30-2020``).
            isos = _iso_shapes(name, fields)
            glued = run.start() > 0 and name[run.start() - 1].isalpha()
            if glued and len(fields) == _DATE_FIELDS + 1:
                # The same four-digit year as after numbers: a well or a position and its indices
                # (``A01_16_2_01``) are not a date.
                date = _DATE_IN_TEXT.fullmatch(name, fields[1][0], fields[-1][1])
                if (
                    date is not None
                    and fields[-1][1] - fields[-1][0] == _YEAR_AFTER_NUMBERS
                    and not _cuts(date.start(), date.end(), isos)
                    and self._is_date(date)
                ):
                    found.append(date)
                continue
            # From the right, three fields at a time: a run's head of three is read again only
            # after a date was read behind it (alone, it is the bare form).
            count, peeled = len(fields), False
            while count > _DATE_FIELDS or (peeled and count == _DATE_FIELDS):
                first, last = fields[count - _DATE_FIELDS], fields[count - 1]
                if last[1] - last[0] != _YEAR_AFTER_NUMBERS or _cuts(first[0], last[1], isos):
                    break
                date = _DATE_IN_TEXT.fullmatch(name, first[0], last[1])
                if date is None or not self._is_date(date):
                    break
                found.append(date)
                count, peeled = count - _DATE_FIELDS, True
        return found

    def _is_date(self, match: re.Match[str]) -> bool:
        """Whether a date-shaped run is read as a date by the chunk it is."""
        atom = self._whole_chunk(match.group(), match.start())
        return atom is not None and atom.kind is AtomKind.DATE

    def _date_runs(self, text: str) -> list[re.Match[str]]:
        """Return every date-shaped run a reader tries, left to right.

        The runs are those :data:`_DATE_IN_TEXT` finds, less a list's members and a version's
        numbers (:func:`_listed_or_versioned`), and each date written at the end of a run of numbers
        (:meth:`_dates_after_numbers`), which a list's first match would otherwise swallow.
        """
        tails = self._dates_after_numbers(text)
        runs = list(tails)
        for match in _DATE_IN_TEXT.finditer(text):
            if any(match.start() < tail.end() and tail.start() < match.end() for tail in tails):
                continue
            if match.group("joiner") and _listed_or_versioned(text, match):
                continue
            runs.append(match)
        return sorted(runs, key=lambda match: match.start())

    def knows(self, word: str) -> bool:
        """Whether the Dictionary reads a word as a sense other than a unit, asked once.

        A unit is left out because a capital and a lower-case letter spell one far more often by
        accident than by intent (``Mm``, ``Na``), and the readings that ask are about words.
        """
        found = self._known.get(word)
        if found is None:
            found = (
                self._store is not None
                and self._store.look_up(word, excluded_kinds=_UNIT_KIND) is not None
            )
            self._known[word] = found
        return found

    def _key_value(self, match: re.Match[str]) -> Atom | None:
        """One indexed atom for a key joined to its value by a hyphen, or None.

        Only a key written in lower case counts, as a key-value convention writes its keys (BIDS's
        ``sub``, ``ses``, ``run``). A key written with a capital is a word and its replicate
        (``Vorn-2``, ``WT-1``). Measured on a laboratory's stored census, the capitalised form was a
        reagent or a condition and its replicate thousands of times, and read as one label each
        lost its word's reading. A month's word is not a key (``jan-2031``). A version marker's key
        is a version (``v-2``).
        """
        key, number = match.group(1), match.group(2)
        if not key.islower() or key in self._months:
            return None
        version = _versioned(_VERSION.match(match.group()))
        if version:
            return Atom(
                text=match.group(),
                kind=AtomKind.VERSION,
                normalised=f"v{version.group(2)}",
                start=match.start(),
                label=version.group(1).casefold(),
                index=int(version.group(2)),
            )
        return Atom(
            text=match.group(),
            kind=AtomKind.INDEXED,
            normalised=f"{key.casefold()}{int(number)}",
            start=match.start(),
            label=key.casefold(),
            index=int(number),
        )

    def _month_word_date(self, match: re.Match[str]) -> Atom | None:
        """A day, a month's word and a year as one date, where a calendar and the span hold it."""
        day, month, year = (
            int(match.group(1)),
            self._months[(match.group(3) or match.group(4)).casefold()],
            int(match.group(5)),
        )
        if not _calendar_holds(month, day, year) or not self._plausible(year):
            return None
        return Atom(
            text=match.group(),
            kind=AtomKind.DATE,
            normalised=f"{year}-{month:02d}-{day:02d}",
            start=match.start(),
        )

    def _compound(self, match: re.Match[str]) -> Atom:
        """One word for a term the Dictionary spells in parts, compared as its parts spaced.

        ``Cell_Line``, ``cell-line`` and ``CellLine`` are one component, ``cell line``, and the
        binder looks the name's own spelling up, as it does every word's.
        """
        squashed = "".join(_PART_JOINERS.split(match.group().casefold()))
        # Its last word in the plural is the term's: ``phase_plates``.
        spelled = next(
            (
                self._compounds[key]
                for key in (squashed, squashed[:-1], squashed[:-2], squashed[:-3] + "y")
                if key in self._compounds
            ),
            match.group().casefold(),
        )
        return Atom(
            text=match.group(),
            kind=AtomKind.WORD,
            normalised=spelled,
            start=match.start(),
        )

    def _compound_quantity(self, match: re.Match[str]) -> Atom | None:
        """Return the quantity a joiner-crossing span names, or None.

        The quantity is reserved only where the match consumes more than its first fragment. A
        match that stops at the first fragment is exactly what ordinary scanning already produces,
        and reserving it would change the reading of names that tokenise correctly for no gain.
        """
        magnitude_text, unit_text = match.group(1), match.group(2)
        unit = self.match_unit(unit_text)
        if unit is None:
            return None
        surface, data = unit
        used = self.consumed(unit_text, surface)
        head = re.match(r"[^\W\d_]+", unit_text)
        if head is None or used <= head.end():
            return None
        return self._quantity(
            magnitude=float(magnitude_text),
            text=match.group()[: len(magnitude_text) + used],
            data=data,
            start=match.start(),
        )

    def _spaced_quantity(self, match: re.Match[str]) -> Atom | None:
        """The quantity a number, a space and a unit name, or None where the word is not a unit."""
        magnitude_text, unit_text = match.group(1), match.group(2)
        if unit_text.casefold() in _SPACED_NEVER:
            return None
        unit = self.match_unit(unit_text)
        if unit is None:
            return None
        surface, data = unit
        if self.consumed(unit_text, surface) != len(unit_text):
            return None
        if not (unit_text.startswith(surface) or unit_text.casefold() == surface.casefold()):
            return None
        return self._quantity(
            magnitude=float(magnitude_text), text=match.group(), data=data, start=match.start()
        )

    def _quantity(self, *, magnitude: float, text: str, data: UnitData, start: int) -> Atom | None:
        """Build one quantity atom, converted into its dimension's base unit.

        The function refuses a magnitude that is not finite. That is a correctness guard rather
        than tidiness. A run identifier holding ``121e0380`` parses as scientific notation and
        overflows, so the engine would store a quantity whose magnitude is infinity: a value nothing
        downstream can compare, order or reduce, in a column every grouping rule reads. Running the
        classifying rules over real laboratory folders showed the problem.

        The refusal is at the one place every quantity is built, so no caller can forget. The caller
        falls back to reading the digits as a bare number, which is what they are.

        Whether a filename should be read as scientific notation at all is a separate and larger
        question, left open: ``e`` between digits is a hex digit or a letter far more often than an
        exponent, and narrowing that is a change to what the engine reads rather than to what it
        refuses.
        """
        base = self.to_base(magnitude, data)
        if not (isfinite(magnitude) and isfinite(base)):
            return None
        declared = data.get("ambiguous_with")
        others = (
            tuple(sorted(str(term) for term in declared))
            if isinstance(declared, (list, tuple))
            else ()
        )
        return Atom(
            text=text,
            kind=AtomKind.QUANTITY,
            # Rendered from the base value, so two spellings of one quantity
            # produce one string and can be compared without arithmetic.
            normalised=f"{base:.12g} {data['base']}",
            start=start,
            magnitude=magnitude,
            unit=str(data["term"]),
            dimension=str(data["dimension"]),
            base_value=base,
            # A unit the Dictionary declared confusable stays confusable here.
            # "63x" is sixty-three fold or sixty-three times magnification and
            # the string does not say which.
            ambiguous=bool(others),
            alternatives=others,
        )

    def _scan(self, text: str, offset: int) -> list[Atom]:
        """Split a stretch of a name that contains no reserved span."""
        found: list[Atom] = []
        for match in re.finditer(r"[^\s_\-+,;:()\[\]{}/\\|@#&'\"]+", text):
            found.extend(self._chunk(match.group(), offset + match.start()))
        return found

    def _chunk(self, chunk: str, offset: int) -> list[Atom]:
        """Split one separator-free run into atoms."""
        # Whole-chunk forms are tried first: a date or a version is a single
        # thing and splitting it would destroy it.
        whole = self._whole_chunk(chunk, offset)
        if whole is not None:
            return [whole]
        # An identifier is one thing too, and split it is a question about what a digest means: a
        # hash's letters became words and its digits quantities.
        identified = _identifier_span(chunk)
        if identified is not None:
            start, end = identified
            head = self._chunk(chunk[:start], offset) if start else []
            return [*head, _identifier(chunk[start:end], offset + start)]
        if self._glued != "split" and _DIVIDING_DOT.search(chunk):
            # A dot that is not a decimal point divides the run, so each part is read, and judged
            # glued or not, on its own.
            atoms: list[Atom] = []
            cursor = 0
            for dot in _DIVIDING_DOT.finditer(chunk):
                if dot.start() > cursor:
                    atoms.extend(self._chunk(chunk[cursor : dot.start()], offset + cursor))
                cursor = dot.end()
            if cursor < len(chunk):
                atoms.extend(self._chunk(chunk[cursor:], offset + cursor))
            return atoms
        split = self._split(chunk, offset)
        if self._reads_whole(chunk, split):
            return [Atom(text=chunk, kind=AtomKind.WORD, normalised=chunk.casefold(), start=offset)]
        return split

    def _reads_whole(self, chunk: str, split: Sequence[Atom]) -> bool:
        """Whether a glued run is read as one word rather than as the atoms it was cut into.

        Under the setting :data:`GLUED_RUNS`, a run of letters and digits with no separator is one
        word, read and asked about whole. The reading never applies to a run that the reading above
        left as one atom (a quantity, a number, an indexed label), a run of letters alone (whose
        case boundaries are settled elsewhere), or a run holding a date, a version or an
        identifier. Under the default, a run beginning with a number is still a quantity and what
        it quantifies, and a counter after a word the Dictionary knows keeps its split.
        """
        if self._glued == "split" or len(split) < 2:
            return False
        if not (any(c.isalpha() for c in chunk) and any(c.isdigit() for c in chunk)):
            return False
        if any(atom.kind in _READ_AS_THEMSELVES for atom in split):
            return False
        if self._glued == "whole":
            return True
        if not chunk[0].isalpha():
            return False
        counted = (
            len(split) == 2
            and split[0].kind is AtomKind.WORD
            and split[1].kind is AtomKind.NUMBER
            and self._store is not None
            and self._store.look_up(split[0].text) is not None
        )
        return not counted

    def _split(self, chunk: str, offset: int) -> list[Atom]:
        """Split a chunk that is not one thing entire into numbers, labels and words."""
        atoms: list[Atom] = []
        position = 0
        while position < len(chunk):
            number = _NUMBER.match(chunk, position)
            if number:
                atoms.append(self._numeric(chunk, number, offset))
                position = atoms[-1].start - offset + len(atoms[-1].text)
                continue
            indexed = _INDEXED.match(chunk, position)
            if indexed and indexed.end() == len(chunk):
                # A short label with a trailing number is one thing: well A1,
                # replicate rep2, position p03. Splitting it loses the pairing,
                # and the pairing is the whole content.
                label, number_text = indexed.groups()
                atoms.append(
                    Atom(
                        text=indexed.group(),
                        kind=AtomKind.INDEXED,
                        normalised=f"{label.casefold()}{int(number_text)}",
                        start=offset + position,
                        label=label.casefold(),
                        index=int(number_text),
                    )
                )
                position = indexed.end()
                continue
            letters = re.match(r"[^\W\d_]+", chunk[position:], re.UNICODE)
            if letters:
                text = letters.group()
                parts = split_words(
                    text,
                    knows=self.knows if self._store is not None else None,
                    endings=self._endings,
                )
                if self._store is not None and self._glued_words == "divide":
                    # A run glued from two Dictionary words divides.
                    parts = [
                        piece
                        for part in parts
                        for piece in (
                            glued_division(
                                part,
                                knows=self.knows,
                                minimum=self._glued_half_minimum,
                                prefixes=self._prefixes,
                            )
                            or (part,)
                        )
                    ]
                # Every piece of a run divided at a capital carries the whole.
                whole = text if len(parts) > 1 else None
                cursor = 0
                for part in parts:
                    atoms.append(
                        Atom(
                            text=part,
                            kind=AtomKind.WORD,
                            normalised=part.casefold(),
                            start=offset + position + cursor,
                            case_whole=whole,
                        )
                    )
                    cursor += len(part)
                position += len(text)
                continue
            position += 1
        return atoms

    def _plausible(self, year: int) -> bool:
        """Whether a year a name spells lies inside the years the run reads."""
        return self.years[0] <= year <= self.years[1]

    def _whole_chunk(self, chunk: str, offset: int) -> Atom | None:
        """Return an atom for a chunk that is one thing entire, or None.

        A date is read only where the plausible span holds its year and a calendar its month and
        day. A run shaped like a date that neither holds is the numbers it is written with, and the
        caller reads it so.
        """
        iso = _ISO_DATE.match(chunk)
        if iso:
            year, month, day = iso.groups()
            if _calendar_holds(int(month), int(day), int(year)) and self._plausible(int(year)):
                return Atom(
                    text=chunk,
                    kind=AtomKind.DATE,
                    normalised=f"{year}-{month}-{day}",
                    start=offset,
                )
        year_month = _YEAR_MONTH.match(chunk)
        if (
            year_month
            and 1 <= int(year_month.group(2)) <= 12
            and self._plausible(int(year_month.group(1)))
        ):
            return Atom(
                text=chunk,
                kind=AtomKind.DATE,
                normalised=f"{year_month.group(1)}-{year_month.group(2)}",
                start=offset,
            )

        six = _SIX_DIGIT_DATE.match(chunk) if self._six_digit == "year_first" else None
        if six:
            year, month, day = (int(group) for group in six.groups())
            if _calendar_holds(month, day, 2000 + year) and self._plausible(2000 + year):
                return Atom(chunk, AtomKind.DATE, f"{2000 + year}-{month:02d}-{day:02d}", offset)
            return None

        short = _SHORT_DATE.match(chunk)
        if short:
            return self._short_date(chunk, offset, short)

        version = _versioned(_VERSION.match(chunk))
        if version:
            return Atom(
                text=chunk,
                kind=AtomKind.VERSION,
                normalised=f"v{version.group(2)}",
                start=offset,
                label=version.group(1).casefold(),
                index=int(version.group(2).split(".")[0]),
            )
        return None

    def _short_date(self, chunk: str, offset: int, short: re.Match[str]) -> Atom | None:
        """Read three numbers joined by one separator by the field orders a calendar allows.

        Each order that the calendar and the years allow is a reading: the year last, month first
        or day first, and, with a month and a day of two digits each, the year first. A two-digit
        year is read in this century. A three-digit year is nobody's.

        - One reading is the date.
        - Two readings under one year, a day and a month that could swap, are one ambiguous date.
          Nothing decides, so neither is chosen (an ordering built on a guess here would be wrong
          for eleven months of every twelve). Its value names both readings in the name's field
          order: the first field read as the month, then as the day. So ``04.03.31`` is
          ``2031-04-03 or 2031-03-04`` and its mirror ``03.04.31`` is ``2031-03-04 or 2031-04-03``,
          two values as they are two spellings. The value must not be the month-first reading alone,
          which every reader of the component would take as the day, so that two spellings of one
          swap would be grouped as one day on a guess. It is not a clock and not a name date. A
          neighbour may still settle it (:meth:`short_date_orders`).
        - Readings that put the year in different places are not a date on their own. The name alone
          cannot say which field is the year, so it is read as its numbers. The binder brings a
          neighbour's reading (:meth:`two_order_dates`), never the tokeniser, which reads one name
          alone.
        """
        readings = self._short_readings(short)
        if not readings:
            return None
        if len(set(readings.values())) > 1:
            return None
        found = sorted(readings)
        if len(found) == 1:
            return Atom(chunk, AtomKind.DATE, found[0], offset)
        # A swap is the two year-last readings: the month first, then the day first.
        orders = self._short_orders(short)
        in_field_order = [orders[order] for order in (MONTH_FIRST, DAY_FIRST) if order in orders]
        return Atom(
            text=chunk,
            kind=AtomKind.DATE,
            normalised=AMBIGUOUS_DATE_JOINER.join(in_field_order or found),
            start=offset,
            ambiguous=True,
            alternatives=tuple(found),
        )

    def two_order_dates(self, name: str) -> tuple[tuple[str, ...], ...]:
        """Return every short date in a name that two field orders allow, each as its readings.

        The readings are sorted. The tokeniser reads such a run as its numbers
        (:meth:`_short_date`). This method says what the run could be, so a reader of the name's
        neighbours (its folder's date) can settle the order.
        """
        found: list[tuple[str, ...]] = []
        for short in self._short_runs(name):
            readings = self._short_readings(short)
            if len(set(readings.values())) > 1:
                found.append(tuple(sorted(readings)))
        return tuple(found)

    def short_date_orders(self, name: str) -> tuple[tuple[tuple[str, str], ...], ...]:
        """Return every field order that each short date in a name allows, with the date it reads.

        There is one entry per short date, in the name's order, each its ``(order, date)`` pairs
        sorted by order: ``day_first``, ``month_first`` (both with the year last) and
        ``year_first``. Two orders reading one date are both named, since a series is settled by
        orders and not by dates. A run that no order reads is not a short date and is left out.
        """
        return tuple(orders for _, orders in self.short_dates(name))

    def short_dates(self, name: str) -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
        """Each short date in a name as its separator and :meth:`short_date_orders`' pairs."""
        found: list[tuple[str, tuple[tuple[str, str], ...]]] = []
        for short in self._short_runs(name):
            orders = self._short_orders(short)
            if orders:
                found.append((short.group(2), tuple(sorted(orders.items()))))
        return tuple(found)

    def _short_runs(self, name: str) -> list[re.Match[str]]:
        """Each run in a name written as a short date, as :data:`_SHORT_DATE` reads it.

        The runs are those a reader tries (:meth:`_date_runs`, so a date after numbers is one),
        less every run the reader takes for a clock. A stamp's clock, and a time of day written as a
        clock after its date, are times, and a folder's series never dates one.
        """
        escaped = _escaped_reading(name)
        text = escaped[0] if escaped is not None else name
        runs = [match for match in self._date_runs(text) if match.group("joiner")]
        if not runs:
            return []
        clocks: list[tuple[int, int]] = []
        self._reserved(text, clocks)
        found = []
        for match in runs:
            if any(match.start() < end and start < match.end() for start, end in clocks):
                continue
            short = _SHORT_DATE.match(match.group())
            if short is not None:
                found.append(short)
        return found

    def _short_orders(self, short: re.Match[str]) -> dict[str, str]:
        """Each field order a short run allows, by the calendar and the years, with its date."""
        first, second, last = short.group(1), short.group(3), short.group(4)
        orders: dict[str, str] = {}
        if len(last) in (2, 4):
            year = int(f"20{last}") if len(last) == 2 else int(last)
            if self._plausible(year):
                if _calendar_holds(int(first), int(second), year):
                    orders[MONTH_FIRST] = f"{year}-{int(first):02d}-{int(second):02d}"
                if _calendar_holds(int(second), int(first), year):
                    orders[DAY_FIRST] = f"{year}-{int(second):02d}-{int(first):02d}"
        # The year leads only before a month and a day of two digits each (a date written to
        # sort). ``19-2-4`` is a sample's label far more often than a date. Measured on a stored
        # census: most name clocks that a one-digit year-first reading gained fell years after the
        # file's own modification time.
        if len(first) == 2 and len(second) == 2 and len(last) == 2:
            year = 2000 + int(first)
            if self._plausible(year) and _calendar_holds(int(second), int(last), year):
                orders[YEAR_FIRST] = f"{year}-{int(second):02d}-{int(last):02d}"
        return orders

    def _short_readings(self, short: re.Match[str]) -> dict[str, str]:
        """Each date a short run could be, with the place its year was read from."""
        readings: dict[str, str] = {}
        orders = self._short_orders(short)
        for order in (MONTH_FIRST, DAY_FIRST, YEAR_FIRST):
            found = orders.get(order)
            if found is not None:
                readings.setdefault(found, "first" if order == YEAR_FIRST else "last")
        return readings

    def _numeric(self, chunk: str, number: re.Match[str], offset: int) -> Atom:
        """Return the atom beginning at a number: a quantity, or the number alone.

        A one-character unit symbol makes a quantity only where it ends its chunk. ``20x``,
        ``2.5h`` and ``Tg2h`` end with their symbol, and a person wrote each as a measure. Followed
        by a letter or a digit, the symbol is inside a longer run: ``7302d9a`` in a build hash,
        ``4417xBglII`` in a digested plasmid, ``3x3`` in a grid. Reading it as days or fold turned
        fragments into questions. A longer symbol is spelled deliberately enough to stand where it
        is (``500nm2hr``).
        """
        magnitude = float(number.group())
        rest = chunk[number.end() :]
        unit = self.match_unit(rest) if rest else None
        if unit is not None:
            surface, data = unit
            used = self.consumed(rest, surface)
            after = rest[used : used + 1]
            stands = len(surface) > 1 or not after.isalnum()
            # A unit matched by folding and followed by a lower-case letter does not stand: ``Pl``
            # in ``2Plate`` folds to picolitres and is a word's first letters.
            if stands and not rest.startswith(surface) and after.isalpha() and after.islower():
                stands = False
            quantity = (
                self._quantity(
                    magnitude=magnitude,
                    text=chunk[number.start() : number.end() + used],
                    data=data,
                    start=offset + number.start(),
                )
                if stands
                else None
            )
            if quantity is not None:
                return quantity
        indexed = _INDEXED.match(chunk)
        if indexed is None and rest and rest[0].isalpha():
            # A number followed by letters that are not a unit: an index with a
            # suffix, or a label the Dictionary has never seen.
            pass
        return Atom(
            text=number.group(),
            kind=AtomKind.NUMBER,
            normalised=str(magnitude if magnitude % 1 else int(magnitude)),
            start=offset + number.start(),
            magnitude=magnitude,
        )

    # -- convenience ------------------------------------------------------

    def words(self, name: str) -> tuple[str, ...]:
        """Return just the word atoms, for a Dictionary lookup."""
        return tuple(atom.text for atom in self.atoms(name) if atom.kind is AtomKind.WORD)

    def quantities(self, name: str) -> tuple[Atom, ...]:
        """Return just the quantities found in a name."""
        return tuple(atom for atom in self.atoms(name) if atom.kind is AtomKind.QUANTITY)

    def comparable(self, name: str) -> tuple[str, ...]:
        """Return every atom's normalised form, in order.

        The form to compare two names by. Two names describing the same thing in
        different words produce the same sequence here, which is what makes them
        groupable without anybody writing a rule about their spelling.
        """
        return tuple(atom.normalised for atom in self.atoms(name))


#: The kinds inside a run that keep it cut: each is one thing whose reading a whole word would lose.
_READ_AS_THEMSELVES = frozenset({AtomKind.DATE, AtomKind.VERSION, AtomKind.IDENTIFIER})

#: The kind a word's Dictionary knowledge leaves out when the tokeniser asks.
_UNIT_KIND = frozenset({"unit"})


def _iso_shapes(name: str, fields: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """Where a run of numbers writes a year, a month and a day in ISO order.

    Four digits not led by a zero (a counter's padding is not a year), then a month and a day of two
    digits each that a calendar holds, whatever the years: an ISO date the years refuse is still
    the numbers of one, and no date after numbers is cut out of it.
    """
    shapes = []
    for year, month, day in zip(fields, fields[1:], fields[2:], strict=False):
        written = [name[start:end] for start, end in (year, month, day)]
        if (
            [len(part) for part in written] == [4, 2, 2]
            and not written[0].startswith("0")
            and _calendar_holds(int(written[1]), int(written[2]))
        ):
            shapes.append((year[0], day[1]))
    return shapes


def _cuts(start: int, end: int, shapes: Sequence[tuple[int, int]]) -> bool:
    """Whether a span overlaps any of the spans given."""
    return any(start < shape_end and shape_start < end for shape_start, shape_end in shapes)


def is_month_sense(theme_kind: str | None, canonical: str | None) -> bool:
    """Whether a sense is one of the Dictionary's months: of the date theme kind, with a month's
    number (``01`` to ``12``) as its canonical value. The one test a reader of month words applies,
    so no reader takes another date-kind word for a month."""
    return theme_kind == "date" and bool(_MONTH_NUMBER.fullmatch(canonical or ""))


def _alternation(words: dict[str, int]) -> str:
    """A regular expression's alternation of words, longest first so a longer word is never cut."""
    return "|".join(re.escape(word) for word in sorted(words, key=lambda word: (-len(word), word)))


def _listed_or_versioned(name: str, match: re.Match[str]) -> bool:
    """Whether a short date's numbers continue a list, or follow a version marker.

    ``1-2-15-16`` is four numbers in a list, and the three at its head are not a date because they
    are shaped like one; nor is ``v1.2.13``, a version. The same separator and a number directly
    before or after the run make it a list's member; a marker directly before it makes it a version.
    """
    joiner = re.escape(match.group("joiner"))
    before, after = name[: match.start()], name[match.end() :]
    return bool(
        re.search(rf"\d{joiner}$", before)
        or re.match(rf"{joiner}\d", after)
        or _VERSION_MARKER_BEFORE.search(before)
    )


def _written_as_a_clock(
    name: str, earlier: Sequence[tuple[int, int, Atom]], start: int, end: int
) -> bool:
    """Whether the run at ``name[start:end]`` is a time of day after a date.

    It must be shaped as hours, minutes and seconds (:func:`_time_of_day`) and follow a date read
    earlier in the name, and it must be written as a clock is: directly after the nearest such
    date, with only separators or the word a clock is read with between them, or followed by the
    half of the day. Anything else keeps the reading it had, so a span of two dates stays two.
    """
    if not _time_of_day(name[start:end]):
        return False
    nearest = max(
        (
            span_end
            for _, span_end, atom in earlier
            if atom.kind is AtomKind.DATE and span_end <= start
        ),
        default=None,
    )
    if nearest is None:
        return False
    return bool(_CLOCK_BEFORE.fullmatch(name[nearest:start]) or _CLOCK_AFTER.match(name, end))


def _clock_numbers(name: str, start: int, end: int) -> list[tuple[int, int, Atom]]:
    """A clock's three numbers as reserved atoms, each where the name writes it.

    The first carries the time of day (:attr:`Atom.clock`): the three numbers as ``HH:MM:SS``,
    which the name's event time takes where the clock follows its date.
    """
    parts = list(re.finditer(r"\d+", name[start:end]))
    digits = "".join(part.group() for part in parts)
    fields: tuple[str, ...] = ()
    if len(parts) == 1 and len(digits) == 6:
        # Six digits with nothing between them: hours, minutes and seconds.
        fields = (digits[:2], digits[2:4], digits[4:])
    elif len(parts) == 3:
        fields = (parts[0].group(), parts[1].group(), parts[2].group())
    clock = ":".join(f"{int(field):02d}" for field in fields) if fields else None
    return [
        (
            start + part.start(),
            start + part.end(),
            Atom(
                text=part.group(),
                kind=AtomKind.NUMBER,
                normalised=str(int(part.group())),
                start=start + part.start(),
                magnitude=float(part.group()),
                clock=clock if index == 0 else None,
            ),
        )
        for index, part in enumerate(parts)
    ]


def _time_of_day(text: str) -> bool:
    """Whether a run of numbers could be a time of day: hours, minutes and seconds."""
    shaped = _TIME_SHAPED.match(text)
    if shaped is None:
        return False
    # The written form, or six digits with nothing between them.
    hours, minutes, seconds = (
        int(shaped.group(1) or shaped.group(5)),
        int(shaped.group(3) or shaped.group(7)),
        int(shaped.group(4) or shaped.group(8)),
    )
    return all(
        value <= limit for value, limit in zip((hours, minutes, seconds), _TIME_LIMITS, strict=True)
    )


#: The most days each month holds, February in a leap year.
_MONTH_DAYS = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _calendar_holds(month: int, day: int, year: int | None = None) -> bool:
    """Whether a month and a day are ones a calendar holds: the ISO form's own test.

    Each month has its own length: a thirty-first of November is not a date, so a short date it
    would be one reading of has one reading fewer (``28-11-31`` is the twenty-eighth of November in
    the year 2031, never the thirty-first of November in the year 2028). The twenty-ninth of
    February is a date only in a leap year, where the year is known.
    """
    if not (1 <= month <= 12 and 1 <= day <= _MONTH_DAYS[month - 1]):
        return False
    return not (month == 2 and day == 29 and year is not None and not calendar.isleap(year))


def _identifier(text: str, start: int) -> Atom:
    """One identifier atom, compared casefolded, because a digest's case is not its content."""
    return Atom(text=text, kind=AtomKind.IDENTIFIER, normalised=text.casefold(), start=start)


def _escaped_reading(name: str) -> tuple[str, list[int], list[int]] | None:
    """Return how a percent-encoded name reads, with where each character came from, or None.

    A name is read as percent-encoded where it holds an escaped space and no bare percent sign. A
    program that writes a name through percent-encoding writes every space as ``%20`` and every
    literal percent sign as ``%25``, so a bare ``%`` says a person wrote the name and a ``%`` beside
    it is a percentage. In an encoded name each escape in :data:`_ESCAPE_READINGS` is read as its
    character, once and left to right, so ``%2520`` reads as ``%20`` and not as a space.

    The one reading this cannot settle is a percentage written flush against a number that begins
    ``20`` in a name holding no other percent sign (``60%201``), which reads as a space. Measured on
    laboratory censuses, every ``%20`` was an escape.

    Returns the reading and, for each of its characters, the offset in ``name`` where it
    begins and the offset just past it, so an atom read from the reading carries the name's own
    text and position: the name's bytes are never rewritten, only read.
    """
    if _ESCAPED_SPACE not in name or _BARE_PERCENT.search(name):
        return None
    reading: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    position = 0
    while position < len(name):
        escape = name[position : position + 3]
        width = 3 if escape in _ESCAPE_READINGS else 1
        reading.append(_ESCAPE_READINGS[escape] if width == 3 else name[position])
        starts.append(position)
        ends.append(position + width)
        position += width
    return "".join(reading), starts, ends


def split_indexed(normalised: str) -> tuple[str, int] | None:
    """Return the label and index a normalised indexed atom was written from, or None.

    This is the one place the split is written. A component stores an indexed atom as one
    normalised value, its casefolded label followed by its index with leading zeros dropped, and
    keeps neither half in a column of its own. A rule reading a label reads it back here rather
    than re-deriving the grammar, because a second copy of a grammar is the copy that drifts. A
    clock's tier and a respect are derived the same way, from their source and their
    specialization.

    Returns None for anything that is not an indexed atom's normalised form, so a caller
    filtering on the component type and a caller filtering on the spelling agree.
    """
    match = _NORMALISED_INDEX.match(normalised)
    if match is None:
        return None
    label, index = match.groups()
    return label, int(index)
