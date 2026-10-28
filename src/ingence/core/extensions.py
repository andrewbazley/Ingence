"""One spelling for a file extension, wherever one is written.

The Index stores an extension lower-cased and without its dot (``tif``, and ``tar.gz`` as the
compound). Every rule that reads the extension slot compares that form. People write extensions in
other ways: ``.TIF`` in a settings file, ``tar.gz`` in a sheet, ``*.csv`` from habit. If each
boundary compared a value as it was given, the same intent would behave differently by notation.
A hash filter naming ``.bin`` would match no file, because the census's ``bin`` is not the string
``.bin``. The processing fingerprint would look for the extractors claiming ``.csv`` among
extensions spelled ``csv`` and find none.

Every boundary passes what it was given through :func:`canonical_extension` and compares in that
one form. The boundaries are settings' include and exclude tuples, reuse's policy, the dive's
filters, the extractors' declared claims as reuse reads them, the Record's extension targets and
the Dashboard's parameters.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable

__all__ = ["canonical_extension", "canonical_extensions"]


def canonical_extension(value: str) -> str:
    """Return the one form of an extension: lower-cased, NFC, without a leading dot or ``*``.

    ``.TIF``, ``tif``, ``TIF`` and ``*.tif`` are ``tif``. ``.TAR.GZ`` and ``tar.gz`` are
    ``tar.gz``. The function casefolds, as the census folds a name's suffix, so the two agree on
    every spelling the census can store. An empty or all-dot value is the empty string, which is
    how the Index spells "no extension".
    """
    spelled = unicodedata.normalize("NFC", value).strip().casefold()
    spelled = spelled.removeprefix("*")
    return spelled.lstrip(".").strip()


def canonical_extensions(values: Iterable[str]) -> tuple[str, ...]:
    """Return a collection of extensions in the one form, without repeats or blanks, sorted.

    The tuple is sorted because a filter is a set and the tuple is hashed into a configuration
    digest. Two settings files that name the same extensions in a different order or notation are
    one policy.
    """
    return tuple(sorted({spelled for spelled in map(canonical_extension, values) if spelled}))
