"""The bead model: one frozen shape for the two JSON shapes bd emits, plus label helpers.

`bd export` and `bd list --json` give dependencies as flat links
(`{issue_id, depends_on_id, type}`) and no `parent` key. `bd show --json` expands each
dependency into the target bead (`{id, title, ..., dependency_type}`) and adds `parent`.
`Bead.from_record` accepts either.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

#: The fields that carry a bead's meaning. A change in any of these invalidates a cached
#: classification. Deliberately excludes updated_at, counts and dependency bookkeeping.
MATERIAL_FIELDS = ("title", "description", "design", "acceptance_criteria", "notes")

#: Dependency types that make the dependent bead wait. `parent-child` is hierarchy, not
#: blocking: bd ready hands out children of open epics.
BLOCKING_DEP_TYPES = frozenset({"blocks", "conditional-blocks", "waits-for"})

#: Statuses that mean "this bead is finished" for the purpose of resolving blockers.
DONE_STATUSES = frozenset({"closed", "scrapped", "done"})

DIMENSION_RE = re.compile(r"^[a-z][a-z0-9-]*$")
LABEL_VALUE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


@dataclass(frozen=True)
class Dependency:
    depends_on_id: str
    type: str


@dataclass(frozen=True)
class Comment:
    text: str
    author: str | None = None
    created_at: str | None = None


@dataclass(frozen=True)
class Bead:
    id: str
    title: str
    description: str = ""
    notes: str = ""
    acceptance_criteria: str = ""
    design: str = ""
    status: str = "open"
    priority: int | None = None
    issue_type: str = "task"
    assignee: str | None = None
    labels: tuple[str, ...] = ()
    dependencies: tuple[Dependency, ...] = ()
    comments: tuple[Comment, ...] = ()
    created_at: str | None = None
    updated_at: str | None = None
    closed_at: str | None = None
    close_reason: str | None = None
    parent_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_record(cls, obj: Mapping[str, Any]) -> Bead:
        deps: list[Dependency] = []
        for raw in obj.get("dependencies") or []:
            if not isinstance(raw, Mapping):
                continue
            target = raw.get("depends_on_id") or raw.get("id")
            dep_type = raw.get("type") or raw.get("dependency_type")
            if target and dep_type:
                deps.append(Dependency(str(target), str(dep_type)))
        parent = obj.get("parent") or None
        if not parent:
            for dep in deps:
                if dep.type == "parent-child":
                    parent = dep.depends_on_id
                    break
        comments: list[Comment] = []
        for raw in obj.get("comments") or []:
            if isinstance(raw, Mapping) and raw.get("text"):
                comments.append(
                    Comment(
                        text=str(raw["text"]),
                        author=raw.get("author"),
                        created_at=raw.get("created_at"),
                    )
                )
        priority = obj.get("priority")
        try:
            priority = int(priority) if priority is not None else None
        except (TypeError, ValueError):
            priority = None
        metadata = obj.get("metadata")
        if not isinstance(metadata, Mapping):
            metadata = {}
        return cls(
            id=str(obj["id"]),
            title=str(obj.get("title") or ""),
            description=str(obj.get("description") or ""),
            notes=str(obj.get("notes") or ""),
            acceptance_criteria=str(obj.get("acceptance_criteria") or ""),
            design=str(obj.get("design") or ""),
            status=str(obj.get("status") or "open"),
            priority=priority,
            issue_type=str(obj.get("issue_type") or "task"),
            assignee=obj.get("assignee") or None,
            labels=tuple(str(x) for x in (obj.get("labels") or [])),
            dependencies=tuple(deps),
            comments=tuple(comments),
            created_at=obj.get("created_at"),
            updated_at=obj.get("updated_at"),
            closed_at=obj.get("closed_at"),
            close_reason=obj.get("close_reason"),
            parent_id=str(parent) if parent else None,
            metadata=dict(metadata),
        )

    @property
    def is_epic(self) -> bool:
        return self.issue_type == "epic"

    def content_hash(self) -> str:
        """Stable digest of the material fields plus comment texts, whitespace-normalised."""
        parts = []
        for name in MATERIAL_FIELDS:
            parts.append(re.sub(r"\s+", " ", getattr(self, name) or "").strip())
        for comment in self.comments:
            parts.append(re.sub(r"\s+", " ", comment.text).strip())
        return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


class BeadIndex:
    """All beads of one repo, with parent/child and blocker lookups."""

    def __init__(
        self, beads: Iterable[Bead], *, done_statuses: Iterable[str] = DONE_STATUSES
    ) -> None:
        self.beads: dict[str, Bead] = {b.id: b for b in beads}
        self.done_statuses = frozenset(done_statuses)
        self._children: dict[str, list[str]] = {}
        for bead in self.beads.values():
            if bead.parent_id:
                self._children.setdefault(bead.parent_id, []).append(bead.id)

    def __len__(self) -> int:
        return len(self.beads)

    def __contains__(self, bead_id: object) -> bool:
        return bead_id in self.beads

    def get(self, bead_id: str) -> Bead | None:
        return self.beads.get(bead_id)

    def parent_of(self, bead: Bead) -> Bead | None:
        return self.beads.get(bead.parent_id) if bead.parent_id else None

    def children_of(self, bead: Bead) -> list[Bead]:
        return [self.beads[i] for i in self._children.get(bead.id, []) if i in self.beads]

    def open_blockers(self, bead: Bead) -> list[Bead]:
        """Beads this one waits on (blocking dep types only) that are not finished."""
        out: list[Bead] = []
        for dep in bead.dependencies:
            if dep.type not in BLOCKING_DEP_TYPES:
                continue
            target = self.beads.get(dep.depends_on_id)
            if target is None:
                continue
            if target.status in self.done_statuses:
                continue
            out.append(target)
        return out

    def is_done(self, bead: Bead) -> bool:
        return bead.status in self.done_statuses


# ---- labels ------------------------------------------------------------------------


def split_label(label: str) -> tuple[str, str] | None:
    """`dim:value` -> (dim, value) when both halves are well-formed, else None."""
    if label.count(":") != 1:
        return None
    dim, value = label.split(":", 1)
    if DIMENSION_RE.match(dim) and LABEL_VALUE_RE.match(value):
        return dim, value
    return None


def labels_for_dimension(labels: Iterable[str], dimension: str) -> set[str]:
    prefix = f"{dimension}:"
    return {label for label in labels if label.startswith(prefix) and split_label(label)}


def validate_dimension(dimension: str) -> None:
    if not DIMENSION_RE.match(dimension):
        raise ValueError(f"invalid dimension name {dimension!r}: use [a-z][a-z0-9-]*")


def validate_value(value: str) -> None:
    if not LABEL_VALUE_RE.match(value) or "," in value or any(c.isspace() for c in value):
        raise ValueError(f"invalid label value {value!r}: use [a-z0-9][a-z0-9._-]*, no commas")
