"""The Graph workspace's fixture project: invented names, built by the engine.

The engine builds the project. The fixture then completes it through the engine's own write path,
as the graph page's fixture does (``graph_project.py``). The engine's steps are a census, a dive
that reads the text and the instruments' headers, and an inference under revision 4. Then a
published run holds records that inference does not produce from this pool: derivations that
fork and join, a mention that is never a derivation, and a withdrawn edge. The Record holds a
person's confirmation of one experiment. Every name is invented from the laboratory pool's stems
(``laboratory_pool.STEMS``), which bind to nothing in the shipped Dictionary. Every date is
invented.

The pool, for ``experiments`` experiments (``scale`` multiplies the scratch and the acquisitions)::

    2024-03-05_kelt_confocal_wt_gfp_assay/
                                      a dated experiment folder whose name says a technique, a
                                      condition and a reagent (the arrangement's themes); the
                                      third experiment writes its technique ``lsm``, a spelling
                                      of the same sense
      raw/kelt_01.nd2 ...             instrument files with clocks
      processed/kelt_01_mask.tif ...  images; kelt_01_quant.csv tables
      analysis/kelt_summary.csv       joins the quantities of two acquisitions
               kelt_figure.pdf        made from the summary
               analyse_kelt.py        a script naming the raw files it read
    ...
    notes/  protocol.md, brisa_notes.txt
    shared/ bundle.zip                an archive
    scratch/ many small files         a dense folder, gathered by the Wheel; one removed
                                      before a second census (a tombstone)
    deep/a/b/c/d/e/f/leaf.txt         deeper than the Wheel's rings
    links/far_scan.tif                an archive link to a file outside the root (where the
                                      platform can make one)

Published lines (``published_run``): in the first experiment, raw 1 -> mask 1 -> quant 1,
raw 2 -> mask 2 -> quant 2, raw 1 -> quant 2 (a fork), quant 1 + quant 2 -> summary (a join),
summary -> figure. The script mentions raw 1 and raw 2 (``references``, never ``derives``). One
derivation is withdrawn. One mention names a file that is not an entry (an open end). The first
experiment's folder is confirmed as a unit by ``a. person``.
"""

from __future__ import annotations

import datetime as dt
import io
import os
import shutil
import zipfile
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NamedTuple

from fixtures.instrument_files import julian_day, nd2_file, tiff_file
from fixtures.laboratory_pool import STEMS

if TYPE_CHECKING:
    from pathlib import Path

CENSUS_RUN = "run_20260101T000001Z_aaaaaaaaaaaa"
#: A second census after one file is removed, so the Index holds a tombstone.
SECOND_CENSUS_RUN = "run_20260101T000001Z_abababababab"
#: The file removed before the second census: never drawn, never counted.
GONE = "scratch/zz_gone.txt"
DIVE_RUN = "run_20260101T000002Z_bbbbbbbbbbbb"
INFER_RUN = "run_20260101T000003Z_cccccccccccc"
PUBLISHED_RUN = "run_20260101T000004Z_dddddddddddd"
#: A run a test publishes after the fixture's on a copy, holding what it adds (``with_published``).
LATER_RUN = "run_20260101T000005Z_eeeeeeeeeeee"
#: The actor of the fixture's one confirmation: an invented person.
PERSON = "a. person"
#: What the fixture's unresolved published mention names: no entry carries it.
UNRESOLVED_TARGET = "unresolved:lost_scan.csv"


@dataclass(frozen=True)
class ExplorerProject:
    project: Path
    pool: Path
    #: Entry identifiers by path under the root.
    ids: dict[str, str]
    experiments: tuple[str, ...]
    #: The published derivations, as (source path, target path).
    derivations: tuple[tuple[str, str], ...]
    mentions: tuple[tuple[str, str], ...]
    withdrawn: tuple[str, str]
    confirmed_folder: str
    link_made: bool
    #: A published mention whose target is not an entry: (the mentioning file, what it names).
    unresolved: tuple[str, str] = ("", "")
    edge_ids: dict[tuple[str, str], str] = field(default_factory=dict)


