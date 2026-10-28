"""A project with many lines of work, as the engine's routes would serve it.

The Years view draws Overlap as a ring from 13 lines up and numbers the Wheel's narrow arcs, and
both need a project with dozens of lines. Growing one with the engine would test its clustering,
and these tests are not about that. So this module builds the routes' answers instead, in the
shapes the engine serves (``/api/v1/themes``, ``/completeness``, ``/themes/stability``,
``/themes/overlap``). It builds them from the answers of the test's own small invented project.
Every line, experiment and pair is a copy of one of that project's, with new words, sizes and
numbers. A browser test serves the answers through Playwright's route handler, so the page reads
exactly what it would read from the engine. Every name is invented from real words."""

from __future__ import annotations

import copy
import json
from typing import Any

#: Real words a line of work is made of (techniques, reagents, cell lines).
WORDS = (
    "HeLa",
    "HEK293",
    "DAPI",
    "GFP",
    "actin",
    "DMSO",
    "confocal",
    "western blot",
    "tubulin",
    "nocodazole",
    "taxol",
    "mitosis",
    "PCR",
    "plasmid",
    "lysate",
    "FRAP",
    "kymograph",
    "transfection",
    "H2B",
    "brightfield",
)
PEOPLE = ("Dana Whitfield", "Sam Okafor", "Priya Raman", "Lee Kowalski", "Marta Silva")


def _label(index: int) -> list[str]:
    """Three distinct words for line ``index``, different for every index up to a thousand."""
    first = index % len(WORDS)
    second = (index * 7 + 3) % len(WORDS)
    third = (index * 11 + 5) % len(WORDS)
    chosen = [first]
    for candidate in (second, third, (first + 1) % len(WORDS), (first + 2) % len(WORDS)):
        if candidate not in chosen:
            chosen.append(candidate)
        if len(chosen) == 3:
            break
    return [WORDS[position] for position in chosen]


def sizes(lines: int) -> list[int]:
    """Experiments per line: from 3 to 40, a fixed spread so a ring has big arcs and small ones."""
    out: list[int] = []
    seed = 3
    for _ in range(lines):
        seed = (seed * 7919 + 13) % 97
        out.append(3 + seed % 38)
    return out


