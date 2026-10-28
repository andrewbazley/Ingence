"""The Dashboard's shell, read from the page's own files.

The last tab is "Advanced", with "Engine" as its first group holding Rules, Dictionary and
Settings. Every old address still lands. The rules are "Rules", never "Rules in force". The
browser half is ``test_browser_shell_import_export.py``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from ingence.api.server import WEB_ROOT

PAGE = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
APP = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
WORKSPACE = (WEB_ROOT / "workspace.js").read_text(encoding="utf-8")
CATALOG = json.loads((WEB_ROOT / "help" / "catalog.json").read_text(encoding="utf-8"))


def test_advanced_is_the_last_of_the_six_tabs() -> None:
    # Engine's sections are Advanced's first group, under the small heading "Engine".
    pages = re.findall(r'<button data-page="(\w+)"', PAGE)
    assert pages == ["overview", "overlap", "experiments", "checks", "history", "advanced"]
    assert re.search(r'<button data-page="advanced"[^>]*>Advanced</button>', PAGE)
    assert ">Parameters<" not in PAGE


def test_engine_holds_rules_dictionary_and_settings_in_that_order() -> None:
    engine = re.search(
        r'aria-labelledby="sections-engine">(.*?)</span>\s*<span class="section-group"',
        PAGE,
        re.DOTALL,
    )
    assert engine is not None
    views = re.findall(r'data-view="(\w+)"', engine.group(1))
    # The Ladders section joins Engine after Settings. Its label is Folder levels.
    assert views == ["rules", "dictionary", "settings", "ladders"]
    labels = re.findall(r'data-view="\w+"[^>]*>([^<]+)</button>', engine.group(1))
    assert labels == ["Rules", "Dictionary", "Settings", "Folder levels"]


def test_the_old_addresses_are_named_as_aliases() -> None:
    aliases = re.search(r"const VIEW_ALIASES = \{(.*?)\};", APP, re.DOTALL)
    assert aliases is not None
    assert "parameters: 'rules'" in aliases.group(1)
    assert "PAGES.engine.push('settings')" in (WEB_ROOT / "settings.js").read_text("utf-8")
    assert "parameters" not in re.search(r"const PAGES = \{(.*?)\n\};", APP, re.DOTALL).group(1)


def test_rules_in_force_is_said_nowhere() -> None:
    """Nowhere a person reads it: no page string, no help entry (comments may name the past)."""
    said = re.compile(r"""['">`][^'"`<\n]*[Rr]ules in force""")
    for script in (
        "index.html",
        "app.js",
        "review.js",
        "rules.js",
        "workspace.js",
        "help/catalog.json",
    ):
        assert not said.search((WEB_ROOT / script).read_text(encoding="utf-8")), script


def test_the_engine_page_and_its_sections_have_words_in_the_catalog() -> None:
    entries = CATALOG["entries"]
    # The Engine page's sections are Advanced's, and the old pages have no words.
    assert entries["page.advanced"]["title"] == "Advanced"
    assert not {"page.explore", "page.graph", "page.questions", "page.engine"} & set(entries)
    assert "page.parameters" not in entries
    assert entries["section.rules"]["title"] == "Rules"
    assert entries["section.dictionary"]["title"] == "Dictionary"
    # The first visit's tour walks the Overview, not the pages.
    assert set(CATALOG["tours"]["first_visit"]) <= set(entries)


# -- the Rules page: one way to show a rule -----------------------------------------------------

RULE_WORDS = __import__("ingence.api.plain_words", fromlist=["plain_words"])


def test_no_plain_sentence_names_its_rule_in_brackets() -> None:
    """A plain sentence states the rule in English and never ends with the rule in parentheses.

    The name and version are the handle that opens the rule, never a tail."""
    for rule_id, sentence in RULE_WORDS.plain_words().rules.items():
        assert f"({rule_id})" not in sentence, rule_id
        assert sentence.endswith("."), rule_id


def test_the_container_sentence_says_an_archive_link_is_never_opened() -> None:
    """A container holds what opening it found, and an archive link is never opened.

    Each ``embeds`` carries why opening stopped. An archive link is characterised from its text
    and never opened, so the sentence does not say it holds what opening found."""
    said = RULE_WORDS.plain_words().rules["cascade.container_member"]
    assert "holds only what Ingence found when it opened it" in said
    assert "Ingence notes whether opening finished" in said
    assert "never opens it" in said and "the record" not in said.lower()


@pytest.fixture(scope="module")
def switched_off(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from fixtures.revision_4 import write_revision
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census
    from ingence.storage.layout import RULEBOOK_DIR

    base = tmp_path_factory.mktemp("rules-inactive")
    pool = base / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "vorn_raw_1.csv").write_text("a,b\n1,2\n")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    # Switched off after the run: the run used it, and the next run will not.
    (project.path / RULEBOOK_DIR / "02-off.yaml").write_text(
        yaml.safe_dump(
            {
                "project_revision": 2,
                "summary": "Off.",
                "changes": [{"disable": "pipeline_stage_from_name"}],
            }
        ),
        encoding="utf-8",
    )
    return project.path


def test_an_inactive_rule_is_listed_apart_with_its_reason(switched_off: Path) -> None:
    from ingence.api.routes import rules as rule_routes
    from ingence.storage.catalog import open_catalog

    with open_catalog(switched_off) as catalog:
        shown = rule_routes.rules(catalog, switched_off)
    assert shown["revisions_unreadable"] is None
    listed = {rule["rule_id"] for section in shown["sections"] for rule in section["rules"]}
    assert "pipeline_stage_from_name" not in listed
    inactive = {rule["rule_id"]: rule for rule in shown["inactive"]}
    # Switched off after the run that used it.
    stage = inactive["pipeline_stage_from_name"]
    assert (stage["reason"], stage["in_this_run"]) == (
        "switched off in your project's rules",
        True,
    )
    assert stage["plain"] and stage["version"]
    # Replaced by a newer rule of the engine's before the run, and so never in it. The reason is
    # in words, never the Rulebook's bookkeeping.
    replaced = inactive["format_from_extension"]
    assert (replaced["reason"], replaced["in_this_run"]) == ("replaced by a newer rule", False)
    assert replaced["plain"] and replaced["version"]


def test_every_control_the_new_scripts_name_has_words_in_the_catalog() -> None:
    keys: set[str] = set()
    for script in ("settings.js", "transfer.js", "app.js"):
        path = WEB_ROOT / script
        if path.is_file():
            keys |= set(re.findall(r"'data-help': '([\w.]+)'", path.read_text(encoding="utf-8")))
    assert keys <= set(CATALOG["entries"]), keys - set(CATALOG["entries"])
