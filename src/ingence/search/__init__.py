"""Search over what a run already understood: experiments, not every file that matches.

A filesystem search answers "where does this string occur", and on a laboratory server that is
thousands of rows. This search answers "which experiments are about this". A run has already read
every name, bound its words to senses, grouped files into classes and placed them in folders. The
search reads those tables and nothing else.

The package has nine modules:

- ``query``      what a person typed, read with the engine's own tokeniser
- ``expand``     each typed word as a *concept*: its sense, its spellings, its variants, its pieces
- ``projection`` the derived tables a search reads, built from a published run, deletable
- ``scale``      what kind of unit each folder is (experiment, analysis, program), which unit
                 every entry belongs to, and which files are products
- ``units``      rolling matches up to their unit, nesting, folding, capping products
- ``rank``       ordering units by a tuple of counts, never a probability
- ``tags``       the chips a result card shows: conditions, technique, replicates, stages, products
- ``hidden``     "remove this result from similar searches", kept and reversible
- ``engine``     the one function a route calls, and the "why this result" lines

A result is the *unit* that a match belongs to: an experiment first, then an analysis folder, a
final-stage product file or an installed program. A search never returns a flood of files. A unit's
contents stay closed until a person asks. A person can hide a result from similar searches. The
search box lives on the Explore page.

The search opens no indexed file. It concludes nothing and writes nothing into a run, because a
search result is a rendering, like the order of the Review queue. It needs no network. DuckDB's
full-text extension is not used, because installing it downloads it.
"""

from __future__ import annotations

__all__ = ["PROJECTION_VERSION"]

#: The version of the projection's tables. It changes whenever the tables change shape or meaning.
#: The search rebuilds a projection built by an older version and never reads it, because a derived
#: table that outlives the code that reads it would return stale results. Version 4 stores each
#: unit's words, for the words that come with a query.
PROJECTION_VERSION = "4"