def _day(start: dt.date, offset: int, hour: int = 10) -> dt.datetime:
    moment = dt.datetime.combine(start + dt.timedelta(days=offset), dt.time(hour, 0), tzinfo=dt.UTC)
    return moment


def _touch(path: Path, moment: dt.datetime) -> None:
    stamp = moment.timestamp()
    os.utime(path, (stamp, stamp))


def _pdf() -> bytes:
    return b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"


#: What each experiment's folder name says, in turn: a technique, a condition, a reagent. They are
#: shipped Dictionary words, so the arrangement has themes to read. ``lsm`` is another spelling of
#: ``confocal``. Arranging by spelling tells the two apart, and arranging by sense does not.
THEMES = (("confocal", "wt", "gfp"), ("tirf", "ko", ""), ("lsm", "wt", "dapi"))


def write_explorer_pool(
    root: Path, *, experiments: int = 3, scale: int = 1
) -> tuple[list[str], bool]:
    """Write the pool under ``root``.

    The function returns the experiment folders and whether a link was made.
    """
    root.mkdir(parents=True, exist_ok=True)
    folders: list[str] = []
    base = dt.date(2024, 3, 5)
    for number in range(experiments):
        stem = STEMS[number % len(STEMS)]
        start = base + dt.timedelta(days=37 * number)
        words = "_".join(word for word in THEMES[number % len(THEMES)] if word)
        folder = f"{start.isoformat()}_{stem}_{words}_assay"
        folders.append(folder)
        raw, processed, analysis = (
            root / folder / part for part in ("raw", "processed", "analysis")
        )
        for part in (raw, processed, analysis):
            part.mkdir(parents=True)
        acquisitions = 4 * scale
        for index in range(1, acquisitions + 1):
            taken = _day(start, 0, 9 + index % 8)
            name = f"{stem}_{index:02d}"
            (raw / f"{name}.nd2").write_bytes(nd2_file(julian_day=julian_day(taken), frames=2))
            _touch(raw / f"{name}.nd2", taken + dt.timedelta(minutes=3))
            (processed / f"{name}_mask.tif").write_bytes(tiff_file(pages=2))
            _touch(processed / f"{name}_mask.tif", _day(start, 2 + index % 3))
            (processed / f"{name}_quant.csv").write_text(f"cell,area\n1,{index}\n2,{index + 1}\n")
            _touch(processed / f"{name}_quant.csv", _day(start, 3 + index % 3))
        (analysis / f"{stem}_summary.csv").write_text("acquisition,mean\n1,2.5\n2,3.0\n")
        _touch(analysis / f"{stem}_summary.csv", _day(start, 9))
        (analysis / f"{stem}_figure.pdf").write_bytes(_pdf())
        _touch(analysis / f"{stem}_figure.pdf", _day(start, 12))
        (analysis / f"analyse_{stem}.py").write_text(
            f'SOURCES = ["../raw/{stem}_01.nd2", "../raw/{stem}_02.nd2"]\n'
            f'OUTPUT = "{stem}_summary.csv"\n'
        )
        _touch(analysis / f"analyse_{stem}.py", _day(start, 8))
    notes = root / "notes"
    notes.mkdir()
    (notes / "protocol.md").write_text("# Protocol\n\nPlain words, no paths.\n")
    (notes / f"{STEMS[0]}_notes.txt").write_text("plain words and nothing else\n")
    shared = root / "shared"
    shared.mkdir()
    held = io.BytesIO()
    with zipfile.ZipFile(held, "w") as archive:
        archive.writestr("inside/one.txt", "one\n")
        archive.writestr("inside/two.csv", "a,b\n1,2\n")
    (shared / "bundle.zip").write_bytes(held.getvalue())
    scratch = root / "scratch"
    scratch.mkdir()
    for index in range(30 * scale):
        (scratch / f"{STEMS[index % len(STEMS)]}_{index:03d}.txt").write_text(f"{index}\n")
    (root / GONE).write_text("removed before the second census\n")
    deep = root / "deep" / "a" / "b" / "c" / "d" / "e" / "f"
    deep.mkdir(parents=True)
    (deep / "leaf.txt").write_text("deep\n")
    # The rest of the pool is dated too, before the experiments. The notes, the archive, the
    # scratch files and the deep leaf keep modification times a year earlier. Birth times cannot
    # be set, so they read as the day the fixture was built. That gives two sources and two
    # answers, as on a real drive.
    for number, path in enumerate(sorted(p for p in root.rglob("*") if p.is_file())):
        if "_assay" not in path.parts[len(root.parts)]:
            _touch(path, _day(base - dt.timedelta(days=365), number % 200, 9 + number % 9))
    links = root / "links"
    links.mkdir()
    outside = root.parent / "elsewhere"
    outside.mkdir(exist_ok=True)
    (outside / "far_scan.tif").write_bytes(tiff_file())
    try:
        os.symlink(outside / "far_scan.tif", links / "far_scan.tif")
        link_made = True
    except (OSError, NotImplementedError):
        link_made = False
    return folders, link_made


