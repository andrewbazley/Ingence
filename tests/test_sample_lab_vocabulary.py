"""The sample collection's vocabulary tier.

The tier is off unless asked for, and while it is off it changes nothing. The same tier, seed and
share write the same names. Every shape it makes appears in a small collection, and the key's words
family says what each spelling means and which spellings are one thing. No spelling is a word the
collection or the Dictionary already has. An experiment whose files are named from outside it is
left alone, so no key answer breaks. No word of its own is in the tier's lists. The measurement that
uses it is held in ``test_teaching_oracle.py``.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import os
import posixpath
import random
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml

from ingence.dictionary.store import DictionaryStore
from ingence.evaluation.key import (
    AnswerKey,
    ValueFamily,
    WordMeaning,
    WordsFamily,
    load_key,
)
from ingence.evaluation.sample_lab import _build, generate_sample_lab
from ingence.evaluation.sample_lab_drive import Drive
from ingence.evaluation.sample_lab_vocabulary import (
    SHAPES,
    VOCABULARY_FILE,
    apply_vocabulary,
    originals_only,
    possible_spellings,
    slip_of,
)
from ingence.evaluation.sample_lab_words import words

if TYPE_CHECKING:
    from ingence.evaluation.sample_lab import SampleLab

STORE = DictionaryStore.builtin()
#: A seed and a share at which the small tier makes every shape once or more.
EVERY_SHAPE = {"seed": 1, "vocabulary": 1.0}


def _walk(pool: Path) -> dict[str, bytes | str]:
    """Every file's bytes and every link's target, by path, as the pool holds them."""
    found: dict[str, bytes | str] = {}
    for folder, _names, files in os.walk(pool):
        for name in files:
            path = Path(folder, name)
            relative = path.relative_to(pool).as_posix()
            found[relative] = os.readlink(path) if path.is_symlink() else path.read_bytes()
    return found


def _beside(lab: SampleLab) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in (lab.key_path, lab.unscored_path, lab.manifest_path, lab.datasheet_path)
    }


def _made(
    tmp_path: Path,
    name: str,
    *,
    seed: int,
    vocabulary: float = 0.0,
    vocabulary_seed: int | None = None,
) -> SampleLab:
    return generate_sample_lab(
        tmp_path / name,
        tier="S",
        seed=seed,
        vocabulary=vocabulary,
        vocabulary_seed=vocabulary_seed,
    )


def test_the_tier_is_off_by_default_and_a_share_of_none_changes_nothing(tmp_path: Path) -> None:
    plain = _made(tmp_path, "plain", seed=0)
    none = _made(tmp_path, "none", seed=0, vocabulary=0.0)

    assert _walk(plain.pool) == _walk(none.pool)
    assert _beside(plain) == _beside(none)
    assert plain.key.words is None
    assert "words" not in json.loads(plain.key_path.read_text(encoding="utf-8"))
    assert "vocabulary" not in plain.manifest
    assert "vocabulary" not in plain.key.describes


#: The pool digest of the small tier at seed 0, taken from a generator that had no vocabulary tier.
#: The default collection is still byte for byte that one.
PLAIN_S0_DIGEST = "ecd88440e09b2a93d8aac43d9cfc7e8dacb17ac6d606d9c21c3f4c19989a8519"


def test_the_default_collection_is_the_one_written_before_the_tier_existed(tmp_path: Path) -> None:
    plain = _made(tmp_path, "plain", seed=0)

    assert plain.manifest["pool_digest"]["value"] == PLAIN_S0_DIGEST
    assert plain.manifest["counts"]["files"] == 423


def test_one_tier_seed_and_share_write_one_collection_byte_for_byte(tmp_path: Path) -> None:
    first = _made(tmp_path, "first", seed=0, vocabulary=0.5)
    second = _made(tmp_path, "second", seed=0, vocabulary=0.5)

    assert _walk(first.pool) == _walk(second.pool)
    assert _beside(first) == _beside(second)
    assert first.manifest["pool_digest"] == second.manifest["pool_digest"]


def test_another_seed_or_another_share_rewrites_other_names(tmp_path: Path) -> None:
    base = _made(tmp_path, "base", seed=0, vocabulary=0.5)
    other_share = _made(tmp_path, "share", seed=0, vocabulary=0.25)
    other_draw = _made(tmp_path, "draw", seed=0, vocabulary=0.5, vocabulary_seed=7)
    plain = _made(tmp_path, "plain", seed=0)

    digests = {
        lab.manifest["pool_digest"]["value"] for lab in (base, other_share, other_draw, plain)
    }
    assert len(digests) == 4


def test_a_share_outside_zero_to_one_is_refused(tmp_path: Path) -> None:
    for share in (-0.1, 1.5):
        with pytest.raises(ValueError, match="between 0 and 1"):
            _made(tmp_path, f"bad{share}", seed=0, vocabulary=share)


def test_every_shape_appears_in_a_small_collection_and_the_key_names_its_meaning() -> None:
    drive = _build("S", EVERY_SHAPE["seed"])
    applied = apply_vocabulary(drive, fraction=1.0, seed=EVERY_SHAPE["seed"], tier="S")

    assert {rewrite.shape for rewrite in applied.rewrites} == set(SHAPES)
    family = applied.family
    for rewrite in applied.rewrites:
        meaning = family.spellings[rewrite.spelling]
        assert rewrite.shape in meaning.shapes
        assert meaning.means.casefold() == rewrite.item.casefold()
        assert meaning.term and meaning.theme_kind
    # And each spelling that is new is written in the names: a name of the pool carries it.
    every_path = (*drive.files, *drive.links, *drive.folders, *drive.empty_folders)
    names = [path.casefold() for path in every_path]
    for rewrite in applied.rewrites:
        if rewrite.shape not in {"case", "separator"}:
            assert any(rewrite.spelling in name for name in names), rewrite


def test_the_words_family_covers_every_rewritten_spelling_and_says_which_are_one_thing() -> None:
    drive = _build("S", 0)
    applied = apply_vocabulary(drive, fraction=0.5, seed=0, tier="S")
    family = applied.family

    assert applied.rewrites
    assert {rewrite.spelling for rewrite in applied.rewrites} <= set(family.spellings)
    for group in family.same:
        assert len(group) >= 2
        assert len({family.spellings[spelling].means for spelling in group}) == 1
    in_a_group = {spelling for group in family.same for spelling in group}
    assert in_a_group
    assert in_a_group <= set(family.spellings)


def test_a_spelling_means_what_the_dictionary_says_or_the_word_itself() -> None:
    drive = _build("S", 1)
    family = apply_vocabulary(drive, fraction=1.0, seed=1, tier="S").family

    checked = 0
    for spelling, meaning in family.spellings.items():
        if " " in meaning.means:
            assert meaning.term == "_".join(meaning.means.casefold().split())
            continue
        hit = STORE.look_up(meaning.means)
        if meaning.known:
            assert hit is not None
            assert (hit.sense.term, hit.sense.theme_kind) == (meaning.term, meaning.theme_kind)
        else:
            assert hit is None, spelling
            assert meaning.term == meaning.means.casefold()
        checked += 1
    assert checked


def test_no_new_spelling_is_a_word_of_the_collection_or_one_the_dictionary_reads() -> None:
    vocabulary = words().everything()
    drive = _build("S", 1)
    applied = apply_vocabulary(drive, fraction=1.0, seed=1, tier="S")

    for rewrite in applied.rewrites:
        if rewrite.shape == "case":
            continue
        letters = rewrite.spelling.partition("-")[0]
        if rewrite.shape == "separator":
            continue
        assert letters not in vocabulary, rewrite
        assert STORE.look_up(letters) is None, rewrite


def test_every_spelling_is_made_from_the_word_it_stands_for() -> None:
    drive = _build("S", 1)
    applied = apply_vocabulary(drive, fraction=1.0, seed=1, tier="S")
    seen: set[str] = set()
    for rewrite in applied.rewrites:
        original = rewrite.item.casefold()
        spelling = rewrite.spelling
        seen.add(rewrite.shape)
        if rewrite.shape == "slip":
            assert len(original) - 1 <= len(spelling) <= len(original) + 1
            assert spelling[0] == original[0]
        elif rewrite.shape == "code":
            head, _, number = spelling.partition("-")
            assert original.startswith(head) and len(head) == 3 and number.isdigit()
        elif rewrite.shape == "condition":
            assert original.startswith(spelling) and len(spelling) == 4
        elif rewrite.shape == "initials":
            assert spelling == "".join(part[0] for part in original.split())
        elif rewrite.shape == "glued":
            assert spelling == original.replace(" ", "")
        elif rewrite.shape == "case":
            assert spelling == original
        else:
            assert spelling.replace("-", " ").replace("_", " ").replace(".", " ") == original
    assert seen == set(SHAPES)


def test_the_dictionary_terms_the_tier_lists_are_the_ones_the_shipped_dictionary_gives() -> None:
    loaded = yaml.safe_load(VOCABULARY_FILE.read_text(encoding="utf-8"))
    vocabulary = words()
    listed = {word: tuple(pair) for word, pair in loaded["terms"].items()}

    for kind, sections in loaded["rewritable"].items():
        for section in sections:
            for word in getattr(vocabulary, kind)[section]:
                hit = STORE.look_up(word)
                if word in listed:
                    assert hit is not None and not hit.ambiguous, word
                    assert listed[word] == (hit.sense.term, hit.sense.theme_kind), word
                else:
                    assert hit is None or hit.ambiguous, word
    assert set(listed) <= {
        word
        for kind, sections in loaded["rewritable"].items()
        for section in sections
        for word in getattr(vocabulary, kind)[section]
    }


def test_the_tier_never_makes_a_spelling_the_shipped_dictionary_reads() -> None:
    """Exhaustive: every spelling the tier could make, and the list of those it must not."""
    loaded = yaml.safe_load(VOCABULARY_FILE.read_text(encoding="utf-8"))
    possible = possible_spellings()

    read = {
        spelling
        for spellings in possible.values()
        for spelling in spellings
        if STORE.look_up(spelling) is not None
    }
    assert read == set(loaded["avoid"])
    assert possible["slip"] and possible["code"] and possible["initials"] and possible["glued"]
    assert set(possible) == set(SHAPES)


def test_a_slip_drops_doubles_or_swaps_a_letter() -> None:
    assert slip_of("tubulin", "drop", 3) == "tublin"
    assert slip_of("tubulin", "double", 2) == "tubbulin"
    assert slip_of("tubulin", "swap", 3) == "tubluin"


def test_the_tier_s_lists_hold_no_word_of_their_own() -> None:
    """The words it can touch are the collection's own word lists; its file adds none."""
    loaded = yaml.safe_load(VOCABULARY_FILE.read_text(encoding="utf-8"))
    vocabulary = words()

    touchable: set[str] = set()
    for kind, sections in loaded["rewritable"].items():
        groups = getattr(vocabulary, kind)
        assert set(sections) <= set(groups)
        for section in sections:
            touchable.update(word.casefold() for word in groups[section])
    assert touchable
    assert touchable <= vocabulary.everything()
    assert not touchable & vocabulary.people_words()
    # Its other lists are shapes, axes, separators and kinds of file; `terms` and `avoid` name only
    # words of the lists above and pieces of them (another test holds both to the Dictionary).
    assert set(loaded["weights"]["word"]) | set(loaded["weights"]["phrase"]) <= set(SHAPES)
    assert set(loaded) == {
        "format",
        "version",
        "rewritable",
        "kinds",
        "terms",
        "avoid",
        "phrase_kind",
        "condition_sections",
        "weights",
        "slip",
        "code",
        "condition",
        "separators",
        "text_kinds",
    }


