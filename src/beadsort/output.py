"""Output for humans and machines: one JSON envelope shape, one plain-text table style."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Sequence
from typing import Any

from beadsort.errors import BeadsortError

SCHEMA_VERSION = 1


def success_envelope(command: str, data: Any) -> dict[str, Any]:
    return {"success": True, "command": command, "schema_version": SCHEMA_VERSION, "data": data}


def error_envelope(command: str, error: BeadsortError) -> dict[str, Any]:
    return {
        "success": False,
        "command": command,
        "schema_version": SCHEMA_VERSION,
        "error": error.to_dict(),
    }


def render_table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    """Fixed-width columns, no dependency. Cells are str()-ed; None renders as '-'."""
    rendered = [[("-" if c is None else str(c)) for c in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in rendered:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(cell))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines = [fmt.format(*headers).rstrip(), fmt.format(*["-" * w for w in widths]).rstrip()]
    for row in rendered:
        padded = list(row) + [""] * (len(widths) - len(row))
        lines.append(fmt.format(*padded[: len(widths)]).rstrip())
    return "\n".join(lines)


class Emitter:
    """Chooses between the JSON envelope and human text for one command invocation."""

    def __init__(self, *, json_mode: bool = False, quiet: bool = False) -> None:
        self.json_mode = json_mode
        self.quiet = quiet

    def result(self, command: str, data: Any, human: str = "") -> None:
        if self.json_mode:
            sys.stdout.write(json.dumps(success_envelope(command, data), indent=1, default=str))
            sys.stdout.write("\n")
        elif human and not self.quiet:
            sys.stdout.write(human.rstrip("\n") + "\n")

    def note(self, text: str) -> None:
        """Progress or warnings: stderr, never mixed into JSON stdout."""
        if not self.quiet:
            sys.stderr.write(text.rstrip("\n") + "\n")

    def fail(self, command: str, error: BeadsortError) -> int:
        if self.json_mode:
            sys.stdout.write(json.dumps(error_envelope(command, error), indent=1, default=str))
            sys.stdout.write("\n")
        else:
            sys.stderr.write(f"error [{error.code}]: {error.message}\n")
            if error.detail and not self.quiet:
                sys.stderr.write(f"  {json.dumps(error.detail, default=str)[:400]}\n")
        return error.exit_code
