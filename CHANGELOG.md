# Changelog

All notable changes to beadsort are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- `run` (dry run by default, `--apply` to write), `status`, `review`, `eval`, `share`,
  `packs list|show|validate`, `config init`, `doctor --probe`.
- Built-in packs `triage`, `size` and `agent-ready`, each with a Python deriver and
  tunable thresholds; a declarative `outputs:` rule set for user-written packs.
- Per-repo `bd` locking, content-keyed answer cache, deterministic state trimming,
  notes and comments merged newest first.
- The fence: beadsort never removes a label it did not write unless `--force`.
- Metadata written under one `beadsort` key when `bd` round-trips it (probed in a
  scratch repo), otherwise labels only.
- Vendored lz-string encoder for playground share links.
- Thresholds tuned on a real 127-bead backlog: multi-option Choices gate on the chosen
  option's probability, Score gates only reject flat distributions, the resource question
  uses probability mass on blocking options, and the cache key no longer depends on which
  packs are enabled.

### Fixed
- The fence's record of what beadsort wrote survives a run that covers other packs; a
  label written for a pack that is not enabled today is still beadsort's to replace later.
- `--apply` re-sends metadata keys beadsort does not own, so a `bd` that replaces the
  whole metadata object on `--metadata` cannot wipe them.
- `run --json` prints exactly one envelope when the API key is missing or some beads
  fail to apply; the run data rides inside the error envelope.
- `design` and `acceptance_criteria` are trimmed like the description when a bead's state
  exceeds `state.max_total_chars`.
- `run --only` warns about ids that do not exist instead of silently selecting nothing.
- `share` sends the same state sections as `run`; `bd` error details list the bd arguments.
