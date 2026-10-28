"""The design grid on invented names: factors, levels, cells, crossing, merging.

Pure: the grid is given each file's name parts and conclusions directly, so every rule is held on
names built here. The end-to-end reading of a run is ``test_design.py``.
"""

from __future__ import annotations

import random

from ingence.design.grid import (
    DesignFile,
    GridParameters,
    NamePart,
    build_design,
)

HOUR = 3600.0


def _word(ordinal: int, shown: str, sense: str | None = None) -> NamePart:
    return NamePart(ordinal=ordinal, kind="word", value=shown.casefold(), shown=shown, sense=sense)


def _time(ordinal: int, hours: int) -> NamePart:
    return NamePart(
        ordinal=ordinal,
        kind="quantity",
        value=f"{hours * 3600} second",
        shown=f"{hours}h",
        sense="unit",
        magnitude=hours * HOUR,
        dimension="time",
    )


def _dose(ordinal: int, micromolar: int) -> NamePart:
    return NamePart(
        ordinal=ordinal,
        kind="quantity",
        value=f"{micromolar * 1e-6:g} molar",
        shown=f"{micromolar}uM",
        sense="unit",
        magnitude=micromolar * 1e-6,
        dimension="concentration",
    )


def _number(ordinal: int, count: int, width: int = 3) -> NamePart:
    return NamePart(ordinal=ordinal, kind="number", value=str(count), shown=f"{count:0{width}d}")


def _date(day: str = "20261012") -> NamePart:
    return NamePart(ordinal=0, kind="date", value=f"{day[:4]}-{day[4:6]}-{day[6:]}", shown=day)


def _extension(ordinal: int, text: str = "tif") -> NamePart:
    return NamePart(ordinal=ordinal, kind="extension", value=text, shown=text, sense="format")


def _drug_time(drug: str, hours: int, count: int, *, day: str = "20261012") -> DesignFile:
    """``20261012_HeLa_DMSO_2h_001.tif``."""
    return DesignFile(
        entry_id=f"{drug}-{hours}-{count}-{day}",
        name=f"{day}_HeLa_{drug}_{hours}h_{count:03d}.tif",
        class_key=f"class-{drug}-{hours}",
        stage=None,
        empty=False,
        parts=(
            _date(day),
            _word(1, "HeLa"),
            _word(2, drug),
            _time(3, hours),
            _number(4, count),
            _extension(5),
        ),
    )


def _grid(files: list[DesignFile], **keywords: object):  # type: ignore[no-untyped-def]
    return build_design("exp", "root", "20261012_HeLa", files, **keywords)  # type: ignore[arg-type]


def _crossed(
    absent: set[tuple[str, int]] | None = None,
    reps: int = 3,
    drugs: tuple[str, ...] = ("DMSO", "taxol"),
) -> list[DesignFile]:
    absent = absent or set()
    return [
        _drug_time(drug, hours, count)
        for drug in drugs
        for hours in (0, 2)
        for count in range(1, reps + 1)
        if (drug, hours) not in absent
    ]


def test_a_crossed_design_has_its_factors_levels_and_cells() -> None:
    design = _grid(_crossed())
    assert [factor.name for factor in design.factors] == ["factor", "time"]
    drug, time = design.factors
    assert [level.shown for level in drug.levels] == ["DMSO", "taxol"]
    assert [level.shown for level in time.levels] == ["0h", "2h"]
    assert time.order == "numeric"
    assert [(cell.levels, cell.files, cell.replicates, cell.state) for cell in design.cells] == [
        ((0, 0), 3, 3, "occupied"),
        ((0, 1), 3, 3, "occupied"),
        ((1, 0), 3, 3, "occupied"),
        ((1, 1), 3, 3, "occupied"),
    ]
    assert [(pair.relation, pair.found, pair.possible) for pair in design.pairs] == [
        ("crossed", 4, 4)
    ]
    assert design.fill == 1.0
    assert design.findings == ()


def test_levels_that_are_numbers_are_in_numeric_order_not_text_order() -> None:
    files = [_drug_time("DMSO", hours, count) for hours in (10, 2, 0, 24) for count in (1, 2)]
    design = _grid(files)
    (time,) = design.factors
    assert [level.shown for level in time.levels] == ["0h", "2h", "10h", "24h"]


