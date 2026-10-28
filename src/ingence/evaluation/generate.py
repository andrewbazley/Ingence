"""A pool of invented files with the shapes laboratories have, and its answer key beside it.

From a seed and a size, one pool, byte for byte, and the key a person would write for it
(:mod:`ingence.evaluation.key`), in a sibling file the pool never holds. Every name is an invented
stem or an ordinary word, and every file's bytes are built from its format's structure with invented
values (:mod:`ingence.evaluation.formats`). Modification times are set from the seed too, so two
pools from one seed differ in nothing a census reads but the times a filesystem keeps for itself.

The shapes, each written ``size`` times with stems of its own, so no two instances share a word
by accident and every group the key names is one a person would name:

- ``experiment``: a folder named by a date and two words, under a year's folder, holding
  acquisitions in ``raw/``, processed files in ``processed/`` and results in ``analysis/``:
  replicates at every stage and a derivation along each acquisition's line;
- ``replicate_days``: three days of one condition, one experiment folder a day;
- ``lattice``: five experiments sharing a core of two condition words, three of them adding one or
  two words (the group the key names by its term set, and the smaller one inside it);
- ``copies``: a file copied byte for byte into a backup folder, a near-copy differing in one byte,
  and a subtree copied whole under ``old/``;
- ``moved``: a readme naming its folder's ``data/`` files by relative path, where ``data/`` now
  stands in an archive folder beside it: the same names elsewhere, which the files alone cannot
  say are the same files (a move is seen between two censuses, not in one);
- ``program``: a Python package laid out as the Dictionary declares (``program_layouts.yaml``),
  whose files would attract stage, replicate and experiment readings anywhere else;
- ``references``: a readme naming two files by relative path, one of them derived from the other
  as it says, and a third it names that is not there;
- ``script``: a script that reads a table and writes a plot, naming both by bare file name;
- ``empty``: an empty output among numbered siblings with content, and an empty file on its own;
- ``contradiction``: a file whose extension says a table and whose bytes are a TIFF;
- ``dates``: notes named with dates in several forms, two of them ambiguous short dates;
- ``ambiguity``: two acquisitions of different conditions and one result, where the files cannot
  say which acquisition the result came from.

The corpus is a tripwire and never a target. It pins behaviour. Nobody changes it to make the
engine's output look right. What the key says is what a person would know from the files and their
names. A shape is never changed because the engine and the key disagree.
"""

from __future__ import annotations

import datetime as dt
import os
import random
import struct
import zlib
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path, PurePosixPath

from ingence.core.destinations import write_atomically
from ingence.evaluation.formats import nd2_bytes, tiff_bytes
from ingence.evaluation.key import (
    FAMILIES,
    PAIR_FAMILIES,
    AnswerKey,
    PairFamily,
    ValueFamily,
)

#: The families the sample collection's key answers and this generator does not lay out: an
#: experiment's chain of stages, and which experiments repeat each other.
_NOT_LAID_OUT = frozenset({"workflow", "repeats"})

__all__ = [
    "GENERATOR_VERSION",
    "KEY_FILE",
    "POOL_DIR",
    "SHAPES",
    "STEMS",
    "GeneratedPool",
    "generate",
    "invented_stems",
    "stems_needed",
]

#: Bumped whenever the same seed and size would write a different pool or key.
GENERATOR_VERSION = 1
POOL_DIR = "pool"
KEY_FILE = "answer_key.json"

#: Invented stems, none of which binds to anything in the shipped Dictionary (a test holds it).
STEMS = (
    "brisa", "kelt", "mirdo", "pellan", "quolt", "saltra", "tamen", "trevik", "vorn", "zeph",
    "zib", "trell", "sorn", "dravo", "fenlo", "gisk", "harnu", "jorvel", "kasto", "lumbri",
    "morv", "nesko", "orvid", "plimso", "quenta", "stavo", "tulvi", "ulmek", "varno", "wesk",
    "yorbi", "zandel", "brevo", "clusk", "dorvin", "elsko", "frandi", "gultro", "istro", "jundi",
    "korva", "lesto", "mivra", "nurko", "olvek", "prask",
)  # fmt: skip

