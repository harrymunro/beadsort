"""The shipped skill (skills/beadsort/SKILL.md) must stay in step with the CLI.

Every `beadsort ...` invocation the skill shows has to resolve to a real command with real
options, and the dimension table has to match what the built-in packs write. Otherwise an
agent following the skill runs commands that do not exist.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import click
import yaml

from beadsort.cli import main

SKILL = Path(__file__).parent.parent / "skills" / "beadsort" / "SKILL.md"
BUILTIN = Path(__file__).parent.parent / "src" / "beadsort" / "packs" / "builtin"


def _frontmatter_and_body() -> tuple[dict, str]:
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    _, meta, body = text.split("---\n", 2)
    return yaml.safe_load(meta), body


def _invocations(body: str) -> list[list[str]]:
    """Every `beadsort ...` command line in fenced blocks or inline code."""
    found: list[list[str]] = []
    fenced = re.findall(r"```(?:sh|bash)?\n(.*?)```", body, flags=re.S)
    lines = [ln for block in fenced for ln in block.splitlines()]
    lines += re.findall(r"`([^`\n]+)`", body)
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line.startswith("beadsort "):
            continue
        found.append(shlex.split(line))
    return found


def _resolve(argv: list[str]) -> tuple[click.Command, list[str]]:
    cmd: click.Command = main
    rest = argv[1:]
    while rest and isinstance(cmd, click.Group) and rest[0] in cmd.commands:
        cmd = cmd.commands[rest[0]]
        rest = rest[1:]
    assert not isinstance(cmd, click.Group) or cmd is main, f"{argv}: incomplete command"
    assert cmd is not main or rest[:1] == ["--version"], f"{argv}: unknown command"
    return cmd, rest


def test_frontmatter_names_the_skill_and_says_when_to_use_it() -> None:
    meta, _ = _frontmatter_and_body()
    assert meta["name"] == "beadsort"
    assert "beadsort" in meta["description"] and "bd ready" in meta["description"]
    assert len(meta["description"]) > 200


def test_every_command_in_the_skill_exists_with_those_options() -> None:
    _, body = _frontmatter_and_body()
    invocations = _invocations(body)
    assert len(invocations) >= 12, "the skill lost its command examples"
    for argv in invocations:
        cmd, rest = _resolve(argv)
        known = {opt for p in cmd.params for opt in getattr(p, "opts", [])}
        known |= {opt for p in main.params for opt in getattr(p, "opts", [])}
        for token in rest:
            if token.startswith("-"):
                assert token.split("=", 1)[0] in known, f"{argv}: unknown option {token}"


def test_dimension_table_matches_the_builtin_packs() -> None:
    _, body = _frontmatter_and_body()
    table = re.findall(r"^\| `([a-z-]+)` \| .* \| .* \|$", body, flags=re.M)
    written = {
        d for path in BUILTIN.glob("*.yaml") for d in yaml.safe_load(path.read_text())["dimensions"]
    }
    assert set(table) == written


def test_skill_never_tells_the_agent_to_force_or_print_the_key() -> None:
    _, body = _frontmatter_and_body()
    for argv in _invocations(body):
        assert "--force" not in argv or argv[1:3] == ["config", "init"], argv
    assert "echo $TYPESAFE_API_KEY" not in body
    assert "Never pass `--force`" in body
