# Writing a pack

A pack is a YAML file: a few questions about a bead, and rules that turn the answers into `dimension:value` labels. Put it in `.beadsort/packs/<id>.yaml` (per repo) or `~/.config/beadsort/packs/<id>.yaml`, enable it in `.beadsort/config.yaml`, and check it with `beadsort packs validate`.

A pack that shares an id with a built-in replaces it for that repo. `beadsort builtin triage > .beadsort/packs/triage.yaml` gives you a copy to edit.

## Shape

```yaml
id: risk                    # [a-z][a-z0-9-]*
version: 1                  # bump when questions change; cached answers are invalidated
description: One line on what this pack decides.

applies_to:                 # all optional
  statuses: [open, in_progress]
  types: [task, feature, bug]
  exclude_types: [epic]

requires: [people]          # config keys the pack needs; skipped with a warning otherwise
state: [parent, open_blockers, children]   # which sections the questions read (documentation
                                           # for readers; every section is always sent)

questions:
  touches_money:
    type: noul
    instructions: "Does `bead.description` describe a change to how money is charged, paid or recorded?"
    criteria:
      "true":  {what: "Charges, invoices, refunds, payouts, ledgers", examples: ["fix the refund double count"]}
      "false": {what: "No money moves or is recorded differently"}
  blast_radius:
    type: score
    instructions: {question: "If this change were wrong, what would break?", focus: "Judge consequences, not difficulty."}
    criteria:
      - {what: "Nothing else depends on it; easy to revert"}
      - {what: "Other code or users rely on it, but tests exist or it can be rolled back"}
      - {what: "Security, credentials, data integrity, money, or no easy rollback"}
  reviewer:
    type: choice
    criteria_from: people   # one option per person in config, then the static ones below
    instructions: "Who should review this change before it lands?"
    criteria:
      nobody: "No review needed"

thresholds:
  min_confidence: 0.6

outputs:
  - {dimension: touches-money, from: touches_money, yes_above: 0.7, no_below: 0.3}
  - {dimension: blast, from: blast_radius, by: argmax, levels: [low, medium, high], min_confidence: 0.5}
  - {dimension: reviewer, from: reviewer, min_confidence: 0.7, skip: [nobody]}
  - dimension: risk
    composite:
      inputs: {blast_radius: {weight: 0.7}, touches_money: {weight: 0.3}}
      buckets: [{max: 0.34, value: low}, {max: 0.67, value: medium}, {value: high}]
```

## Questions

Three types, one judgment each:

- **noul**: a yes/no. Returns the probability of yes. Phrase it so high means yes. Optional `criteria` with `"true"` and `"false"` descriptions (quote the keys in YAML). Use it when the probability itself is the signal.
- **choice**: one option from a fixed set. Returns the option plus a probability for every option and a confidence. Add an `other` or `nobody` option so the model can decline. `criteria_from: people` or `repos` injects entries from the config before your static options.
- **score**: a position on an ordered scale of 2 to 10 levels. Returns a weighted position plus per-level probabilities. Describe each level as a **situation**, not a degree: "broken but a workaround exists" works, "moderately severe" does not. The model never sees level numbers or neighbours.

`instructions` and every criterion can be a string or a structured object. Use objects when an option needs `what`, `not_for` and `examples`, and keep the same field names across options.

### What the model sees

The state for one bead looks like this. Point questions at parts of it with backticked paths.

```json
{
  "project": {"name": "...", "summary": "...", "owner": "Harry", "agent_names": ["Notarius"]},
  "bead": {
    "id": "app-1234", "title": "...", "description": "...",
    "acceptance_criteria": null, "design": null,
    "status": "open", "priority": 2, "issue_type": "task",
    "latest_update": {"date": "2026-09-05", "source": "note", "author": "Notarius", "text": "..."},
    "updates": [ {"date": "2026-09-05", ...}, {"date": "2026-08-20", ...} ]
  },
  "parent": {"id": "app-12", "title": "...", "description_excerpt": "...", "status": "open"},
  "open_blockers": [{"id": "app-99", "title": "...", "status": "open"}],
  "children": [{"id": "app-1234.1", "title": "...", "status": "closed"}]
}
```

`updates` is newest first and `latest_update` is a copy of the first entry. When a question depends on the newest information, say so: "Does `bead.latest_update.text` say the material has arrived?"

`{{owner}}` anywhere in instructions or criteria is replaced with the `owner` from config.

### Things this kind of model is bad at

Keep these out of questions and do them in code or in the state instead:

- counting, arithmetic, comparing dates, reading numeric fields;
- indirection and double negatives;
- long, irrelevant context: trim the state rather than asking the model to ignore parts of it;
- instructions that contradict their criteria.

The provider documents these in detail. Write the exact condition, put the boundary cases in the criteria, and test on your own beads.

## Output rules

Each rule owns one dimension. A dimension may belong to only one enabled pack.

| Rule | Fields |
|---|---|
| choice | `from`, `min_confidence`, `skip: [options that write no label]`, `rename: {option: value}` |
| noul | `from`, `yes_above` (default 0.7), `no_below` (default 0.3), `values: {yes: .., no: ..}`, `uncertain: value` |
| score, argmax | `from`, `by: argmax`, `levels: [value per level]`, `min_confidence` |
| score, expectation | `from`, `by: expectation`, `buckets: [{max, value}, ..., {value}]`, `min_confidence` |
| composite | `composite: {inputs: {qid: {weight, invert}}, buckets, min_confidence}`; score and noul inputs only, each normalised to 0..1 |

Label values must match `[a-z0-9][a-z0-9._-]*` and contain no commas.

When a rule is unsure (confidence below the threshold, a noul in the middle band, a composite with an unsure score input), no label is written for that dimension and the bead is listed by `beadsort review`. With `on_uncertain: write_unsure` in the config, `<dimension>:unsure` is written instead.

## Thresholds

`thresholds:` in the pack holds the numbers the rules read. Override per repo without copying the pack:

```yaml
packs:
  - {id: risk, thresholds: {min_confidence: 0.75}}
```

## Testing a pack

- `beadsort packs validate path/to/pack.yaml` checks the shape.
- `beadsort packs show risk` prints the questions after config is applied.
- `beadsort share <bead> --pack risk` opens one bead with those questions in the playground.
- `beadsort run --pack risk --limit 20` runs it on twenty beads for a fraction of a cent.
- `beadsort eval --pack risk --dimension blast --truth-prefix severity:` scores it against labels you already trust.

Built-in packs use a Python deriver instead of `outputs:` rules, because their cross-question logic (overrides, corroboration, precedence) is real code. The YAML questions and thresholds are still the place to tune them.
