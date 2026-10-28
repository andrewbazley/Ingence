"""What a suffix must be to name a kind of file: one rule, read by the census and the harvester.

This module imports only the standard library, because the extractor path imports it and must not
import pydantic. The census decides what a name's extension is. The mention harvester decides
whether a string in a file's bytes is a path at all. The two must agree about what an extension
is. If they disagree, the harvester reads ``Comp-FL1-A/1.0000`` in a cytometry workspace as a
relative path with the extension ``0000``. Thousands of such strings crowd the real data
references out of a file's bounded list and then resolve to nothing, which reads as a missing
file.
"""

from __future__ import annotations

__all__ = ["is_extension"]


def is_extension(suffix: str) -> bool:
    """Whether a suffix, given without its dot, names a kind of file at all.

    An extension is letters and digits, and never digits alone. A digits-only suffix is a version
    or the decimals of a number. Without this rule, ``epipred2.0`` has the extension ``0``,
    ``python3.7`` has ``7``, a compiler named ``...darwin13.4.0-ar`` has ``0-ar``, and a
    chromatogram written ``...framegap=20`` has ``framegap=20``. The engine would look each one up
    as a format, count it among the extensions it has no knowledge of, and ask a person about it.

    Any other character is punctuation that no file type is spelled with. Examples are an equals
    sign, a hyphen, an underscore, a space and a tilde. A refused suffix keeps its name and its
    position. It loses only the claim that its last dot introduced a type.
    """
    return bool(suffix) and suffix.isalnum() and not suffix.isdigit()
