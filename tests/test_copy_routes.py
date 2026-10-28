"""What the Dashboard's Copy buttons copy is the text the website's reference writes.

``GET /api/v1/rules/copy`` and ``GET /api/v1/dictionary/copy`` call the functions in
``ingence.copy_text`` that the reference's generator calls (``tools/docs/``), over the rule as the
project has it and the word as the project's Dictionary holds it. This proves it the way a reader
uses it: the text the route serves is compared with the generator's own output for the same rule
and the same word, and is then written into a revision file and a pack file, which the engine's own
loaders read.

Every word is a shipped word or rule, or an invented one in a test project.
"""

from __future__ import annotations

import html
import re
import sys
from functools import lru_cache
from http import HTTPStatus
from pathlib import Path
from typing import Any

import pytest
import yaml

from ingence.api.v1 import V1Context, dispatch
from ingence.core.project import Project
from ingence.dictionary.laboratory_packs import install_pack_text, load_named_packs
from ingence.rulebook import shipped_rulebook
from ingence.rulebook.load import load_project_revisions, load_revisions
from ingence.rulebook.predicates import load_builtins
from ingence.rulebook.render import value_in_force
from ingence.rulebook.resolve import effective_rulebook
from ingence.storage.layout import RULEBOOK_DIR

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
TOOLS = REPOSITORY_ROOT / "tools" / "docs"

#: A rule with settings, one with none, and a shipped word with several spellings.
WITH_SETTINGS = "earliest_credible_time"
WORD = "confocal"


def _context(project: Project) -> V1Context:
    return V1Context(project=project.path, run_current=None, run_pinned=None, job_running=False)


def _ask(project: Project, path: str, **query: str) -> tuple[HTTPStatus, dict[str, Any]]:
    return dispatch(_context(project), path, {name: [value] for name, value in query.items()})


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    base = tmp_path_factory.mktemp("copy_routes")
    pool = base / "pool"
    pool.mkdir()
    return Project.create(base / "project", created_at="t", roots=[pool])


def _without_settings() -> str:
    """The name of a shipped rule that takes no parameter."""
    for rule in shipped_rulebook().rules:
        if not rule.parameters and rule.status.value != "provisional":
            return str(rule.rule_id)
    pytest.skip("every shipped rule has settings")


# ---------------------------------------------------------------------------
# The same text as the website's generator
# ---------------------------------------------------------------------------


def _generator_sources() -> dict[str, str]:
    """The text of every Copy button on the generated catalogue page, by the button's source."""
    if not (TOOLS / "generate_reference.py").is_file():
        pytest.skip("the documentation generators are not part of this install")
    sys.path.insert(0, str(TOOLS))
    try:
        from generate_reference import _catalogue  # type: ignore[import-not-found]
    finally:
        sys.path.remove(str(TOOLS))
    (page,) = _catalogue([])
    return {
        found.group(1): html.unescape(found.group(2))
        for found in re.finditer(
            r'<pre id="([^"]+)"[^>]*><code>(.*?)</code></pre>', page.text, re.S
        )
    }


@pytest.fixture(scope="module")
def generated() -> dict[str, str]:
    return _generator_sources()


def test_a_rule_copies_the_text_the_generator_writes(
    project: Project, generated: dict[str, str]
) -> None:
    status, copied = _ask(project, "rules/copy", rule=WITH_SETTINGS)
    assert status == HTTPStatus.OK
    assert copied["rule_id"] == WITH_SETTINGS
    assert copied["values"] == generated[f"y-r-{WITH_SETTINGS}-set"]
    assert copied["switch_off"] == generated[f"y-r-{WITH_SETTINGS}-off"]
    # Copy as a revision copies the rule with its values where it has any.
    assert copied["revision"] == copied["values"]
    assert copied["provisional"] is False


def test_a_rule_with_no_settings_copies_as_switched_off(
    project: Project, generated: dict[str, str]
) -> None:
    named = _without_settings()
    status, copied = _ask(project, "rules/copy", rule=named)
    assert status == HTTPStatus.OK
    assert copied["values"] is None
    assert copied["switch_off"] == generated[f"y-r-{named}-off"]
    assert copied["revision"] == copied["switch_off"] == f"  - disable: {named}"