def test_an_experiment_with_a_reference_or_a_copy_crossing_it_keeps_its_names() -> None:
    before = _build("S", 0)
    after = copy.deepcopy(before)
    apply_vocabulary(after, fraction=1.0, seed=0, tier="S")
    experiments = {p for p, v in before.values["experiment_folder"].items() if v == "experiment"}

    def unit_of(path: str) -> str | None:
        while path:
            if path in experiments:
                return path
            path = path.rpartition("/")[0]
        return None

    crossing: set[str] = set()
    for table in (before.pairs, before.undecidable_pairs):
        for source, target in table.get("reference", set()):
            if unit_of(source) != unit_of(target):
                crossing.update(u for u in (unit_of(source), unit_of(target)) if u)
    assert crossing
    for unit in crossing:
        inside = [path for path in before.files if unit_of(path) == unit]
        assert inside
        assert all(path in after.files for path in inside), unit
        assert all(after.files[path] == before.files[path] for path in inside), unit


def test_a_script_or_note_inside_a_rewritten_experiment_still_names_the_files_it_names() -> None:
    before = _build("S", 0)
    after = copy.deepcopy(before)
    applied = apply_vocabulary(after, fraction=1.0, seed=0, tier="S")
    moved = applied.moved
    assert applied.summary["files_with_text_rewritten"] > 0

    checked = 0
    for source, target in sorted(before.pairs["reference"]):
        data = before.files.get(source, b"")
        if posixpath.basename(target).encode() not in data:
            continue
        new_source, new_target = moved.get(source, source), moved.get(target, target)
        assert posixpath.basename(new_target).encode() in after.files[new_source], (source, target)
        checked += moved.get(target, target) != target
    assert checked


