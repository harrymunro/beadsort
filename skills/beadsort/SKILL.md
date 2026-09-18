---
name: beadsort
description: Set up and run beadsort, the intelligence layer for beads, in a repository tracked with bd. Covers installing the tool, writing .beadsort/config.yaml, checking health with doctor, dry-running classification, reviewing unsure beads, applying labels only with the user's go-ahead, and filtering bd ready by the labels it writes (waiting-on, size, risk, agent-ready, blocker). Use this whenever the user mentions beadsort, wants beads labelled or triaged automatically, asks who a bead is waiting on or how big it is, wants to know which beads an agent can start cold, or wants bd ready to hand out only workable beads, even when they never say the word beadsort.
---

# beadsort

beadsort reads every bead in a beads repo, asks a small typed-answer model a fixed set of
questions about each one, and writes the answers back as plain `dimension:value` labels
such as `waiting-on:dana`, `size:m` and `agent-ready:no`. A whole backlog costs about a
cent. Nothing is written until `--apply`.

## Rules that keep the user safe

Read these first; the rest of the skill assumes them.

- **Dry run first, always.** `beadsort run` changes nothing. Show the user the dry run and
  get an explicit yes before `beadsort run --apply`. Labels are cheap to add, but the user
  has to live with them in `bd ready`.
- **Never pass `--force`.** beadsort only ever removes labels it wrote itself (the fence).
  `--force` lets it replace labels a human set by hand. If a foreign label looks wrong, say
  so to the user; do not force it.
- **Never handle the API key as text.** Do not ask the user to paste it, do not echo it, do
  not write it into any file, and do not put it in `.beadsort/config.yaml` (beadsort refuses
  that file if it holds a key). The key comes from the `TYPESAFE_API_KEY` environment
  variable, or from `bd config set custom.beadsort.api_key ...`, which the user runs
  themselves. If neither is set, stop and point the user at
  https://console.typesafe.ai/settings/keys.
- **Bead text leaves the machine.** Titles, descriptions, notes, the project brief and the
  people list go to the model provider under the user's key. Before the first run on a
  tracker that might hold confidential material, say so in one sentence and let the user
  decide. `docs/privacy.md` in the beadsort repo has the detail.
- **Do not close, edit or reprioritise beads on beadsort's behalf.** `stale:done` and its
  siblings are flags for a human, not instructions.

## Setup

Work through these in order. Each step can be re-run after a fix.

1. **Check the tools.** Run `bd --version` and `beadsort --version`. Install beadsort if it
   is missing:

   ```sh
   uv tool install git+https://github.com/harrymunro/beadsort
   # without uv: pipx install git+https://github.com/harrymunro/beadsort
   ```

   beadsort needs Python 3.11 or newer and a `bd` on PATH. If `bd` is missing, the repo is
   not a beads repo yet; ask before running `bd init`.

2. **Confirm a key is available without reading it.** `beadsort doctor` reports whether an
   API key was found and where it came from (`env` or `bd config`). It never prints the
   key. If none is found, stop and tell the user how to set one.

3. **Write the config.** `beadsort config init` creates a commented `.beadsort/config.yaml`
   and a `.beadsort/.gitignore`. Fill in the config from what the repo already tells you:

   - `owner`: the person who runs this backlog, by first name (from `git log` or ask).
     Questions read "{{owner}} must decide", and a placeholder collides with beads that
     talk about business owners.
   - `project.name` and `project.summary`: two or three sentences on what the project is,
     taken from the README. Leave out anything the user would not want sent to a third
     party.
   - `project.agent_names`: the names automated agents sign updates with. Look at bead
     notes for a pattern like `2026-09-05 (Scriba): ...`. They are never stakeholders.
   - `people`: everyone a bead might be waiting on, as `slug: {name, aliases, role}`. Mine
     bead titles and notes (`bd list`, `bd show ID`) for recurring first names, then
     confirm the list with the user. The triage pack is skipped with a warning while this
     is empty.
   - Leave `packs`, `model`, `state` and `select` at their defaults on a first run.

4. **Check health.** `beadsort doctor --probe` checks bd, config, packs and the key, and
   tests in a scratch repo whether the installed bd round-trips metadata. Fix anything it
   reports. `beadsort doctor --online` also checks that the API answers with the user's key.

## Running

```sh
beadsort run                 # dry run: table of proposed label changes, cost estimate
beadsort review              # beads the model was unsure about, and why
beadsort run --apply         # write labels (and metadata when bd supports it)
beadsort status              # counts per label, cache freshness, last run
```

