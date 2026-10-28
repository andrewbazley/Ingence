"""Content extractors, one per format family, reachable only through the registry.

Every extractor registers itself and is individually switchable. Nothing imports one by name.
Removing an extractor is deleting a module and a registration line.

An extractor is given a context and returns readings. It never constructs a
record, never writes anything, never executes anything it finds, and never
raises to signal an ordinary outcome. See :mod:`ingence.extractors.base` for the
contract and the reasons behind each of those.
"""

from __future__ import annotations

__all__ = ["load_builtins"]


def load_builtins() -> None:
    """Import the modules that register the built-in extractors.

    The imports are explicit. Importing the modules at this module's top level
    would reintroduce the coupling the registry exists to prevent, and one
    missing optional dependency would then break the whole package rather than
    one extractor.

    Removing an extractor is deleting its module and its line here. That is why
    nothing else in the engine may name an extractor.
    """
    from ingence.extractors import archive as _archive  # noqa: F401
    from ingence.extractors import compound as _compound  # noqa: F401
    from ingence.extractors import document as _document  # noqa: F401
    from ingence.extractors import generic as _generic  # noqa: F401
    from ingence.extractors import image_header as _image_header  # noqa: F401
    from ingence.extractors import keywords as _keywords  # noqa: F401
    from ingence.extractors import lambert_fli as _lambert_fli  # noqa: F401
    from ingence.extractors import leica_lif as _leica_lif  # noqa: F401
    from ingence.extractors import nikon_nd2 as _nikon_nd2  # noqa: F401
    from ingence.extractors import notebook as _notebook  # noqa: F401
    from ingence.extractors import office as _office  # noqa: F401
    from ingence.extractors import script_calls as _script_calls  # noqa: F401
    from ingence.extractors import spreadsheet as _spreadsheet  # noqa: F401
    from ingence.extractors import tabular as _tabular  # noqa: F401
    from ingence.extractors import text as _text  # noqa: F401
    from ingence.extractors import tiff as _tiff  # noqa: F401