def test_a_rewrite_moves_every_answer_with_its_path_and_keeps_identical_files_identical() -> None:
    before = _build("S", 0)
    after = copy.deepcopy(before)
    moved = apply_vocabulary(after, fraction=1.0, seed=0, tier="S").moved
    assert moved

    def at(path: str) -> str:
        return moved.get(path, path)

    for family, pairs in before.pairs.items():
        assert after.pairs[family] == {(at(a), at(b)) for a, b in pairs}, family
    for family, entries in before.values.items():
        assert after.values[family] == {at(path): value for path, value in entries.items()}, family
    assert after.groups == {
        family: [tuple(at(path) for path in members) for members in groups]
        for family, groups in before.groups.items()
    }

    def identical(drive: Drive) -> set[frozenset[str]]:
        by_bytes: dict[bytes, set[str]] = {}
        for path, data in drive.files.items():
            if data:
                by_bytes.setdefault(data, set()).add(path)
        return {frozenset(paths) for paths in by_bytes.values() if len(paths) > 1}

    assert identical(after) == {
        frozenset(at(path) for path in group) for group in identical(before)
    }
    assert len(after.files) == len(before.files)
    assert not set(moved.values()) & set(before.files) - set(moved)


def test_a_rewritten_collection_keeps_the_key_sound(tmp_path: Path) -> None:
    lab = _made(tmp_path, "sound", seed=1, vocabulary=1.0)
    # Every shape is in this small collection, and the key names a spelling made by each.
    assert set(lab.manifest["vocabulary"]["by_shape"]) == set(SHAPES)
    assert lab.key.words is not None
    assert {shape for meaning in lab.key.words.spellings.values() for shape in meaning.shapes} == {
        *SHAPES,
        "original",
    }
    present = set(_walk(lab.pool))
    present.update(
        p.relative_to(lab.pool).as_posix()
        for p in lab.pool.rglob("*")
        if p.is_dir() or p.is_symlink()
    )

    for family in lab.key.families.values():
        for path in family.paths():
            assert path in present, path
    experiments = lab.key.families["experiment_folder"]
    assert isinstance(experiments, ValueFamily)
    assert all(
        (lab.pool / path).is_dir()
        for path, value in experiments.expected.items()
        if value == "experiment"
    )


