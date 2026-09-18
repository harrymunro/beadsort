# beadsort

The intelligence layer for beads: a Python CLI that classifies beads with a System One model and writes `dimension:value` labels back. See README.md for what it does and docs/ for how packs work.

## Working here

- `uv sync --dev`, then `uv run pytest -q` and `uv run ruff check . && uv run ruff format --check .` before every commit. A behaviour change gets a test.
- Tests use a fake `bd` (`tests/fake_bd.py`) and a fake model (`tests/fakes.py`). Never add a test that needs the network or a key.
- The one hard rule is the fence: beadsort never removes a label it did not write unless `--force`. `tests/test_fence.py` enforces it mechanically. Do not weaken it.
- Dry run is the default; nothing writes to beads without `--apply`.
- Arithmetic, dates, counting stay in code, never in a question to the model.
- Never print, log or store an API key. Never set the SDK log level.
- No benchmark numbers about the model provider in the repo (their terms). No provider name in the project's branding.
- Prose rule: hyphens, never em or en dashes.

## Layout

`src/beadsort/`: `cli.py` (commands only), `bd.py` (subprocess adapter with per-repo lock), `beads.py` (model + labels), `updates.py` (notes newest first), `state.py` (what the model sees), `packs/` (loader, declarative rules, built-in YAML + derivers), `engine.py` (run loop, cache keys, model client), `cache.py`, `writeback.py` (fence), `eval.py`, `share.py` (vendored lz-string), `doctor.py` (metadata probe).

`skills/beadsort/SKILL.md` is the skill users install into their agents; its commands are checked against the CLI by `tests/test_skill.py`, so update both together. Every name, bead id and project in examples, docs, packs and tests is fictional; never paste real client material into the repo.

<!-- BEGIN FRIENDLY SUMMARY POLICY -->
## Friendly Summary policy

Every bead in this repo carries a `## Friendly Summary` heading at the top of its description: one to three plain-English sentences on the user-visible outcome and why it matters, no jargon, file paths or library names. A `## Technical` section follows. A bead without a friendly summary is incomplete.
<!-- END FRIENDLY SUMMARY POLICY -->

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal -->
## Issue tracking

This repo uses beads. `bd ready` to find work, `bd show <id>` to read it, `bd update <id> --claim` to take it, `bd close <id>` when done, `bd prime` for the session brief. Track work in beads, not in markdown TODO lists. Remember things with `bd remember`, not a MEMORY.md.
<!-- END BEADS INTEGRATION -->

## Session completion

Before ending a session: file follow-up beads for anything left, run the quality gates above, update bead status, and `git push`. Work is not complete until the push succeeds.
