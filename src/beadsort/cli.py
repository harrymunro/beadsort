"""Command line entry point. Owns exit codes and the JSON/human switch; no logic lives here."""

from __future__ import annotations

import click

from beadsort import __version__


@click.group()
@click.version_option(__version__, prog_name="beadsort")
def main() -> None:
    """beadsort: typed, calibrated labels for your beads backlog."""
