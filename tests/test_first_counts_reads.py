"""What the first tabs' counts read, read as before, in a fraction of the time.

The classes: the first Questions page, and so the first tab counts, read every name of the
census against every spelling of a file nobody chose to make, one spelling at a time; and the
claims page ranked every file of every value, sorted twice, to show ten of each at most. Read
against the whole set of spellings at once, each name gets the same answer; read lazily, folder
by folder, a value's files come in the same order. Every name is invented.
"""

from __future__ import annotations

import random

from ingence.textword.slots import spelled_like, spelling_test

_SPELLINGS = ("Thumbs.db", "._", ".part", ".DS_Store", "~$", ".tmp", "", "desktop.ini")


def _one_at_a_time(name: str, spellings: tuple[str, ...]) -> bool:
    """The reading as it was written before: each spelling in turn."""
    for surface in spellings:
        if not surface:
            continue
        if name == surface:
            return True
        if len(name) > len(surface) and (name.startswith(surface) or name.endswith(surface)):
            return True
    return False


def test_the_whole_set_at_once_answers_as_each_spelling_in_turn() -> None:
    rng = random.Random(34)
    pieces = ("kelt", "vorn_001", ".tif", "._", ".part", "~$", "Thumbs.db", ".tmp", "zeph", "")
    names = [*_SPELLINGS, "._", ".", "Thumbs.dbx", "xThumbs.db", "part", "a.part", ".partx"]
    names += ["".join(rng.choice(pieces) for _ in range(rng.randint(1, 4))) for _ in range(5000)]
    test = spelling_test(_SPELLINGS)
    for name in names:
        expected = _one_at_a_time(name, _SPELLINGS)
        assert test(name) is expected, name
        assert spelled_like(name, _SPELLINGS) is expected, name
    assert spelling_test(())("Thumbs.db") is False
    assert spelling_test(("",))("anything") is False


def _spread_by_sorting(members: list[dict[str, str]]) -> list[dict[str, str]]:
    """The claims sample's round-robin as written before: every member ranked, then sorted."""
    ranks: dict[str, int] = {}
    ranked = []
    for member in sorted(
        members, key=lambda item: (item["folder"], item["name"], item["claim_id"])
    ):
        rank = ranks.get(member["folder"], 0)
        ranked.append((rank, member["folder"], member["name"], member["claim_id"], member))
        ranks[member["folder"]] = rank + 1
    return [item[-1] for item in sorted(ranked, key=lambda item: item[:4])]


def test_a_value_s_files_are_read_round_robin_over_folders_only_as_far_as_its_sample_reads() -> (
    None
):
    """The claims sample read every value's files whole, sorted twice, to show ten at most:
    read lazily, folder by folder, it gives the same files in the same order."""
    from ingence.api.routes.pages import _Spread

    rng = random.Random(7)
    for _ in range(200):
        members = [
            {
                "folder": rng.choice(("kelt", "kelt/raw", "vorn", "", "zeph/b")),
                "name": f"{rng.choice(('brisa', 'quolt', 'sorn'))}_{rng.randint(1, 30):03d}.csv",
                "claim_id": f"claim_{number:04d}",
            }
            for number in rng.sample(range(10_000), rng.randint(1, 60))
        ]
        expected = _spread_by_sorting(members)
        spread = _Spread(members)
        assert len(spread) == len(expected)
        assert [spread[index] for index in range(len(expected))] == expected
    lazy = _Spread(
        [{"folder": "kelt", "name": f"z_{n:03d}", "claim_id": str(n)} for n in range(500)]
    )
    assert lazy[0]["name"] == "z_000"
    assert lazy.read == 1  # one file handed out, not five hundred ranked