def test_the_words_family_reads_back_from_the_key_s_json(tmp_path: Path) -> None:
    lab = _made(tmp_path, "round", seed=0, vocabulary=0.5)
    loaded = load_key(lab.key_path)

    assert loaded.words == lab.key.words
    assert loaded.words is not None
    assert loaded.dumps().encode("utf-8") != b""
    assert (
        json.loads(lab.key_path.read_text(encoding="utf-8"))["words"]["describes"]["rewrites"] > 0
    )


def test_a_key_without_a_words_family_is_written_as_it_always_was() -> None:
    key = AnswerKey(families={})
    assert "words" not in key.to_json()
    with_words = AnswerKey(
        families={},
        words=WordsFamily(
            spellings={"tublin": WordMeaning("tubulin", "tubulin", "reagent", False, ("slip",))},
            same=(("tublin", "tubulin"),),
        ),
    )
    assert AnswerKey.from_json(with_words.to_json()) == with_words


def test_the_drive_gives_a_path_a_new_name_in_every_answer_that_named_it() -> None:
    moment = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
    drive = Drive(random=random.Random(0))
    drive.file("a/x one.csv", b"x one", moment, kind="csv")
    drive.file("a/y.py", b"opens a/x one.csv", moment, kind="python_script")
    drive.folder("a/empty", moment)
    drive.link("a/to old", "elsewhere", moment)
    drive.value("stage", "a/x one.csv", "analysis")
    drive.pair("reference", "a/y.py", "a/x one.csv")
    drive.group("group", ("a/x one.csv", "a/y.py"))
    drive.decline("a/x one.csv", "reference")
    drive.note("things", {"path": "a/x one.csv", "paths": ["a/x one.csv", "a/y.py"]})

    drive.rename(
        {"a/x one.csv": "a/x 1.csv", "a/empty": "a/hollow"}, {"a/y.py": b"opens a/x 1.csv"}
    )

    assert set(drive.files) == {"a/x 1.csv", "a/y.py"}
    assert drive.files["a/y.py"] == b"opens a/x 1.csv"
    assert drive.values["stage"] == {"a/x 1.csv": "analysis"}
    assert drive.pairs["reference"] == {("a/y.py", "a/x 1.csv")}
    assert drive.groups["group"] == [("a/x 1.csv", "a/y.py")]
    assert drive.declined["reference"] == {"a/x 1.csv"}
    assert drive.empty_folders == {"a/hollow"}
    assert "a/hollow/" in drive.times and "a/empty/" not in drive.times
    assert drive.unscored["things"] == [{"path": "a/x 1.csv", "paths": ["a/x 1.csv", "a/y.py"]}]
    assert drive.taken("A/X 1.CSV") and not drive.taken("a/x one.csv")


