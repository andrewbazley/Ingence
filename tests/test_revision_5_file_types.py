"""Two rules: a format read from the bytes, and the program that wrote a file read from its tags.

The predicates are tested on constructed observations, then the two rules run over a small
pool whose files lie about themselves: a PNG named ``.csv``, a table named ``.nd2.csv`` beside
the ``.nd2`` it was exported from, and TIFFs tagged by an acquisition program, an analysis
program and a drawing program. Every name and value is invented.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fixtures.instrument_files import nd2_file, tiff_file
from ingence.core.project import Project
from ingence.core.schema import Observation, ValueKind
from ingence.core.tiers import ExtractionTier, ResolutionStatus
from ingence.infer.run import run_inference
from ingence.rulebook import shipped_rulebook
from ingence.rulebook.predicates import load_builtins
from ingence.rulebook.registry import PREDICATES, Concluded, PredicateNotEvaluableError
from ingence.rulebook.revision_5.file_types import RULES
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen
from ingence.storage.catalog import open_catalog

load_builtins()

SOFTWARE = 305
RULE_IDS = {rule["id"] for rule in RULES}


def _observation(key: str, value: object, *, identifier: str | None = None) -> Observation:
    kind = ValueKind.TEXT
    text = boolean = None
    if isinstance(value, bool):
        kind, boolean = ValueKind.BOOLEAN, value
    else:
        text = str(value)
    return Observation(
        observation_id=identifier or f"o:{key}",
        entry_id="e",
        run_id="r",
        observer="tiff",
        observer_version="2",
        observed_at="t",
        key=key,
        value_kind=kind,
        value_text=text,
        value_boolean=boolean,
        extraction_tier=ExtractionTier.FORMALLY_EXTRACTED,
    )


def _entry(name: str) -> object:
    from test_discover import entry_named

    return entry_named(name)


def _parameters(rule_id: str) -> dict[str, object]:
    (rule,) = (rule for rule in RULES if rule["id"] == rule_id)
    return {
        parameter["name"]: parameter["default"]  # type: ignore[index]
        for parameter in rule["parameters"]  # type: ignore[union-attr]
    }


WRITER = _parameters("stage_from_writer_tags")
FORMAT = _parameters("format_from_content")
INNER = _parameters("export_stage_from_inner_format")


# -------------------------------------------------------------------- the rules are declared


def test_both_rules_are_in_force_in_the_shipped_rulebook() -> None:
    book = shipped_rulebook()
    in_force = {rule.rule_id: rule for rule in book.rules}
    assert set(in_force) >= RULE_IDS
    assert in_force["format_from_content"].tier.value == "defeasible"
    # The stage rules carry "stage" in their ids, which is how completeness counts a stage claim.
    assert {"stage_from_writer_tags", "export_stage_from_inner_format"} <= set(in_force)
    assert in_force["stage_from_writer_tags"].tier.value == "abductive"
    assert "assumes" in in_force["stage_from_writer_tags"].statement
    # The folder deferral adds its own defeater to every rule. The two named are among them.
    assert set(in_force["stage_from_writer_tags"].defeated_by) >= {
        "pipeline_stage_from_name",
        "later_stage_from_name",
    }


# ------------------------------------------------------------------- writer_from_content_tags


@pytest.mark.parametrize(
    ("key", "value", "concluded"),
    [
        ("image.software", "vornscope NIS-Elements AR 5.21", "acquisition"),
        ("image.software", "ZEN blue 3.1", "acquisition"),
        ("image.software", "ImageJ 1.54f", "processed"),
        ("image.software", "Adobe Photoshop 25.0", "figure"),
        ("image.ome_creator", "Micro-Manager 2.0", "acquisition"),
        ("image.vendor_tags", "fluoview,metamorph", "acquisition"),
        ("image.vendor_tags", "lsm", "acquisition"),
    ],
)
def test_the_program_a_tag_names_gives_the_file_its_stage(key, value, concluded) -> None:
    predicate = PREDICATES.get("writer_from_content_tags")
    verdict = predicate.evaluate((_observation(key, value),), WRITER)
    assert isinstance(verdict, Concluded)
    assert verdict.values == (concluded,)
    assert verdict.inputs == (f"o:{key}",)


def test_tags_naming_two_kinds_of_program_conflict_rather_than_choose() -> None:
    """A stack an acquisition program wrote carries its vendor tag beside an ImageJ-shaped
    description. A crop ImageJ saved keeps the vendor tag. The tags cannot say which."""
    predicate = PREDICATES.get("writer_from_content_tags")
    observations = (
        _observation("image.software", "ImageJ 1.54f"),
        _observation("image.vendor_tags", "micromanager"),
    )
    assert predicate.evaluate(observations, WRITER) is None
    why = predicate.explain_abstention(observations, WRITER)
    assert why.status is ResolutionStatus.EXAMINED_CONFLICTING
    assert why.inputs == ("o:image.software", "o:image.vendor_tags")
    assert "acquisition, analysis" in why.reason
    # Two programs of one kind agree, and the description's kind is not read at all.
    agreeing = (
        _observation("image.vendor_tags", "micromanager"),
        _observation("image.description_kind", "imagej"),
        _observation("image.ome_creator", "Micro-Manager 2.0"),
    )
    verdict = predicate.evaluate(agreeing, WRITER)
    assert isinstance(verdict, Concluded)
    assert verdict.values == ("acquisition",)
    assert verdict.inputs == ("o:image.ome_creator", "o:image.vendor_tags")


def test_a_name_holding_a_writers_spelling_inside_a_word_names_no_writer() -> None:
    predicate = PREDICATES.get("writer_from_content_tags")
    assert predicate.evaluate((_observation("image.software", "frozen lens 2"),), WRITER) is None
    assert predicate.evaluate((_observation("image.software", "zen"),), WRITER) is not None


def test_the_writer_rule_abstains_in_words() -> None:
    predicate = PREDICATES.get("writer_from_content_tags")
    unexamined = predicate.explain_abstention((), WRITER)
    assert unexamined.status is ResolutionStatus.UNEXAMINED
    untagged = predicate.explain_abstention((_observation("image.width", "640"),), WRITER)
    assert untagged.status is ResolutionStatus.EXAMINED_NO_EVIDENCE
    assert "name no program" in untagged.reason
    unknown = predicate.explain_abstention((_observation("image.software", "vornpaint"),), WRITER)
    assert unknown.inputs == ("o:image.software",)
    assert "not one the file-type table lists" in unknown.reason
    with pytest.raises(PredicateNotEvaluableError):
        predicate.evaluate((_observation("image.software", "ImageJ"),), {**WRITER, "tag_keys": []})


# ------------------------------------------------------------------------ format_from_content


def test_a_signature_names_the_format_whatever_the_extension_says() -> None:
    predicate = PREDICATES.get("format_from_content")
    observations = (
        _observation("type.opinion.signature", "image/png"),
        _observation("type.opinion.extension", "text/csv"),
        _observation("type.extension_agrees", False),
    )
    verdict = predicate.evaluate(observations, FORMAT)
    assert isinstance(verdict, Concluded)
    assert verdict.values == ("image_format",)
    assert verdict.inputs == ("o:type.opinion.signature",)
    agreeing = (_observation("type.opinion.signature", "application/x-nikon-nd2"),)
    assert predicate.evaluate(agreeing, FORMAT).values == ("image_format",)  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "media_type", ["application/x-hdf5", "application/zip", "application/gzip"]
)
def test_a_container_signature_concludes_no_format(media_type) -> None:
    predicate = PREDICATES.get("format_from_content")
    observations = (_observation("type.opinion.signature", media_type),)
    assert predicate.evaluate(observations, FORMAT) is None
    why = predicate.explain_abstention(observations, FORMAT)
    assert "container" in why.reason and why.inputs == ("o:type.opinion.signature",)


def test_the_format_rule_abstains_in_words() -> None:
    predicate = PREDICATES.get("format_from_content")
    assert predicate.explain_abstention((), FORMAT).status is ResolutionStatus.UNEXAMINED
    only_a_hint = (_observation("type.opinion.extension", "text/csv"),)
    assert predicate.evaluate(only_a_hint, FORMAT) is None
    assert "no signature" in predicate.explain_abstention(only_a_hint, FORMAT).reason
    no_term = (_observation("type.opinion.signature", "application/ogg"),)
    assert predicate.evaluate(no_term, FORMAT) is None
    assert "names no term" in predicate.explain_abstention(no_term, FORMAT).reason


# ------------------------------------------------------------- derived_from_inner_format


@pytest.mark.parametrize(
    "name", ["x.nd2.csv", "cells.czi.tif", "plate_a01.xlsx.csv", "field_02.lif.png"]
)
def test_a_file_named_with_an_inner_kind_was_exported_from_one(name) -> None:
    predicate = PREDICATES.get("derived_from_inner_format")
    assert predicate.evaluate(_entry(name), INNER) == "export"


@pytest.mark.parametrize(
    "name",
    [
        "counts.raw.csv",
        "plate.log.csv",
        "run.out.csv",
        "notes.txt.csv",
        "x.tmp.csv",
        "img.fig.png",
        "table.csv.nd2",
    ],
)
def test_an_inner_half_that_is_a_word_or_an_instrument_outer_half_is_no_export(name) -> None:
    """The table marks raw, log, out, txt, tmp and fig as words after a dot: a stage word or a
    role, not a source's extension. And nothing is written out of a file into an ND2."""
    predicate = PREDICATES.get("derived_from_inner_format")
    assert predicate.evaluate(_entry(name), INNER) is None
    why = predicate.explain_abstention(_entry(name), INNER).reason
    assert "ordinary word" in why or "document" in why or "instrument" in why


