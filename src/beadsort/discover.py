"""Find beads repos: the one containing the working directory, or every one under a root."""

from __future__ import annotations

import os
from pathlib import Path

#: Directories never worth descending into when scanning for `.beads/`.
PRUNE = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".tox",
        ".nox",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "dist",
        "build",
        "target",
        "site-packages",
        ".cache",
        ".next",
        ".turbo",
        "coverage",
        "worktrees",
    }
)


def is_repo(path: Path) -> bool:
    return (path / ".beads").is_dir()


def find_repo(start: Path | None = None) -> Path | None:
    """Walk up from `start` (default cwd) to the nearest directory containing `.beads/`."""
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if is_repo(candidate):
            return candidate
    return None


def discover(root: Path, max_depth: int = 4) -> list[Path]:
    """Every directory under `root` (inclusive) that contains `.beads/`, sorted by path.

    A repo's own subtree is still scanned, because nested trackers are common (a monorepo
    with one `.beads/` per tool), but junk directories in PRUNE and hidden directories other
    than `.beads` are skipped.
    """
    root = root.resolve()
    found: list[Path] = []
    root_depth = len(root.parts)
    for dirpath, dirnames, _ in os.walk(root):
        here = Path(dirpath)
        depth = len(here.parts) - root_depth
        if is_repo(here):
            found.append(here)
        if depth >= max_depth:
            dirnames[:] = []
            continue
        dirnames[:] = sorted(
            d for d in dirnames if d not in PRUNE and not (d.startswith(".") and d != ".beads")
        )
        # never descend into .beads itself
        dirnames[:] = [d for d in dirnames if d != ".beads"]
    return sorted(found)