def test_the_datasheet_says_the_tier_was_applied_only_when_it_was(tmp_path: Path) -> None:
    on = _made(tmp_path, "on", seed=0, vocabulary=0.5)
    off = _made(tmp_path, "off", seed=0)

    sheet = on.datasheet_path.read_text(encoding="utf-8")
    assert "Was the vocabulary tier applied?" in sheet
    # A chance for each word of the experiments that can be rewritten, never "a share of the words".
    assert "chance of 0.5" in sheet and "left as they were" in sheet
    assert "a share of" not in sheet
    left = on.manifest["vocabulary"]["experiments_left_alone"]
    assert f"{left:,} of {on.manifest['vocabulary']['experiments']:,} experiment folders" in sheet
    assert "Was the vocabulary tier applied?" not in off.datasheet_path.read_text(encoding="utf-8")
    assert on.manifest["vocabulary"]["fraction"] == 0.5
    assert on.manifest["vocabulary"]["seed"] == 0


def _one_experiment_of_a_word(word: str) -> Drive:
    moment = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
    drive = Drive(random=random.Random(0))
    folder = f"Dana Whitfield/2021 {word} test"
    drive.file(f"{folder}/{word}_001.tif", b"x", moment, kind="tiff_image")
    drive.value("experiment_folder", folder, "experiment")
    return drive


