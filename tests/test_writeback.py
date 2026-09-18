from __future__ import annotations

from pathlib import Path

from beadsort.bd import BdClient
from beadsort.beads import Bead
from beadsort.cache import CacheStore
from beadsort.engine import BeadResult
from beadsort.packs import Verdict
from beadsort.writeback import apply, build_metadata, flatten_metadata, plan_all, plan_bead
from tests.conftest import read_calls, read_store


def _result(bead: Bead, labels: dict[str, str | None], fresh: bool = True) -> BeadResult:
    result = BeadResult(bead=bead)
    result.verdicts["triage"] = Verdict(labels=labels, meta={"confidence": 0.8})
    result.packs_applied = ["triage"]
    if fresh:
        result.packs_fresh = ["triage"]
    return result


def _cache(tmp_path: Path) -> CacheStore:
    cache = CacheStore(tmp_path / "cache.json").load()
    cache.record_answers(
        "bs-1",
        "triage",
        pack_version=1,
        key="k",
        model="jev-x",
        request_id="r1",
        input_tokens=10,
        answers={
            "blocking_party": {
                "type": "choice",
                "choice": "named_person",
                "confidence": 0.8,
                "probabilities": {"named_person": 0.85},
            }
        },
    )
    return cache


def test_first_write_adds_label_and_metadata(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    plan = plan_bead(_result(Bead(id="bs-1", title="T"), {"waiting-on": "mike"}), cache=cache)
    assert plan.add == ["waiting-on:mike"] and plan.remove == []
    assert plan.metadata is not None and plan.metadata["beadsort"]["triage"]["labels"] == {
        "waiting-on": "mike"
    }
    assert plan.audit and plan.audit["packs"][0]["id"] == "triage"


def test_rerun_replaces_only_its_own_label(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    cache.set_written_labels("bs-1", {"waiting-on": "mike"})
    bead = Bead(id="bs-1", title="T", labels=("waiting-on:mike", "human"))
    plan = plan_bead(_result(bead, {"waiting-on": "romy"}), cache=cache)
    assert plan.add == ["waiting-on:romy"] and plan.remove == ["waiting-on:mike"]
    assert "human" not in plan.remove
    assert plan.written_after == {"waiting-on": "romy"}


def test_none_removes_own_label(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    cache.set_written_labels("bs-1", {"waiting-on": "mike"})
    bead = Bead(id="bs-1", title="T", labels=("waiting-on:mike",))
    plan = plan_bead(_result(bead, {"waiting-on": None}), cache=cache)
    assert plan.add == [] and plan.remove == ["waiting-on:mike"]
    assert plan.written_after == {}


def test_noop_when_nothing_changed(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    cache.set_written_labels("bs-1", {"waiting-on": "mike"})
    cache.set_metadata_written("bs-1", True)
    bead = Bead(id="bs-1", title="T", labels=("waiting-on:mike",))
    plan = plan_bead(
        _result(bead, {"waiting-on": "mike"}, fresh=False), cache=cache, metadata_changed=False
    )
    assert plan.is_noop


def test_kv_and_none_capabilities(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    result = _result(Bead(id="bs-1", title="T"), {"waiting-on": "mike"})
    kv = plan_bead(result, cache=cache, capability="kv")
    assert (
        kv.metadata is None and ("beadsort.triage.blocking_party", "named_person") in kv.metadata_kv
    )
    none = plan_bead(result, cache=cache, capability="none")
    assert none.metadata is None and none.metadata_kv == [] and none.add == ["waiting-on:mike"]
    unknown = plan_bead(result, cache=cache, capability="unknown")
    assert unknown.metadata is None and unknown.metadata_kv == []


def test_metadata_shape(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    meta = build_metadata(_result(Bead(id="bs-1", title="T"), {"waiting-on": "mike"}), cache)
    body = meta["beadsort"]
    assert body["v"] == 1 and body["triage"]["model"] == "jev-x" and body["triage"]["key"] == "k"
    assert body["triage"]["blocking_party"] == {"choice": "named_person", "p": 0.85, "conf": 0.8}
    assert body["triage"]["confidence"] == 0.8
    flat = dict(flatten_metadata(meta))
    assert flat["beadsort.triage.blocking_party"] == "named_person"
    assert flat["beadsort.triage.blocking_party.conf"] == "0.8"


def test_apply_end_to_end_with_fake_bd(fake_bd: dict) -> None:
    cache = CacheStore(fake_bd["repo"] / ".beadsort" / "cache.json").load()
    cache.record_answers(
        "bs-e1.1",
        "triage",
        pack_version=1,
        key="k",
        model="jev-x",
        request_id="r",
        input_tokens=5,
        answers={},
    )
    bead = Bead(id="bs-e1.1", title="Fix the thing")
    plans = plan_all([_result(bead, {"waiting-on": "agent"})], cache=cache)
    bd = BdClient(fake_bd["repo"])
    report = apply(plans, bd=bd, cache=cache, model="jev-x")
    assert report.writes == 1 and report.audits == 1 and report.failed == []
    issue = next(i for i in read_store(fake_bd["store"])["issues"] if i["id"] == "bs-e1.1")
    assert "waiting-on:agent" in issue["labels"]
    assert issue["metadata"]["beadsort"]["triage"]["labels"] == {"waiting-on": "agent"}
    audit = read_store(fake_bd["store"])["audit"][-1]
    assert (
        audit["kind"] == "llm_call"
        and audit["issue_id"] == "bs-e1.1"
        and audit["actor"] == "beadsort"
    )
    assert cache.written_labels("bs-e1.1") == {"waiting-on": "agent"} and cache.metadata_written(
        "bs-e1.1"
    )

    # Second apply with the same verdict and no fresh answers: zero bd writes.
    calls_before = len(read_calls(fake_bd["log"]))
    bead2 = Bead(id="bs-e1.1", title="Fix the thing", labels=("waiting-on:agent",))
    plans2 = plan_all([_result(bead2, {"waiting-on": "agent"}, fresh=False)], cache=cache)
    report2 = apply(plans2, bd=bd, cache=cache, model="jev-x")
    assert report2.writes == 0 and report2.noop == ["bs-e1.1"]
    assert len(read_calls(fake_bd["log"])) == calls_before


def test_one_failure_does_not_abort(fake_bd: dict) -> None:
    cache = CacheStore(fake_bd["repo"] / ".beadsort" / "cache.json").load()
    good = _result(Bead(id="bs-e1.1", title="ok"), {"waiting-on": "agent"})
    bad = _result(Bead(id="bs-missing", title="nope"), {"waiting-on": "agent"})
    plans = plan_all([bad, good], cache=cache)
    report = apply(plans, bd=BdClient(fake_bd["repo"]), cache=cache, model="jev-x", audit=False)
    assert report.applied == ["bs-e1.1"]
    assert report.failed and report.failed[0][0] == "bs-missing"
    assert cache.written_labels("bs-missing") == {}
