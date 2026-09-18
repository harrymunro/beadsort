from __future__ import annotations

import pytest

from beadsort.beads import (
    Bead,
    BeadIndex,
    labels_for_dimension,
    split_label,
    validate_dimension,
    validate_value,
)
from tests.conftest import load_fixture_issues


def _index() -> BeadIndex:
    return BeadIndex(Bead.from_record(r) for r in load_fixture_issues())


def test_export_shape_recovers_parent_from_edge() -> None:
    idx = _index()
    child = idx.get("bs-e1.1")
    assert child is not None
    assert child.parent_id == "bs-e1"
    assert idx.parent_of(child) is not None and idx.parent_of(child).id == "bs-e1"
    assert {b.id for b in idx.children_of(idx.get("bs-e1"))} == {"bs-e1.1", "bs-e1.2"}


def test_show_shape_is_normalised_to_the_same_bead() -> None:
    show_shaped = {
        "id": "bs-e1.1",
        "title": "Fix the thing",
        "parent": "bs-e1",
        "dependencies": [
            {
                "id": "bs-e1",
                "title": "Payment reconciliation epic",
                "dependency_type": "parent-child",
            }
        ],
        "labels": ["x:y"],
        "priority": "1",
    }
    bead = Bead.from_record(show_shaped)
    assert bead.parent_id == "bs-e1"
    assert bead.dependencies[0].depends_on_id == "bs-e1"
    assert bead.dependencies[0].type == "parent-child"
    assert bead.priority == 1


def test_open_blockers_ignore_parent_child_and_closed_targets() -> None:
    idx = _index()
    assert [b.id for b in idx.open_blockers(idx.get("bs-a1"))] == ["bs-b1"]
    assert idx.open_blockers(idx.get("bs-a2")) == []  # bs-c1 is closed
    assert idx.open_blockers(idx.get("bs-e1.1")) == []  # parent-child is not blocking


def test_content_hash_ignores_whitespace_reflow_and_bookkeeping() -> None:
    a = Bead(id="x", title="T", description="one two  three", updated_at="2026-01-01")
    b = Bead(id="x", title="T", description="one\ntwo three", updated_at="2026-02-02")
    c = Bead(id="x", title="T", description="one two four")
    assert a.content_hash() == b.content_hash()
    assert a.content_hash() != c.content_hash()


def test_comments_are_material() -> None:
    a = Bead(id="x", title="T")
    b = Bead.from_record({"id": "x", "title": "T", "comments": [{"text": "new info"}]})
    assert a.content_hash() != b.content_hash()


def test_label_helpers() -> None:
    assert split_label("waiting-on:mike") == ("waiting-on", "mike")
    assert split_label("mike-question") is None
    assert split_label("a:b:c") is None
    assert split_label("Size:M") is None
    assert labels_for_dimension(["size:m", "size:xl", "risk:low", "human"], "size") == {
        "size:m",
        "size:xl",
    }
    validate_dimension("agent-ready")
    validate_value("no-consumer")
    with pytest.raises(ValueError):
        validate_dimension("Agent")
    with pytest.raises(ValueError):
        validate_value("a,b")