SHAPES = (
    "experiment",
    "replicate_days",
    "lattice",
    "copies",
    "moved",
    "program",
    "references",
    "script",
    "empty",
    "contradiction",
    "dates",
    "ambiguity",
)

#: How many stems one instance of each shape takes.
_STEMS_PER_INSTANCE = {
    "experiment": 2,
    "replicate_days": 2,
    "lattice": 5,
    "copies": 4,
    "moved": 1,
    "program": 1,
    "references": 1,
    "script": 1,
    "empty": 4,
    "contradiction": 1,
    "dates": 1,
    "ambiguity": 3,
}

#: Letters chosen so a made stem reads as invented (``zavok``, ``vekiz``), never as an ordinary
#: word or a surname: onsets led by the rarer consonants, and endings English words seldom take.
_ONSETS = ("z", "v", "k", "d", "g", "b", "t", "m")
_VOWELS = ("a", "e", "i", "o", "u")
_CODAS = ("k", "v", "z")
#: Made stems the shipped Dictionary reads as a slip of one of its words, and so never invented
#: (every domain section loads, and a five-letter spelling one edit from a word reads as that
#: word): ``zidak`` reads as a statistics correction's name.
_READ_AS_WORDS = frozenset({"zidak"})


def stems_needed(size: int) -> int:
    """How many stems a pool of ``size`` instances of every shape takes."""
    return size * sum(_STEMS_PER_INSTANCE.values())


def invented_stems(count: int) -> tuple[str, ...]:
    """The first ``count`` stems: the listed ones, then five-letter ones made from syllables.

    The made ones follow the listed ones in a fixed order, so a larger pool only adds stems.
    """
    made: list[str] = list(STEMS[:count])
    for first in _ONSETS:
        for vowel in _VOWELS:
            for second in _ONSETS:
                for other in _VOWELS:
                    for coda in _CODAS:
                        if len(made) >= count:
                            return tuple(made)
                        stem = f"{first}{vowel}{second}{other}{coda}"
                        if stem not in made and stem not in _READ_AS_WORDS:
                            made.append(stem)
    return tuple(made)


@dataclass(frozen=True)
class GeneratedPool:
    """Where a pool and its key were written, and the key itself."""

    pool: Path
    key_path: Path
    key: AnswerKey
    files: tuple[str, ...]


@dataclass
class _Builder:
    """The files and the answers, gathered before anything is written."""

    random: random.Random
    files: dict[str, bytes] = field(default_factory=dict)
    times: dict[str, dt.datetime] = field(default_factory=dict)
    folders: set[str] = field(default_factory=set)
    values: dict[str, dict[str, str | None]] = field(default_factory=dict)
    undecidable_values: dict[str, set[str]] = field(default_factory=dict)
    ignored: dict[str, set[str]] = field(default_factory=dict)
    groups: dict[str, list[tuple[str, ...]]] = field(default_factory=dict)
    pairs: dict[str, set[tuple[str, str]]] = field(default_factory=dict)
    undecidable_pairs: dict[str, set[tuple[str, str]]] = field(default_factory=dict)
    named_groups: dict[str, tuple[str, ...]] = field(default_factory=dict)
    counter: int = 0

    def unique(self) -> bytes:
        """Bytes no other call returns, so no two files agree by accident."""
        self.counter += 1
        return struct.pack("<I", self.counter) + self.random.randbytes(4)

    def file(self, path: str, data: bytes, moment: dt.datetime) -> str:
        if path in self.files:
            message = f"the generator wrote {path} twice"
            raise ValueError(message)
        self.files[path] = data
        self.times[path] = moment
        parent = PurePosixPath(path).parent
        while str(parent) != ".":
            self.folders.add(str(parent))
            parent = parent.parent
        return path

    def value(self, family: str, path: str, value: str | None) -> None:
        self.values.setdefault(family, {})[path] = value

    def pair(self, family: str, first: str, second: str) -> None:
        self.pairs.setdefault(family, set()).add((first, second))

    def group(self, family: str, members: tuple[str, ...], *, name: str | None = None) -> None:
        self.groups.setdefault(family, []).append(members)
        if name is not None:
            self.named_groups[name] = tuple(sorted(members))


