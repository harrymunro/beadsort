from __future__ import annotations

import json
from pathlib import Path

import pytest

from beadsort.beads import Bead
from beadsort.config import BeadsortConfig, PackRef, Person, load_config
from beadsort.packs import (
    BUILTIN_IDS,
    Answers,
    PackError,
    builtin_pack_text,
    find_pack,
    load_enabled_packs,
    load_pack_text,
    resolve_pack,
)

MINIMAL = """
id: risk
version: 2
description: test pack
applies_to: {types: [task]}
questions:
  touches_money:
    type: noul
    instructions: "Does `bead.description` mention money?"
  who:
    type: choice
    criteria_from: people
    instructions: "Who?"
    criteria: {nobody: "no one"}
  severity:
    type: score
    instructions: "How bad?"
    criteria: ["fine", "bad", "worse"]
outputs:
  - {dimension: touches-money, from: touches_money, yes_above: 0.7, no_below: 0.3}
  - {dimension: who, from: who, min_confidence: 0.6, skip: [nobody]}
  - {dimension: severity, from: severity, by: argmax, levels: [low, mid, high]}
"""


def _config(repo: Path, **overrides) -> BeadsortConfig:  # type: ignore[no-untyped-def]
    base = BeadsortConfig(
        repo=repo,
        owner="Harry",
        people={"mike": Person("mike", "Mike Tanner", ("Mike",), "sponsor")},
    )
    return BeadsortConfig(**{**base.__dict__, **overrides})


@pytest.mark.parametrize("pack_id", BUILTIN_IDS)
def test_builtins_validate_and_substitute_owner(repo: Path, pack_id: str) -> None:
    pack = load_pack_text(builtin_pack_text(pack_id), source=pack_id)
    assert pack.deriver
    resolved = resolve_pack(pack, _config(repo))
    blob = json.dumps(resolved.questions)
    assert "{{owner}}" not in blob
    assert "{{" not in blob
    if pack_id == "triage":
        assert "Harry" in blob
        assert (
            resolved.questions["stakeholder"]["criteria"]["mike"]
            == "Mike Tanner, also written Mike: sponsor"
        )
        # static options come after the injected people
        assert list(resolved.questions["stakeholder"]["criteria"])[-1] == "nobody"


def test_declarative_pack_parses(repo: Path) -> None:
    pack = load_pack_text(MINIMAL, source="mem")
    assert pack.dimensions == ("touches-money", "who", "severity")
    assert pack.applies(Bead(id="x", title="t", issue_type="task"), _config(repo))
    assert not pack.applies(Bead(id="x", title="t", issue_type="epic"), _config(repo))


@pytest.mark.parametrize(
    "mutation",
    [
        ("id: risk", "id: Risk"),
        ("by: argmax, levels: [low, mid, high]", "by: argmax, levels: [low, mid]"),
        ("from: touches_money", "from: nope"),
        ('criteria: ["fine", "bad", "worse"]', 'criteria: ["only"]'),
        ("outputs:", "deriver: x.y\noutputs:"),
    ],
)
def test_invalid_packs(mutation: tuple[str, str]) -> None:
    text = MINIMAL.replace(*mutation)
    assert text != MINIMAL
    with pytest.raises(PackError):
        load_pack_text(text, source="mem")


def test_when_gated_question_is_dropped_without_repos(repo: Path) -> None:
    pack = load_pack_text(builtin_pack_text("agent-ready"), source="agent-ready")
    without = resolve_pack(pack, _config(repo))
    assert "target_repo" not in without.questions
    with_repos = resolve_pack(pack, _config(repo, repos={"proposals": "RFQ tool"}))
    criteria = with_repos.questions["target_repo"]["criteria"]
    assert list(criteria)[:2] == ["this_repo", "proposals"]
    assert list(criteria)[-1] == "unclear"


def test_search_order_prefers_repo_pack_over_builtin(repo: Path) -> None:
    (repo / ".beadsort" / "packs").mkdir(parents=True)
    (repo / ".beadsort" / "packs" / "size.yaml").write_text(
        MINIMAL.replace("id: risk", "id: size"), encoding="utf-8"
    )
    pack = find_pack(PackRef(id="size"), _config(repo))
    assert pack.source.endswith("size.yaml") and pack.version == 2
    with pytest.raises(PackError):
        find_pack(PackRef(id="nonexistent"), _config(repo))


def test_enabled_packs_skip_when_requirements_missing(repo: Path) -> None:
    cfg = load_config(repo)  # no people configured
    packs, warnings = load_enabled_packs(cfg)
    assert [p.id for p in packs] == ["size", "agent-ready"]
    assert any("triage skipped" in w for w in warnings)


def test_duplicate_dimension_is_an_error(repo: Path) -> None:
    (repo / ".beadsort" / "packs").mkdir(parents=True)
    (repo / ".beadsort" / "packs" / "dup.yaml").write_text(
        MINIMAL.replace("id: risk", "id: dup").replace("dimension: severity", "dimension: size"),
        encoding="utf-8",
    )
    cfg = _config(repo, packs=(PackRef(id="size"), PackRef(id="dup")))
    with pytest.raises(PackError):
        load_enabled_packs(cfg)


def test_threshold_overrides_from_config(repo: Path) -> None:
    pack = load_pack_text(builtin_pack_text("size"), source="size")
    resolved = resolve_pack(pack, _config(repo), PackRef(id="size", thresholds={"dead_zone": 0.2}))
    assert resolved.thresholds["dead_zone"] == 0.2
    assert resolved.thresholds["risk_conf"] == 0.35


def test_answers_helpers() -> None:
    a = Answers(
        {
            "c": {
                "type": "choice",
                "choice": "x",
                "confidence": 0.7,
                "probabilities": {"x": 0.8, "y": 0.2},
            },
            "s": {
                "type": "score",
                "score": 1.5,
                "confidence": 0.5,
                "probabilities": {"0": 0.0, "1": 0.5, "2": 0.5},
            },
            "n": {"type": "noul", "noul": 0.9},
        }
    )
    assert a.choice("c") == "x" and a.conf("c") == 0.7
    assert a.top("c") == [("x", 0.8), ("y", 0.2)]
    assert a.normalised_score("s") == 0.75 and a.levels("s") == 3
    assert a.p("n") == 0.9 and round(a.conf("n"), 2) == 0.8
    assert a.choice("missing") is None and a.p("missing") is None and a.conf("missing") == 0.0
    assert (
        a.top_p("c") == 0.8 and round(a.margin("c"), 2) == 0.6 and a.mass("c", ["y", "zzz"]) == 0.2
    )
