# Contributing

Thanks for looking. beadsort is small on purpose; the best contributions keep it that way.

## Setup

```sh
git clone https://github.com/harrymunro/beadsort && cd beadsort
uv sync --dev
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Tests run against a fake `bd` (`tests/fake_bd.py`) and a fake model (`tests/fakes.py`). No network, no key. If you add a `bd` behaviour beadsort depends on, teach the fake first.

## Ground rules

- **The fence stays.** beadsort never removes a label it did not write unless `--force`. `tests/test_fence.py` encodes this; do not weaken it.
- **Dry run is the default.** Nothing writes to beads without `--apply`.
- **Arithmetic, dates and counting live in code**, never in a question to the model.
- **No key ever touches disk, output or logs.**
- **No benchmark tables** about the model provider in the repo (their terms). Accuracy numbers belong in your own notes; `beadsort eval` exists for that.
- Hyphens, not em dashes, in prose.

## Packs

The built-in packs are YAML questions plus a Python deriver each (`src/beadsort/packs/*.py`). Question wording changes should come with a reason: a real bead that was misjudged and how the new wording fixes it. Bump the pack `version` when questions change.

User-facing pack rules (`outputs:`) live in `src/beadsort/packs/declarative.py` and are documented in `docs/pack-authoring.md`. Keep the two in sync.

## Pull requests

- One change per PR, with a test that fails before and passes after.
- Run ruff and pytest locally; CI runs the same on Python 3.11 to 3.13.
- Update `CHANGELOG.md` under Unreleased.