def test_a_date_a_constant_word_and_the_counter_are_never_factors() -> None:
    files = [
        _drug_time(drug, 2, count, day=day)
        for drug in ("DMSO", "taxol")
        for count in (1, 2, 3)
        for day in ("20261012", "20261013")
    ]
    design = _grid(files)
    # Only the drug varies as a condition: the date differs between files and is no factor, the
    # cell line is one level, and the numbers count (001, 002, 003) and are the replicate axis.
    assert [factor.positions for factor in design.factors] == [(3,)]
    assert design.replicate_positions == (5,)
    assert design.cells[0].replicates == 3


def test_a_combination_not_found_is_a_question_and_never_called_missing() -> None:
    design = _grid(_crossed(absent={("taxol", 2)}))
    states = {cell.levels: cell.state for cell in design.cells}
    assert states[(1, 1)] == "not found"
    assert [pair.relation for pair in design.pairs] == ["partly crossed"]
    (finding,) = design.findings
    assert finding.kind == "combination_not_found"
    assert finding.combination == ("taxol", "2h")
    assert finding.question.startswith("Combination not found: taxol / 2h.")
    assert finding.question.endswith("?")
    assert "missing" not in finding.question.casefold()
    assert design.fill == 0.75


def test_two_absent_combinations_raise_no_finding() -> None:
    design = _grid(
        _crossed(absent={("taxol", 2), ("nocodazole", 0)}, drugs=("DMSO", "taxol", "nocodazole"))
    )
    assert design.findings == ()
    assert sum(cell.state == "not found" for cell in design.cells) == 2
    assert design.fill is not None


def test_a_nested_design_is_said_nested_and_lists_no_combination_as_not_found() -> None:
    def nested(drug: str, micromolar: int, count: int) -> DesignFile:
        return DesignFile(
            entry_id=f"{drug}-{micromolar}-{count}",
            name=f"x{drug}{micromolar}{count}.tif",
            class_key=f"class-{drug}-{micromolar}",
            stage=None,
            empty=False,
            parts=(
                _date(),
                _word(1, drug),
                _dose(2, micromolar),
                _number(3, count),
                _extension(4),
            ),
        )

    files = [
        nested(drug, micromolar, count)
        for drug, doses in (("taxol", (1, 10)), ("nocodazole", (5, 50)))
        for micromolar in doses
        for count in (1, 2)
    ]
    design = _grid(files)
    (pair,) = design.pairs
    assert pair.relation == "nested"
    assert pair.nested_in == design.factors[0].factor_id
    assert design.fill is None
    assert design.not_found_listed is False
    assert "nested" in design.not_found_said
    assert all(cell.state == "occupied" for cell in design.cells)
    assert design.findings == ()


def test_a_dose_ladder_is_a_label_on_the_levels() -> None:
    def dosed(micromolar: int, count: int) -> DesignFile:
        return DesignFile(
            entry_id=f"{micromolar}-{count}",
            name=f"HeLa_nocodazole-{micromolar}uM_{count:03d}.tif",
            class_key=f"class-{micromolar}",
            stage=None,
            empty=False,
            parts=(
                _word(0, "HeLa"),
                _word(1, "nocodazole"),
                _dose(2, micromolar),
                _number(3, count),
                _extension(4),
            ),
        )

    ladder = _grid([dosed(micromolar, count) for micromolar in (1, 10, 100) for count in (1, 2)])
    (dose,) = ladder.factors
    assert [level.shown for level in dose.levels] == ["1uM", "10uM", "100uM"]
    assert dose.ladder is not None
    assert (dose.ladder.kind, round(dose.ladder.step)) == ("ratio", 10)
    steps = _grid([dosed(micromolar, count) for micromolar in (2, 4, 6, 8) for count in (1, 2)])
    assert steps.factors[0].ladder is not None
    assert steps.factors[0].ladder.kind == "difference"
    irregular = _grid([dosed(micromolar, count) for micromolar in (1, 3, 10) for count in (1, 2)])
    assert irregular.factors[0].ladder is None


