"""The append-only log of human decisions.

The log holds ordered, scoped events. Each later inference reads the decisions it needs from the
log, so a rescan cannot erase a decision. A decision whose basis has vanished becomes orphaned and
visible. It is never silently dropped.

``log.py`` is the write path. It writes one fragment per event, named for its sequence, claims it
under an exclusive lock and makes it visible only once whole. ``questions.py`` reads back a decision
about a raised term. :mod:`ingence.dictionary.answers` turns that decision into senses that the
binder consults, so an answer takes effect on the next index. ``workflow.py`` reads a declared
workflow back the same way.

Orphanhood is concluded and never stored. ``RecordEvent.orphaned`` says that a decision's basis has
vanished. An append-only log cannot flip a flag on a row it has written, so the field stays false.
``questions.orphaned_answers`` is the reader that says which in-force answers name a rule the
Rulebook no longer holds. An orphaned decision is reported and still honoured. A person's word about
what a term means stays true when somebody switches off the rule that prompted them to say it.

Replaying the whole Record over an inference is not built. The code reads the decisions that one
consumer needs, such as a term's meaning or a declared workflow, where that consumer needs them. A
pass that walks every decision in order and says what each one did to the run it was replayed over
does not exist yet.
"""
