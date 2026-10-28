"""What the Dashboard says is in American spelling and names the pages it has.

Two things a reader of the Dashboard would trip on are held at once for everything the page's own
code can show (the strings of its JavaScript, the words of its two HTML pages and the help's
catalog):

1. No British spelling ("analysed", "colour", "neighbourhood", "organise", "behaviour" and the other
   words the pattern names). Field names, routes and keys of the engine keep their spelling
   (``analysed_elsewhere``, ``/api/reorganisation``, ``section.neighbourhood``): a word that carries
   an underscore, a slash, a dot or a capital inside it is a name, not prose, and is not read.
2. No page named that is not there. Pages and sections the navigation no longer has (Parameters,
   Review, Claims, Candidates, Classes, Structure) are not named in a string as a page, tab, section
   or view, and a string that names "the X section of Y" names a section X that Y has.

No browser is needed: the strings are read with the reader of JavaScript in
``fixtures/page_text_readers.py``, which leaves comments out.
"""

from __future__ import annotations

import json
import re

from fixtures.page_text_readers import _own_scripts, _walk, string_literals
from ingence.api.server import WEB_ROOT

#: British forms and the American form each stands for. Spelled out, so a new one is one line.
#: ``-ise`` verbs are listed by stem, because "raise", "rise" and "otherwise" are not British.
_STEMS = [
    "organis",
    "recognis",
    "summaris",
    "normalis",
    "realis",
    "initialis",
    "visualis",
    "customis",
    "minimis",
    "maximis",
    "finalis",
    "optimis",
    "prioritis",
    "categoris",
    "characteris",
    "standardis",
    "synchronis",
    "serialis",
    "utilis",
    "specialis",
    "generalis",
    "authoris",
    "capitalis",
    "criticis",
    "memoris",
    "randomis",
    "materialis",
    "centralis",
    "localis",
    "digitis",
    "neutralis",
    "stabilis",
    "harmonis",
    "mobilis",
    "legalis",
    "apologis",
    "sanitis",
    "modernis",
]
_ENDINGS = "(?:e|es|ed|ing|er|ers|ation|ations)"
BRITISH = re.compile(
    "(?i)^(?:"
    + "|".join(f"{stem}{_ENDINGS}" for stem in _STEMS)
    # "analyses" is the plural of "analysis" in American spelling too: only these three are British.
    + r"|analyse|analysed|analysing"
    + r"|colour\w*|behaviour\w*|neighbour\w*|favour\w*|honour\w*|labour\w*|flavour\w*|harbour\w*"
    + r"|rumour\w*|vapour\w*"
    + r"|centres?|centred|centring|recentre\w*|catalogues?|licences?|greys?|programmes?"
    + r"|judgement\w*|acknowledgement\w*|ageing|whilst|amongst|learnt|spelt|metres?|litres?"
    + r"|fibres?|defence|offence|practised?|sceptic\w*|labelled|labelling|modelled|modelling"
    + r"|travelled|travelling|aluminium|mould\w*|plough\w*|storey|kerb|draught|tyres?"
    + r"|sulphur\w*|paediatric\w*|oestro\w*|haem\w*|foetus|anaemi\w*"
    + ")$"
)

#: A page or section the Dashboard once had, named now as one. "Years view" is current; "Review
#: page" is not.
RETIRED = re.compile(
    r"\b(?:Review|Parameters|Claims|Candidates|Classes|Structure)\s+(?:page|tab|section|view)\b"
)
SECTION_OF = re.compile(
    r"\b(?:the\s+)?([A-Z][A-Za-z]*(?:\s[A-Z][a-z]+)*)\s+section\s+of\s+"
    r"(Explore|Graph|Questions|History|Engine|Overview|Overlap|Experiments|Checks|Advanced)\b"
)
#: The pages of before, as the page that holds their sections now. A string that names
#: a section "of Questions" is checked against Advanced, where Questions' sections are.
PAGE_NOW = {
    "explore": "experiments",
    "graph": "advanced",
    "questions": "advanced",
    "engine": "advanced",
}


def _prose_words(text: str) -> list[str]:
    """The words of a string that are prose: not a name (an underscore, a slash, a dot, a digit, a
    capital inside it, an equals sign) and with the punctuation around them taken off."""
    words = []
    for token in text.split():
        word = token.strip("()[]{}\"'`,;:.!?*<>|+-\u2019\u2018\u201c\u201d")
        if not word or re.search(r"[_/.=#\\@0-9]|[a-z][A-Z]", word):
            continue
        words.append(word)
    return words


