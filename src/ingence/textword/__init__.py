"""Word questions: what the names a folder was written from say about a word nothing binds.

This package holds one corpus statistic over names, one that needs no model at all:

- :mod:`ingence.textword.slots` aligns the names of one folder that were written from one template
  and lists, for each place in the template, every word written there (a *paradigm*: the words that
  stand instead of one another in one place);
- :mod:`ingence.textword.questions` turns the places where words of one kind stand beside a word
  nothing binds into one question per word, every place that word stands gathered beneath it,
  with the engine's own reading of where it stands.

The package never makes a binding, a sense, a claim or a change to a threshold, and no language
model writes any of its output. A statistic here is computed by engine code from the Index,
deterministically, and shipped as the engine's own reading. It may decide what a person is asked
and in what order, never what a word means. The flagging rule that raises the words is
:mod:`ingence.rulebook.predicates.substitution`, and a person's answer is the Record's.
"""

from __future__ import annotations

__all__ = ["TEXTWORD_VERSION"]

#: Bumped whenever any reading here could change for unchanged input.
TEXTWORD_VERSION = "1"
