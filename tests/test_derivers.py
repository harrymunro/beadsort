"""Table-driven tests for the three built-in derivers, one case per rule."""

from __future__ import annotations

from pathlib import Path

import pytest

from beadsort.beads import Bead
from beadsort.config import BeadsortConfig, Person
from beadsort.packs import (
    Answers,
    DeriveContext,
    builtin_pack_text,
    derive,
    load_pack_text,
    precheck,
    resolve_pack,
)
from beadsort.packs.agent_ready import precheck as ar_precheck
from tests.fakes import choice, noul, score

PEOPLE = {
    "mike": Person("mike", "Mike Lefler", ("Mike",), "sponsor"),
    "romy": Person("romy", "Romy", (), "CRM"),
}


def _cfg(repo: Path) -> BeadsortConfig:
    return BeadsortConfig(repo=repo, owner="Harry", people=PEOPLE)


def _ctx(repo: Path, bead: Bead | None = None, **kw) -> DeriveContext:  # type: ignore[no-untyped-def]
    return DeriveContext(bead=bead or Bead(id="x", title="T"), config=_cfg(repo), state={}, **kw)


def _pack(repo: Path, pack_id: str):  # type: ignore[no-untyped-def]
    return resolve_pack(load_pack_text(builtin_pack_text(pack_id), source=pack_id), _cfg(repo))


# ---- triage -------------------------------------------------------------------------

TRIAGE_BASE = {
    "blocking_party": choice("named_person", 0.85),
    "stakeholder": choice("mike", 0.9),
    "waiting_on_person": noul(0.9),
    "already_done": noul(0.05),
    "owner_action_kind": choice("not_applicable", 0.9),
    "ask_urgency": score(1, 3, 0.9),
    "has_code_half": noul(0.1),
    "code_can_start_now": noul(0.1),
    "decision_needs_owner": noul(0.5),
    "decision_is_blocking": noul(0.5),
    "superseded": noul(0.02),
    "no_consumer": noul(0.02),
    "umbrella_only": noul(0.02),
}


def _triage(repo: Path, bead: Bead | None = None, **overrides):  # type: ignore[no-untyped-def]
    answers = Answers({**TRIAGE_BASE, **overrides})
    return derive(_pack(repo, "triage"), answers, _ctx(repo, bead))


def test_triage_named_person(repo: Path) -> None:
    v = _triage(repo)
    assert v.labels == {
        "waiting-on": "mike",
        "owner-kind": None,
        "ask-urgency": "soon",
        "stale": None,
    }
    assert v.review == []


def test_triage_satisfied_ask_wins(repo: Path) -> None:
    v = _triage(repo, already_done=noul(0.85))
    assert v.labels["stale"] == "done" and v.labels["waiting-on"] is None
    assert "satisfied" in v.review[0]


def test_triage_unclear_category(repo: Path) -> None:
    v = _triage(repo, blocking_party=choice("named_person", 0.2, owner_decision=0.2))
    assert v.labels["waiting-on"] is None and "waiting-on" in v.uncertain


def test_triage_code_half_override(repo: Path) -> None:
    v = _triage(repo, has_code_half=noul(0.9), code_can_start_now=noul(0.9))
    assert v.labels["waiting-on"] == "agent"
    assert v.meta["also_ask"] == "mike" and v.meta["override"] == "code-half-can-start"


def test_triage_technical_decision_demotes_to_agent(repo: Path) -> None:
    v = _triage(
        repo,
        blocking_party=choice("owner_decision", 0.9),
        waiting_on_person=noul(0.1),
        has_code_half=noul(0.8),
        decision_needs_owner=noul(0.1),
    )
    assert v.labels["waiting-on"] == "agent"


def test_triage_owner_decision_and_admin(repo: Path) -> None:
    v = _triage(repo, blocking_party=choice("owner_decision", 0.9), waiting_on_person=noul(0.1))
    assert v.labels["waiting-on"] == "owner" and v.labels["owner-kind"] == "decision"
    v = _triage(
        repo,
        blocking_party=choice("owner_admin", 0.9),
        waiting_on_person=noul(0.1),
        owner_action_kind=choice("credentials_or_access", 0.9),
    )
    assert v.labels["owner-kind"] == "admin" and v.meta["owner_action"] == "credentials_or_access"


