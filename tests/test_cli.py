from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from beadsort import __version__
from beadsort.cli import main
from beadsort.engine import JudgeResult
from tests.conftest import read_store
from tests.fakes import FakeJudge, choice, noul, score

CONFIG = """
owner: Harry
project: {name: Test, summary: "A test backlog."}
people:
  mike: {name: Mike Lefler, aliases: [Mike], role: sponsor}
packs: [triage, size, agent-ready]
"""


def _table(state, qid):  # type: ignore[no-untyped-def]
    table = {
        "blocking_party": choice("named_person", 0.9),
        "stakeholder": choice("mike", 0.9),
        "waiting_on_person": noul(0.9),
        "already_done": noul(0.05),
        "ask_urgency": score(1, 3, 0.9),
        "scope_breadth": score(1, 4, 0.9),
        "investigation_needed": score(1, 4, 0.9),
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


@pytest.fixture
def configured(fake_bd: dict, monkeypatch: pytest.MonkeyPatch) -> dict:
    repo = fake_bd["repo"]
    (repo / ".beadsort").mkdir()
    (repo / ".beadsort" / "config.yaml").write_text(CONFIG, encoding="utf-8")
    fake = FakeJudge(_table)
    monkeypatch.setattr("beadsort.cli.TypeSafeJudge", lambda key, model: fake)
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-test")
    fake_bd["judge"] = fake
    return fake_bd


def invoke(*args: str, cwd: Path) -> tuple[int, str, str]:
    result = CliRunner().invoke(main, ["-C", str(cwd), *args], catch_exceptions=False)
    return result.exit_code, result.stdout, result.stderr if hasattr(result, "stderr") else ""


def test_version() -> None:
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0 and __version__ in result.output


def test_outside_a_repo_is_a_usage_error(tmp_path: Path) -> None:
    code, out, _ = invoke("status", "--json", cwd=tmp_path)
    assert code == 2
    envelope = json.loads(out)
    assert envelope["success"] is False and envelope["error"]["code"] == "usage"


def test_status_counts(configured: dict) -> None:
    code, out, _ = invoke("status", "--json", cwd=configured["repo"])
    assert code == 0
    data = json.loads(out)["data"]
    assert data["beads"] == 13 and data["by_status"]["open"] == 9
    assert data["packs"] == ["triage", "size", "agent-ready"]
    assert data["dimensions"]["size"] == {"xl": 1}
    assert data["last_run"] is None


def test_run_dry_then_apply_then_noop(configured: dict) -> None:
    repo = configured["repo"]
    code, out, _ = invoke("run", "--only", "bs-e1.1", "--only", "bs-d1", "--json", cwd=repo)
    assert code == 0, out
    data = json.loads(out)["data"]
    assert data["calls"] == 2 and "applied" not in data
    by_id = {r["id"]: r for r in data["results"]}
    assert by_id["bs-e1.1"]["labels"]["waiting-on"] == "mike"
    assert by_id["bs-e1.1"]["labels"]["agent-ready"] == "yes"
    assert by_id["bs-d1"]["current"] == {"size": "xl", "waiting-on": "mike"}
    store = read_store(configured["store"])
    assert (
        "waiting-on:mike" not in next(i for i in store["issues"] if i["id"] == "bs-e1.1")["labels"]
    )

    # apply: cached answers, no new model calls; labels land; foreign size:xl respected
    code, out, _ = invoke(
        "run", "--only", "bs-e1.1", "--only", "bs-d1", "--apply", "--json", cwd=repo
    )
    assert code == 0, out
    data = json.loads(out)["data"]
    assert data["calls"] == 0
    assert data["applied"]["writes"] == 2
    assert data["applied"]["respected"] == 1
    assert data["applied"]["metadata"] == "json"
    store = read_store(configured["store"])
    e11 = next(i for i in store["issues"] if i["id"] == "bs-e1.1")
    assert {"waiting-on:mike", "size:m", "agent-ready:yes", "ask-urgency:soon"} <= set(
        e11["labels"]
    )
    assert e11["metadata"]["beadsort"]["triage"]["labels"]["waiting-on"] == "mike"
    d1 = next(i for i in store["issues"] if i["id"] == "bs-d1")
    assert "size:xl" in d1["labels"]  # the fence
    assert "waiting-on:mike" in d1["labels"]  # foreign too; left alone, not duplicated

    # third run: nothing to do
    code, out, _ = invoke(
        "run", "--only", "bs-e1.1", "--only", "bs-d1", "--apply", "--json", cwd=repo
    )
    data = json.loads(out)["data"]
    assert data["applied"]["writes"] == 0 and data["changes"] == 0


def test_run_without_key_reports_how_to_get_one(
    fake_bd: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = fake_bd["repo"]
    (repo / ".beadsort").mkdir()
    (repo / ".beadsort" / "config.yaml").write_text(CONFIG, encoding="utf-8")
    code, out, _ = invoke("run", "--only", "bs-e1.1", "--json", cwd=repo)
    assert code == 4
    # the run envelope prints first, then the error envelope
    assert '"no_api_key"' in out


def test_review_and_status_for_one_bead(configured: dict) -> None:
    repo = configured["repo"]

    def unsure(state, qid):  # type: ignore[no-untyped-def]
        if qid == "blocking_party":
            return choice("named_person", 0.2, owner_decision=0.2)
        return _table(state, qid)

    configured["judge"].table = unsure
    invoke("run", "--only", "bs-e1.1", cwd=repo)
    code, out, _ = invoke("review", "--json", cwd=repo)
    data = json.loads(out)["data"]
    assert data["count"] == 1 and data["items"][0]["id"] == "bs-e1.1"
    assert "waiting-on" in data["items"][0]["unsure"]
    code, out, _ = invoke("status", "bs-e1.1", "--json", cwd=repo)
    data = json.loads(out)["data"][0]
    assert data["stale"] is False and "triage" in data["packs"]


def test_eval_against_label(configured: dict) -> None:
    repo = configured["repo"]
    code, out, _ = invoke(
        "eval",
        "--pack",
        "triage",
        "--dimension",
        "waiting-on",
        "--truth-label",
        "human",
        "--positive",
        "mike,person,owner",
        "--status",
        "open",
        "--json",
        cwd=repo,
    )
    assert code == 0, out
    data = json.loads(out)["data"]
    assert data["total"] >= 1 and "per_value" in data


def test_share_packs_config_doctor(configured: dict) -> None:
    repo = configured["repo"]
    code, out, _ = invoke("share", "bs-e1.1", "--pack", "triage", "--json", cwd=repo)
    assert code == 0 and json.loads(out)["data"]["url"].startswith(
        "https://console.typesafe.ai/playground#share/"
    )

    code, out, _ = invoke("packs", "list", "--json", cwd=repo)
    assert [p["id"] for p in json.loads(out)["data"]["packs"]] == ["triage", "size", "agent-ready"]
    code, out, _ = invoke("packs", "show", "size", "--json", cwd=repo)
    assert "scope_breadth" in json.loads(out)["data"]["questions"]

    pack_path = repo / "custom.yaml"
    pack_path.write_text(
        "id: custom\nquestions:\n  q:\n    type: noul\n    instructions: hi\n"
        "outputs:\n  - {dimension: custom, from: q}\n",
        encoding="utf-8",
    )
    code, out, _ = invoke("packs", "validate", str(pack_path), cwd=repo)
    assert code == 0 and "ok" in out

    code, out, _ = invoke("config", "init", cwd=repo)
    assert code == 1  # exists already, refuse
    code, out, _ = invoke("config", "init", "--force", "--json", cwd=repo)
    assert code == 0

    code, out, _ = invoke("doctor", "--probe", "--json", cwd=repo)
    data = json.loads(out)["data"]
    names = {c["name"]: c for c in data["checks"]}
    assert names["bd version"]["detail"] == "1.2.2"
    assert names["metadata round-trip"]["ok"] and names["metadata round-trip"]["detail"].startswith(
        "json"
    )


def test_root_iterates_repos(configured: dict, tmp_path: Path) -> None:
    code, out, _ = invoke("status", "--root", str(tmp_path), "--json", cwd=tmp_path)
    assert code == 0
    data = json.loads(out)["data"]
    assert isinstance(data, list) and data[0]["repo"].endswith("repo")


def test_judge_result_type_is_importable() -> None:
    assert JudgeResult(answers={}, model="x").model == "x"
