from __future__ import annotations

from pathlib import Path

import pytest

from beadsort.bd import BdClient
from beadsort.config import (
    ConfigError,
    load_config,
    resolve_api_key,
    write_template,
)


def _write(repo: Path, text: str) -> None:
    (repo / ".beadsort").mkdir(exist_ok=True)
    (repo / ".beadsort" / "config.yaml").write_text(text, encoding="utf-8")


def test_defaults_without_file(repo: Path) -> None:
    cfg = load_config(repo)
    assert cfg.path is None
    assert [p.id for p in cfg.packs] == ["triage", "size", "agent-ready"]
    assert cfg.owner == "the project owner"
    assert cfg.project_brief() == {"owner": "the project owner"}


def test_full_file(repo: Path) -> None:
    _write(
        repo,
        """
owner: Harry
project: {name: Acme, summary: "A small products team", agent_names: [Notarius]}
people:
  mike: {name: Mike Tanner, aliases: [Mike], role: sponsor}
  lena: "CRM owner"
repos: {proposals: "RFQ tool"}
packs:
  - triage
  - {id: size, thresholds: {dead_zone: 0.1}}
  - {path: ./packs/custom.yaml}
model: jev-1.13.0
workers: 3
state: {max_updates: 2}
select: {statuses: [open], types: [task]}
on_uncertain: write_unsure
audit: false
mystery: 1
""",
    )
    cfg = load_config(repo)
    assert cfg.owner == "Harry"
    assert cfg.people["mike"].describe() == "Mike Tanner, also written Mike: sponsor"
    assert cfg.people["lena"].name == "Lena" and cfg.people["lena"].role == "CRM owner"
    assert cfg.packs[1].thresholds == {"dead_zone": 0.1}
    assert cfg.packs[2].path == "./packs/custom.yaml" and cfg.packs[2].key == "custom"
    assert cfg.model == "jev-1.13.0" and cfg.workers == 3
    assert cfg.state.max_updates == 2 and cfg.state.max_total_chars == 24000
    assert cfg.select.statuses == ("open",) and cfg.select.types == ("task",)
    assert cfg.on_uncertain == "write_unsure" and cfg.audit is False
    assert cfg.project_brief()["agent_names"] == ["Notarius"]
    assert any("mystery" in w for w in cfg.warnings)


@pytest.mark.parametrize(
    "text",
    [
        "api_key: sk-123\n",
        "people: [a, b]\n",
        "people: {Bad Slug: x}\n",
        "packs: {id: triage}\n",
        "on_uncertain: maybe\n",
        "state: {nope: 1}\n",
        "workers: many\n",
    ],
)
def test_invalid_files(repo: Path, text: str) -> None:
    _write(repo, text)
    with pytest.raises(ConfigError):
        load_config(repo)


def test_api_key_env_then_bd(fake_bd: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    bd = BdClient(fake_bd["repo"])
    assert resolve_api_key(bd) == (None, "none")
    monkeypatch.setenv("TYPESAFE_API_KEY", "  sk-env ")
    assert resolve_api_key(bd) == ("sk-env", "env")


def test_template_round_trips_and_refuses_overwrite(repo: Path) -> None:
    config_path, ignore_path = write_template(repo)
    cfg = load_config(repo)
    assert cfg.path == config_path
    assert [p.id for p in cfg.packs] == ["triage", "size", "agent-ready"]
    assert "cache.json" in ignore_path.read_text(encoding="utf-8")
    with pytest.raises(ConfigError):
        write_template(repo)
    write_template(repo, force=True)