@pytest.mark.parametrize(
    ("name", "why"),
    [
        ("plate_a01.csv", "one extension"),
        ("volume_07.ome.tif", "own spelling"),
        ("bundle.tar.gz", "own spelling"),
        ("table.csv.gz", "compression"),
        ("notes.schema.json", "no kind of file"),
        ("stack.tif.tiff", "same kind"),
    ],
)
def test_what_is_not_an_export_is_said_in_words(name, why) -> None:
    predicate = PREDICATES.get("derived_from_inner_format")
    assert predicate.evaluate(_entry(name), INNER) is None
    assert why in predicate.explain_abstention(_entry(name), INNER).reason


# ------------------------------------------------------------------- the rules over a pool


def _write_pool(pool: Path) -> None:
    folder = pool / "zorb_assay" / "2026-03-04_zorbexin_plates"
    folder.mkdir(parents=True)
    png = b"\x89PNG\r\n\x1a\n" + bytes(600)
    (folder / "plate_a01_counts.csv").write_bytes(png)
    (folder / "plate_a02_counts.csv").write_text("well,count\nA01,12\nA02,9\n")
    (folder / "zorbexin_field_01.nd2").write_bytes(nd2_file(julian_day=2_461_100.25))
    (folder / "zorbexin_field_01.nd2.csv").write_text("frame,x,y\n1,2.0,3.0\n")
    (folder / "zorbexin_field_01.tif").write_bytes(
        tiff_file(text_tags={SOFTWARE: "NIS-Elements AR 5.21.03"})
    )
    (folder / "zorbexin_field_01_b.tif").write_bytes(
        tiff_file(text_tags={SOFTWARE: "Fiji 2.14 (ImageJ 1.54f)"})
    )
    # A name stating a later stage (crop) overrides what the tags assume.
    (folder / "zorbexin_field_01_crop.tif").write_bytes(
        tiff_file(text_tags={SOFTWARE: "ImageJ 1.54f"})
    )
    # An acquisition program's stack: its vendor tag beside an ImageJ-shaped description.
    (folder / "zorbexin_stack.tif").write_bytes(
        tiff_file(private_tags={51123: b'{"Summary": {}}'}, imagej_info="sLensName = x\n")
    )
    (folder / "zorbexin_panel.tif").write_bytes(
        tiff_file(text_tags={SOFTWARE: "Adobe Photoshop 25.0 (Macintosh)"})
    )
    (folder / "zorbexin_field_02.tif").write_bytes(tiff_file(pages=2))


