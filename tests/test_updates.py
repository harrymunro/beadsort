from __future__ import annotations

from beadsort.beads import Bead, Comment
from beadsort.updates import collect_updates, parse_notes


def test_dated_entries_with_authors() -> None:
    notes = (
        "2026-08-20 (Scriba, session 3): chaser drafted, not yet sent.\n"
        "2026-09-05 (Notarius, session 9): Mike replied: export received."
    )
    entries = parse_notes(notes)
    assert [e.date for e in entries] == ["2026-08-20", "2026-09-05"]
    assert [e.author for e in entries] == ["Scriba", "Notarius"]
    assert entries[1].text == "Mike replied: export received."


def test_filed_by_line_and_continuations() -> None:
    notes = (
        "Filed by Notarius, session 2, 2026-08-10, on Harry's instruction.\n"
        "Draft outline agreed with the on-call lead.\n"
        "No date on this line either."
    )
    entries = parse_notes(notes)
    assert len(entries) == 1
    assert entries[0].date == "2026-08-10"
    assert entries[0].author == "Notarius"
    assert "No date on this line either." in entries[0].text


def test_undated_first_line_is_its_own_entry_and_sorts_oldest() -> None:
    bead = Bead(
        id="x",
        title="T",
        notes="Some context without a date.\n2026-09-01 (Scriba): later note.",
        comments=(Comment(text="a comment", author="harry", created_at="2026-09-03T10:00:00Z"),),
    )
    updates = collect_updates(bead)
    assert [u.source for u in updates] == ["comment", "note", "note"]
    assert updates[0].date == "2026-09-03"
    assert updates[-1].date is None


def test_newest_first_ties_prefer_later_position() -> None:
    bead = Bead(id="x", title="T", notes="2026-09-01: first\n2026-09-01: second")
    updates = collect_updates(bead)
    assert updates[0].text == "second"


def test_caps() -> None:
    bead = Bead(
        id="x", title="T", notes="\n".join(f"2026-09-{i:02d}: " + "x" * 50 for i in range(1, 10))
    )
    updates = collect_updates(bead, max_updates=3, max_chars=20)
    assert len(updates) == 3
    assert updates[0].date == "2026-09-09"
    assert updates[0].text.endswith("[trimmed]")
    assert len(updates[0].text) <= 20