def _table(builder: _Builder, *columns: str) -> bytes:
    """A small table whose values are invented and whose bytes are its own."""
    token = builder.unique().hex()
    rows = [",".join(columns)]
    rows.extend(",".join(str(builder.random.randint(0, 999)) for _ in columns) for _ in range(3))
    rows.append(f"# {token}")
    return ("\n".join(rows) + "\n").encode()


def _png(builder: _Builder) -> bytes:
    """A four-pixel grey PNG whose pixels are its own."""
    pixels = builder.unique()[:4]

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    header = struct.pack(">IIBBBBB", 4, 1, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"\x00" + pixels))
        + chunk(b"IEND", b"")
    )


def _day(builder: _Builder) -> dt.datetime:
    """A day between 2015 and 2023, at ten in the morning."""
    year = builder.random.randint(2015, 2023)
    start = dt.datetime(year, 1, 1, 10, tzinfo=dt.UTC)
    return start + dt.timedelta(days=builder.random.randint(0, 330))


#: English month abbreviations, written out rather than read from the locale.
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _compact(day: dt.datetime) -> str:
    return day.strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# The shapes.
# ---------------------------------------------------------------------------


def _experiment(builder: _Builder, stems: tuple[str, ...]) -> None:
    first, second = stems
    day = _day(builder)
    folder = f"{day.year}/{_compact(day)} {first} {second}"
    builder.value("experiment_folder", folder, "experiment")
    count = builder.random.randint(2, 3)
    stages: dict[str, list[str]] = {"raw": [], "processed": [], "analysis": []}
    for index in range(1, count + 1):
        moment = day + dt.timedelta(minutes=index)
        stem = f"{first}_{second}_{index:03d}"
        raw = builder.file(
            f"{folder}/raw/{stem}.nd2", nd2_bytes(moment, pixels=builder.unique()), moment
        )
        processed = builder.file(
            f"{folder}/processed/{stem}_processed.tif",
            tiff_bytes(builder.unique() * 2),
            moment + dt.timedelta(hours=2),
        )
        results = builder.file(
            f"{folder}/analysis/{stem}_results.csv",
            _table(builder, "frame", "intensity"),
            moment + dt.timedelta(hours=4),
        )
        for stage, path in (("raw", raw), ("processed", processed), ("analysis", results)):
            builder.value("stage", path, stage)
            stages[stage].append(path)
        builder.pair("derivation", raw, processed)
        builder.pair("derivation", processed, results)
    for paths in stages.values():
        builder.group("replicate", tuple(paths))


def _replicate_days(builder: _Builder, stems: tuple[str, ...]) -> None:
    first, second = stems
    day = _day(builder)
    folders: list[str] = []
    acquisitions: list[str] = []
    results: list[str] = []
    for offset in range(3):
        moment = day + dt.timedelta(days=offset)
        folder = f"{_compact(moment)} {first} {second}"
        folders.append(folder)
        builder.value("experiment_folder", folder, "experiment")
        raw = builder.file(
            f"{folder}/{first}_{second}_001.nd2", nd2_bytes(moment, pixels=builder.unique()), moment
        )
        table = builder.file(
            f"{folder}/{first}_{second}_001_results.csv",
            _table(builder, "track", "speed"),
            moment + dt.timedelta(hours=3),
        )
        builder.value("stage", raw, "raw")
        builder.value("stage", table, "analysis")
        builder.pair("derivation", raw, table)
        acquisitions.append(raw)
        results.append(table)
    builder.group("replicate", tuple(acquisitions))
    builder.group("replicate", tuple(results))
    builder.group("group", tuple(folders), name=f"{first} {second}")