def _channels(exception: bool, *, per_channel: int = 7) -> list[DesignFile]:
    def channel(stain: str, wavelength: int, count: int, drug: str) -> DesignFile:
        return DesignFile(
            entry_id=f"{stain}-{wavelength}-{count}-{drug}",
            name=f"HeLa_{drug}_{stain}_{wavelength}_{count:03d}.tif",
            class_key=f"class-{stain}-{drug}",
            stage=None,
            empty=False,
            parts=(
                _word(0, "HeLa"),
                _word(1, drug),
                _word(2, stain),
                NamePart(ordinal=3, kind="number", value=str(wavelength), shown=str(wavelength)),
                _number(4, count),
                _extension(5),
            ),
        )

    files = [
        channel(stain, wavelength, count, drug)
        for drug in ("DMSO", "taxol")
        for stain, wavelength in (("GFP", 488), ("mCherry", 561))
        for count in range(1, per_channel + 1)
    ]
    if exception:
        files[0] = channel("GFP", 561, 1, "DMSO")
    return files


def test_parts_that_always_go_together_are_one_factor() -> None:
    design = _grid(_channels(exception=False))
    names = [factor.name for factor in design.factors]
    assert len(design.factors) == 2, names
    merged = [factor for factor in design.factors if factor.merged]
    assert len(merged) == 1
    (factor,) = merged
    assert [level.shown for level in factor.levels] == ["GFP + 488", "mCherry + 561"]
    assert factor.positions == (3, 4)
    (note,) = factor.merged
    assert (note.observations, note.exceptions, note.said) == (
        28,
        0,
        "always together over 28 files",
    )
    assert design.merged_parts == 1


def test_an_exception_is_counted_in_words_and_the_parts_still_go_together() -> None:
    # Fourteen files, one of them off the pairing.
    files = [file for file in _channels(exception=True, per_channel=7) if "taxol" not in file.name]
    assert len(files) == 14
    design = _grid(files)
    (factor,) = [factor for factor in design.factors if factor.merged]
    (note,) = factor.merged
    assert (note.observations, note.exceptions) == (14, 1)
    assert note.said == "one exception in 14"
    assert note.examples and note.examples[0].startswith("HeLa_DMSO_GFP_561")


def test_too_many_exceptions_or_too_few_files_do_not_merge() -> None:
    files = [file for file in _channels(exception=False, per_channel=7) if "taxol" not in file.name]
    off = [_swapped(file) if index in (0, 1, 7, 8) else file for index, file in enumerate(files)]
    assert not any(factor.merged for factor in _grid(off).factors)
    single = _grid([f for f in _channels(exception=False, per_channel=1) if "taxol" not in f.name])
    assert len(single.factors) == 2
    assert not any(factor.merged for factor in single.factors)
    unmerged = _grid(_channels(exception=False), parameters=GridParameters(merge_parts=False))
    assert len(unmerged.factors) == 3


def _swapped(file: DesignFile) -> DesignFile:
    parts = list(file.parts)
    other = "561" if file.parts[3].shown == "488" else "488"
    parts[3] = NamePart(ordinal=3, kind="number", value=other, shown=other)
    return DesignFile(
        file.entry_id, file.name, file.class_key, file.stage, file.empty, tuple(parts)
    )


def test_a_cell_whose_made_files_are_all_empty_outputs_has_an_empty_output() -> None:
    def part(drug: str, stage: str, *, empty: bool, count: int = 1) -> DesignFile:
        base = _drug_time(drug, 2, count)
        return DesignFile(
            f"{base.entry_id}-{stage}", base.name, base.class_key, stage, empty, base.parts
        )

    files = [
        part("DMSO", "acquisition", empty=False),
        part("DMSO", "analysis", empty=False),
        part("taxol", "acquisition", empty=False),
        part("taxol", "analysis", empty=True),
    ]
    design = _grid(files)
    assert {cell.levels: cell.state for cell in design.cells} == {
        (0,): "occupied",
        (1,): "empty output",
    }
    raw_only = _grid(
        [part("DMSO", "acquisition", empty=False), part("taxol", "acquisition", empty=False)]
    )
    assert {cell.state for cell in raw_only.cells} == {"occupied"}