Read the dry-run table for the user: one row per bead and dimension, `change` is
`old -> new`, and a note of `unsure` means that label will not be written. The summary line
gives beads, model calls, cache hits and an estimated cost. Say what the run would write and
what it costs, then ask for `--apply`.

Useful narrowing flags on `run`: `--only ID` (repeatable), `--status open`, `--type task`,
`--limit 20` for a cheap first look, `--pack triage` for one pack, `--offline` to reuse
cached answers with no model call, and `--root DIR` to run over every beads repo under a
directory. Re-runs only call the model for beads whose text, questions or model changed.

## Using the labels

The output is ordinary beads labels, so the whole `bd` toolkit already understands them:

```sh
bd ready -l agent-ready:yes                 # only work an agent can start cold
bd ready --exclude-label waiting-on:dana    # skip anything waiting on one person
bd list -l waiting-on:owner                 # decisions and admin only the owner can do
bd query "label=size:s AND status=open"     # small open work
```

| Dimension | Values | Meaning |
|---|---|---|
| `waiting-on` | a person slug, `person`, `owner`, `agent`, `nobody` | who has to act next |
| `owner-kind` | `decision`, `admin` | what the owner has to do |
| `ask-urgency` | `blocking`, `soon`, `whenever` | how urgent the ask is |
| `stale` | `done`, `superseded`, `no-consumer`, `umbrella` | the bead may be closable; a human decides |
| `size` | `s`, `m`, `l` | effort |
| `risk` | `low`, `medium`, `high` | breakage risk |
| `agent-ready` | `yes`, `no` | can an agent start it in a fresh checkout |
| `blocker` | `dependency`, `human-input`, `owner-decision`, `done`, `not-code`, `private-file`, `secret-or-network`, `deployed-env`, `off-board`, `spec` | why an agent cannot start it |

When choosing your own next task, `bd ready -l agent-ready:yes` is the shortlist, and the
`blocker` label on the rest tells you what to ask the user for.

## Machine-readable output

Every command takes `--json` and prints exactly one envelope on stdout. Progress and
warnings go to stderr, so stdout always parses.

```json
{"success": true, "command": "run", "schema_version": 1, "data": {}}
{"success": false, "command": "run", "schema_version": 1, "error": {"code": "no_api_key", "message": "..."}, "data": {}}
```

Exit codes: 0 ok, 1 error, 2 usage, 3 bd failed, 4 model API or key problem, 5 partial
apply (some beads failed; `data.applied.failed` lists them). Prefer `--json` when you need
per-bead results: `data.results[]` carries `labels`, `current`, `review`, `uncertain` and
`error` for each bead.

## Command reference

| Command | Purpose |
|---|---|
| `beadsort run` | Classify. Dry run by default; `--apply` writes. |
| `beadsort status [ID]` | What beadsort knows; with an id, that bead's cached answers. |
| `beadsort review --dimension D` | Beads left unlabelled on one dimension, with reasons. Omit the flag for all. |
| `beadsort eval --pack P --dimension D --truth-prefix D:` | Score a pack against labels the user already trusts. See `docs/eval.md`. |
| `beadsort share ID` | Playground link with the exact state and questions for one bead. The link embeds bead text; treat it as you would the bead. |
| `beadsort packs list`, `beadsort packs show P`, `beadsort packs validate FILE` | Inspect and check question packs. |
| `beadsort config init --force` | Rewrite the config template over an existing one. |
| `beadsort doctor --probe --online` | Health checks, metadata probe, API reachability. |

Every command also takes `-C DIR` to run in another directory, `--json` and `-q`.

## When something goes wrong

- `doctor` finds no API key: stop. The user sets `TYPESAFE_API_KEY` or runs
  `bd config set custom.beadsort.api_key ...` themselves.
- A warning that the triage pack requires `people`: fill in `people` in the config.
- `--apply` reports metadata as unsupported or unknown: the installed bd does not
  round-trip metadata. Labels were still written, and that is fine.
- Exit code 3: the error detail lists the bd arguments that failed. Usually a bd version
  or lock problem; `bd doctor` helps.
- The apply summary says foreign labels were respected: that is the fence working. Tell the
  user, and do not `--force`.
- Many beads land in `review`: normal on a first run. Show the user the reasons; they often
  point at a missing person in `people` or a project summary that is too thin.

## Going further

Read these from the beadsort repository only when the task needs them:

- `docs/pack-authoring.md`: writing a custom pack (a YAML of questions plus rules) for a
  dimension the built-ins do not cover.
- `docs/eval.md`: measuring a pack against ground truth before trusting `--apply`, and
  tuning thresholds.
- `docs/privacy.md`: exactly what is sent to the provider and how to send less.
- `docs/bring-your-own-key.md`: where the key comes from and what beadsort never does
  with it.