def many_lines(
    base: dict[str, dict[str, Any]], lines: int = 39, pairs: int = 27
) -> dict[str, dict[str, Any]]:
    """The four answers for a project of ``lines`` lines and ``pairs`` pairs that share a word.

    ``base`` holds the test's own small invented project's answers by route: ``themes``,
    ``completeness``, ``stability`` and ``overlap``. Lines 4, 13, 22 and 29 have a stability under
    0.80 (the others over it), and the pairs link lines in a fixed order, no pair twice.
    """
    themes = copy.deepcopy(base["themes"])
    completeness = copy.deepcopy(base["completeness"])
    stability = copy.deepcopy(base["stability"])
    overlap = copy.deepcopy(base["overlap"])
    theme_template = themes["themes"][0]
    broad_template = themes["broad"][0]
    experiment_template = themes["experiments"][0]
    completeness_template = completeness["experiments"][0]
    sizes_of = sizes(lines)
    soft = {4, 13, 22, 29}
    broad_count = 8
    new_themes: list[dict[str, Any]] = []
    new_experiments: list[dict[str, Any]] = []
    new_completeness: list[dict[str, Any]] = []
    by_person: dict[str, dict[str, int]] = {}
    by_theme: dict[str, dict[str, int]] = {}
    number = 0
    for index in range(lines):
        words = _label(index)
        label = " · ".join(words)
        value = 0.62 + (index % 5) * 0.02 if index in soft else 0.86 + (index % 7) * 0.02
        item = copy.deepcopy(theme_template)
        item.update(
            theme=index,
            label=label,
            words=[
                *words,
                f"{WORDS[(index * 3) % len(WORDS)]}",
                f"{WORDS[(index * 5 + 1) % len(WORDS)]}",
            ],
            senses=[],
            broad_theme=index % broad_count,
            broad=f"broad {index % broad_count}",
            experiments=sizes_of[index],
            stability=round(value, 2),
            core_experiments=max(1, int(sizes_of[index] * value)),
            first=2016 + index % 6,
            peak=2017 + index % 6,
            last=2022 + index % 3,
            going=index % 4 != 0,
            target=f"theme:{'+'.join(sorted(w.lower() for w in words))}",
            person_label=None,
            person_note=None,
            labelled_by=None,
            labelled_at=None,
        )
        new_themes.append(item)
        for within in range(sizes_of[index]):
            number += 1
            year = 2016 + (index + within) % 9
            person = PEOPLE[(index + within) % len(PEOPLE)]
            identifier = f"entry_{number:032x}"
            month = (within % 12) + 1
            folder = (
                f"{person.split()[0].lower()}/{year}{month:02d}15 {words[0]} {words[1]} {number}"
            )
            shown = f"{words[0]} {words[1]} {number}"
            row = copy.deepcopy(experiment_template)
            row.update(
                id=identifier,
                folder=folder,
                name=f"{year}{(within % 12) + 1:02d}15 {shown}",
                shown_name=shown,
                date=f"{year}-{(within % 12) + 1:02d}-15",
                last_file_time=f"{year}-{(within % 12) + 1:02d}-15T10:00:00Z",
                year=year,
                theme=index,
                theme_label=label,
                broad_theme=index % broad_count,
                broad_label=f"broad {index % broad_count}",
                person=person,
                words=words,
                unplaced_reason=None,
            )
            new_experiments.append(row)
            raw = 3
            followed = 3 if (number % 5) else 1
            found = copy.deepcopy(completeness_template)
            found.update(
                id=identifier,
                folder=folder,
                name=row["name"],
                shown_name=shown,
                person=person,
                theme=str(index),
                theme_label=label,
                date=row["date"],
                raw_acquisitions=raw,
                raw_followed=followed,
                share_of_raw_followed_by_analysis=followed / raw,
                answer_target=f"analysed_elsewhere:{identifier}",
            )
            new_completeness.append(found)
            for sums, key in ((by_person, person), (by_theme, str(index))):
                held = sums.setdefault(key, {"experiments": 0, "raw": 0, "followed": 0})
                held["experiments"] += 1
                held["raw"] += raw
                held["followed"] += followed
    themes["themes"] = new_themes
    themes["broad"] = [
        {
            **copy.deepcopy(broad_template),
            "broad_theme": b,
            "label": f"broad {b}",
            "themes": [t["theme"] for t in new_themes if t["broad_theme"] == b],
            "experiments": sum(t["experiments"] for t in new_themes if t["broad_theme"] == b),
        }
        for b in range(broad_count)
    ]
    themes["experiments"] = new_experiments
    themes["experiments_not_shown"] = 0
    themes["unplaced_note"] = "0 experiments have no theme"
    summary = themes["summary"]
    summary.update(
        experiments=len(new_experiments),
        placed=len(new_experiments),
        unplaced=0,
        undated=0,
        themes=lines,
        broad_themes=broad_count,
    )
    completeness["experiments"] = new_completeness

    def group(key: str, held: dict[str, int]) -> dict[str, Any]:
        row = copy.deepcopy(completeness["people"][0])
        row.update(
            key=key,
            experiments=held["experiments"],
            experiments_with_raw=held["experiments"],
            raw_acquisitions=held["raw"],
            raw_followed=held["followed"],
            share_of_raw_followed_by_analysis=held["followed"] / held["raw"],
            analysis_files=held["experiments"],
            result_tables=held["experiments"],
            no_analysis_found=0,
            analysed_elsewhere=0,
        )
        return row

    completeness["people"] = [group(key, held) for key, held in sorted(by_person.items())]
    completeness["themes"] = [
        group(key, held) for key, held in sorted(by_theme.items(), key=lambda kv: int(kv[0]))
    ]
    stability["themes"] = [
        {
            "theme": t["theme"],
            "label": t["label"],
            "experiments": t["experiments"],
            "stability": t["stability"],
            "core_experiments": t["core_experiments"],
            "label_stability": None,
        }
        for t in new_themes
    ]
    stability["sweep"] = [{**row, "themes": lines} for row in stability["sweep"]]
    chosen: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    step = 0
    while len(chosen) < pairs:
        step += 1
        a = (step * 7) % lines
        b = (step * 11 + 5) % lines
        low, high = min(a, b), max(a, b)
        if low == high or (low, high) in seen:
            continue
        seen.add((low, high))
        common = min(len(new_themes[low]["words"]), len(new_themes[high]["words"]))
        shared = 1 + (step % 3)
        chosen.append(
            {
                "theme_a": low,
                "theme_b": high,
                "words": new_themes[low]["words"][:shared],
                "shared": shared,
                "of_smaller": common,
            }
        )
    chosen.sort(key=lambda pair: (pair["theme_a"], pair["theme_b"]))
    overlap["themes"] = lines
    overlap["pairs"] = chosen
    return {
        "themes": themes,
        "completeness": completeness,
        "stability": stability,
        "overlap": overlap,
    }


def serve_many_lines(
    page: Any, base: dict[str, dict[str, Any]], lines: int = 39, pairs: int = 27
) -> dict[str, dict[str, Any]]:
    """Make ``page`` read ``lines`` lines of work from the four routes; returns what is served."""
    served = many_lines(base, lines, pairs)
    paths = {
        "/api/v1/themes": "themes",
        "/api/v1/completeness": "completeness",
        "/api/v1/themes/stability": "stability",
        "/api/v1/themes/overlap": "overlap",
    }

    def handle(route: Any) -> None:
        path = route.request.url.split("?")[0].split("//", 1)[1].split("/", 1)[1]
        key = paths.get("/" + path)
        if key is None:
            route.continue_()
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(served[key]))

    page.route("**/api/v1/**", handle)
    return served