def test_coverage_names_each_file_left_out_and_the_reason() -> None:
    files = _crossed(reps=2)
    stray_class = DesignFile("a", "a.tif", None, None, False, _drug_time("zzz", 2, 1).parts)
    stray_layout = DesignFile("b", "b.tif", "k", None, False, ())
    odd = DesignFile("c", "c.tif", "k2", None, False, (_word(0, "HeLa"), _extension(1)))
    strict = GridParameters(include_unclassed=False)
    design = _grid([*files, stray_class, stray_layout, odd], programs=4, parameters=strict)
    assert design.coverage.files == len(files) + 3 + 4
    assert design.coverage.in_grid == len(files)
    assert dict(design.coverage.left_out) == {
        "no_condition_class": 1,
        "no_name_layout": 1,
        "another_layout": 1,
        "program": 4,
    }
    assert (
        design.coverage.in_grid + sum(dict(design.coverage.left_out).values())
        == design.coverage.files
    )


def test_a_file_no_class_holds_fills_its_cell_and_never_makes_one() -> None:
    """The only file of its condition has no class. It fills the cell its name gives, so that cell
    is not "not found" and no question is asked about it; a file whose level the grid lacks makes
    no cell, no level and no grid."""
    files = _crossed(reps=2)
    lone = _drug_time("taxol", 2, 1)
    others = [f for f in files if not f.name.startswith("20261012_HeLa_taxol_2h")]
    single = DesignFile(lone.entry_id, lone.name, None, None, False, lone.parts)
    filled = _grid([*others, single])
    assert {cell.levels: (cell.state, cell.files) for cell in filled.cells}[(1, 1)] == (
        "occupied",
        1,
    )
    assert filled.findings == ()
    assert dict(filled.coverage.left_out) == {}
    assert filled.coverage.in_grid == filled.coverage.files == len(others) + 1
    # With the setting on it takes part like any file; the grid is the same.
    drawn = _grid([*others, single], parameters=GridParameters(include_unclassed=True))
    assert {cell.levels: cell.state for cell in drawn.cells}[(1, 1)] == "occupied"
    # A level the grid does not have is left out, and no file alone makes a grid.
    stranger = _drug_time("nocodazole", 2, 1)
    alone = DesignFile(stranger.entry_id, stranger.name, None, None, False, stranger.parts)
    assert len(_grid([*others, single, alone]).cells) == len(filled.cells)
    assert dict(_grid([*others, single, alone]).coverage.left_out) == {"no_condition_class": 1}
    only = _grid([single, alone])
    assert not only.has_grid
    assert dict(only.coverage.left_out) == {"no_condition_class": 2}


def test_one_condition_only_is_no_grid_and_says_why() -> None:
    files = [_drug_time("DMSO", 2, count) for count in (1, 2, 3)]
    design = _grid(files)
    assert not design.has_grid
    assert "varies" in design.reason
    assert dict(design.coverage.left_out) == {"no_factor": 3}


def test_the_order_files_arrive_in_changes_nothing() -> None:
    files = _crossed(absent={("taxol", 2)})
    shuffled = list(files)
    random.Random(7).shuffle(shuffled)
    assert _grid(files) == _grid(shuffled)


def test_a_part_with_too_many_values_is_an_identifier_and_no_condition() -> None:
    files = [_drug_time("DMSO", hours, 1) for hours in range(0, 60)]
    assert not _grid(files).has_grid
    assert _grid(files, parameters=GridParameters(factor_levels_at_most=100)).has_grid