def _lattice(builder: _Builder, stems: tuple[str, ...]) -> None:
    core_one, core_two, added, other, further = stems
    day = _day(builder)
    # Five experiments: the group lattice asks about a set only when five or more experiments
    # hold it (``DEFAULT_MINIMUM_EXPERIMENTS``), so the shape holds five to measure the group
    # rules at their default.
    words_of = ((), (added,), (other,), (added, further), ())
    folders: list[str] = []
    with_added: list[str] = []
    for offset, extra in enumerate(words_of):
        moment = day + dt.timedelta(days=7 * offset)
        words = (core_one, core_two, *extra)
        folder = f"{_compact(moment)} {' '.join(words)}"
        folders.append(folder)
        if added in extra:
            with_added.append(folder)
        builder.value("experiment_folder", folder, "experiment")
        stem = "_".join(words)
        raw = builder.file(
            f"{folder}/{stem}_001.nd2", nd2_bytes(moment, pixels=builder.unique()), moment
        )
        table = builder.file(
            f"{folder}/{stem}_001_results.csv",
            _table(builder, "object", "area"),
            moment + dt.timedelta(hours=5),
        )
        builder.value("stage", raw, "raw")
        builder.value("stage", table, "analysis")
        builder.pair("derivation", raw, table)
    builder.group("group", tuple(folders), name=f"{core_one} {core_two}")
    builder.named_groups[f"{core_one} {core_two} {added}"] = tuple(sorted(with_added))


def _copies(builder: _Builder, stems: tuple[str, ...]) -> None:
    owner, runner, one, two = stems
    day = _day(builder)
    original = _table(builder, "well", "value")
    first = builder.file(f"{owner}/{owner}_table.csv", original, day)
    second = builder.file(
        f"{owner} backup/{owner}_table.csv", original, day + dt.timedelta(days=30)
    )
    builder.group("copy", (first, second))
    near = _table(builder, "well", "count")
    changed = near[:-2] + bytes((near[-2] ^ 1,)) + near[-1:]
    builder.file(f"{owner}/{owner}_counts.csv", near, day)
    builder.file(f"{owner} backup/{owner}_counts.csv", changed, day + dt.timedelta(days=30))
    for word in (one, two):
        body = _table(builder, "time", word)
        kept = builder.file(f"{runner}_run/{runner}_{word}.csv", body, day)
        copied = builder.file(
            f"old/{runner}_run/{runner}_{word}.csv", body, day + dt.timedelta(days=60)
        )
        builder.group("copy", (kept, copied))


def _moved(builder: _Builder, stems: tuple[str, ...]) -> None:
    (name,) = stems
    day = _day(builder)
    later = day + dt.timedelta(days=90)
    readme = builder.file(
        f"{name}/README.txt",
        (
            f"The raw counts are data/{name}_counts.csv and the fits data/{name}_fits.csv.\n"
            f"({builder.unique().hex()})\n"
        ).encode(),
        day,
    )
    for word in ("counts", "fits"):
        target = builder.file(
            f"{name} archive/data/{name}_{word}.csv", _table(builder, "cell", word), later
        )
        builder.undecidable_pairs.setdefault("reference", set()).add((readme, target))


