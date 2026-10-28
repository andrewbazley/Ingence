"""A charge or a sign at the end of a word stays with it: ``Ca2+``, ``Fe3+``, ``Xyz+``.

A charge suffix stays part of its atom. If the ``+`` were a separator (one of the characters
``Normaliser._scan`` cuts at), ``Ca2+`` would read as ``Ca2``, and the Dictionary's ``ca2+`` would
be reached only by a loose match, which any rule with a match-quality floor refuses. A sign also
carries a meaning that a divider throws away: a word with a ``+`` and the same word are not the
same thing.

The rule is a grammar, not a vocabulary. A trailing sign is:

- one or two ``+``, or a digit and a ``+`` (``Ca2+``, ``Fe++``), directly after a letter or a digit,
- and followed by a separator, a dot or the end of the name, never by a letter or a digit:
  ``Xy+Zw`` is a mixture of two things and its ``+`` stays the divider it was.

The sign becomes part of the word before it (``Ca2+`` is one word, normalised ``ca2+``), so the
binder meets the Dictionary's own spelling exactly. This grammar takes no trailing minus. A hyphen
is also the commonest joiner and divider in a name. So a trailing minus is a sign only after a
word that the Dictionary marks ``signed: true`` (``GFP-``), which the
:class:`~ingence.dictionary.normalize.Normaliser` reads, and a divider everywhere else.

Known limit: ``Xyz+Abc+`` is read as ``Xyz`` and ``Abc+``, because the first sign is followed by a
letter, so it divides. Reading a sign between two names needs the Dictionary to say that the left
side is a marker.
"""

from __future__ import annotations

import re

__all__ = ["TRAILING_SIGN", "trailing_sign_spans"]

#: A word (a letter, then letters and digits) with a trailing sign, followed by a separator, a dot
#: or the end. The separators are the normaliser's own, less ``+`` itself, which is the sign. The
#: extension is split off before a name is read.
TRAILING_SIGN = re.compile(
    r"(?<![A-Za-z0-9])"
    r"([A-Za-z][A-Za-z0-9]*?)"
    r"(\d?\+{1,2})"
    r"(?=$|[\s_\-,;:()\[\]{}/\\|@#&'\".])"
)


def trailing_sign_spans(name: str) -> list[tuple[int, int, str]]:
    """Every word-with-a-sign in a name, as (start, end, normalised), left to right.

    The normaliser reserves each span before it divides the name, as it reserves dates and compound
    units, and emits it as one word atom whose normalised form is the span casefolded.
    """
    return [
        (match.start(), match.end(), match.group(0).casefold())
        for match in TRAILING_SIGN.finditer(name)
    ]