def test_a_rewrite_that_changes_no_name_is_not_counted() -> None:
    """GFP has three letters: only a change of case can be drawn for it, and a case the names
    already have (capitals) is no change."""
    outcomes = []
    for seed in range(80):
        applied = apply_vocabulary(
            _one_experiment_of_a_word("GFP"), fraction=1.0, seed=seed, tier="S"
        )
        outcomes.append(applied)
        assert bool(applied.rewrites) == bool(applied.moved), seed
        assert applied.summary["rewrites"] == len(applied.rewrites)
        assert applied.summary["by_shape"] == {
            shape: sum(1 for rewrite in applied.rewrites if rewrite.shape == shape)
            for shape in SHAPES
            if any(rewrite.shape == shape for rewrite in applied.rewrites)
        }
        for rewrite in applied.rewrites:
            assert rewrite.shape == "case"
            assert all("GFP" not in new for new in applied.moved.values())
            assert applied.family.spellings["gfp"].shapes == ("case", "original")
    # Some draws changed the case, and some drew the capitals the names already had. The second kind
    # is not counted.
    assert any(applied.rewrites for applied in outcomes)
    assert any(
        not applied.rewrites and applied.summary["experiments_not_rewritten"] == 1
        for applied in outcomes
    )


def test_the_original_words_alone_are_the_tier_off_key(tmp_path: Path) -> None:
    plain = _made(tmp_path, "plain", seed=0)
    off = generate_sample_lab(
        tmp_path / "off", tier="S", seed=0, vocabulary=0.0, vocabulary_originals=True
    )

    # The pool, the manifest, the datasheet and the unscored sections are the default sample's.
    assert _walk(plain.pool) == _walk(off.pool)
    assert off.manifest_path.read_bytes() == plain.manifest_path.read_bytes()
    assert off.datasheet_path.read_bytes() == plain.datasheet_path.read_bytes()
    assert off.unscored_path.read_bytes() == plain.unscored_path.read_bytes()
    # The key differs by the words family alone (and the line saying so).
    assert off.key.families == plain.key.families
    assert off.key.words is not None and plain.key.words is None
    words_of_off = off.key.words
    assert words_of_off.same == ()
    assert words_of_off.spellings
    for spelling, meaning in words_of_off.spellings.items():
        assert meaning.shapes == ("original",), spelling
        assert meaning.means.casefold() == spelling
        assert meaning.parts == ()
    assert words_of_off.describes["originals_only"] is True
    assert off.key.describes["vocabulary"]["rewrites"] == 0
    assert dict(off.key.describes) != dict(plain.key.describes)


def test_the_original_words_of_the_tier_on_collection_are_the_tier_off_key_s_words(
    tmp_path: Path,
) -> None:
    on = _made(tmp_path, "on", seed=0, vocabulary=0.5)
    off = generate_sample_lab(
        tmp_path / "off", tier="S", seed=0, vocabulary=0.0, vocabulary_originals=True
    )
    assert on.key.words is not None and off.key.words is not None

    originals_on = {
        spelling: meaning
        for spelling, meaning in on.key.words.spellings.items()
        if "original" in meaning.shapes
    }
    # Every original word the tier-off names hold is a word the tier-on key also calls original,
    # with the same meaning; the tier-on pool holds fewer of them (some were rewritten).
    for spelling, meaning in originals_on.items():
        assert off.key.words.spellings[spelling].term == meaning.term
        assert off.key.words.spellings[spelling].theme_kind == meaning.theme_kind
    assert set(originals_on) <= set(off.key.words.spellings)


def test_the_originals_flag_is_ignored_when_the_tier_is_on(tmp_path: Path) -> None:
    plain_on = _made(tmp_path, "a", seed=0, vocabulary=0.5)
    flagged = generate_sample_lab(
        tmp_path / "b", tier="S", seed=0, vocabulary=0.5, vocabulary_originals=True
    )

    assert plain_on.key_path.read_bytes() == flagged.key_path.read_bytes()
    assert originals_only(_build("S", 0)).summary["rewrites"] == 0