def test_a_word_copies_the_text_the_generator_writes(
    project: Project, generated: dict[str, str]
) -> None:
    status, copied = _ask(project, "dictionary/copy", term=WORD)
    assert status == HTTPStatus.OK
    assert copied["term"] == WORD
    assert copied["entries"]
    written = {text for key, text in generated.items() if key.startswith("y-w-")}
    for entry in copied["entries"]:
        assert entry["text"] in written
    assert copied["text"] == "\n".join(entry["text"] for entry in copied["entries"])


def test_the_route_refuses_in_words_what_it_cannot_copy(project: Project) -> None:
    status, refused = _ask(project, "rules/copy", rule="no_such_rule")
    assert status == HTTPStatus.NOT_FOUND
    assert "no_such_rule" in refused["error"]["message"]
    status, refused = _ask(project, "rules/copy")
    assert status == HTTPStatus.BAD_REQUEST
    assert refused["error"]["parameter"] == "rule"
    status, refused = _ask(project, "dictionary/copy", term="no-such-word")
    assert status == HTTPStatus.NOT_FOUND
    status, refused = _ask(project, "dictionary/copy")
    assert status == HTTPStatus.BAD_REQUEST
    assert refused["error"]["parameter"] == "term"


# ---------------------------------------------------------------------------
# The copied text loads
# ---------------------------------------------------------------------------


def _revision_file(project: Project, number: int, changes: list[str]) -> None:
    folder = project.path / RULEBOOK_DIR
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"project-{number:04d}.yaml").write_text(
        "\n".join(
            [
                f"project_revision: {number}",
                "summary: Changes copied from the Dashboard.",
                "changes:",
                *changes,
                "",
            ]
        ),
        encoding="utf-8",
    )


@lru_cache(maxsize=1)
def _shipped_revisions() -> Any:
    """The shipped revisions, read once: reading them is the slow part of composing the rules."""
    load_builtins()
    return load_revisions()


def _composed(project: Project) -> Any:
    return effective_rulebook(
        _shipped_revisions(), project=load_project_revisions(project.path / RULEBOOK_DIR)
    )


