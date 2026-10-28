"""The workspace: what a person changes in the Dashboard, saved at once and committed on purpose.

Settings and changes are saved automatically, and committed manually or when the program closes.
The workspace has three parts, kept apart because they have different owners:

- Drafts (``drafts``) hold every edit to a setting or a rule parameter. Each is written to disk
  within a second of the keystroke, so a crash or a closed laptop loses nothing. A draft changes
  nothing the engine reads.
- A commit (``checkpoints``) applies the drafts. It lays the settings into the Dashboard's own file
  (``settings_overlay``) and appends a project Rulebook revision for the rule parameters. It then
  records one small append-only file that names what the project believed at that moment and what
  the commit needs run before the current run reflects it.
- A re-run (``rerun``, ``state``) is one list of every change waiting, counted on the Re-run button
  by kind. Nothing starts by itself.

A review answer is never a draft. It is a decision and goes to the Record at once. Staging
decisions until a commit would make the Dashboard the only holder of something important. A commit
does not group or hold decisions back. What an answer changes waits for Re-run, including a
definition: the decision is kept at once, and applied when a person chooses.

Restoring a checkpoint (``restore``) uses the ``withdraw`` Record event. A commit records
everything a restore reads.
"""

from __future__ import annotations

__all__ = ["WORKSPACE_DIR", "WORKSPACE_VERSION"]

#: Beside ``runs/`` and ``record/`` in a project. Operational state, not scientific truth:
#: losing it loses drafts and the checkpoint list, never a decision or a run.
WORKSPACE_DIR = "workspace"
WORKSPACE_VERSION = "1"
