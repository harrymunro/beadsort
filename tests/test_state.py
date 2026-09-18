from __future__ import annotations

import json
from pathlib import Path

from beadsort.beads import Bead, BeadIndex, Comment
from beadsort.config import BeadsortConfig, StateLimits
from beadsort.state import build_state, state_size
from tests.conftest import load_fixture_issues

GOLDEN = Path(__file__).parent / "golden" / "state"


def _index() -> BeadIndex:
    return BeadIndex(Bead.from_record(r) for r in load_fixture_issues())


def _config(repo: Path, **kw) -> BeadsortConfig:  # type: ignore[no-untyped-def]
    return BeadsortConfig(
        repo=repo, owner="Harry", project={"name": "Test", "summary": "A test backlog."}, **kw
    )


def test_child_state_has_parent_and_updates_newest_first(repo: Path) -> None:
    idx = _index()
    state = build_state(idx.get("bs-e1.2"), idx, _config(repo), needs={"parent", "open_blockers"})
    assert state["project"] == {"name": "Test", "summary": "A test backlog.", "owner": "Harry"}
    assert state["parent"]["id"] == "bs-e1"
    assert state["bead"]["latest_update"]["date"] == "2026-09-05"
    assert [u["source"] for u in state["bead"]["updates"]] == ["note", "comment", "note"]
    assert state["open_blockers"] == []
    assert "labels" not in state["bead"] and "created_at" not in state["bead"]
    assert "children" not in state


def test_epic_gets_children_and_tighter_caps(repo: Path) -> None:
    idx = _index()
    state = build_state(idx.get("bs-e1"), idx, _config(repo), needs={"parent", "children"})
    assert {c["id"] for c in state["children"]} == {"bs-e1.1", "bs-e1.2"}
    assert state["parent"] is None


def test_open_blockers_listed(repo: Path) -> None:
    idx = _index()
    state = build_state(idx.get("bs-a1"), idx, _config(repo), needs={"open_blockers"})
    assert [b["id"] for b in state["open_blockers"]] == ["bs-b1"]


def test_golden_state(repo: Path) -> None:
    idx = _index()
    state = build_state(
        idx.get("bs-e1.2"), idx, _config(repo), needs={"parent", "children", "open_blockers"}
    )
    golden_path = GOLDEN / "bs-e1.2.json"
    if not golden_path.exists():
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    assert json.loads(golden_path.read_text(encoding="utf-8")) == json.loads(json.dumps(state))


def test_huge_bead_is_trimmed_deterministically(repo: Path) -> None:
    big = Bead(
        id="big",
        title="Huge",
        description="lorem ipsum " * 4000,  # ~48k chars
        notes="\n".join(f"2026-09-{i:02d}: " + "note text " * 100 for i in range(1, 10)),
        comments=tuple(
            Comment(text="c " * 300, created_at=f"2026-08-{i:02d}T00:00:00Z") for i in range(1, 6)
        ),
        parent_id="bs-e1",
    )
    idx = BeadIndex([big, *(Bead.from_record(r) for r in load_fixture_issues())])
    cfg = _config(repo, state=StateLimits(max_total_chars=8000))
    first = build_state(big, idx, cfg, needs={"parent", "children", "open_blockers"})
    second = build_state(big, idx, cfg, needs={"parent", "children", "open_blockers"})
    assert first == second
    assert state_size(first) <= 8000
    assert first["bead"]["description"].endswith("[trimmed]")
    assert first["bead"]["latest_update"]["date"] == "2026-09-09"


def test_no_limit_means_no_trim(repo: Path) -> None:
    idx = _index()
    cfg = _config(repo, state=StateLimits(max_total_chars=0))
    state = build_state(idx.get("bs-e1.1"), idx, cfg)
    assert "[trimmed]" not in json.dumps(state)
