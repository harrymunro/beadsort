"""The one invariant that must never regress: beadsort never removes a label it did not write."""

from __future__ import annotations

from pathlib import Path

import pytest

from beadsort.beads import Bead
from beadsort.cache import CacheStore
from beadsort.engine import BeadResult
from beadsort.packs import Verdict
from beadsort.writeback import plan_bead


def _result(bead: Bead, value: str | None) -> BeadResult:
    result = BeadResult(bead=bead)
    result.verdicts["size"] = Verdict(labels={"size": value})
    result.packs_applied = ["size"]
    result.packs_fresh = ["size"]
    return result


@pytest.mark.parametrize("written_before", [None, "m"])
@pytest.mark.parametrize("desired", ["s", "m", None])
@pytest.mark.parametrize("labels", [("size:xl",), ("size:xl", "size:m"), ("size:xl", "human")])
def test_foreign_label_is_never_removed_without_force(
    tmp_path: Path, written_before: str | None, desired: str | None, labels: tuple[str, ...]
) -> None:
    cache = CacheStore(tmp_path / "cache.json").load()
    if written_before:
        cache.set_written_labels("bs-1", {"size": written_before})
    bead = Bead(id="bs-1", title="T", labels=labels)
    plan = plan_bead(_result(bead, desired), cache=cache, capability="none")
    assert "size:xl" not in plan.remove
    assert plan.add == []  # the whole dimension is skipped while a foreign label is present
    assert plan.respected and plan.respected[0][0] == "size"
    assert "size:xl" in plan.respected[0][1]


@pytest.mark.parametrize("labels", [("size:xl",), ("size:xl", "size:m")])
def test_force_replaces_foreign_labels(tmp_path: Path, labels: tuple[str, ...]) -> None:
    cache = CacheStore(tmp_path / "cache.json").load()
    cache.set_written_labels("bs-1", {"size": "m"})
    bead = Bead(id="bs-1", title="T", labels=labels)
    plan = plan_bead(_result(bead, "s"), cache=cache, force=True, capability="none")
    assert "size:xl" in plan.remove and plan.add == ["size:s"]
    assert plan.respected == []


def test_own_label_is_removed_when_value_changes(tmp_path: Path) -> None:
    cache = CacheStore(tmp_path / "cache.json").load()
    cache.set_written_labels("bs-1", {"size": "m"})
    bead = Bead(id="bs-1", title="T", labels=("size:m",))
    plan = plan_bead(_result(bead, "l"), cache=cache, capability="none")
    assert plan.remove == ["size:m"] and plan.add == ["size:l"]


def test_non_dimension_labels_are_untouched(tmp_path: Path) -> None:
    cache = CacheStore(tmp_path / "cache.json").load()
    bead = Bead(id="bs-1", title="T", labels=("human", "mike-question", "fls-abcd"))
    plan = plan_bead(_result(bead, "s"), cache=cache, capability="none")
    assert plan.remove == [] and plan.add == ["size:s"]