def test_the_copied_revision_loads_as_a_project_revision(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    pool.mkdir()
    made = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    named = _without_settings()
    _, with_values = _ask(made, "rules/copy", rule=WITH_SETTINGS)
    _, without = _ask(made, "rules/copy", rule=named)

    _revision_file(made, 1, [with_values["revision"]])
    book = _composed(made)
    assert {rule.rule_id for rule in book.rules} >= {WITH_SETTINGS}
    shipped = shipped_rulebook().by_id(WITH_SETTINGS)
    got = book.by_id(WITH_SETTINGS)
    assert got is not None and shipped is not None
    assert [value_in_force(spec) for spec in got.parameters] == [
        value_in_force(spec) for spec in shipped.parameters
    ]

    # The same rule, switched off, leaves the rules in force and says why.
    _revision_file(made, 2, [without["revision"]])
    book = _composed(made)
    assert named not in {rule.rule_id for rule in book.rules}
    assert dict(book.inactive)[named].startswith("disabled at project revision")


def test_a_rule_whose_values_were_changed_copies_the_values_in_force(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    pool.mkdir()
    made = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    _revision_file(
        made,
        1,
        [
            f"  - override: {WITH_SETTINGS}",
            "    parameters:",
            "      earliest_credible_year: 2001",
        ],
    )
    _, copied = _ask(made, "rules/copy", rule=WITH_SETTINGS)
    document = yaml.safe_load(f"changes:\n{copied['values']}\n")
    assert document["changes"][0]["parameters"]["earliest_credible_year"] == 2001


def _pack_file(entries: list[str], name: str = "my-laboratory") -> str:
    return "\n".join(
        [
            "pack:",
            f"  name: {name}",
            '  version: "1"',
            "  author: Dana Whitfield",
            "  licence: MIT",
            f"section: {name}",
            "theme_kind: technique",
            "summary: Words copied from the Dashboard.",
            "entries:",
            *entries,
            "",
        ]
    )


def test_the_copied_word_loads_as_a_pack_entry(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    pool.mkdir()
    made = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    _, copied = _ask(made, "dictionary/copy", term=WORD)
    install_pack_text(made.path, "my-laboratory.yaml", _pack_file([copied["text"]]))
    _, sections = load_named_packs(made.path, ("my-laboratory",))
    read = [sense for section in sections for sense in section.senses]
    assert {sense.term for sense in read} == {WORD}
    shipped = {
        spelling
        for entry in copied["entries"]
        for spelling in yaml.safe_load(entry["text"])[0]["surfaces"]
    }
    assert {spelling for sense in read for spelling in sense.surfaces} == shipped


def test_a_word_a_person_taught_copies_as_a_pack_entry_too(tmp_path: Path) -> None:
    from ingence.api.routes import dictionary as dictionary_routes
    from ingence.scan.census import run_census
    from ingence.storage.catalog import open_catalog

    pool = tmp_path / "pool"
    (pool / "2024-03-05 HeLa DAPI").mkdir(parents=True)
    (pool / "2024-03-05 HeLa DAPI" / "HeLa_DAPI_01.csv").write_text("a\n1\n")
    made = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(made, run_id="run_20260101T000001Z_111111111111", started_at="2026-01-01T00:00:01Z")
    with open_catalog(made.path) as catalog:
        dictionary_routes.write(
            catalog,
            made.path,
            {
                "actor": "Dana Whitfield",
                "dictionary": "add",
                "spelling": "HeLaG",
                "term": "HeLaS3",
                "theme_kind": "reagent",
            },
            occurred_at="2026-01-02T00:00:00Z",
        )
        listed = dictionary_routes.listing(catalog, made.path, text="HeLaS3")
        shipped = dictionary_routes.listing(catalog, made.path, text="confocal")
    (row,) = listed["rows"]
    # Who taught it is in the listing, and only a person's own word has one.
    assert row["yours"] is True
    assert row["taught"]["by"] == "Dana Whitfield"
    assert shipped["rows"]
    assert all(each["taught"] is None for each in shipped["rows"])

    status, copied = _ask(made, "dictionary/copy", term="HeLaS3")
    assert status == HTTPStatus.OK
    assert [entry["origin"] for entry in copied["entries"]] == ["yours"]
    install_pack_text(made.path, "my-laboratory.yaml", _pack_file([copied["text"]]))
    _, sections = load_named_packs(made.path, ("my-laboratory",))
    read = [sense for section in sections for sense in section.senses]
    assert [(sense.term, tuple(sense.surfaces)) for sense in read] == [("HeLaS3", ("HeLaG",))]


# ---------------------------------------------------------------------------
# A rule a newer one replaced offers nothing to copy
# ---------------------------------------------------------------------------


def _declared_rule_ids() -> list[str]:
    """Every rule any shipped revision declares: in force, switched off, or replaced."""
    return sorted({rule.rule_id for revision in load_revisions() for rule in revision.rules})


def test_a_replaced_rule_offers_no_text_the_engine_would_refuse(project: Project) -> None:
    from ingence.rulebook import shipped_rulebook

    replaced = [rule_id for rule_id, why in shipped_rulebook().inactive if "superseded" in why]
    assert replaced, "the shipped rules replace some rule"
    for rule_id in replaced:
        status, copied = _ask(project, "rules/copy", rule=rule_id)
        assert status == HTTPStatus.OK, rule_id
        assert copied["replaced"] is True, rule_id
        assert copied["revision"] is copied["values"] is copied["switch_off"] is None, rule_id
        assert copied["file_text"] is copied["file_name"] is copied["next_revision"] is None


def test_every_copy_the_route_offers_loads_as_a_project_revision(tmp_path: Path) -> None:
    """The Rulebook refuses a change to a rule not in force, and one refused file makes every rule
    of the project unreadable: so each text the route offers, for every rule there is, is written
    into a revision file and composed with the engine's own code."""
    pool = tmp_path / "pool"
    pool.mkdir()
    made = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    offered = 0
    for rule_id in _declared_rule_ids():
        status, copied = _ask(made, "rules/copy", rule=rule_id)
        assert status == HTTPStatus.OK, rule_id
        for kind in ("values", "switch_off"):
            if copied[kind] is None:
                continue
            _revision_file(made, 1, [copied[kind]])
            try:
                _composed(made)
            except Exception as error:  # the engine's refusal, named with the rule
                pytest.fail(f"{rule_id}: the copied {kind} is refused: {error}")
            offered += 1
    assert offered > 100


# ---------------------------------------------------------------------------
# The whole file that switches a rule off
# ---------------------------------------------------------------------------


def test_the_switch_off_file_is_numbered_past_the_files_the_project_has(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    pool.mkdir()
    made = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    named = _without_settings()
    # With no file yet, the first number.
    _, fresh = _ask(made, "rules/copy", rule=named)
    assert fresh["next_revision"] == 1
    assert fresh["file_name"] == "project-0001.yaml"
    # A project that already has revision 1 (every commit of a setting writes one): the next is 2.
    _revision_file(
        made,
        1,
        [f"  - override: {WITH_SETTINGS}", "    parameters:", "      earliest_credible_year: 2001"],
    )
    _, copied = _ask(made, "rules/copy", rule=named)
    assert copied["next_revision"] == 2
    assert copied["file_name"] == "project-0002.yaml"
    assert copied["file_text"].startswith("project_revision: 2\n")
    assert copied["file_text"].rstrip().endswith(copied["switch_off"])
    # Saved as shown, under the name shown, the project still reads, and the rule is out.
    (made.path / RULEBOOK_DIR / copied["file_name"]).write_text(
        copied["file_text"], encoding="utf-8"
    )
    book = _composed(made)
    assert named not in {rule.rule_id for rule in book.rules}
    assert dict(book.inactive)[named] == "disabled at project revision 2"
    # The first file's own change still stands.
    got = book.by_id(WITH_SETTINGS)
    assert got is not None
    assert {spec.name: value_in_force(spec) for spec in got.parameters}[
        "earliest_credible_year"
    ] == 2001
    # Numbered again, the next is 3.
    _, again = _ask(made, "rules/copy", rule=_other_rule_without_settings(named))
    assert again["next_revision"] == 3


def _other_rule_without_settings(not_this: str) -> str:
    for rule in shipped_rulebook().rules:
        if not rule.parameters and rule.status.value != "provisional" and rule.rule_id != not_this:
            return str(rule.rule_id)
    pytest.skip("only one shipped rule has no settings")


def test_no_file_is_offered_where_the_project_s_rules_cannot_be_read(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    pool.mkdir()
    made = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    folder = made.path / RULEBOOK_DIR
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "project-0001.yaml").write_text("project_revision: one\nchanges: nonsense\n")
    _, copied = _ask(made, "rules/copy", rule=_without_settings())
    # The switch-off text stands, and no numbered file is offered against rules nobody can read.
    assert copied["switch_off"]
    assert copied["next_revision"] is copied["file_name"] is copied["file_text"] is None


# ---------------------------------------------------------------------------
# A word's copy leaves out what is not vocabulary
# ---------------------------------------------------------------------------


def _spelled(text: str) -> set[str]:
    """Every spelling the entries of a copied text hold."""
    return {spelling for item in yaml.safe_load(text) for spelling in item["surfaces"]}


def test_a_taught_word_copies_without_bookkeeping_or_what_was_inactivated(tmp_path: Path) -> None:
    from ingence.api.routes import dictionary as dictionary_routes
    from ingence.scan.census import run_census
    from ingence.storage.catalog import open_catalog

    pool = tmp_path / "pool"
    (pool / "2024-03-05 HeLa DAPI").mkdir(parents=True)
    (pool / "2024-03-05 HeLa DAPI" / "HeLa_DAPI_01.csv").write_text("a\n1\n")
    made = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(made, run_id="run_20260101T000001Z_111111111111", started_at="2026-01-01T00:00:01Z")

    def act(**body: str) -> None:
        with open_catalog(made.path) as catalog:
            dictionary_routes.write(
                catalog,
                made.path,
                {"actor": "Dana Whitfield", **body},
                occurred_at="2026-01-02T00:00:00Z",
            )

    act(dictionary="add", spelling="HeLaG", term="HeLaS3", theme_kind="reagent")
    act(dictionary="add", spelling="HeLaR", term="HeLaS3")
    _, whole = _ask(made, "dictionary/copy", term="HeLaS3")
    assert "raised_by" not in whole["text"] and "defined" not in whole["text"]
    assert _spelled(whole["text"]) == {"HeLaG", "HeLaR"}

    act(dictionary="inactivate", term="HeLaS3", spelling="HeLaR")
    _, part = _ask(made, "dictionary/copy", term="HeLaS3")
    assert _spelled(part["text"]) == {"HeLaG"}

    act(dictionary="inactivate", term="HeLaS3")
    status, gone = _ask(made, "dictionary/copy", term="HeLaS3")
    assert status == HTTPStatus.OK
    assert gone["entries"] == [] and gone["text"] == ""
