"""Turn a bead's notes and comments into one dated list, newest first.

Notes accumulate over months as newline-separated entries that usually lead with a date
and an author in brackets: `2026-09-07 (Notarius, session 6): ...`. The newest entry is
the current truth and often says an ask has already been satisfied. The model cannot order
dates reliably, so ordering happens here and every question that cares points at
`bead.latest_update.text` by name.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

from beadsort.beads import Bead

_DATE = r"(\d{4}-\d{2}-\d{2})"
_LEAD_RE = re.compile(rf"^\s*(?:NOTES?:\s*)?{_DATE}(?:T[\d:]+Z?)?\s*(?:\(([^)]*)\))?\s*[:\-]?\s*")
_ANY_DATE_RE = re.compile(_DATE)
_FILED_BY_RE = re.compile(r"\bfiled by\s+([A-Z][\w-]*)", re.IGNORECASE)

TRIM_MARK = " [trimmed]"


@dataclass(frozen=True)
class Update:
    date: str | None
    source: str  # "note" | "comment"
    author: str | None
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_notes(notes: str) -> list[Update]:
    """Split a notes field into entries in file order (oldest first is the usual convention)."""
    entries: list[Update] = []
    for raw in notes.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _LEAD_RE.match(line)
        if match:
            date = match.group(1)
            author = (match.group(2) or "").split(",")[0].strip() or None
            text = line[match.end() :].strip() or line
            entries.append(Update(date, "note", author, text))
            continue
        anywhere = _ANY_DATE_RE.search(line)
        if anywhere is None and entries:
            last = entries[-1]
            entries[-1] = Update(last.date, last.source, last.author, f"{last.text}\n{line}")
            continue
        author = None
        filed = _FILED_BY_RE.search(line)
        if filed:
            author = filed.group(1)
        entries.append(Update(anywhere.group(1) if anywhere else None, "note", author, line))
    return entries


def _comment_updates(bead: Bead) -> list[Update]:
    out: list[Update] = []
    for comment in bead.comments:
        date = (comment.created_at or "")[:10] or None
        if date and not _ANY_DATE_RE.fullmatch(date):
            date = None
        out.append(Update(date, "comment", comment.author, comment.text.strip()))
    return out


def _trim(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: max(limit - len(TRIM_MARK), 0)].rstrip() + TRIM_MARK


def collect_updates(
    bead: Bead,
    *,
    max_updates: int = 6,
    max_chars: int = 1200,
) -> list[Update]:
    """All updates for a bead, newest first, capped in count and per-entry length.

    Ordering key: date descending, then original position descending, so an undated
    fragment sorts as older than any dated entry and later fragments beat earlier ones.
    """
    combined: list[Update] = parse_notes(bead.notes) + _comment_updates(bead)
    indexed = list(enumerate(combined))
    indexed.sort(key=lambda pair: (pair[1].date or "0000-00-00", pair[0]), reverse=True)
    ordered = [Update(u.date, u.source, u.author, _trim(u.text, max_chars)) for _, u in indexed]
    if max_updates > 0:
        ordered = ordered[:max_updates]
    return ordered


def updates_to_dicts(updates: Iterable[Update]) -> list[dict[str, Any]]:
    return [u.to_dict() for u in updates]
