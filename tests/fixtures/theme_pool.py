"""A generated pool with known themes: invented words in planted co-appearance groups.

Every word is invented and binds to nothing in the shipped Dictionary. The pool holds:

- six planted themes, each with four words of its own, over dated experiments of three invented
  people;
- filler words that every theme shares;
- a frequent unknown word that co-appears with one theme's words;
- outliers that share nothing;
- a pair too small to be a theme;
- two undated experiments.

The author wrote the expectations with the plant, before the themes were first drawn.

Two shapes of the same plant: :func:`theme_documents` (word lists, for the pure core) and
:func:`write_theme_pool` (folders and files, for the engine).
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

#: Each planted theme: its four words, its years (first, last), its people.
PLANT: dict[str, tuple[tuple[str, str, str, str], tuple[int, int], tuple[str, ...]]] = {
    "t0": (("keltan", "vornish", "brisol", "zephan"), (2019, 2020), ("kipper",)),
    "t1": (("quolt", "tamen", "sornel", "ludra"), (2020, 2022), ("kipper",)),
    "t2": (("miravo", "pellit", "droska", "fennow"), (2021, 2023), ("tovald",)),
    "t3": (("ostrel", "gavish", "nuntle", "praska"), (2022, 2024), ("tovald",)),
    # Shared by two people, one before the other (a line handed on); its second word is written
    # only by the first, its fourth only by the second (a word succeeded by another).
    "t4": (("hoben", "ilskar", "cadrom", "wemble"), (2019, 2024), ("kipper", "zandri")),
    "t5": (("troskel", "ezzlem", "rimova", "jadder"), (2023, 2024), ("zandri",)),
}
#: Experiments per planted theme.
PER_THEME = 10
#: Words every theme draws from, one each: never a theme's own.
FILLERS = ("alpok", "nirkel", "sebbit", "tullor", "gormin", "wixet")
#: The frequent unknown word: in every experiment of theme t2, beside its words.
FREQUENT_UNKNOWN = "tarnick"
#: Which words the "Dictionary" knows, and their canonical names (spelling differs on purpose).
SENSES = {
    "keltan": "keltane_assay",
    "brisol": "brisol_marker",
    "quolt": "quolting",
    "miravo": "miravo_stain",
    "ostrel": "ostrel_probe",
    "hoben": "hobenite",
    "troskel": "troskel_line",
    # The pair: senses so they count with two experiments, yet two are no theme.
    "fablen": "fablen_dye",
    "gorvin": "gorvin_dye",
}
#: Outliers: each has words no other experiment has.
OUTLIERS = {"lone_a": ("qixel", "brumf"), "lone_b": ("yondle", "skarp"), "lone_c": ("vibbet",)}
#: An experiment whose only words are fillers every theme shares: kept words, close to nobody.
FILLER_ONLY = {"filler_only": ("alpok", "nirkel")}
#: The pair too small to be a theme.
PAIR = {"pair_a": ("fablen", "gorvin"), "pair_b": ("fablen", "gorvin")}
#: The word theme t4's first owner writes, and the word its second writes in its place.
HANDED_FROM, HANDED_TO = "ilskar", "wemble"
#: Two experiments with no date, inside theme t1.
UNDATED = ("t1_08", "t1_09")


@dataclass(frozen=True)
class ThemeDocuments:
    documents: dict[str, list[str]]
    planted: dict[str, str]
    years: dict[str, int | None]
    people: dict[str, str]
    #: Each unit's experiment folder below the root, where the plant was written as folders.
    folders: dict[str, str] = field(default_factory=dict)


def theme_documents(seed: int = 7) -> ThemeDocuments:
    """The plant as word lists, ``{unit: words}``, with each unit's planted theme and year."""
    chooser = random.Random(seed)
    documents: dict[str, list[str]] = {}
    planted: dict[str, str] = {}
    years: dict[str, int | None] = {}
    people: dict[str, str] = {}
    for theme, (words, (first, last), owners) in PLANT.items():
        span = list(range(first, last + 1))
        for number in range(PER_THEME):
            unit = f"{theme}_{number:02d}"
            if len(owners) == 1:
                own = sorted(chooser.sample(words, 3), key=words.index)
            else:
                # A word handed on: the first owner writes the second word, the second the fourth.
                own = (
                    [words[0], words[1], words[2]]
                    if number < PER_THEME // 2
                    else [words[0], words[2], words[3]]
                )
            document = [*own, FILLERS[(number + len(theme)) % len(FILLERS)]]
            if theme == "t2":
                document.append(FREQUENT_UNKNOWN)
            documents[unit] = document
            planted[unit] = theme
            if len(owners) == 1:
                people[unit] = owners[0]
                years[unit] = span[number * len(span) // PER_THEME]
            else:
                # The first owner the first half of the span, the second the rest.
                half = number < PER_THEME // 2
                people[unit] = owners[0] if half else owners[1]
                cut = len(span) // 2
                part = span[:cut] if half else span[cut:]
                index = (number if half else number - PER_THEME // 2) % len(part)
                years[unit] = part[index]
            if unit in UNDATED:
                years[unit] = None
    for unit, words_of in {**OUTLIERS, **PAIR, **FILLER_ONLY}.items():
        documents[unit] = list(words_of)
        planted[unit] = "none"
        years[unit] = 2022
        people[unit] = "zandri"
    return ThemeDocuments(documents, planted, years, people)


def _campaign_words(count: int) -> list[tuple[str, str]]:
    """``count`` pairs of invented words, from syllables nobody's vocabulary holds."""
    syllables = ("ba", "ko", "ri", "tu", "me", "sa", "lo", "vi", "ne", "zu", "fa", "gi")
    made = sorted(f"{a}{b}{c}x" for a in syllables for b in syllables for c in syllables[:3])
    step = len(made) // (2 * count)
    picked = made[::step][: 2 * count]
    return [(picked[2 * i], picked[2 * i + 1]) for i in range(count)]


def machine_and_human(
    machine_units: int = 3000, human_per_theme: int = 10, seed: int = 11, campaigns: int = 3
) -> tuple[dict[str, list[str]], dict[str, str], dict[str, str]]:
    """One huge machine-made collection beside a small human one: documents, collections, plant.

    The machine collection: thousands of near-identical units, three planted campaigns (40, 35
    and 25 in a hundred; or ``campaigns`` of equal size, more words than the LSA has
    dimensions), each unit its campaign's two words, one of a few machine words, and the tool's
    own word every unit carries. The human collection: four planted themes of
    ``human_per_theme`` units, each unit three of its theme's four words and one filler.
    """
    chooser = random.Random(seed)
    documents: dict[str, list[str]] = {}
    collections: dict[str, str] = {}
    plant: dict[str, str] = {}
    planted_campaigns = (
        (
            ("m0", ("plavo", "rennet"), 0.40),
            ("m1", ("skovil", "dranth"), 0.35),
            ("m2", ("umbrik", "teslov"), 0.25),
        )
        if campaigns == 3
        else tuple(
            (f"m{index}", pair, 1.0 / campaigns)
            for index, pair in enumerate(_campaign_words(campaigns))
        )
    )
    machine_words = ("wellow", "sitrex", "kanzo", "bluvver")
    edges = [0]
    for _, _, share in planted_campaigns:
        edges.append(edges[-1] + round(share * machine_units))
    edges[-1] = machine_units
    for number in range(machine_units):
        index = next(i for i in range(len(planted_campaigns)) if number < edges[i + 1])
        name, words, _ = planted_campaigns[index]
        unit = f"machine_{number:05d}"
        documents[unit] = [*words, machine_words[number % len(machine_words)], "autoscan"]
        collections[unit] = "machine"
        plant[unit] = name
    human = {
        "h0": ("corvel", "linnet", "masko", "pontiv"),
        "h1": ("quarrel", "sabbat", "tindle", "voskar"),
        "h2": ("abrel", "bexit", "chorvan", "dulmer"),
        "h3": ("erkle", "fossan", "glimber", "hartov"),
    }
    for theme, words in human.items():
        for number in range(human_per_theme):
            unit = f"human_{theme}_{number:02d}"
            own = sorted(chooser.sample(words, 3), key=words.index)
            documents[unit] = [*own, FILLERS[number % len(FILLERS)]]
            collections[unit] = "human"
            plant[unit] = theme
    return documents, collections, plant


#: Invented words written as a shipped Dictionary surface in the engine's pool, so the engine
#: binds them to a sense: ``widefield`` (whose sense is another word), ``airyscan``, and
#: ``trackmate`` (whose sense's canonical name is written ``TrackMate``). Shipped vocabulary.
ENGINE_SURFACES = {"ostrel": "widefield", "troskel": "airyscan", "ludra": "trackmate"}


def write_theme_pool(
    root: Path, seed: int = 7, surfaces: dict[str, str] | None = None
) -> ThemeDocuments:
    """The plant as folders: ``<person>/<YYYYMMDD> <word> <word>/<word>_<word>_<nn>.nd2``.

    The experiment folder's name carries a date and the unit's first two words; its files carry
    the rest, as acquisitions (``.nd2``) and a results table. An undated experiment's folder has
    no date in its name and its files' clocks are set before any credible time (the year 1985,
    which the earliest credible time sets aside). Every other file's clock is set inside its
    experiment's year.
    """
    plant = theme_documents(seed)
    import datetime as dt

    folders: dict[str, str] = {}
    for order, (unit, written) in enumerate(sorted(plant.documents.items())):
        words = [(surfaces or {}).get(word, word) for word in written]
        day = 1 + order % 28
        person = plant.people[unit]
        year = plant.years[unit]
        title_words = " ".join(words[:2])
        number = int(unit.rsplit("_", 1)[1]) if unit[-1].isdigit() else 0
        if year is None:
            folder = root / person / f"{title_words} {unit.replace('_', '')}"
        else:
            folder = root / person / f"{year}{(number % 12) + 1:02d}{day:02d} {title_words}"
        folders[unit] = folder.relative_to(root).as_posix()
        rest = words[2:] or words[:1]
        stem = "_".join(rest)
        files = [f"{stem}_{index:02d}.nd2" for index in range(1, 4)] + [f"{stem}_results.csv"]
        moment = dt.datetime(year or 1985, (number % 12) + 1, day, 10, tzinfo=dt.UTC).timestamp()
        for name in files:
            path = folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{unit} {name}\n")
            os.utime(path, (moment, moment))
    return ThemeDocuments(plant.documents, plant.planted, plant.years, plant.people, folders)


#: The runs :func:`build_theme_project` writes.
THEME_CENSUS_RUN = "run_20260101T000001Z_aaaaaaaaaaaa"
THEME_INFER_RUN = "run_20260101T000002Z_bbbbbbbbbbbb"


def build_theme_project(base: Path) -> tuple[Path, ThemeDocuments]:
    """The plant written as folders under ``base/pool``, indexed and inferred by the engine under
    revision 4 (a census and an inference, no dive): the project's path and the plant."""
    from fixtures.revision_4 import write_revision
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census

    pool = base / "pool"
    pool.mkdir(parents=True)
    plant = write_theme_pool(pool, surfaces=ENGINE_SURFACES)
    project = Project.create(base / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    run_census(project, run_id=THEME_CENSUS_RUN, started_at="t")
    run_inference(project, run_id=THEME_INFER_RUN, started_at="t")
    return project.path, plant


def many_themes(
    units: int = 2500, themes: int = 60, seed: int = 2
) -> tuple[dict[str, list[str]], dict[str, int]]:
    """More themes than the LSA's minimum of fifty dimensions.

    The pool plants ``themes`` themes of ten invented words each. Each unit holds three of its
    theme's words and six noise words. A Zipf law draws the noise words from a vocabulary of
    invented syllables.
    """
    chooser = random.Random(seed)
    syllables = ("ka", "lo", "mi", "nu", "pe", "ro", "si", "tu", "va", "ze", "bo", "di", "fu", "ga")
    made = sorted({"".join(chooser.choice(syllables) for _ in range(4)) for _ in range(20000)})
    vocabulary = made[:8000]
    zipf = [1 / (rank + 1) for rank in range(len(vocabulary))]
    planted_words = [chooser.sample(vocabulary[200:], 10) for _ in range(themes)]
    documents: dict[str, list[str]] = {}
    plant: dict[str, int] = {}
    for number in range(units):
        theme = chooser.randrange(themes)
        unit = f"u{number:05d}"
        documents[unit] = chooser.sample(planted_words[theme], 3) + chooser.choices(
            vocabulary, zipf, k=6
        )
        plant[unit] = theme
    return documents, plant
