"""Scientific evaluation: a held-out answer key, and the engine's figures against it.

Two halves. The public half is here: :mod:`~ingence.evaluation.generate` writes a pool of
invented files with the shapes laboratories have and the answer key a person would write for it,
and :mod:`~ingence.evaluation.score` runs the engine over it and counts, family by family, what it
concluded against the key. The private half is the same scorer over a real folder a laboratory
indexed, against a key a person wrote in the same format (:mod:`~ingence.evaluation.key`); which
folders, and how much of a person's time a key may take, are that laboratory's decisions.

No single score. Every figure is a count, and every proportion carries its Wilson interval
(the convention ``ingence rule show -p`` uses). Abstention is counted apart from error,
because an unknown is not a wrong.
Nothing here is a probability.

A third piece is for sharing (:mod:`~ingence.evaluation.sample_lab`): the sample
collection, an invented laboratory at three sizes. It is a shared drive over several years with
several people, written from a tier and a seed and nothing else, with its answer key, a manifest
and a datasheet beside it. It is built to be archived and shared (CC0), to teach with, and to
measure speed and memory on. It is never a target to tune a rule against.

Never read by the engine. Indexing and inference never open a key. The scorer reads a run
through the engine's own storage after it is published.
"""

from __future__ import annotations

__all__: list[str] = []
