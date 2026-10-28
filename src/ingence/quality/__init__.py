"""Quality figures every run carries, comparable across runs.

The figures make runs comparable, in numbers simple enough to quote in a paper or on the website.
A run carries a small table, ``quality_figures``: counts of what it found, read, understood of
names, concluded and cost, each with the whole it is part of, each named in a catalogue that
states its method once. A figure is only what the engine counts exactly from what it stored. A
figure a pass does not reach is "not run", and one the engine does not record is "not
recorded", never zero.

- ``catalogue``: what each figure is (``catalogue.yaml`` beside the module).
- ``registry``: the registry of summaries; others register theirs there (its docstring is the
  recipe).
- ``core``: the core's summary.
- ``publish``: writing a staged run's figures, and reading them back.
- ``compare``: a run's figures beside another's, as sentences.
- ``export``: a run's, or every run's, figures as CSV and JSON for a paper.

Imports here are lazy so the engine starts without a summary's package.
"""

from __future__ import annotations

from ingence.quality.registry import SUMMARIES, Summary

__all__ = ["SUMMARIES", "Summary"]
