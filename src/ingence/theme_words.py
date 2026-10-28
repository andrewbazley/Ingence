"""Why themes cannot run, in a person's words.

A module of its own, with nothing but the standard library, so that every place that has to say
"themes are not available" (the Dashboard's routes, completeness, ``ingence export``) can say it
the same way without importing the themes component, which is what is missing.
"""

from __future__ import annotations

import re

__all__ = ["unavailable_in_words"]

#: The name a person installs each import name under.
_PACKAGE_OF = {"sklearn": "scikit-learn"}


def unavailable_in_words(error: BaseException) -> str:
    """Why themes cannot run, for a person: what is missing and how to get it.

    The registry's own message names the component and the import (``theme_engine 'lsa_louvain'
    needs sklearn, scipy, which is not installed``); a person needs the packages by the names
    they install under, and the command.
    """
    text = str(error)
    missing = re.search(r"needs (.+?), which (?:is|are) not installed", text)
    if missing:
        names = [_PACKAGE_OF.get(n.strip(), n.strip()) for n in missing.group(1).split(",")]
        listed = " and ".join(names)
        return (
            f"themes need {listed}, which {'is' if len(names) == 1 else 'are'} not installed here. "
            f"Install Ingence's themes support, in the environment that runs Ingence, with: "
            f"python -m pip install {' '.join(names)}"
        )
    if "disabled" in text:
        return "themes are switched off in this installation"
    return f"themes could not start here ({text})"
