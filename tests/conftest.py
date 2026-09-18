from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
FAKE_BD = Path(__file__).parent / "fake_bd.py"


def load_fixture_issues() -> list[dict]:
    rows = []
    for line in (FIXTURES / "issues.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A fake beads repo: `.beads/` marker plus a git dir marker for discovery tests."""
    root = tmp_path / "repo"
    (root / ".beads").mkdir(parents=True)
    (root / ".beads" / "config.yaml").write_text("issue-prefix: bs\n", encoding="utf-8")
    return root


@pytest.fixture
def fake_bd(tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Put a fake `bd` on PATH backed by the fixture issues. Returns paths for assertions."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    store = tmp_path / "store.json"
    store.write_text(
        json.dumps({"issues": load_fixture_issues(), "audit": [], "config": {}, "prefix": "bs"}),
        encoding="utf-8",
    )
    log = tmp_path / "bd_calls.jsonl"
    wrapper = bin_dir / "bd"
    wrapper.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_BD}" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_BD_STORE", str(store))
    monkeypatch.setenv("FAKE_BD_LOG", str(log))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    return {"bin": wrapper, "store": store, "log": log, "repo": repo}


def read_calls(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]


def read_store(store: Path) -> dict:
    return json.loads(store.read_text(encoding="utf-8"))