type Pair = tuple[str, str]


def _published(
    project: Path, ids: dict[str, str], first: str, stem: str
) -> tuple[list[object], dict[Pair, str], tuple[Pair, ...], tuple[Pair, ...], Pair]:
    from ingence.core.ids import Scope, ScopeKind
    from ingence.core.relations import Primitive, ReferenceResolution
    from ingence.core.schema import Assertion, DecisionStatus
    from ingence.core.tiers import InferenceTier
    from ingence.storage.catalog import open_catalog

    with open_catalog(project) as catalog:
        rule = catalog.query(
            "SELECT rule_id, version FROM rules ORDER BY rule_id, version LIMIT 1"
        ).fetchone()
        root_id = catalog.query("SELECT root_id FROM roots LIMIT 1").fetchone()
    assert rule is not None and root_id is not None
    scope = Scope(ScopeKind.ROOT, str(root_id[0]))
    raw = lambda n: f"{first}/raw/{stem}_{n:02d}.nd2"  # noqa: E731
    mask = lambda n: f"{first}/processed/{stem}_{n:02d}_mask.tif"  # noqa: E731
    quant = lambda n: f"{first}/processed/{stem}_{n:02d}_quant.csv"  # noqa: E731
    summary = f"{first}/analysis/{stem}_summary.csv"
    figure = f"{first}/analysis/{stem}_figure.pdf"
    script = f"{first}/analysis/analyse_{stem}.py"
    derivations = (
        (raw(1), mask(1)),
        (mask(1), quant(1)),
        (raw(2), mask(2)),
        (mask(2), quant(2)),
        (raw(1), quant(2)),
        (quant(1), summary),
        (quant(2), summary),
        (summary, figure),
    )
    mentions = ((script, raw(1)), (script, raw(2)))
    withdrawn = (figure, raw(3))
    records: list[object] = []
    edge_ids: dict[tuple[str, str], str] = {}
    for number, (source, target) in enumerate(derivations):
        edge = f"edge_explorer_derives_{number:02d}"
        edge_ids[(source, target)] = edge
        records.append(
            Assertion(
                edge_id=edge,
                run_id=PUBLISHED_RUN,
                source_id=ids[source],
                target_id=ids[target],
                primitive=Primitive.DERIVES,
                rule_id=str(rule[0]),
                rule_version=str(rule[1]),
                scope=scope,
                inference_tier=InferenceTier.DEFEASIBLE,
                decision_status=DecisionStatus.ASSERTED,
            )
        )
    for number, (source, target) in enumerate(mentions):
        edge = f"edge_explorer_mentions_{number:02d}"
        edge_ids[(source, target)] = edge
        records.append(
            Assertion(
                edge_id=edge,
                run_id=PUBLISHED_RUN,
                source_id=ids[source],
                target_id=ids[target],
                primitive=Primitive.REFERENCES,
                rule_id=str(rule[0]),
                rule_version=str(rule[1]),
                scope=scope,
                reference_resolution=ReferenceResolution.EXACT,
            )
        )
    records.append(
        Assertion(
            edge_id="edge_explorer_unresolved",
            run_id=PUBLISHED_RUN,
            source_id=ids[script],
            target_id=UNRESOLVED_TARGET,
            primitive=Primitive.REFERENCES,
            rule_id=str(rule[0]),
            rule_version=str(rule[1]),
            scope=scope,
            reference_resolution=ReferenceResolution.UNRESOLVED,
        )
    )
    records.append(
        Assertion(
            edge_id="edge_explorer_withdrawn",
            run_id=PUBLISHED_RUN,
            source_id=ids[withdrawn[0]],
            target_id=ids[withdrawn[1]],
            primitive=Primitive.DERIVES,
            rule_id=str(rule[0]),
            rule_version=str(rule[1]),
            scope=scope,
            decision_status=DecisionStatus.WITHDRAWN,
        )
    )
    return records, edge_ids, derivations, mentions, withdrawn


