from __future__ import annotations

from pathlib import Path

from beadsort.beads import Bead, BeadIndex
from beadsort.cache import CacheStore
from beadsort.config import BeadsortConfig, Person
from beadsort.engine import run, select_beads
from beadsort.packs import load_enabled_packs
from tests.conftest import load_fixture_issues
from tests.fakes import FakeJudge, choice, noul, score

PEOPLE = {"mike": Person("mike", "Mike Lefler", ("Mike",), "sponsor")}


def _setup(repo: Path, **kw):  # type: ignore[no-untyped-def]
    cfg = BeadsortConfig(repo=repo, owner="Harry", people=PEOPLE, **kw)
    index = BeadIndex(Bead.from_record(r) for r in load_fixture_issues())
    packs, warnings = load_enabled_packs(cfg)
    assert warnings == []
    cache = CacheStore(cfg.cache_path).load()
    return cfg, index, packs, cache


def _table(state, qid):  # type: ignore[no-untyped-def]
    """A judge that says: this bead waits on Mike, is medium, and is agent-ready."""
    table = {
        "blocking_party": choice("named_person", 0.9),
        "stakeholder": choice("mike", 0.9),
        "waiting_on_person": noul(0.9),
        "already_done": noul(0.05),
        "ask_urgency": score(1, 3, 0.9),
        "scope_breadth": score(2, 4, 0.9),
        "investigation_needed": score(2, 4, 0.9),
        "verification_effort": score(1, 3, 0.9),
        "risk_of_breakage": score(1, 3, 0.9),
        "overall_effort": score(1, 3, 0.9),
        "work_kind": choice("feature", 0.9),
        "needs_external_resource": choice("none", 0.9),
        "is_code_work": noul(0.95),
        "spec_clarity": score(2, 4, 0.9),
        "scope_contained": noul(0.9),
    }
    return table.get(qid)


def test_select_beads(repo: Path) -> None:
    cfg, index, _, _ = _setup(repo)
    ids = [b.id for b in select_beads(index, cfg)]
    assert "bs-h1" not in ids and "bs-i1" not in ids  # closed, scrapped
    assert "bs-f1" in ids  # in_progress
    assert [b.id for b in select_beads(index, cfg, only=["bs-h1"])] == ["bs-h1"]
    assert [b.id for b in select_beads(index, cfg, types=["epic"])] == ["bs-e1"]
    assert len(select_beads(index, cfg, limit=2)) == 2


def test_run_merges_packs_into_one_request_and_caches(repo: Path) -> None:
    cfg, index, packs, cache = _setup(repo)
    judge = FakeJudge(_table, tokens=700)
    beads = select_beads(index, cfg, only=["bs-e1.1", "bs-a1"])
    report = run(
        beads, packs, index=index, config=cfg, cache=cache, judge=judge, model="jev-fake", workers=2
    )

    assert report.selected == 2
    # bs-a1 is blocked by bs-b1: agent-ready is prechecked, but triage and size still ask.
    assert report.calls == 2
    assert len(judge.calls) == 2
    state, questions = judge.calls[0]
    assert {q.split("__")[0] for q in questions} == {"triage", "size", "agent-ready"} or {
        q.split("__")[0] for q in questions
    } == {"triage", "size"}
    assert report.input_tokens == 1400
    assert report.estimated_cost_usd > 0

    by_id = {r.bead.id: r for r in report.results}
    assert by_id["bs-e1.1"].labels["waiting-on"] == "mike"
    assert by_id["bs-e1.1"].labels["size"] == "m"
    assert by_id["bs-e1.1"].labels["agent-ready"] == "yes"
    assert by_id["bs-a1"].labels["agent-ready"] == "no"
    assert by_id["bs-a1"].labels["blocker"] == "dependency"
    assert by_id["bs-a1"].packs_prechecked == ["agent-ready"]

    cache.save()
    # Second run: nothing changed, so no model calls; verdicts come from the cache.
    cache2 = CacheStore(cfg.cache_path).load()
    judge2 = FakeJudge(_table)
    report2 = run(
        beads, packs, index=index, config=cfg, cache=cache2, judge=judge2, model="jev-fake"
    )
    assert report2.calls == 0 and judge2.calls == []
    assert report2.cached >= 4
    assert {r.bead.id: r.labels for r in report2.results} == {
        r.bead.id: r.labels for r in report.results
    }
    assert cache2.last_run["calls"] == 0


def test_force_re_asks(repo: Path) -> None:
    cfg, index, packs, cache = _setup(repo)
    beads = select_beads(index, cfg, only=["bs-e1.1"])
    run(
        beads,
        packs,
        index=index,
        config=cfg,
        cache=cache,
        judge=FakeJudge(_table),
        model="jev-fake",
    )
    judge = FakeJudge(_table)
    run(
        beads,
        packs,
        index=index,
        config=cfg,
        cache=cache,
        judge=judge,
        model="jev-fake",
        force=True,
    )
    assert len(judge.calls) == 1


def test_model_change_invalidates_cache(repo: Path) -> None:
    cfg, index, packs, cache = _setup(repo)
    beads = select_beads(index, cfg, only=["bs-e1.1"])
    run(beads, packs, index=index, config=cfg, cache=cache, judge=FakeJudge(_table), model="jev-a")
    judge = FakeJudge(_table)
    run(beads, packs, index=index, config=cfg, cache=cache, judge=judge, model="jev-b")
    assert len(judge.calls) == 1


def test_offline_without_cache_reports_error_not_crash(repo: Path) -> None:
    cfg, index, packs, cache = _setup(repo)
    beads = select_beads(index, cfg, only=["bs-e1.1"])
    report = run(
        beads,
        packs,
        index=index,
        config=cfg,
        cache=cache,
        judge=None,
        model="jev-fake",
        offline=True,
    )
    assert report.results[0].error and report.calls == 0


def test_write_unsure_option(repo: Path) -> None:
    cfg, index, packs, cache = _setup(repo, on_uncertain="write_unsure")

    def unsure(state, qid):  # type: ignore[no-untyped-def]
        if qid == "blocking_party":
            return choice("named_person", 0.2, owner_decision=0.2)
        return _table(state, qid)

    beads = select_beads(index, cfg, only=["bs-e1.1"])
    report = run(
        beads,
        packs,
        index=index,
        config=cfg,
        cache=cache,
        judge=FakeJudge(unsure),
        model="jev-fake",
    )
    assert report.results[0].labels["waiting-on"] == "unsure"


def test_one_failing_bead_does_not_sink_the_run(repo: Path) -> None:
    cfg, index, packs, cache = _setup(repo)

    def flaky(state, qid):  # type: ignore[no-untyped-def]
        if state["bead"]["id"] == "bs-b1":
            raise RuntimeError("boom")
        return _table(state, qid)

    beads = select_beads(index, cfg, only=["bs-e1.1", "bs-b1"])
    report = run(
        beads, packs, index=index, config=cfg, cache=cache, judge=FakeJudge(flaky), model="jev-fake"
    )
    by_id = {r.bead.id: r for r in report.results}
    assert by_id["bs-b1"].error and "boom" in by_id["bs-b1"].error
    assert by_id["bs-e1.1"].labels["waiting-on"] == "mike"
    assert report.calls == 1