def _british(text: str) -> list[str]:
    return [word for word in _prose_words(text) if BRITISH.match(word)]


def _html_text(name: str) -> list[str]:
    """The words a page shows: its text between tags and its placeholder, title and label
    attributes; comments left out."""
    html = re.sub(r"<!--.*?-->", "", (WEB_ROOT / name).read_text(encoding="utf-8"), flags=re.DOTALL)
    return re.findall(r">([^<>]+)<", html) + re.findall(
        r'(?:placeholder|title|aria-label|alt)="([^"]*)"', html
    )


def _catalog_words() -> list[str]:
    catalog = json.loads((WEB_ROOT / "help" / "catalog.json").read_text(encoding="utf-8"))
    return [text for _, text in _walk(catalog)]


def _everything_it_can_show() -> list[tuple[str, str]]:
    """Every string the page's code can put on screen, with where it is."""
    found: list[tuple[str, str]] = []
    for path in _own_scripts():
        where = path.relative_to(WEB_ROOT).as_posix()
        found += [
            (f"{where}:{line}", text) for line, text in string_literals(path.read_text("utf-8"))
        ]
    for page in ("index.html", "landing.html"):
        found += [(page, text) for text in _html_text(page)]
    found += [("help/catalog.json", text) for text in _catalog_words()]
    return found


def test_the_pattern_finds_british_words_and_leaves_names_and_american_words() -> None:
    for british in ("analysed", "Colour", "neighbourhood", "organise", "behaviour", "recentre"):
        assert BRITISH.match(british), british
    for american in (
        "analyzed",
        "analyses",
        "color",
        "neighborhood",
        "organize",
        "raise",
        "rise",
        "otherwise",
        "center",
        "licensed",
        "analysis",
        "recognize",
        "promise",
    ):
        assert not BRITISH.match(american), american
    assert _british("analysed_elsewhere /api/reorganisation section.neighbourhood") == []
    assert _british("This was analysed, in colour.") == ["analysed", "colour"]


def test_no_string_the_page_can_show_is_spelled_the_british_way() -> None:
    strings = _everything_it_can_show()
    assert len(strings) > 5000, "the reader read almost nothing"
    hits = [
        f"{where}: {word!r} in {text[:80]!r}" for where, text in strings for word in _british(text)
    ]
    assert hits == [], "\n".join(hits)


def _navigation() -> tuple[list[str], dict[str, set[str]]]:
    """The page names and, for each page, the names of its sections, from index.html."""
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    pages = re.findall(r'<button data-page="[a-z]+"[^>]*>([^<]+)</button>', html)
    sections: dict[str, set[str]] = {}
    for page, block in re.findall(r'data-sections-of="([a-z]+)"[^>]*>(.*?)</nav>', html, re.DOTALL):
        sections[page] = set(re.findall(r'<button data-view="[a-z]+"[^>]*>([^<]+)</button>', block))
    return pages, sections


def test_the_navigation_has_the_six_sections_this_test_checks_names_against() -> None:
    # Six sections. The Overview is one of them, so it is not in RETIRED.
    pages, sections = _navigation()
    assert pages == ["Overview", "Overlap", "Experiments", "Checks", "History", "Advanced"]
    assert "Collections" in sections["experiments"] and "Word groups" in sections["advanced"]
    assert "Rules" in sections["advanced"] and "Programs" in sections["advanced"]


def test_no_string_names_a_page_the_dashboard_no_longer_has() -> None:
    hits = [
        f"{where}: {found.group(0)!r} in {text[:80]!r}"
        for where, text in _everything_it_can_show()
        for found in [RETIRED.search(text)]
        if found
    ]
    assert hits == [], "\n".join(hits)


def test_every_section_a_string_names_is_a_section_of_the_page_it_names() -> None:
    _, sections = _navigation()
    checked = 0
    wrong = []
    for where, text in _everything_it_can_show():
        for found in SECTION_OF.finditer(text):
            checked += 1
            section, page = found.group(1), found.group(2).lower()
            page = PAGE_NOW.get(page, page)
            if section not in sections[page]:
                wrong.append(
                    f"{where}: {found.group(0)!r} (the {page} page has {sorted(sections[page])})"
                )
    assert checked >= 4, "no string names a section; the pattern has stopped matching"
    assert wrong == [], "\n".join(wrong)