def build_explorer_project(base: Path, *, experiments: int = 3, scale: int = 1) -> ExplorerProject:
    """Build the fixture under ``base``: engine runs, then the published additions."""
    from fixtures.graph_project import publish_with
    from fixtures.revision_4 import write_revision
    from ingence.core.project import Project
    from ingence.core.schema import RecordEventKind
    from ingence.core.settings import ExtractionSettings
    from ingence.infer.run import run_inference
    from ingence.record.log import append_event
    from ingence.scan.census import run_census
    from ingence.scan.deepen import run_deepen
    from ingence.storage.catalog import open_catalog

    pool = base / "pool"
    if pool.exists():
        shutil.rmtree(pool)
    folders, link_made = write_explorer_pool(pool, experiments=experiments, scale=scale)
    project = Project.create(base / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    project = project.save_settings(
        project.settings.model_copy(update={"extraction": ExtractionSettings(enabled=True)})
    )
    run_census(project, run_id=CENSUS_RUN, started_at="t")
    (pool / GONE).unlink()
    run_census(project, run_id=SECOND_CENSUS_RUN, started_at="t")
    run_deepen(project, run_id=DIVE_RUN, started_at="t")
    run_inference(project, run_id=INFER_RUN, started_at="t")
    with open_catalog(project.path) as catalog:
        ids = {
            str(path): str(entry_id)
            for entry_id, path in catalog.query(
                "SELECT entry_id, relative_path FROM entries WHERE NOT is_deleted ORDER BY entry_id"
            ).fetchall()
        }
    first, stem = folders[0], STEMS[0]
    records, edge_ids, derivations, mentions, withdrawn = _published(project.path, ids, first, stem)
    publish_with(project.path, PUBLISHED_RUN, records)
    with open_catalog(project.path) as catalog:
        claim = catalog.query(
            "SELECT claim_id FROM claims WHERE predicate LIKE 'classifies.experiment_folder%' "
            "AND subject = ? ORDER BY claim_id LIMIT 1",
            [ids[first]],
        ).fetchone()
    if claim is not None:
        append_event(
            project.path,
            kind=RecordEventKind.CONFIRM,
            actor=PERSON,
            occurred_at="2026-01-02T10:00:00Z",
            target=f"claim:{claim[0]}",
            against_run_id=PUBLISHED_RUN,
            payload={"source": "explorer fixture"},
        )
    return ExplorerProject(
        project=project.path,
        pool=pool,
        ids=ids,
        experiments=tuple(folders),
        derivations=derivations,
        mentions=mentions,
        withdrawn=withdrawn,
        confirmed_folder=first if claim is not None else "",
        link_made=link_made,
        edge_ids=edge_ids,
        unresolved=(mentions[0][0], UNRESOLVED_TARGET),
    )


class AddedClaim(NamedTuple):
    """One claim a test adds on a copy: a copy of an entry's own claim of a predicate."""

    #: The entry whose claim is copied, by path.
    path: str
    predicate: str
    #: A value in place of the claim's own, or None to keep it.
    value: str | None = None
    #: Published as conflicting (a defeater fired).
    conflicting: bool = False
    #: The entry the copy is about, by path, where it is not the same entry.
    onto: str | None = None
    #: The rule the copy is concluded by (its predicate ``classifies.<rule>``), where not the
    #: claim's own: a person's rule named for anything at all.
    rule: str | None = None
    #: Make the copy a group claim of this rule instead (its predicate ``groups.<rule>``, its rule
    #: the name given), whose value is the group's key: how a test forms a name family.
    group_rule: str | None = None


def with_published(
    explorer: ExplorerProject,
    base: Path,
    *,
    lines: tuple[tuple[str, str, str], ...] = (),
    claims: tuple[AddedClaim | tuple[Any, ...], ...] = (),
) -> Path:
    """A copy of the fixture under ``base`` whose later run (``LATER_RUN``) publishes more.

    ``lines`` are (source, target, primitive), each end a path under the root or, where it is
    none, an entry identifier as given (a container's member), asserted by the fixture's first
    rule;
    ``claims`` copy an entry's own claim of a predicate (``AddedClaim``), with another value where
    one is given, published as conflicting (a defeater fired) where asked, and about another
    entry where one is named. The copy holds no projection, so the
    first request builds one from the later run. Invented names only, like the fixture.
    """
    from fixtures.graph_project import publish_with
    from ingence.core.ids import Scope, ScopeKind
    from ingence.core.relations import Primitive, ReferenceResolution
    from ingence.core.schema import Assertion, Claim, DecisionStatus, ResolutionStatus
    from ingence.core.tiers import InferenceTier
    from ingence.storage.catalog import open_catalog
    from ingence.storage.index_store import record_from_row

    copy = base / "project"
    shutil.copytree(explorer.project, copy)
    shutil.rmtree(copy / "projections", ignore_errors=True)
    records: list[object] = []
    with open_catalog(copy) as catalog:
        rule = catalog.query(
            "SELECT rule_id, version FROM rules ORDER BY rule_id, version LIMIT 1"
        ).fetchone()
        root_id = catalog.query("SELECT root_id FROM roots LIMIT 1").fetchone()
        assert rule is not None and root_id is not None
        for number, given in enumerate(claims):
            path, predicate, value, conflicting, onto, claim_rule, group_rule = AddedClaim(*given)
            rows = (
                catalog.query(
                    "SELECT * FROM claims WHERE subject = ? AND predicate = ? "
                    "ORDER BY claim_id LIMIT 1",
                    [explorer.ids[path], predicate],
                )
                .to_arrow_table()
                .to_pylist()
            )
            assert rows, (path, predicate)
            held = record_from_row(rows[0], Claim)
            status = (
                ResolutionStatus.EXAMINED_CONFLICTING if conflicting else held.resolution_status
            )
            records.append(
                held.model_copy(
                    update={
                        "claim_id": f"claim_explorer_added_{number:02d}",
                        "subject": held.subject if onto is None else explorer.ids[onto],
                        "predicate": (
                            f"groups.{group_rule}"
                            if group_rule is not None
                            else held.predicate
                            if claim_rule is None
                            else f"classifies.{claim_rule}"
                        ),
                        "rule_id": group_rule
                        or (held.rule_id if claim_rule is None else claim_rule),
                        "run_id": LATER_RUN,
                        "object_reference": held.object_reference if value is None else value,
                        "resolution_status": status,
                    }
                )
            )
    for number, (source, target, primitive) in enumerate(lines):
        # As the fixture's own claims: a derivation is defeasible and a mention resolves exactly.
        mention = primitive == "references"
        kind: dict[str, object] = (
            {"primitive": Primitive.REFERENCES, "reference_resolution": ReferenceResolution.EXACT}
            if mention
            else {"primitive": Primitive.DERIVES, "inference_tier": InferenceTier.DEFEASIBLE}
        )
        records.append(
            Assertion(
                edge_id=f"edge_explorer_added_{number:02d}",
                run_id=LATER_RUN,
                source_id=explorer.ids.get(source, source),
                target_id=explorer.ids.get(target, target),
                rule_id=str(rule[0]),
                rule_version=str(rule[1]),
                scope=Scope(ScopeKind.ROOT, str(root_id[0])),
                decision_status=DecisionStatus.ASSERTED,
                **kind,  # type: ignore[arg-type]
            )
        )
    publish_with(copy, LATER_RUN, records)
    return copy