def _program(builder: _Builder, stems: tuple[str, ...]) -> None:
    (name,) = stems
    day = _day(builder)
    root = f"{name}-tools"
    builder.value("program", root, "program")
    text = builder.unique().hex()
    beneath = [
        builder.file(
            f"{root}/pyproject.toml", f'[project]\nname = "{name}-tools"\n# {text}\n'.encode(), day
        ),
        builder.file(
            f"{root}/{name}_io.py", f"def read(path):\n    return path  # {text}\n".encode(), day
        ),
        builder.file(
            f"{root}/{name}_plot.py",
            f"def plot(values):\n    return values  # {text}\n".encode(),
            day,
        ),
        builder.file(
            f"{root}/tests/test_{name}.py",
            f"def test_{name}():\n    assert True  # {text}\n".encode(),
            day,
        ),
        builder.file(f"{root}/data/{name}_sample_raw.csv", _table(builder, "x", "y"), day),
        builder.file(f"{root}/data/{name}_001.tif", tiff_bytes(builder.unique() * 2), day),
        builder.file(f"{root}/data/{name}_002.tif", tiff_bytes(builder.unique() * 2), day),
        builder.file(f"{root}/data/{name}_results.csv", _table(builder, "x", "fit"), day),
    ]
    builder.ignored.setdefault("program", set()).update({f"{root}/tests", f"{root}/data"})
    for path in beneath:
        # Beneath a program every reading defers: no stage, and nothing an empty file means.
        builder.value("stage", path, None)
        builder.ignored.setdefault("empty_file", set()).add(path)


def _references(builder: _Builder, stems: tuple[str, ...]) -> None:
    (name,) = stems
    day = _day(builder)
    counts = builder.file(f"{name}/{name}_counts.csv", _table(builder, "cell", "count"), day)
    fits = builder.file(
        f"{name}/fits/{name}_fits.csv", _table(builder, "cell", "rate"), day + dt.timedelta(hours=1)
    )
    readme = builder.file(
        f"{name}/README.txt",
        (
            f"The counts are in ./{name}_counts.csv.\n"
            f"fits/{name}_fits.csv was computed from ./{name}_counts.csv.\n"
            f"The first table, ./{name}_gone.csv, was removed.\n"
            f"({builder.unique().hex()})\n"
        ).encode(),
        day + dt.timedelta(hours=2),
    )
    builder.pair("reference", readme, counts)
    builder.pair("reference", readme, fits)
    builder.pair("derivation", counts, fits)


def _script(builder: _Builder, stems: tuple[str, ...]) -> None:
    (name,) = stems
    day = _day(builder)
    counts = builder.file(f"{name} plots/{name}_counts.csv", _table(builder, "bin", "count"), day)
    script = builder.file(
        f"{name} plots/{name}_figure.py",
        (
            "import pandas as pd\n"
            f'frame = pd.read_csv("{name}_counts.csv")\n'
            "axes = frame.plot()\n"
            f'axes.figure.savefig("{name}_plot.png")\n'
            f"# {builder.unique().hex()}\n"
        ).encode(),
        day + dt.timedelta(hours=1),
    )
    plot = builder.file(f"{name} plots/{name}_plot.png", _png(builder), day + dt.timedelta(hours=2))
    builder.pair("reference", script, counts)
    builder.pair("reference", script, plot)
    builder.pair("derivation", counts, plot)


def _empty(builder: _Builder, stems: tuple[str, ...]) -> None:
    numbered, alone, note_word, table_word = stems
    day = _day(builder)
    kept: list[str] = []
    for index in (1, 2, 3):
        body = b"" if index == 2 else _table(builder, "time", "signal")
        path = builder.file(
            f"{numbered}/{numbered}_{index:03d}.csv", body, day + dt.timedelta(minutes=index)
        )
        if index == 2:
            builder.value("empty_file", path, "empty_output")
            builder.ignored.setdefault("replicate", set()).add(path)
        else:
            kept.append(path)
    builder.group("replicate", tuple(kept))
    lone = builder.file(f"{alone}/{alone}_{note_word}.txt", b"", day)
    builder.value("empty_file", lone, "empty")
    builder.file(f"{alone}/{alone}_{table_word}.csv", _table(builder, "a", "b"), day)


def _contradiction(builder: _Builder, stems: tuple[str, ...]) -> None:
    (name,) = stems
    day = _day(builder)
    path = builder.file(f"{name} export/{name}_image.csv", tiff_bytes(builder.unique() * 2), day)
    builder.value("contradicted_extension", path, "image/tiff")
    builder.file(f"{name} export/{name}_table.csv", _table(builder, "x", "y"), day)