@pytest.fixture(scope="module")
def inferred(tmp_path_factory: pytest.TempPathFactory) -> Project:
    base = tmp_path_factory.mktemp("e2-rules")
    pool = base / "pool"
    _write_pool(pool)
    project = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_deepen(
        project,
        run_id="run_20260101T000002Z_222222222222",
        started_at="t",
        budget_seconds=120.0,
    )
    report = run_inference(project, run_id="run_20260101T000003Z_333333333333", started_at="t")
    assert not report.cancelled
    return project


def _claims(project: Project, rule_id: str, *, status: str = "resolved") -> dict[str, str]:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.name, c.object_reference FROM claims c JOIN entries e "
            "ON e.entry_id = c.subject WHERE c.rule_id = ? AND c.resolution_status = ?",
            [rule_id, status],
        ).fetchall()
    return {str(name): str(value) for name, value in rows}


def test_a_png_named_csv_is_an_image_by_its_content(inferred: Project) -> None:
    concluded = _claims(inferred, "format_from_content")
    assert concluded["plate_a01_counts.csv"] == "image_format"
    assert "plate_a02_counts.csv" not in concluded
    assert concluded["zorbexin_field_01.nd2"] == "image_format"
    assert concluded["zorbexin_field_01.tif"] == "image_format"
    # The extension's own claim (a table) is retracted by the contradiction and left conflicting;
    # the content's claim is what the file is.
    by_extension = _claims(
        inferred, "format_from_extension_unless_contradicted", status="examined_conflicting"
    )
    assert by_extension["plate_a01_counts.csv"] == "tabular_format"
    assert "plate_a01_counts.csv" not in _claims(
        inferred, "format_from_extension_unless_contradicted"
    )


