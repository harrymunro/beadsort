from __future__ import annotations

import json
from pathlib import Path

from beadsort.cache import CacheStore, call_key


def test_call_key_is_stable_and_sensitive() -> None:
    a = call_key("triage", 1, "jev", {"q": {"type": "noul"}}, {"bead": {"id": "x"}})
    b = call_key("triage", 1, "jev", {"q": {"type": "noul"}}, {"bead": {"id": "x"}})
    c = call_key("triage", 2, "jev", {"q": {"type": "noul"}}, {"bead": {"id": "x"}})
    d = call_key("triage", 1, "jev", {"q": {"type": "noul"}}, {"bead": {"id": "y"}})
    assert a == b and a != c and a != d and len(a) == 16


def test_round_trip_and_atomic_save(tmp_path: Path) -> None:
    path = tmp_path / ".beadsort" / "cache.json"
    cache = CacheStore(path).load()
    cache.touch_bead("bs-1", content_hash="abc", title="One")
    cache.record_answers(
        "bs-1",
        "triage",
        pack_version=1,
        key="k1",
        model="jev",
        request_id="r",
        input_tokens=12,
        answers={"q": {"type": "noul", "noul": 0.5}},
    )
    cache.record_verdict("bs-1", "triage", {"labels": {"waiting-on": "mike"}})
    cache.set_written_labels("bs-1", {"waiting-on": "mike"})
    cache.set_metadata_written("bs-1", True)
    cache.capabilities["metadata"] = "json"
    cache.set_last_run(calls=1)
    cache.save()
    assert not path.with_suffix(".json.tmp").exists()

    again = CacheStore(path).load()
    assert again.status_for("bs-1", "triage", "k1") == "unchanged"
    assert again.status_for("bs-1", "triage", "k2") == "changed"
    assert again.status_for("bs-2", "triage", "k1") == "new"
    assert again.written_labels("bs-1") == {"waiting-on": "mike"}
    assert again.metadata_written("bs-1") and again.capabilities["metadata"] == "json"
    assert again.last_run["calls"] == 1
    assert again.pack_entry("bs-1", "triage")["verdict"]["labels"]["waiting-on"] == "mike"


def test_corrupt_or_old_cache_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    path.write_text("{not json", encoding="utf-8")
    assert CacheStore(path).load().beads == {}
    path.write_text(json.dumps({"version": 99, "beads": {"x": {}}}), encoding="utf-8")
    assert CacheStore(path).load().beads == {}


def test_drop_missing(tmp_path: Path) -> None:
    cache = CacheStore(tmp_path / "c.json").load()
    cache.touch_bead("a", content_hash="1", title="A")
    cache.touch_bead("b", content_hash="2", title="B")
    assert cache.drop_missing({"a"}) == ["b"]
    assert list(cache.beads) == ["a"]