def _dates(builder: _Builder, stems: tuple[str, ...]) -> None:
    (name,) = stems
    folder = f"{name} notes"
    base = _day(builder)
    # One day per spelling, so no two notes share a date by accident.
    days = [base + dt.timedelta(days=17 * index) for index in range(7)]
    unambiguous = [
        (days[0], f"{days[0]:%Y-%m-%d}_{name}.txt"),
        (days[1], f"{_compact(days[1])}_{name}.txt"),
        (days[2], f"{days[2].day:02d}{_MONTHS[days[2].month - 1]}{days[2].year}_{name}.txt"),
    ]
    late = days[3].replace(day=max(days[3].day, 13))
    unambiguous.append((late, f"{name} {late:%d.%m.%Y}.txt"))
    for moment, file_name in unambiguous:
        path = builder.file(
            f"{folder}/{file_name}", f"{name} {builder.unique().hex()}\n".encode(), moment
        )
        builder.value("name_date", path, f"{moment:%Y-%m-%d}")
    # Day and month both twelve or under: the files cannot say which is which.
    for index, spelling in ((4, "{d:%d.%m.%Y}"), (5, "{d:%m-%d-%y}")):
        moment = days[index].replace(day=builder.random.randint(1, 12))
        if moment.day == moment.month:
            moment = moment.replace(day=moment.month % 12 + 1)
        file_name = f"{name} {spelling.format(d=moment)}.txt"
        path = builder.file(
            f"{folder}/{file_name}", f"{name} {builder.unique().hex()}\n".encode(), moment
        )
        builder.undecidable_values.setdefault("name_date", set()).add(path)


def _ambiguity(builder: _Builder, stems: tuple[str, ...]) -> None:
    name, one, two = stems
    day = _day(builder)
    folder = f"{name} {one} {two}"
    builder.value("experiment_folder", folder, "experiment")
    first = builder.file(f"{folder}/{name}_{one}.nd2", nd2_bytes(day, pixels=builder.unique()), day)
    second = builder.file(
        f"{folder}/{name}_{two}.nd2",
        nd2_bytes(day + dt.timedelta(minutes=5), pixels=builder.unique()),
        day + dt.timedelta(minutes=5),
    )
    results = builder.file(
        f"{folder}/{name}_results.csv",
        _table(builder, "object", "size"),
        day + dt.timedelta(hours=1),
    )
    builder.value("stage", first, "raw")
    builder.value("stage", second, "raw")
    builder.value("stage", results, "analysis")
    for source in (first, second):
        builder.undecidable_pairs.setdefault("derivation", set()).add((source, results))


_WRITERS = {
    "experiment": _experiment,
    "replicate_days": _replicate_days,
    "lattice": _lattice,
    "copies": _copies,
    "moved": _moved,
    "program": _program,
    "references": _references,
    "script": _script,
    "empty": _empty,
    "contradiction": _contradiction,
    "dates": _dates,
    "ambiguity": _ambiguity,
}


# ---------------------------------------------------------------------------
# The key, complete for every family but the dates'.
# ---------------------------------------------------------------------------


