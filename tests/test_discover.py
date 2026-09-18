from __future__ import annotations

from pathlib import Path

from beadsort.discover import discover, find_repo


def test_find_repo_walks_up(repo: Path) -> None:
    nested = repo / "src" / "deep"
    nested.mkdir(parents=True)
    assert find_repo(nested) == repo.resolve()
    assert find_repo(repo.parent) is None


def test_discover_prunes_junk_and_respects_depth(tmp_path: Path) -> None:
    (tmp_path / "a" / ".beads").mkdir(parents=True)
    (tmp_path / "b" / "inner" / ".beads").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / ".beads").mkdir(parents=True)
    (tmp_path / "a" / "worktrees" / "wt" / ".beads").mkdir(parents=True)
    (tmp_path / ".hidden" / ".beads").mkdir(parents=True)
    found = discover(tmp_path, max_depth=4)
    names = {p.relative_to(tmp_path.resolve()).as_posix() for p in found}
    assert names == {"a", "b/inner"}
    assert discover(tmp_path, max_depth=1) == [tmp_path.resolve() / "a"]
