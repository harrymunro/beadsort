"""Build the JSON state the model sees for one bead.

Only what the enabled packs need goes in. Labels are never sent (they would leak the
model's own previous output and any ground truth), nor are dates (the model cannot compare
them) or the assignee. Notes and comments become `updates`, newest first, with the newest
duplicated as `latest_update` so questions can point at it by name. Trimming is
deterministic so the cache key is stable.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from beadsort.beads import Bead, BeadIndex
from beadsort.config import BeadsortConfig
from beadsort.updates import TRIM_MARK, collect_updates, updates_to_dicts

EPIC_DESCRIPTION_CHARS = 2000
EPIC_UPDATES = 3


def _cut(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: max(limit - len(TRIM_MARK), 0)].rstrip() + TRIM_MARK


def _excerpt(text: str, limit: int) -> str:
    return _cut(" ".join(text.split()), limit)


def state_size(state: dict[str, Any]) -> int:
    return len(json.dumps(state, ensure_ascii=False))


def build_state(
    bead: Bead,
    index: BeadIndex,
    config: BeadsortConfig,
    needs: Iterable[str] = ("parent",),
) -> dict[str, Any]:
    needs = set(needs)
    limits = config.state
    desc_limit = limits.max_description_chars
    max_updates = limits.max_updates
    if bead.is_epic:
        desc_limit = min(desc_limit, EPIC_DESCRIPTION_CHARS)
        max_updates = min(max_updates, EPIC_UPDATES)

    updates = updates_to_dicts(
        collect_updates(bead, max_updates=max_updates, max_chars=limits.max_update_chars)
    )
    bead_obj: dict[str, Any] = {
        "id": bead.id,
        "title": bead.title,
        "description": _cut(bead.description, desc_limit),
        "acceptance_criteria": _cut(bead.acceptance_criteria, desc_limit) or None,
        "design": _cut(bead.design, desc_limit) or None,
        "status": bead.status,
        "priority": bead.priority,
        "issue_type": bead.issue_type,
        "latest_update": updates[0] if updates else None,
        "updates": updates,
    }
    state: dict[str, Any] = {"project": config.project_brief(), "bead": bead_obj}

    parent = index.parent_of(bead) if "parent" in needs else None
    state["parent"] = (
        {
            "id": parent.id,
            "title": parent.title,
            "description_excerpt": _excerpt(parent.description, limits.parent_excerpt_chars),
            "status": parent.status,
        }
        if parent
        else None
    )
    if "open_blockers" in needs:
        state["open_blockers"] = [
            {"id": b.id, "title": b.title, "status": b.status}
            for b in index.open_blockers(bead)[: limits.max_blockers]
        ]
    if "children" in needs and bead.is_epic:
        state["children"] = [
            {"id": c.id, "title": c.title, "status": c.status}
            for c in index.children_of(bead)[: limits.max_children]
        ]
    return _fit(state, limits.max_total_chars)


def _fit(state: dict[str, Any], budget: int) -> dict[str, Any]:
    """Trim in a fixed order until the JSON fits: children, oldest updates, description,
    design and acceptance criteria, blockers, parent excerpt. Each step is deterministic."""
    if budget <= 0 or state_size(state) <= budget:
        return state
    if state.get("children"):
        state["children"] = []
        if state_size(state) <= budget:
            return state
    bead = state["bead"]
    while len(bead["updates"]) > 1 and state_size(state) > budget:
        bead["updates"] = bead["updates"][:-1]
    if state_size(state) <= budget:
        return state
    overflow = state_size(state) - budget
    desc = bead["description"]
    if len(desc) > 400:
        bead["description"] = _cut(desc, max(400, len(desc) - overflow))
    if state_size(state) <= budget:
        return state
    for key in ("design", "acceptance_criteria"):
        text = bead.get(key) or ""
        if len(text) > 400:
            overflow = state_size(state) - budget
            bead[key] = _cut(text, max(400, len(text) - overflow))
    if state_size(state) <= budget:
        return state
    if state.get("open_blockers"):
        state["open_blockers"] = state["open_blockers"][:1]
    if state_size(state) <= budget:
        return state
    if state.get("parent"):
        state["parent"]["description_excerpt"] = _cut(state["parent"]["description_excerpt"], 120)
    if state_size(state) <= budget:
        return state
    if bead["updates"]:
        bead["updates"] = [bead["updates"][0]]
        bead["latest_update"] = bead["updates"][0]
        overflow = state_size(state) - budget
        if overflow > 0:
            text = bead["latest_update"]["text"]
            trimmed = _cut(text, max(200, len(text) - overflow))
            bead["latest_update"] = {**bead["latest_update"], "text": trimmed}
            bead["updates"] = [bead["latest_update"]]
    for key in ("description", "design", "acceptance_criteria"):
        if state_size(state) <= budget:
            break
        text = bead.get(key) or ""
        if len(text) > 100:
            overflow = state_size(state) - budget
            bead[key] = _cut(text, max(100, len(text) - overflow))
    return state