def _key(builder: _Builder, *, seed: int, size: int) -> AnswerKey:
    files = sorted(builder.files)
    folders = sorted(builder.folders)
    programs = [path for path, value in builder.values.get("program", {}).items() if value]
    beneath = {
        path for path in (*files, *folders) for root in programs if path.startswith(f"{root}/")
    }
    families: dict[str, ValueFamily | PairFamily] = {}
    over_files = ("stage", "empty_file", "contradicted_extension")
    over_folders = ("experiment_folder", "program")
    for name in (*over_files, *over_folders):
        stated = builder.values.get(name, {})
        every = files if name in over_files else folders
        ignored = set(builder.ignored.get(name, set()))
        if name == "experiment_folder":
            ignored |= beneath
        expected = {path: stated.get(path) for path in every if path not in ignored}
        families[name] = ValueFamily(expected=expected, ignored=tuple(sorted(ignored)))
    families["name_date"] = ValueFamily(
        expected=dict(builder.values.get("name_date", {})),
        undecidable=tuple(sorted(builder.undecidable_values.get("name_date", set()))),
        complete=False,
    )
    for name, directed in PAIR_FAMILIES.items():
        if name in _NOT_LAID_OUT:
            continue
        pairs = set(builder.pairs.get(name, set()))
        for members in builder.groups.get(name, []):
            pairs.update(combinations(sorted(members), 2))
        families[name] = PairFamily(
            directed=directed,
            expected=tuple(sorted(pairs)),
            undecidable=tuple(sorted(builder.undecidable_pairs.get(name, set()))),
            ignored=tuple(sorted(builder.ignored.get(name, set()))),
        )
    if builder.named_groups:
        group = families["group"]
        assert isinstance(group, PairFamily)
        families["group"] = PairFamily(
            directed=False,
            expected=group.expected,
            undecidable=group.undecidable,
            ignored=group.ignored,
            named={name: builder.named_groups[name] for name in sorted(builder.named_groups)},
        )
    key = AnswerKey(
        families={name: families[name] for name in FAMILIES if name in families},
        describes={
            "written_by": "ingence.evaluation.generate",
            "generator_version": GENERATOR_VERSION,
            "seed": seed,
            "size": size,
            "shapes": list(SHAPES),
        },
    )
    # Through its own JSON, so the key returned is the key a reader of the file gets.
    return AnswerKey.from_json(key.to_json())


def _write(pool: Path, builder: _Builder) -> None:
    for path in sorted(builder.files):
        target = pool.joinpath(*PurePosixPath(path).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(builder.files[path])
        stamp = builder.times[path].timestamp()
        os.utime(target, (stamp, stamp))
    # Folders last and deepest first, each dated by the latest file beneath it.
    for folder in sorted(builder.folders, key=lambda path: (-path.count("/"), path)):
        latest = max(
            moment for path, moment in builder.times.items() if path.startswith(f"{folder}/")
        )
        stamp = latest.timestamp()
        os.utime(pool.joinpath(*PurePosixPath(folder).parts), (stamp, stamp))


def generate(destination: Path, *, seed: int, size: int) -> GeneratedPool:
    """Write a pool of ``size`` instances of every shape under ``destination``, and its key beside.

    The pool is ``destination/pool`` and the key ``destination/answer_key.json``. Nothing already
    there is written over: a pool folder that exists is refused.
    """
    if size < 1:
        message = f"a pool holds at least one instance of every shape, not {size}"
        raise ValueError(message)
    pool = destination / POOL_DIR
    key_path = destination / KEY_FILE
    if pool.exists() or key_path.exists():
        message = f"refusing to write a pool over {destination}: a pool or a key is already there"
        raise FileExistsError(message)
    # Not for secrets: a seeded generator is what makes one seed one pool.
    seeded = random.Random(f"ingence-evaluation:{GENERATOR_VERSION}:{seed}")  # noqa: S311
    builder = _Builder(random=seeded)
    stems = list(invented_stems(stems_needed(size)))
    builder.random.shuffle(stems)
    taken = iter(stems)
    for _ in range(size):
        for shape in SHAPES:
            chosen = tuple(next(taken) for _ in range(_STEMS_PER_INSTANCE[shape]))
            _WRITERS[shape](builder, chosen)
    key = _key(builder, seed=seed, size=size)
    pool.mkdir(parents=True)
    _write(pool, builder)
    # Bytes, not text: no platform turns the key's line endings into its own.
    write_atomically(key_path, key.dumps().encode("utf-8"))
    return GeneratedPool(pool=pool, key_path=key_path, key=key, files=tuple(sorted(builder.files)))