def test_triage_caution_band_needs_corroboration(repo: Path) -> None:
    hesitant = choice("named_person", 0.45, owner_decision=0.35)
    v = _triage(repo, blocking_party=hesitant, waiting_on_person=noul(0.2))
    assert v.labels["waiting-on"] is None and "not corroborated" in v.review[0]
    v = _triage(repo, blocking_party=hesitant, waiting_on_person=noul(0.9))
    assert v.labels["waiting-on"] == "mike"


def test_triage_person_unclear_falls_back_to_coarse_label(repo: Path) -> None:
    v = _triage(repo, stakeholder=choice("mike", 0.4, romy=0.4))
    assert v.labels["waiting-on"] == "person" and "person unclear" in v.review[0]
    v = _triage(repo, stakeholder=choice("other_named", 0.9))
    assert v.labels["waiting-on"] == "person"
    v = _triage(repo, stakeholder=choice("unspecified", 0.9))
    assert v.labels["waiting-on"] == "owner" and v.labels["owner-kind"] == "decision"


def test_triage_urgency_from_priority_zero(repo: Path) -> None:
    v = _triage(repo, Bead(id="x", title="T", priority=0), ask_urgency=score(0, 3, 0.9))
    assert v.labels["ask-urgency"] == "blocking"


def test_triage_parked_and_stale_flags(repo: Path) -> None:
    v = _triage(
        repo,
        blocking_party=choice("parked", 0.9),
        waiting_on_person=noul(0.1),
        no_consumer=noul(0.9),
    )
    assert v.labels["waiting-on"] == "nobody" and v.labels["stale"] == "no-consumer"


def test_triage_possibly_satisfied_keeps_labels_but_reviews(repo: Path) -> None:
    v = _triage(repo, already_done=noul(0.55))
    assert v.labels["waiting-on"] == "mike" and any("possibly satisfied" in r for r in v.review)


# ---- size ---------------------------------------------------------------------------

SIZE_BASE = {
    "scope_breadth": score(2, 4, 0.9),
    "investigation_needed": score(2, 4, 0.9),
    "verification_effort": score(1, 3, 0.9),
    "risk_of_breakage": score(1, 3, 0.9),
    "overall_effort": score(1, 3, 0.9),
    "work_kind": choice("feature", 0.9),
    "is_umbrella": noul(0.05),
}


def _size(repo: Path, bead: Bead | None = None, **overrides):  # type: ignore[no-untyped-def]
    return derive(_pack(repo, "size"), Answers({**SIZE_BASE, **overrides}), _ctx(repo, bead))


def test_size_medium_and_risk(repo: Path) -> None:
    v = _size(repo)
    assert v.labels == {"size": "m", "risk": "medium"}
    assert 0.4 <= v.meta["composite"] <= 0.7


def test_size_small_and_large(repo: Path) -> None:
    v = _size(
        repo,
        scope_breadth=score(0, 4, 0.95),
        investigation_needed=score(0, 4, 0.95),
        verification_effort=score(0, 3, 0.95),
        overall_effort=score(0, 3, 0.9),
    )
    assert v.labels["size"] == "s"
    v = _size(
        repo,
        scope_breadth=score(3, 4, 0.95),
        investigation_needed=score(3, 4, 0.95),
        verification_effort=score(2, 3, 0.95),
        overall_effort=score(2, 3, 0.9),
    )
    assert v.labels["size"] == "l"


def test_size_unsure_when_a_score_is_unsure(repo: Path) -> None:
    v = _size(repo, scope_breadth=score(1, 4, 0.1))
    assert v.labels["size"] is None and "size" in v.uncertain


def test_size_holistic_disagreement(repo: Path) -> None:
    v = _size(
        repo,
        scope_breadth=score(0, 4, 0.95),
        investigation_needed=score(0, 4, 0.95),
        verification_effort=score(0, 3, 0.95),
        overall_effort=score(2, 3, 0.9),
    )
    assert v.labels["size"] is None and any("holistic" in r for r in v.review)


def test_size_skips_non_repo_work(repo: Path) -> None:
    v = _size(
        repo, Bead(id="x", title="T", issue_type="feature"), work_kind=choice("not_repo_work", 0.9)
    )
    assert v.labels["size"] is None and v.meta["skipped"] == "not repository work"
    assert any("skipped" in r for r in v.review)


def test_risk_unsure_with_non_adjacent_mass(repo: Path) -> None:
    spread = {
        "type": "score",
        "score": 1.0,
        "confidence": 0.55,
        "probabilities": {"0": 0.45, "1": 0.1, "2": 0.45},
    }
    v = _size(repo, risk_of_breakage=spread)
    assert v.labels["risk"] is None and "risk" in v.uncertain


