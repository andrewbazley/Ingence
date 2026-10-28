"""The members of the sample laboratory, how each names things, and the projects each works on.

Everything a person does with a name is decided here, from the generator's seeded stream:

- a member is a made-up person (an ordinary first and family name) with a folder at the top of
  the drive, active over a span of years. A member who left has a folder in ``Lab Alumni``, which
  stops early and holds an ``_old`` or ``archive`` subfolder;
- a habit is how that member writes a dated folder: which of the seven date spellings, whether
  the date comes first, the separator, where the stage folders are and what they are called, how a
  replicate is labeled and whether the raw files keep the name their instrument gave them. A
  member's habit changes with the years and slips from time to time;
- a project is a subject the member keeps coming back to: a cell line and a protein or gene
  that name it, a few treatments that vary, and the techniques used.

Every word is from the vocabulary (``sample_lab_words.yaml``, read by
:mod:`ingence.evaluation.sample_lab_words`): words the shipped Dictionary reads, common biology
words and ordinary English. There are no invented words.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from ingence.evaluation.sample_lab_words import words

__all__ = [
    "ALUMNI_FOLDER",
    "ANALYSIS_FOLDERS",
    "DATE_STYLES",
    "DATE_WEIGHTS",
    "MEMBER_FOLDER_STYLES",
    "MONTH_NAMES",
    "PROCESSED_FOLDERS",
    "RAW_FOLDERS",
    "REPLICATE_STYLES",
    "Condition",
    "Habit",
    "Member",
    "Project",
    "date_text",
    "member_folder",
    "replicate_parts",
]

#: Where a member who left keeps a folder.
ALUMNI_FOLDER = "Lab Alumni"

#: The seven spellings of an acquisition date, for the twelfth of October 2026:
#: ``12Oct2026``, ``Oct-12-2026``, ``20261012``, ``10-12-2026``, ``2026_10_12``, ``10-12-26`` and
#: ``12-10-26``.
DATE_STYLES = ("dmy_long", "mdy_name", "compact", "us_long", "under", "us_short", "dmy_short")
DATE_WEIGHTS = (15, 10, 25, 12, 15, 10, 13)
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
#: Month names written out rather than read from the locale, so the same seed writes the same text.
MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip

#: How a stage folder may be called, and whether its name is a word the Dictionary reads as that
#: stage.
RAW_FOLDERS = (("raw", True), ("Raw Data", True), ("images", False), ("data", False))
PROCESSED_FOLDERS = (("processed", True), ("Processed", True), ("proc", True), ("tifs", False))
ANALYSIS_FOLDERS = (("analysis", True), ("Analysis", True), ("results", True), ("figs", False))

#: How a member labels a replicate. ``counter`` is the instrument's own number (``_001``). A well
#: label is a position, never a replicate, so it is not a style.
REPLICATE_STYLES = ("rep", "R", "bare", "take", "counter")
#: The share of members using each label, 40/20/20/10/10: rep, R, a bare number, take and the
#: instrument's counter.
REPLICATE_WEIGHTS = (40, 20, 20, 10, 10)

MEMBER_FOLDER_STYLES = (
    "{First} {Family}",
    "{First}_{Family}",
    "{First}",
    "{Family}",
    "{first}",
    "{F}{Family}",
    "{First} {Family} data",
)

_DESIGN_WORDS = ("test", "titration", "timecourse", "repeat", "redo", "again")
_IMAGING = ("confocal", "widefield", "timelapse", "zstack", "tirf", "brightfield")


def member_folder(style: str, first: str, family: str) -> str:
    """A member's folder name in one of the styles people use."""
    return style.format(First=first, Family=family, first=first.lower(), F=first[:1].upper())


@dataclass(frozen=True)
class Condition:
    """One condition of an experiment: its words as written, and its levels of the design."""

    #: The words a name holds, for example ``("nocodazole", "10uM")``.
    parts: tuple[str, ...]
    #: The same condition as a design: ``(("treatment", "nocodazole"), ("dose", "10uM"))``.
    levels: tuple[tuple[str, str], ...]

    @property
    def level_names(self) -> tuple[str, ...]:
        return tuple(level for _, level in self.levels)


@dataclass(frozen=True)
class Habit:
    """How one member names a dated experiment folder, and what lies in it, over some years."""

    date_style: str
    date_first: bool
    sep: str
    #: ``stages`` (raw, processed and analysis folders), ``flat`` (one folder holds everything) or
    #: ``images_and_rest`` (an images folder, and everything else beside it).
    layout: str
    raw_folder: tuple[str, bool]
    processed_folder: tuple[str, bool]
    analysis_folder: tuple[str, bool]
    process_word: str
    results_word: str
    replicate: str
    long_names: bool
    #: How a raw file is named: ``instrument`` (the name its instrument gave it, ``Image0012``),
    #: ``descriptive`` (the person names the sample and condition, the instrument numbers the
    #: replicates: ``20261012_HeLa_nocodazole-10uM_001``) or ``renamed`` (the member's own labels).
    raw_naming: str
    #: Whether a descriptive raw name starts with the acquisition date.
    raw_dated: bool
    raw_word: bool
    #: How many words of the project beyond its cell line and protein a file name holds (the
    #: technique, the stain).
    file_words: int
    #: Whether the files made from an acquisition keep its name (a program saves
    #: ``Image0012_processed.tif``) or the member names them again.
    names_follow: bool
    #: Whether the member's analysis tables are tab-separated text files rather than CSV.
    results_text: bool
    #: Words a member puts in the names of processed images beside the stage word (``mask``).
    detail: tuple[str, ...] = ()

    @property
    def raw_descriptive(self) -> bool:
        """Whether a raw file's name says its condition."""
        return self.raw_naming != "instrument"