def test_the_writers_tags_give_each_tiff_its_stage_or_role(inferred: Project) -> None:
    concluded = _claims(inferred, "stage_from_writer_tags")
    assert concluded["zorbexin_field_01.tif"] == "acquisition"
    assert concluded["zorbexin_field_01_b.tif"] == "processed"
    assert concluded["zorbexin_panel.tif"] == "figure"
    assert concluded["zorbexin_stack.tif"] == "acquisition"
    assert "zorbexin_field_02.tif" not in concluded
    assert "plate_a01_counts.csv" not in concluded
    # The crop's name states a later stage, which defeats the assumption: the claim is kept,
    # marked conflicting, and never resolved.
    assert "zorbexin_field_01_crop.tif" not in concluded
    defeated = _claims(inferred, "stage_from_writer_tags", status="examined_conflicting")
    assert defeated["zorbexin_field_01_crop.tif"] == "processed"


def test_a_table_named_for_the_image_it_came_from_is_an_export_joined_to_it(
    inferred: Project,
) -> None:
    exported = _claims(inferred, "export_stage_from_inner_format")
    assert exported == {"zorbexin_field_01.nd2.csv": "export"}
    with open_catalog(inferred.path) as catalog:
        rows = catalog.query(
            "SELECT e.name, c.object_reference FROM claims c JOIN entries e ON e.entry_id = "
            "c.subject WHERE c.rule_id = 'named_for_an_acquisition' AND e.name LIKE ?",
            ["zorbexin_field_01.nd2%"],
        ).fetchall()
    grouped = {str(name): str(value) for name, value in rows}
    assert set(grouped) == {"zorbexin_field_01.nd2", "zorbexin_field_01.nd2.csv"}
    assert len(set(grouped.values())) == 1


def test_an_export_is_never_at_the_acquisition_stage(inferred: Project) -> None:
    """The acquisition rule reads the extension slot, which for x.nd2.csv is the table's, so no
    acquisition reading stands on the export, while the image beside it keeps its own. Naming
    the export rule as that rule's defeater would need a rename of the rule."""
    with open_catalog(inferred.path) as catalog:
        rows = catalog.query(
            "SELECT e.name, c.resolution_status FROM claims c JOIN entries e ON e.entry_id = "
            "c.subject WHERE c.rule_id = 'acquisition_stage_from_format' AND e.name LIKE ?",
            ["zorbexin_field_01.nd2%"],
        ).fetchall()
    by_name = {str(name): str(status) for name, status in rows}
    assert "zorbexin_field_01.nd2.csv" not in by_name
    assert _claims(inferred, "export_stage_from_inner_format") == {
        "zorbexin_field_01.nd2.csv": "export"
    }