# ---- agent-ready --------------------------------------------------------------------

AR_BASE = {
    "needs_human_input_first": noul(0.05),
    "needs_external_resource": choice("none", 0.9),
    "requires_owner_judgement": noul(0.05),
    "already_done": noul(0.05),
    "is_code_work": noul(0.95),
    "spec_clarity": score(2, 4, 0.9),
    "done_is_checkable": noul(0.8),
    "spec_lives_elsewhere": noul(0.1),
    "scope_contained": noul(0.9),
}


def _ar(repo: Path, **overrides):  # type: ignore[no-untyped-def]
    return derive(_pack(repo, "agent-ready"), Answers({**AR_BASE, **overrides}), _ctx(repo))


def test_agent_ready_clean_yes(repo: Path) -> None:
    v = _ar(repo)
    assert v.labels == {"agent-ready": "yes", "blocker": None}
    assert v.meta["needs_person_check"] is False


@pytest.mark.parametrize(
    ("override", "blocker"),
    [
        ({"needs_human_input_first": noul(0.9)}, "human-input"),
        ({"requires_owner_judgement": noul(0.9)}, "owner-decision"),
        ({"already_done": noul(0.9)}, "done"),
        ({"is_code_work": noul(0.1)}, "not-code"),
        ({"needs_external_resource": choice("private_file_or_data", 0.9)}, "private-file"),
        ({"needs_external_resource": choice("secret_or_network", 0.9)}, "secret-or-network"),
        ({"needs_external_resource": choice("deployed_environment", 0.9)}, "deployed-env"),
        ({"spec_clarity": score(0, 4, 0.9)}, "spec"),
    ],
)
def test_agent_ready_blockers_in_precedence(repo: Path, override: dict, blocker: str) -> None:
    v = _ar(repo, **override)
    assert v.labels == {"agent-ready": "no", "blocker": blocker}


def test_agent_ready_precedence_human_beats_spec(repo: Path) -> None:
    v = _ar(repo, needs_human_input_first=noul(0.9), spec_clarity=score(0, 4, 0.9))
    assert v.labels["blocker"] == "human-input"


def test_agent_ready_off_board(repo: Path) -> None:
    v = _ar(repo, scope_contained=noul(0.1), target_repo=choice("propgen", 0.9))
    assert v.labels["blocker"] == "off-board" and v.meta["target_repo"] == "propgen"


def test_agent_ready_unsure_band(repo: Path) -> None:
    v = _ar(repo, needs_human_input_first=noul(0.5))
    assert v.labels["agent-ready"] is None and "agent-ready" in v.uncertain


def test_agent_ready_person_check_is_still_yes(repo: Path) -> None:
    v = _ar(repo, needs_external_resource=choice("person_check", 0.9))
    assert v.labels["agent-ready"] == "yes" and v.meta["needs_person_check"] is True


def test_agent_ready_precheck_on_open_blocker(repo: Path) -> None:
    blocker = Bead(id="bs-b1", title="B", status="open")
    ctx = _ctx(repo, open_blockers=(blocker,))
    v = ar_precheck({}, ctx)
    assert v is not None and v.labels == {"agent-ready": "no", "blocker": "dependency"}
    assert precheck(_pack(repo, "agent-ready"), ctx) is not None
    assert ar_precheck({}, _ctx(repo)) is None
    assert precheck(_pack(repo, "size"), ctx) is None


def test_agent_ready_yes_needs_only_low_blocking_mass(repo: Path) -> None:
    """A hesitant `none` with little mass on blocking resources is still a yes."""
    spread = {
        "type": "choice",
        "choice": "none",
        "confidence": 0.3,
        "probabilities": {
            "none": 0.45,
            "person_check": 0.30,
            "secret_or_network": 0.15,
            "unclear": 0.10,
        },
    }
    v = _ar(repo, needs_external_resource=spread)
    assert v.labels["agent-ready"] == "yes"
    heavy = {
        **spread,
        "probabilities": {"none": 0.40, "private_file_or_data": 0.35, "secret_or_network": 0.25},
    }
    v = _ar(repo, needs_external_resource=heavy)
    assert v.labels["agent-ready"] is None and "agent-ready" in v.uncertain


def test_triage_person_needs_a_margin(repo: Path) -> None:
    v = _triage(repo, stakeholder=choice("mike", 0.5, romy=0.45))
    assert v.labels["waiting-on"] == "person"
    v = _triage(repo, stakeholder=choice("mike", 0.55, romy=0.2))
    assert v.labels["waiting-on"] == "mike"