@dataclass
class Project:
    """A subject a member keeps working on."""

    owner: Member
    #: The cell line and the protein or gene that name the project.
    core: tuple[str, ...]
    #: The treatments that vary, each with the doses it is used at.
    extras: tuple[str, ...]
    doses: dict[str, tuple[str, ...]]
    times: tuple[str, ...]
    control: str
    #: A word written after a treatment, when the vehicle is part of the name (``DMSO``).
    vehicle: str | None
    techniques: tuple[str, ...]
    stain: str | None
    kind: str
    #: ``xml``, ``json`` or ``None``: the record the instrument writes beside an acquisition.
    sidecar: str | None
    #: ``csv``, ``txt`` or ``xlsx``: what the plate reader exports.
    plate_form: str
    start: dt.datetime
    cursor: int = 0
    experiments: list[str] = field(default_factory=list)
    subgroups: dict[str, list[str]] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return " ".join(self.core)


@dataclass
class Member:
    """One person of the laboratory."""

    given: str
    family: str
    #: The folder's name as the member wrote it.
    folder_name: str
    #: The folder's path from the top of the drive (inside ``Lab Alumni`` for a member who left).
    folder: str
    #: The first and the last year the member was in the laboratory.
    first: int
    last: int
    departed: bool
    system: str
    kinds: tuple[str, ...]
    top: str
    year_style: str
    eras: tuple[tuple[int, Habit], ...]
    scripting: str
    tidy: float
    #: A folder named for the microscope that holds everything the member made on it, or ``None``.
    instrument_folder: str | None = None
    old_folder: str | None = None
    old_before: dt.datetime | None = None
    projects: list[Project] = field(default_factory=list)

    @property
    def name(self) -> str:
        return f"{self.given} {self.family}"

    def habit_on(self, year: int) -> Habit:
        chosen = self.eras[0][1]
        for start, habit in self.eras:
            if start <= year:
                chosen = habit
        return chosen

    def year_folder(self, year: int) -> str:
        return self.year_style.format(year=year)


def date_text(day: dt.datetime, style: str) -> tuple[str, str | None]:
    """A day as written in ``style``, and the ISO date the spelling settles (``None``: it does not).

    A spelling settles its date when only one reading of it is a date. A month spelled in letters,
    four digits for the year first (``20261012``, ``2026_10_12``) settle it. ``10-12-2026`` settles
    it where the day is above twelve or equals the month. A two-digit year (``10-12-26``,
    ``12-10-26``) never does: the same digits read as year, month and day, too.
    """
    iso = f"{day:%Y-%m-%d}"
    month = _MONTHS[day.month - 1]
    settled: str | None = iso
    if style == "dmy_long":
        text = f"{day.day:02d}{month}{day.year}"
    elif style == "mdy_name":
        text = f"{month}-{day.day:02d}-{day.year}"
    elif style == "compact":
        text = f"{day:%Y%m%d}"
    elif style == "us_long":
        text = f"{day.month:02d}-{day.day:02d}-{day.year}"
        settled = iso if day.day > 12 or day.day == day.month else None
    elif style == "under":
        text = f"{day.year}_{day.month:02d}_{day.day:02d}"
    elif style == "us_short":
        text, settled = f"{day.month:02d}-{day.day:02d}-{day:%y}", None
    elif style == "dmy_short":
        text, settled = f"{day.day:02d}-{day.month:02d}-{day:%y}", None
    else:
        message = f"no date style {style!r}"
        raise ValueError(message)
    return text, settled


def replicate_parts(style: str, number: int, serial: int, variant: int = 0) -> tuple[str, ...]:
    """The parts of a replicate label: ``rep2``, ``R2``, ``02`` or the instrument's ``002``.

    ``number`` counts the replicates of one condition, ``serial`` the acquisitions of the session.
    ``variant`` spells the same label slightly differently (``rep2``, ``Rep2``, ``rep02``), as a
    person does within one experiment.
    """
    if style == "rep":
        return (("rep{}", "Rep{}", "rep{:02d}")[variant % 3].format(number),)
    if style == "R":
        return (("R{}", "r{}", "R{:02d}")[variant % 3].format(number),)
    if style == "bare":
        return (("{:02d}", "{}", "{:03d}")[variant % 3].format(number),)
    if style == "take":
        return (("take{}", "Take{}", "take{:02d}")[variant % 3].format(number),)
    if style == "counter":
        return (("{:03d}", "{:04d}", "{}")[variant % 3].format(serial),)
    message = f"no replicate style {style!r}"
    raise ValueError(message)


def imaging_techniques() -> tuple[str, ...]:
    """The imaging techniques of the vocabulary that a folder name may carry."""
    known = set(words().dictionary["techniques"])
    return tuple(word for word in _IMAGING if word in known)


def design_words() -> tuple[str, ...]:
    """Words that say what an experiment was for."""
    known = set(words().dictionary["design"])
    return tuple(word for word in _DESIGN_WORDS if word in known)
