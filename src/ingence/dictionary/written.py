"""A word shown as the files wrote it, never in the parts a name was cut into.

Why: a spelling the Dictionary holds in parts (``u 2 os``, ``hek 293t``, ``cos 7``, ``wide field``:
a multi-part spelling binds the hyphen, underscore, space and flush forms of the name alike) is read
by the name reader as one word whose value is the parts. Without this module, the words a group is
made of would show in those parts, and a person reading "dapi u 2 os" would see a word they never
wrote.

What it shows: the way the run's own files wrote the word, which is the raw spelling the most
files hold (``U2OS``, ``HEK293T``, ``COS-7``, ``widefield``). It never shows the Dictionary's name
for the sense. The sense's name is a synonym that the files do not hold (``widefield`` is not
``epifluorescence``, ``HEK293T`` is not ``HEK293``), and a page that asked about
``epifluorescence`` would show a word nobody wrote.

What it leaves: the parts stay the word's value. A group's key, the words a file is found by and
every binding do not change, so no file binds differently and no group forms or re-keys. Only the
text a page shows is read through :func:`written_forms`. A word with one part, a word whose raw
spelling is its parts, and a word the run did not bind are never touched.

Read from the run's own components and bindings; nothing is written.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ingence.storage.catalog import Catalog

__all__ = ["written_forms", "written_terms", "written_text", "written_word"]

#: Each multi-part word a sense won, with every raw spelling the run's names hold of it and how
#: many files hold each. A component is one per file and distinct value, so the count is files.
_FORMS = """
SELECT lower(c.normalized_value) AS parts, c.raw_value AS raw,
       count(DISTINCT c.component_id) AS files
FROM components c
JOIN dictionary_bindings b ON b.component_id = c.component_id
WHERE c.component_type = 'word' AND c.source_scope = 'name'
  AND b.resolution_status = 'resolved' AND c.normalized_value LIKE '% %'
  AND c.raw_value IS NOT NULL AND c.raw_value <> ''
GROUP BY 1, 2
ORDER BY parts, files DESC, raw
"""


#: A Dictionary term, and every raw spelling the run's names write it in. A group made of terms
#: (``groups.words_as``) is shown in the spelling most files hold, never in the Dictionary's name
#: for the sense.
_TERM_FORMS = """
SELECT lower(b.term) AS term, c.raw_value AS raw, count(DISTINCT c.component_id) AS files
FROM components c
JOIN dictionary_bindings b ON b.component_id = c.component_id
WHERE c.component_type = 'word' AND c.source_scope = 'name'
  AND b.resolution_status = 'resolved' AND b.term IS NOT NULL
  AND c.raw_value IS NOT NULL AND c.raw_value <> ''
GROUP BY 1, 2
ORDER BY term, files DESC, raw
"""


def written_forms(catalog: Catalog) -> dict[str, str]:
    """Each multi-part word the run bound to a sense, to the way its files wrote it.

    Keyed by the word as the name reader cut it, in lower case. The spelling is the raw one the
    most files hold, with an underscore between its parts shown as a space; a tie goes to the
    first in character order. A word its files wrote as its parts have no entry.
    """
    try:
        rows = catalog.query(_FORMS).fetchall()
    except Exception:  # a run without bindings or components has no word to write again
        return {}
    found: dict[str, str] = {}
    for parts, raw, _ in rows:
        found.setdefault(str(parts), str(raw).replace("_", " "))
    return {parts: written for parts, written in found.items() if written != parts}


def written_terms(catalog: Catalog) -> dict[str, str]:
    """Each Dictionary term the run's names write, to the way the most files wrote it.

    A group made of terms (``groups.words_as``) holds ``nuclear_stain`` and ``u2os``, which no file
    wrote. The page shows it as ``DAPI`` and ``U2OS``, the raw spelling the most files hold (an
    underscore shown as a space, a tie to the first in character order). It never shows the
    Dictionary's name for the sense. The keys are terms in lower case, and a term written as itself
    has no entry. This function is separate from :func:`written_forms`, whose keys are the parts of
    a spelling. A term is the lattice's word, and a page that shows a spelling's own text must not
    read one as the other.
    """
    try:
        rows = catalog.query(_TERM_FORMS).fetchall()
    except Exception:  # a run whose bindings carry no term
        return {}
    found: dict[str, str] = {}
    for term, raw, _ in rows:
        found.setdefault(str(term), str(raw).replace("_", " "))
    return {term: written for term, written in found.items() if written != term}


def written_word(word: str, forms: Mapping[str, str]) -> str:
    """One word as its files wrote it, or as it is where it is not a multi-part spelling."""
    return forms.get(word.casefold(), word)


def written_text(text: str, forms: Mapping[str, str]) -> str:
    """A text with each multi-part word in it written as its files wrote it.

    A word is found whole, never inside another (``u 2 os`` is not found in ``u 2 osmo``), the
    longest first, and whatever case the text writes it in.
    """
    if not forms or " " not in text:
        return text
    ordered = sorted(forms, key=lambda parts: (-len(parts), parts))
    pattern = re.compile(
        r"(?<![0-9A-Za-z])("
        + "|".join(re.escape(parts) for parts in ordered)
        + r")(?![0-9A-Za-z])",
        re.IGNORECASE,
    )
    return pattern.sub(lambda found: forms[found.group(1).casefold()], text)
