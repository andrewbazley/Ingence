"""Themes over time, the year profile, who else, and the undated.

These are pure functions over plain mappings, like the core: each experiment's theme, year, words
and person go in, and JSON-ready rows come out. The field names are the Dashboard's where they are
sensible. The rules use one definition of an experiment's year and one base for every share
(themed experiments only). Beside each theme's ``going`` the module gives a direction (rising,
falling, steady, new or stopped). Beside the themes over their usual share in a year it gives
those under it.

A person is whatever the caller names (a setting, never inferred from names). Nothing here reads
storage. :mod:`ingence.themes.projection` feeds the functions from a run.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from statistics import median
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ingence.core.settings import ThemeSettings

__all__ = [
    "substitutes",
    "theme_timelines",
    "undated",
    "who_else",
    "year_profile",
]

#: How many of each list a response carries at most (display lengths, not thresholds).
SPECIFIC_WORDS_LISTED = 15
STORY_THEMES_LISTED = 3
NEW_WORDS_LISTED = 6
SUBSTITUTES_LISTED = 12
SHARED_WORDS_LISTED = 400
#: Words whose company is compared at once when finding words handed on (memory a band of rows).
SUBSTITUTE_BAND = 512
#: The fewest letters a word handed on needs (three). Shorter words are codes.
SUBSTITUTE_MIN_LETTERS = 3


def _span(years: Sequence[int]) -> str:
    return f"{min(years)}-{max(years)}" if years else ""


def _middle(years: Sequence[int]) -> float:
    return float(median(years))


def _specific_words(
    members: Sequence[str],
    years: Mapping[str, int | None],
    words: Mapping[str, frozenset[str]],
    themed_with: Mapping[str, int],
    display: Mapping[str, str],
    settings: ThemeSettings,
) -> list[dict[str, Any]]:
    """The theme's specific words: sorted by median year, then most experiments."""
    size = len(members)
    carrying: dict[str, list[str]] = defaultdict(list)
    for unit in members:
        for word in words.get(unit, frozenset()):
            carrying[word].append(unit)
    found: list[dict[str, Any]] = []
    for word in sorted(carrying):
        units = carrying[word]
        k = len(units)
        if k < 2 or k > settings.specific_max_share * size:
            continue
        if k / max(themed_with.get(word, k), 1) < settings.specific_min_specificity:
            continue
        dated = sorted(y for u in units if (y := years.get(u)) is not None)
        if len(dated) < 2:
            continue
        found.append(
            {
                "word": display.get(word, word),
                "experiments": k,
                "first": dated[0],
                "last": dated[-1],
                "median": _middle(dated),
            }
        )
    found.sort(key=lambda row: (row["median"], -row["experiments"], row["word"]))
    return found


