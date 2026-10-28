"""Closing the program from a terminal asks every time, and remembers nothing.

Every close asks the user. The program remembers no choice and saves no default for committing. So
there is no "always", no "never", no preferences file, and nothing about the answer is written
anywhere: the next close asks again.

A browser tab cannot ask this. A closing page may only trigger the browser's own generic "Leave
site?" box. So the question lives where the program closes: ``ingence see`` stopped with Ctrl-C.
An answer that cannot be read is asked again, three times, and then the drafts stay drafts. That
loses nothing, because the drafts are on disk and the next session opens with them.

A second Ctrl-C cannot stop the commit halfway. The commit writes the settings first and its record
last, so while it is written Ctrl-C is set aside. It is put back as it was the moment the commit
is done.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import signal
import threading
from typing import TYPE_CHECKING

from ingence.workspace.checkpoints import commit
from ingence.workspace.drafts import uncommitted

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

__all__ = ["ASKS", "QUESTION", "ask_before_closing"]

#: Asked after a line saying how many changes are saved but not committed, with ``{them}`` as
#: "it" or "them". The changes can be days old, so the question names them, not the day.
QUESTION = "Commit {them} before closing? [y]es / [n]o: "
#: How many times an unreadable answer is asked again before the drafts are left as drafts.
ASKS = 3


@contextlib.contextmanager
def _ctrl_c_set_aside() -> Iterator[None]:
    """Ignore Ctrl-C while the block runs, and put back what it did before.

    Only the main thread may change what a signal does; elsewhere the block runs as it is.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    before = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, before if before is not None else signal.SIG_DFL)


def ask_before_closing(
    project: Path,
    *,
    actor: str,
    ask: Callable[[str], str] = input,
    say: Callable[[str], None] = print,
    interactive: bool = True,
) -> str:
    """Ask whether to commit the uncommitted changes; return ``committed``, ``kept`` or ``none``.

    ``none``: nothing was uncommitted, so nothing was asked. ``kept``: the drafts stay drafts
    (the answer was no, unreadable, or there is no name to commit under). ``interactive`` is
    False where nobody can type an answer (no terminal): nothing is asked, the drafts stay, and
    one line says so.
    """
    count = sum(uncommitted(project).values())
    if not count:
        return "none"
    one = count == 1
    them, they = ("it", "it stays") if one else ("them", "they stay")
    saved = f"{count} {'change is' if one else 'changes are'} saved but not committed."
    if not interactive:
        say(
            f"{saved} With no terminal to answer in, nothing was asked: {they} saved, and the "
            f"next session opens with {them}."
        )
        return "kept"
    say(saved)
    for _ in range(ASKS):
        try:
            answer = ask(QUESTION.format(them=them)).strip().lower()
        except (EOFError, KeyboardInterrupt):
            break
        if answer in ("y", "yes"):
            if not actor:
                say(
                    "Nobody is named as deciding on this machine, so nothing was committed; "
                    f"{they} saved."
                )
                return "kept"
            today = dt.datetime.now().astimezone().strftime("%B %d, %Y").replace(" 0", " ")
            try:
                with _ctrl_c_set_aside():
                    commit(project, actor=actor, message=f"Changes of {today}")
            except ValueError as refused:
                # A value the engine would not accept is said, never a traceback.
                say(
                    f"Nothing was committed: {refused}. {they.capitalize()} saved; correct "
                    f"{them} in Settings, on the Engine page, and commit there."
                )
                return "kept"
            # Nothing starts when the next session opens. Re-run applies the commit.
            say("Committed. It waits for Re-run.")
            return "committed"
        if answer in ("n", "no"):
            break
        say("Please answer y or n.")
    say(f"{they.capitalize()} saved but not committed, and the next session opens with {them}.")
    return "kept"