def test_a_stage_word_and_a_replicate_label_do_not_make_another_layout() -> None:
    """``HeLa_DMSO_take1.nd2`` and ``HeLa_DMSO_take1_processed.tif`` are one layout: the stage word
    is set aside, the replicate label counts repeats, and the extension is no condition."""

    def made(drug: str, take: int, *, stage_word: bool) -> DesignFile:
        parts = [
            _word(0, "HeLa"),
            _word(1, drug),
            NamePart(
                ordinal=2,
                kind="indexed",
                value=f"take{take}",
                shown=f"take{take}",
                sense="replicate",
            ),
        ]
        if stage_word:
            parts.append(_word(3, "processed", sense="stage"))
        parts.append(_extension(len(parts), "tif" if stage_word else "nd2"))
        stage = "processed" if stage_word else "acquisition"
        return DesignFile(
            f"{drug}-{take}-{stage_word}",
            f"{drug}{take}",
            f"class-{drug}-{stage_word}",
            stage,
            False,
            tuple(parts),
        )

    files = [
        made(drug, take, stage_word=with_stage)
        for drug in ("DMSO", "taxol")
        for take in (1, 2, 3)
        for with_stage in (False, True)
    ]
    design = _grid(files)
    assert [factor.name for factor in design.factors] == ["factor"]
    assert dict(design.coverage.left_out) == {}
    assert [(cell.files, cell.replicates) for cell in design.cells] == [(6, 3), (6, 3)]
    assert dict(design.cells[0].stages) == {"acquisition": 3, "processed": 3}
    assert design.replicate_positions == (3,)


def test_a_name_with_no_part_that_could_be_a_condition_is_left_out() -> None:
    bare = DesignFile("a", "a.tif", "k", None, False, (_date(), _extension(1)))
    design = _grid([*_crossed(), bare])
    assert dict(design.coverage.left_out) == {"no_condition_part": 1}


def test_the_layouts_left_out_are_named_with_their_files() -> None:
    files = _crossed(reps=2)
    arm = [
        DesignFile(
            f"v{n}",
            f"HeLa_vehicle_{n:03d}.tif",
            "class-vehicle",
            None,
            False,
            (_word(0, "HeLa"), _word(1, "vehicle"), _number(2, n), _extension(3)),
        )
        for n in (1, 2)
    ]
    design = _grid([*files, *arm])
    assert dict(design.coverage.left_out) == {"another_layout": 2}
    (other,) = design.other_layouts
    assert other.files == 2
    assert other.pattern.startswith("HeLa_vehicle_")
    assert _grid(files).other_layouts == ()


def test_a_label_that_counts_is_the_replicate_axis_and_one_that_does_not_is_a_condition() -> None:
    def labelled(drug: str, label: str, index: int) -> DesignFile:
        parts = (
            _word(0, "HeLa"),
            _word(1, drug),
            NamePart(ordinal=2, kind="indexed", value=f"{label}{index}", shown=f"{label}{index}"),
            _extension(3),
        )
        return DesignFile(
            f"{drug}-{label}{index}", f"{drug}{label}{index}", f"c-{drug}", None, False, parts
        )

    counting = _grid([labelled(d, "exp", i) for d in ("DMSO", "taxol") for i in (1, 2, 3)])
    assert [factor.name for factor in counting.factors] == ["factor"]
    assert counting.replicate_positions == (3,)
    assert counting.cells[0].replicates == 3
    # Numbers that do not count with each other may name a condition (d1, d10, d100).
    spread = _grid([labelled(d, "d", i) for d in ("DMSO", "taxol") for i in (1, 10, 100)])
    assert [factor.kind for factor in spread.factors] == ["word", "indexed"]


def test_a_time_point_label_is_a_factor_and_a_bare_t_label_is_the_replicate_axis() -> None:
    def labelled(drug: str, label: str, point: int, sense: str | None) -> DesignFile:
        parts = (
            _word(0, "HeLa"),
            _word(1, drug),
            NamePart(2, "indexed", f"{label}{point}", f"{label}{point}", sense=sense),
            _number(3, 1),
            _extension(4),
        )
        return DesignFile(f"{drug}{label}{point}", "n", f"c-{drug}", None, False, parts)

    points = _grid(
        [labelled(d, "tp", i, "timepoint") for d in ("DMSO", "taxol") for i in (3, 1, 2)]
    )
    assert [factor.kind for factor in points.factors] == ["word", "indexed"]
    assert [level.shown for level in points.factors[1].levels] == ["tp1", "tp2", "tp3"]
    assert points.factors[1].order == "numeric"
    bare = _grid([labelled(d, "t", i, None) for d in ("DMSO", "taxol") for i in (1, 2, 3)])
    assert [factor.kind for factor in bare.factors] == ["word"]
    assert 3 in bare.replicate_positions