def _succession(candidates: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Words that follow one another within a theme: each begins at least a year after the last,
    with at most one year's overlap. A chain of fewer than two words is not returned."""
    if not candidates:
        return []
    chain = [candidates[0]]
    for word in candidates[1:]:
        last = chain[-1]
        if word["first"] >= last["first"] + 1 and last["last"] <= word["first"] + 1:
            chain.append(word)
        elif (
            word["first"] == last["first"]
            and word["experiments"] > last["experiments"]
            and (len(chain) == 1 or chain[-2]["last"] <= word["first"] + 1)
        ):
            chain[-1] = word
    return chain if len(chain) >= 2 else []


def _direction(
    by_year: Mapping[int, int],
    totals: Mapping[int, int],
    first: int,
    going: bool,
    latest: int,
    settings: ThemeSettings,
) -> str:
    """Say whether a theme is rising, falling, steady, new or stopped.

    A going theme's share of all themed experiments over the last ``direction_recent_years``
    years is compared against its share over the years before, from its first year on. The
    direction is ``new`` where the theme began within the recent years, and ``stopped`` where it
    is not going."""
    if not going:
        return "stopped"
    recent_from = latest - settings.direction_recent_years + 1
    if first >= recent_from:
        return "new"

    def share(years: range) -> float:
        own = sum(by_year.get(y, 0) for y in years)
        all_themed = sum(totals.get(y, 0) for y in years)
        return own / all_themed if all_themed else 0.0

    recent = share(range(recent_from, latest + 1))
    before = share(range(first, recent_from))
    if before == 0:
        return "rising" if recent > 0 else "steady"
    if recent >= settings.direction_ratio * before:
        return "rising"
    if recent * settings.direction_ratio <= before:
        return "falling"
    return "steady"


def _people_in(
    members: Sequence[str],
    years: Mapping[str, int | None],
    people: Mapping[str, str | None],
    settings: ThemeSettings,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Each person's span in a theme, and the lines handed on between them."""
    by_person: dict[str, list[str]] = defaultdict(list)
    for unit in members:
        person = people.get(unit)
        if person:
            by_person[person].append(unit)
    spans: dict[str, dict[str, Any]] = {}
    for person in sorted(by_person):
        units = by_person[person]
        dated = sorted(y for u in units if (y := years.get(u)) is not None)
        spans[person] = {
            "experiments": len(units),
            "first": dated[0] if dated else None,
            "last": dated[-1] if dated else None,
            "median": _middle(dated) if dated else None,
        }
    counted = [
        person
        for person, span in spans.items()
        if span["experiments"] >= settings.person_min_experiments and span["median"] is not None
    ]
    handed: list[dict[str, Any]] = []
    for earlier in counted:
        for later in counted:
            a, b = spans[earlier], spans[later]
            if a["median"] < b["median"] and b["first"] >= a["last"] - 1:
                handed.append(
                    {
                        "earlier": earlier,
                        "later": later,
                        "earlier_years": f"{a['first']}-{a['last']}",
                        "later_years": f"{b['first']}-{b['last']}",
                    }
                )
    handed.sort(key=lambda row: (row["earlier_years"], row["earlier"], row["later"]))
    return spans, handed


def theme_timelines(
    themes: Mapping[str, int],
    labels: Mapping[int, str],
    broad_labels: Mapping[int, str],
    broad_of: Mapping[int, int],
    years: Mapping[str, int | None],
    words: Mapping[str, frozenset[str]],
    display: Mapping[str, str],
    people: Mapping[str, str | None],
    settings: ThemeSettings,
) -> list[dict[str, Any]]:
    """Per theme: first, peak, last, going, direction, by_year, specific words, succession, and
    per person their span and the lines handed on. ``themes``: each placed unit's theme."""
    members_of: dict[int, list[str]] = defaultdict(list)
    for unit in sorted(themes):
        members_of[themes[unit]].append(unit)
    themed_with: Counter[str] = Counter()
    for unit in themes:
        themed_with.update(words.get(unit, frozenset()))
    totals: Counter[int] = Counter(y for u in themes if (y := years.get(u)) is not None)
    latest = max(totals) if totals else None
    rows: list[dict[str, Any]] = []
    for theme in sorted(members_of):
        members = members_of[theme]
        by_year = Counter(y for u in members if (y := years.get(u)) is not None)
        first = min(by_year) if by_year else None
        last = max(by_year) if by_year else None
        peak = min(by_year, key=lambda y: (-by_year[y], y)) if by_year else None
        going = bool(latest is not None and last is not None and last >= latest - 1)
        direction = (
            _direction(by_year, totals, first, going, latest, settings)
            if first is not None and latest is not None
            else "undated"
        )
        candidates = _specific_words(members, years, words, themed_with, display, settings)
        spans, handed = _people_in(members, years, people, settings)
        rows.append(
            {
                "theme": theme,
                "label": labels.get(theme, ""),
                "broad": broad_labels.get(broad_of.get(theme, -1), ""),
                "broad_theme": broad_of.get(theme),
                "experiments": len(members),
                "undated": sum(1 for u in members if years.get(u) is None),
                "first": first,
                "peak": peak,
                "last": last,
                "going": going,
                "direction": direction,
                "by_year": {str(y): by_year[y] for y in sorted(by_year)},
                "specific_words": candidates[:SPECIFIC_WORDS_LISTED],
                "succession": _succession(candidates),
                "people": spans,
                "shared": sum(
                    1 for s in spans.values() if s["experiments"] >= settings.person_min_experiments
                )
                >= 2,
                "handed_on": handed,
            }
        )
    return rows


def year_profile(
    themes: Mapping[str, int],
    labels: Mapping[int, str],
    years: Mapping[str, int | None],
    words: Mapping[str, frozenset[str]],
    display: Mapping[str, str],
    settings: ThemeSettings,
) -> dict[str, dict[str, Any]]:
    """Per year, over themed experiments: experiments; themes over their usual share
    (``most``) and under it (``under``), each as ``[label, experiments, lift]``; themes that began;
    themes last seen the year before; words first written (``new_words``, ``[word, experiments]``).
    """
    dated = {u: y for u in themes if (y := years.get(u)) is not None}
    total = len(dated)
    if not total:
        return {}
    per_theme = Counter(themes[u] for u in dated)
    first_of: dict[int, int] = {}
    last_of: dict[int, int] = {}
    for unit, year in dated.items():
        theme = themes[unit]
        first_of[theme] = min(first_of.get(theme, year), year)
        last_of[theme] = max(last_of.get(theme, year), year)
    word_years: dict[str, list[int]] = defaultdict(list)
    for unit, year in dated.items():
        for word in words.get(unit, frozenset()):
            word_years[word].append(year)
    by_year: dict[int, list[str]] = defaultdict(list)
    for unit, year in sorted(dated.items()):
        by_year[year].append(unit)
    profile: dict[str, dict[str, Any]] = {}
    for year in sorted(by_year):
        units = by_year[year]
        if len(units) < settings.year_min_experiments:
            continue
        here = Counter(themes[u] for u in units)
        lifts = {
            theme: (here.get(theme, 0) / len(units)) / (per_theme[theme] / total)
            for theme in per_theme
            if first_of[theme] <= year <= last_of[theme]
        }
        enough = settings.year_min_experiments
        over = sorted(
            (t for t, lift in lifts.items() if lift > 1 and here[t] >= enough),
            key=lambda t: (-lifts[t], t),
        )[:STORY_THEMES_LISTED]
        under = sorted(
            (t for t, lift in lifts.items() if lift < 1),
            key=lambda t: (lifts[t], t),
        )[:STORY_THEMES_LISTED]
        new_words = sorted(
            (
                (word, len(ys))
                for word, ys in word_years.items()
                if min(ys) == year and len(ys) >= 2
            ),
            key=lambda item: (-item[1], display.get(item[0], item[0])),
        )[:NEW_WORDS_LISTED]
        profile[str(year)] = {
            "experiments": len(units),
            "most": [[labels.get(t, ""), here[t], round(lifts[t], 1)] for t in over],
            "under": [[labels.get(t, ""), here.get(t, 0), round(lifts[t], 1)] for t in under],
            "began": [labels.get(t, "") for t in sorted(first_of) if first_of[t] == year],
            "last_seen_year_before": [
                labels.get(t, "") for t in sorted(last_of) if last_of[t] == year - 1
            ],
            "new_words": [[display.get(w, w), n] for w, n in new_words],
        }
    return profile


def substitutes(
    themes: Mapping[str, int],
    years: Mapping[str, int | None],
    words: Mapping[str, frozenset[str]],
    display: Mapping[str, str],
    settings: ThemeSettings,
) -> list[dict[str, Any]]:
    """Words used in each other's place, earlier to later: a positive PMI profile alike, rarely
    together, the later one's median a year or more after, little overlap."""
    units = sorted(u for u in themes if years.get(u) is not None)
    count = len(units)
    if count < 2:
        return []
    frequency: Counter[str] = Counter()
    for unit in units:
        frequency.update(words.get(unit, frozenset()))
    vocabulary = sorted(
        word
        for word, n in frequency.items()
        if 3 <= n <= 0.5 * count and len(display.get(word, word)) >= SUBSTITUTE_MIN_LETTERS
    )
    if len(vocabulary) < 2:
        return []
    from scipy import sparse

    column = {w: i for i, w in enumerate(vocabulary)}
    size = len(vocabulary)
    cells = sorted(
        (row, column[word])
        for row, unit in enumerate(units)
        for word in words.get(unit, frozenset())
        if word in column
    )
    binary = sparse.csr_matrix(
        (np.ones(len(cells)), ([r for r, _ in cells], [c for _, c in cells])),
        shape=(count, size),
    )
    # Word by word, sparse: how often two words are written together, and each word's count.
    together = (binary.T @ binary).tocsr()
    df = together.diagonal().copy()
    together.setdiag(0.0)
    together.eliminate_zeros()
    grand = float(together.sum())
    if grand <= 0:
        return []
    rows = np.asarray(together.sum(axis=1)).ravel()
    pairs = together.tocoo()
    pmi = np.log(pairs.data * grand / (rows[pairs.row] * rows[pairs.col]))
    keep = pmi > 0
    positive = sparse.csr_matrix(
        (pmi[keep], (pairs.row[keep], pairs.col[keep])), shape=(size, size)
    )
    norms = np.sqrt(np.asarray(positive.multiply(positive).sum(axis=1)).ravel())
    norms[norms == 0] = 1.0
    profiles = (sparse.diags(1.0 / norms) @ positive).tocsr()
    # Each word's years: first, last and median of the experiments carrying it.
    year_of = np.array([years[u] for u in units], dtype=float)
    by_column = binary.tocsc()
    spans: list[np.ndarray] = [
        np.sort(year_of[by_column.indices[by_column.indptr[i] : by_column.indptr[i + 1]]])
        for i in range(size)
    ]
    first = np.array([span[0] for span in spans])
    last = np.array([span[-1] for span in spans])
    middle = np.array([float(np.median(span)) for span in spans])
    found: list[dict[str, Any]] = []
    # The profiles' similarity a band of rows at a time: memory a band, never the square.
    for start in range(0, size, SUBSTITUTE_BAND):
        band = (profiles[start : start + SUBSTITUTE_BAND] @ profiles.T).toarray()
        a_index, b_index = np.nonzero(band >= settings.substitute_min_similarity)
        similarity = band[a_index, b_index]
        a_index = a_index + start
        apart = a_index != b_index
        a_index, b_index, similarity = a_index[apart], b_index[apart], similarity[apart]
        if not len(a_index):
            continue
        shared = np.asarray(together[a_index, b_index]).ravel()
        jaccard = shared / (df[a_index] + df[b_index] - shared)
        chosen = (
            (jaccard <= settings.substitute_max_together)
            & (middle[b_index] - middle[a_index] >= 1)
            & (last[a_index] <= first[b_index] + 1)
            & (first[b_index] >= first[a_index])
        )
        for i, j, alike, both in zip(
            a_index[chosen].tolist(),
            b_index[chosen].tolist(),
            similarity[chosen].tolist(),
            shared[chosen].tolist(),
            strict=True,
        ):
            a, b = vocabulary[i], vocabulary[j]
            found.append(
                {
                    "earlier": display.get(a, a),
                    "later": display.get(b, b),
                    "similarity": round(float(alike), 2),
                    "together": int(both),
                    "earlier_years": f"{int(first[i])}-{int(last[i])}",
                    "later_years": f"{int(first[j])}-{int(last[j])}",
                    "earlier_n": int(df[i]),
                    "later_n": int(df[j]),
                }
            )
    found.sort(
        key=lambda row: (
            -row["similarity"] * math.log1p(min(row["earlier_n"], row["later_n"])),
            row["earlier"],
            row["later"],
        )
    )
    return found[:SUBSTITUTES_LISTED]


def who_else(
    units: Sequence[str],
    years: Mapping[str, int | None],
    words: Mapping[str, frozenset[str]],
    display: Mapping[str, str],
    senses: frozenset[str],
    people: Mapping[str, str | None],
    folders: Mapping[str, str],
    settings: ThemeSettings,
) -> dict[str, Any]:
    """Per person: experiments, first and last year, one example; and the words two or more
    people wrote, each by token or by sense (``sense``: matched by a Dictionary sense)."""
    by_person: dict[str, list[str]] = defaultdict(list)
    for unit in sorted(units):
        person = people.get(unit)
        if person:
            by_person[person].append(unit)
    roster: list[dict[str, Any]] = []
    for person in sorted(by_person):
        own = by_person[person]
        dated = sorted(y for u in own if (y := years.get(u)) is not None)
        roster.append(
            {
                "person": person,
                "experiments": len(own),
                "first": dated[0] if dated else None,
                "last": dated[-1] if dated else None,
                "example": folders.get(own[0], own[0]),
            }
        )
    per_word: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for person, own in by_person.items():
        for unit in own:
            for word in words.get(unit, frozenset()):
                per_word[word][person].append(unit)
    shared: list[dict[str, Any]] = []
    for word in sorted(per_word):
        counted = {
            person: units_of
            for person, units_of in per_word[word].items()
            if len(units_of) >= settings.person_min_experiments
        }
        if len(counted) < 2:
            continue
        shared.append(
            {
                "word": display.get(word, word),
                "sense": word in senses,
                "people": {p: len(counted[p]) for p in sorted(counted)},
                "years": {
                    p: _span([y for u in counted[p] if (y := years.get(u)) is not None])
                    for p in sorted(counted)
                },
            }
        )
    shared.sort(key=lambda row: (-len(row["people"]), -sum(row["people"].values()), row["word"]))
    return {"people": roster, "shared_words": shared[:SHARED_WORDS_LISTED]}


def undated(units: Sequence[str], years: Mapping[str, int | None]) -> list[str]:
    """The experiments with no year: no date in a name and no believed time of a file."""
    return sorted(u for u in units if years.get(u) is None)
