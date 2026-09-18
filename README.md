<div align="center">

# beadsort

**The intelligence layer for [beads](https://github.com/gastownhall/beads).**

[![PyPI version](https://img.shields.io/pypi/v/beadsort)](https://pypi.org/project/beadsort/)
[![Python](https://img.shields.io/pypi/pyversions/beadsort)](https://pypi.org/project/beadsort/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/harrymunro/beadsort/actions/workflows/ci.yml/badge.svg)](https://github.com/harrymunro/beadsort/actions/workflows/ci.yml)

</div>

---

beadsort labels every bead with fast, calibrated judgments so `bd ready` hands agents only the work they can actually do, and hands you only the decisions that are actually yours.

Typed answers, not prose. Re-run the whole backlog in seconds. Written straight back into beads.

```text
$ beadsort run
bead         title                                     dimension    change            note
-----------  ----------------------------------------  -----------  ----------------  ------
fls-tq6e.11  Bring a named FLS cyber security person…  waiting-on   - -> sebastian
fls-tq6e.11  Bring a named FLS cyber security person…  ask-urgency  - -> blocking
fls-tq6e.22  Send the retention email to Sebastian     stale        - -> done
fls-jytu.2   Get the client list from Mike             waiting-on   mike -> romy
fls-m3cc.1   Decide where the forecast lives           waiting-on   - -> owner
fls-m3cc.1   Decide where the forecast lives           owner-kind   - -> decision
fls-8yih     Exclude epics from the workable count     size         - -> s
fls-8yih     Exclude epics from the workable count     agent-ready  - -> yes
127 bead(s), 127 model call(s), 0 cached, 233,410 input tokens (~$0.0098)
6 bead(s) need a human look: `beadsort review`
dry run: nothing written. Add --apply to write labels into beads.
```

## Why beadsort?

A beads backlog knows more than its labels do. Every bead says, somewhere in its text, who it is waiting on, how big it is and whether an agent could pick it up cold. Nobody labels that by hand for long, and asking a chat model to do it is slow, expensive and drifts from one bead to the next.

beadsort asks a [System One model](https://docs.typesafe.ai/concepts/system-one) instead: a model built to answer typed questions with calibrated probabilities rather than to write text. Each bead becomes a JSON state; each pack is a set of atomic questions about it; every question is answered independently in one request. The answers come back as labels like `waiting-on:mike`, `size:m` and `agent-ready:no`, plus the probabilities behind them. The whole backlog costs about a cent, so labels stop being state you maintain and become a view you regenerate.

### 🧑‍💻 For Humans

- **Three packs out of the box.** `triage` (who is this waiting on: a named person, you, or an agent; is the ask already satisfied), `size` (t-shirt size and breakage risk from independent scores), `agent-ready` (can an agent start it cold, and if not, what blocks it).
- **Dry run by default.** `beadsort run` shows every proposed change. `--apply` writes.
- **Only what changed.** Re-runs skip beads whose text, questions and model are unchanged.
- **Never clobbers you.** beadsort removes only labels it wrote itself. A label you set by hand on the same dimension is left alone and reported.
- **Unsure means unsure.** Below the confidence thresholds nothing is written; the bead lands in `beadsort review` with the reason and the runner-up answer.
- **Tune on your own data.** `beadsort eval` scores a pack against labels you already have. `beadsort share` opens any bead in the provider's playground with the exact questions beadsort sends.

### 🤖 For Agents

- `--json` on every command, one envelope shape, stable error codes, exit codes that mean something.
- Zero interactive prompts.
- The labels are plain `dimension:value` beads labels, so the whole `bd` toolkit already understands them:

```sh
bd ready -l agent-ready:yes                 # only work an agent can start cold
bd ready --exclude-label waiting-on:mike    # skip anything waiting on Mike
bd list -l waiting-on:owner                 # decisions and admin only you can do
bd query "label=size:s AND status=open"     # small open work
bd state fls-8yih size                      # -> s
```

## Running it

```sh
uv tool install beadsort            # or: pipx install beadsort / pip install beadsort
export TYPESAFE_API_KEY=...         # see docs/bring-your-own-key.md

cd your-beads-repo
beadsort config init                # writes .beadsort/config.yaml; fill in owner, project, people
beadsort doctor --probe             # checks bd, config, packs, your key, and metadata support
beadsort run                        # dry run: what would change, what it would cost
beadsort review                     # beads the model was unsure about, and why
beadsort run --apply                # write labels and metadata into beads
```

| Command | What it does |
|---|---|
| `run` | Classify beads. Dry run by default; `--apply` writes. `--only ID`, `--status`, `--type`, `--limit`, `--pack`, `--force`, `--workers`, `--model`, `--offline`, `--root DIR` (every repo under a directory). |
| `status [ID]` | What beadsort knows: counts per label, cache freshness, last run, metadata support. With an id: that bead's cached answers. |
| `review` | Beads that need a human look, ordered by priority, with the reasons. |
| `eval` | Score one dimension against ground truth: `--truth-label`, `--truth-prefix`, `--truth-metadata` or `--truth-json`. Precision, recall, F1, confidence bands. |
| `share ID` | A playground link carrying the exact state and questions for one bead. |
| `packs list \| show \| validate` | Inspect and check question packs. |
| `config init` | Write a commented config template. |
| `doctor [--probe] [--online]` | Health checks; `--probe` tests metadata round-trip in a scratch repo. |

Every command takes `-C DIR` to run elsewhere, `--json` for the envelope and `-q` for silence.

## What gets written

| Dimension | Values | Pack |
|---|---|---|
| `waiting-on` | `<person>` `person` `owner` `agent` `nobody` | triage |
| `owner-kind` | `decision` `admin` | triage |
| `ask-urgency` | `blocking` `soon` `whenever` | triage |
| `stale` | `done` `superseded` `no-consumer` `umbrella` (flags only; beadsort never closes a bead) | triage |
| `size` | `s` `m` `l` | size |
| `risk` | `low` `medium` `high` | size |
| `agent-ready` | `yes` `no` | agent-ready |
| `blocker` | `dependency` `human-input` `owner-decision` `done` `not-code` `private-file` `secret-or-network` `deployed-env` `off-board` `spec` | agent-ready |

Probabilities, confidence, the pack version and the model that answered go into the bead's metadata under one `beadsort` key, when the installed `bd` round-trips metadata (`doctor --probe` finds out). Each model call is also recorded with `bd audit record`.

## Configuration

`.beadsort/config.yaml` is committed with the repo. The important parts:

```yaml
owner: "Harry"                       # substituted for {{owner}} in every question
project:
  summary: "Consultancy engagement: four hosted tools plus advisory material."
  agent_names: [Notarius, Scriba]    # update authors that are never stakeholders
people:                              # who a bead can be waiting on
  mike:  {name: "Mike Lefler", aliases: [Mike], role: "programme sponsor"}
  romy:  {name: "Romy", role: "CRM owner"}
packs: [triage, size, agent-ready]
model: jev-latest                    # pin a version once thresholds are tuned
```

The API key is never in that file. It comes from `TYPESAFE_API_KEY`, or from `bd config set custom.beadsort.api_key ...`.

Writing your own pack is a YAML file with questions and a small rules block that turns answers into labels. See [docs/pack-authoring.md](docs/pack-authoring.md) and [examples/packs/risk.yaml](examples/packs/risk.yaml).

## How it decides

Each bead is sent as structured state: the title, description and acceptance criteria; the notes and comments merged into one list, newest first, because the newest update is the truth; the parent's title; open blockers; children for epics. Labels, dates and the assignee are never sent.

Each pack asks a handful of literal, one-thing questions: "Does the latest update say the ask has already been answered?", "What does this work need that a fresh checkout does not contain?". The model returns a probability distribution per question. Code then combines them: a code half that can already start beats a pending question; a hesitant category needs corroboration from an independent yes/no; a satisfied ask suppresses `waiting-on` and flags `stale:done`. Thresholds live in the pack YAML and can be overridden per repo.

Anything the model is unsure about is left unlabelled and listed by `beadsort review`, not guessed.

## Privacy

Bead text, your project brief and the names in your people list are sent to the model provider's API with your own key. Read [docs/privacy.md](docs/privacy.md) before pointing beadsort at a tracker that holds confidential material.

## Development

```sh
git clone https://github.com/harrymunro/beadsort && cd beadsort
uv sync --dev
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Tests run against a fake `bd` and a fake model; no network and no key are needed.

## License

MIT. beadsort is an independent project and is not affiliated with the model provider or with beads.
