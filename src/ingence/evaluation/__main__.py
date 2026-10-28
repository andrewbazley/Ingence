"""``python -m ingence.evaluation``: generate a pool, score a pool, or score a project already run.

This is not an ``ingence`` verb. Scoring the engine is a person's tool beside the verbs.
Six commands:

- ``generate DEST --seed N --size N``: write ``DEST/pool`` and ``DEST/answer_key.json``;
- ``run DEST --seed N --size N [--revision FILE ...]``: generate, run the engine over the pool
  in ``DEST/work``, and write ``DEST/report.json`` and ``DEST/summary.txt`` (the shipped rules,
  and a second column with the revision files named, where any are);
- ``project --key KEY --project P [--root NAME] [--within FOLDER]``: score a project's current run
  against a person's key (the private half), writing the report and the summary where ``--out``
  says. A key saved inside one of the project's roots is refused, and so is a report folder inside
  a root or inside the project (its exports folder apart): nothing is written there. A project of
  several roots needs ``--root``, since two roots can each hold one relative path;
- ``sample-lab DEST --tier S|M|L --seed N [--vocabulary SHARE] [--run]``: write a synthetic sample
  collection's drive in ``DEST/pool``, and beside it its answer key, a manifest and a datasheet. The
  same tier and seed write the same files, byte for byte. ``--run`` goes on as ``run`` does for the
  small generator: the engine runs over the pool in ``DEST/work`` and is scored, writing
  ``DEST/report.json`` and ``DEST/summary.txt``; the summary holds no time and no path, so the
  same tier and seed on the same engine write the same bytes. Or score a run over ``DEST/pool``
  later with ``project``, giving ``DEST/answer_key.json`` as the key. ``--vocabulary`` gives each
  subject-matter word in an experiment folder that chance of being written the way laboratories
  write it (slips, initials, codes...) and adds the words family to the key; without it nothing
  changes.
  ``--vocabulary-originals`` (with the tier off) adds to the key a words family of the original
  words alone and changes nothing else;
- ``questions DEST --tier S|M|L --seed N [--count 30] [--project P]``: write a sample collection
  and ask its run (made in ``DEST/work``, or ``--project``, a project already run over the same
  tier and seed's pool) questions in a scientist's words drawn from the collection's own record
  of what it invented, scoring the experiments each returns per tier against the experiments
  carrying the conditions asked about. The command writes ``report.json``, ``summary.txt`` and
  ``questions.json`` beside the pool;
- ``taught DEST --tier S|M|L --seed N --vocabulary SHARE --k K ... [--with-tier-off]``: write a
  sample collection with the vocabulary tier (a share above 0), then measure what teaching the
  engine its first ``K`` rows of the Words queue does (once for each ``--k``): the scorer's counts,
  the reading figure, the themes' agreement with the key's lines of work and their stability, the
  cores found and the experiments found by contents, in ``DEST/report.json`` and
  ``DEST/summary.txt``. ``--with-tier-off`` adds columns for the same tier and seed with no word
  rewritten and a key of the original words alone (written in ``DEST/tier_off``).
  ``--words-as`` measures under ``groups.words_as`` ``terms``, ``spellings`` or both (the default),
  ``--pairs`` says how a spelling of two words is answered (``own``, ``first`` or ``split``), and
  ``--queue`` picks the Dashboard's Words queue (the default) or the plain list of unbound words.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ingence.core.destinations import (
    DestinationRefusedError,
    ReadOnlyFileError,
    check_output_folder,
    declared_roots,
    typed_path,
    write_replacing,
)
from ingence.core.project import Project
from ingence.evaluation.generate import generate
from ingence.evaluation.sample_lab import TIERS, describe, generate_sample_lab
from ingence.evaluation.score import (
    PAIR_ANSWERS,
    WORDS_AS_CHOICES,
    KeyPlacementError,
    dumps,
    evaluate,
    measure_teaching_arms,
    score_project,
    summary,
    taught_summary,
    words_queue_rows,
    words_rows,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ingence.evaluation", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    made = commands.add_parser("generate", help="write a pool and its answer key")
    ran = commands.add_parser("run", help="generate a pool and score the engine over it")
    for command in (made, ran):
        command.add_argument("destination", type=Path)
        command.add_argument("--seed", type=int, default=0)
        command.add_argument("--size", type=int, default=1)
    ran.add_argument(
        "--revision",
        type=Path,
        action="append",
        default=[],
        help="a project revision file the second column adds to the shipped rules",
    )
    ran.add_argument(
        "--approve-cores",
        action="store_true",
        help=(
            "also run the last column again with the key's cores approved as answer_key in its own "
            "scratch project, so the questions beneath them show (off by default)"
        ),
    )
    ran.add_argument(
        "--isolate",
        action="store_true",
        help="read files in supervised child processes, as a dive does by default",
    )
    sample = commands.add_parser("sample-lab", help="write a sample collection and its key")
    sample.add_argument("destination", type=Path, help="a folder that holds no collection yet")
    sample.add_argument(
        "--tier",
        choices=sorted(TIERS),
        default="S",
        help="S (about 500 files), M (about 10 thousand) or L (about 100 thousand)",
    )
    sample.add_argument(
        "--seed", type=int, default=0, help="the same tier and seed write the same files"
    )
    sample.add_argument(
        "--vocabulary",
        type=float,
        default=0.0,
        help="the chance that each subject-matter word of an experiment folder is written the way "
        "laboratories write it (off at 0)",
    )
    sample.add_argument(
        "--vocabulary-originals",
        action="store_true",
        help="with the tier off, add a words family of the original words alone to the key",
    )
    sample.add_argument(
        "--run",
        action="store_true",
        help="then run the engine over the pool in DEST/work and score it, writing "
        "DEST/report.json and DEST/summary.txt",
    )
    sample.add_argument(
        "--isolate",
        action="store_true",
        help="with --run, read files in supervised child processes, as a dive does by default",
    )
    taught = commands.add_parser(
        "taught", help="write a sample collection with the vocabulary tier and measure teaching"
    )
    taught.add_argument("destination", type=Path, help="a folder that holds no collection yet")
    taught.add_argument("--tier", choices=sorted(TIERS), default="S")
    taught.add_argument("--seed", type=int, default=0)
    taught.add_argument(
        "--vocabulary",
        type=float,
        default=0.5,
        help="the chance that each subject-matter word of an experiment folder is rewritten; "
        "more than 0, at most 1 (default 0.5)",
    )
    taught.add_argument(
        "--with-tier-off",
        action="store_true",
        help="also measure the same tier and seed with no word rewritten (a words family of the "
        "original words alone), written in DEST/tier_off, as columns headed 'tier off'",
    )
    taught.add_argument(
        "--k",
        type=int,
        action="append",
        default=None,
        help="how many rows of the queue the oracle looks at; repeat for several (default 0, 10)",
    )
    taught.add_argument(
        "--k-counts-answers",
        action="store_true",
        help="let k count the rows answered, not the rows looked at",
    )
    taught.add_argument(
        "--pairs",
        choices=PAIR_ANSWERS,
        default="own",
        help="what a spelling of a name of two words is answered with: a sense of its own "
        "(own), the sense of its first word (first), or 'separate words' where the Words page asks "
        "it of a spelling a capital divides (split)",
    )
    taught.add_argument(
        "--words-as",
        choices=[*WORDS_AS_CHOICES, "both"],
        default="both",
        help="which words the groups are made of (groups.words_as): the Dictionary's terms, the "
        "spellings the names write, or both settings side by side (default both)",
    )
    taught.add_argument(
        "--queue",
        choices=["route", "unbound"],
        default="route",
        help="the Words queue the oracle answers: the Dashboard's own (route, with the spellings "
        "it offers beside each word) or the plain list of unbound words (unbound)",
    )
    taught.add_argument("--isolate", action="store_true", help="read files in child processes")
    asked = commands.add_parser(
        "questions",
        help="write a sample collection, ask it questions in a scientist's words, score the "
        "experiments each returns",
    )
    asked.add_argument("destination", type=Path, help="a folder that holds no collection yet")
    asked.add_argument("--tier", choices=sorted(TIERS), default="S")
    asked.add_argument("--seed", type=int, default=0)
    asked.add_argument(
        "--count", type=int, default=30, help="how many questions are asked (default 30)"
    )
    asked.add_argument(
        "--project",
        type=Path,
        default=None,
        help="a project already run over the same tier and seed's pool, asked instead of "
        "running the engine in DEST/work",
    )
    asked.add_argument("--isolate", action="store_true", help="read files in child processes")
    scored = commands.add_parser("project", help="score a project's current run against a key")
    scored.add_argument("--key", type=Path, required=True)
    scored.add_argument("--project", type=Path, required=True)
    scored.add_argument("--root", default=None, help="the root the key describes: label or name")
    scored.add_argument("--within", default="", help="the folder the key's paths are relative to")
    scored.add_argument("--out", type=Path, required=True, help="a folder for the report")
    return parser


def _refuse_out_inside_the_project_or_a_root(out: Path, project: Path) -> None:
    """Refuse a report folder inside a folder the project reads or inside the project itself
    (its exports folder apart): nothing the engine writes lands in the data it reads."""
    opened = Project.open(project)
    check_output_folder(out, project=opened.path, roots=declared_roots(opened))


def _write_report(out: Path, report: dict[str, Any], text: str) -> int:
    """Write ``report.json`` and ``summary.txt`` into ``out`` and print the summary."""
    for name, data in (
        ("report.json", dumps(report).encode("utf-8")),
        ("summary.txt", text.encode("utf-8")),
    ):
        try:
            write_replacing(out / name, data)
        except OSError as failed:
            why = "read-only" if isinstance(failed, ReadOnlyFileError) else failed.strerror
            sys.stderr.write(f"could not write {out / name} ({why})\n")
            return 1
    sys.stdout.write(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "generate":
        written = generate(arguments.destination, seed=arguments.seed, size=arguments.size)
        print(f"{len(written.files)} files in {written.pool}; the key is {written.key_path}")
        return 0
    if arguments.command == "sample-lab":
        try:
            collection = generate_sample_lab(
                arguments.destination,
                tier=arguments.tier,
                seed=arguments.seed,
                vocabulary=arguments.vocabulary,
                vocabulary_originals=arguments.vocabulary_originals,
            )
        except (FileExistsError, ValueError) as refused:
            sys.stderr.write(f"refused: {refused}\n")
            return 2
        print(f"{describe(collection.manifest)}; the pool is {collection.pool}")
        print(f"the key is {collection.key_path}; the manifest is {collection.manifest_path}")
        if not arguments.run:
            return 0
        report = evaluate(
            collection.key,
            collection.pool,
            arguments.destination / "work",
            {"shipped": ()},
            isolate=arguments.isolate,
        )
        # Without the seconds, so the same tier and seed write the same bytes.
        return _write_report(arguments.destination, report, summary(report, times=False))
    if arguments.command == "taught":
        if not 0.0 < arguments.vocabulary <= 1.0:
            sys.stderr.write(
                "refused: the measurement teaches the words of a collection with the vocabulary "
                f"tier, so --vocabulary is more than 0 and at most 1, not {arguments.vocabulary}\n"
            )
            return 2
        try:
            collection = generate_sample_lab(
                arguments.destination,
                tier=arguments.tier,
                seed=arguments.seed,
                vocabulary=arguments.vocabulary,
            )
            arms = {"": (collection.key, collection.pool)}
            if arguments.with_tier_off:
                off = generate_sample_lab(
                    arguments.destination / "tier_off",
                    tier=arguments.tier,
                    seed=arguments.seed,
                    vocabulary_originals=True,
                )
                arms["tier off "] = (off.key, off.pool)
        except FileExistsError as refused:
            sys.stderr.write(f"refused: {refused}\n")
            return 2
        report = measure_teaching_arms(
            arms,
            arguments.destination / "work",
            arguments.k if arguments.k is not None else [0, 10],
            count_skipped=not arguments.k_counts_answers,
            pairs=arguments.pairs,
            words_as=WORDS_AS_CHOICES if arguments.words_as == "both" else (arguments.words_as,),
            queue=words_queue_rows if arguments.queue == "route" else words_rows,
            isolate=arguments.isolate,
        )
        out = arguments.destination
        text = taught_summary(report) + "\n" + summary(report)
        for name, data in (
            ("report.json", dumps(report).encode("utf-8")),
            ("summary.txt", text.encode("utf-8")),
        ):
            write_replacing(out / name, data)
        sys.stdout.write(text)
        return 0
    if arguments.command == "questions":
        from ingence.evaluation.questions import measure_questions, questions_summary

        try:
            collection = generate_sample_lab(
                arguments.destination, tier=arguments.tier, seed=arguments.seed
            )
        except (FileExistsError, ValueError) as refused:
            sys.stderr.write(f"refused: {refused}\n")
            return 2
        if arguments.project is not None:
            project_path = arguments.project.resolve()
        else:
            from ingence.evaluation.score import run_columns

            project_path = run_columns(
                collection.pool,
                arguments.destination / "work",
                {"shipped": ()},
                isolate=arguments.isolate,
            )["shipped"].project
        family, report = measure_questions(
            collection.unscored_path, project_path, seed=arguments.seed, count=arguments.count
        )
        out = arguments.destination
        text = questions_summary(report)
        for name, data in (
            ("report.json", dumps(report).encode("utf-8")),
            ("summary.txt", text.encode("utf-8")),
            ("questions.json", (json.dumps(family.to_json(), indent=1) + "\n").encode("utf-8")),
        ):
            write_replacing(out / name, data)
        sys.stdout.write(text)
        return 0
    if arguments.command == "run":
        written = generate(arguments.destination, seed=arguments.seed, size=arguments.size)
        columns = {"shipped": ()}
        if arguments.revision:
            columns["with revisions"] = tuple(path.resolve() for path in arguments.revision)
        report = evaluate(
            written.key,
            written.pool,
            arguments.destination / "work",
            columns,
            isolate=arguments.isolate,
            approve_cores_in=(list(columns)[-1],) if arguments.approve_cores else (),
        )
        out = arguments.destination
    else:
        out = typed_path(arguments.out)
        try:
            _refuse_out_inside_the_project_or_a_root(out, arguments.project.resolve())
            report = score_project(
                arguments.key,
                arguments.project.resolve(),
                within=arguments.within,
                root=arguments.root,
            )
        except (KeyPlacementError, DestinationRefusedError) as refused:
            sys.stderr.write(f"refused: {refused}\n")
            return 2
        out.mkdir(parents=True, exist_ok=True)
    return _write_report(out, report, summary(report))


if __name__ == "__main__":
    raise SystemExit(main())
