# Evaluating a pack on your own beads

`beadsort eval` scores one output dimension against ground truth you already have. Run it before trusting `--apply`, and again whenever you change a question or a threshold. The numbers are for you; the model provider's terms do not allow publishing benchmarks of their service, so keep the tables in your own notes.

## Sources of truth

| Flag | Truth |
|---|---|
| `--truth-label human` | The bead carries that label: yes/no. |
| `--truth-prefix waiting-on:` | The value after the prefix on an existing label. |
| `--truth-metadata triage.category` | A value in the bead's metadata. |
| `--truth-json truth.json` | A file `{ "bead-id": "value", ... }`. |

`--positive a,b,c` collapses both sides to yes/no: the prediction is yes when its value is in the set. Useful when the truth is binary (a `human` label) and the prediction is not (`waiting-on:mike`).

## Examples

```sh
# Did triage find the beads I had labelled 'human'?
beadsort eval --pack triage --dimension waiting-on \
  --truth-label human --positive person,owner,mike,james,josiane --status open

# Exact stakeholder, from old ad hoc labels like mike-question
beadsort eval --pack triage --dimension waiting-on --truth-json truth/waiting-on.json

# Size against S/M/L estimates from an older triage
beadsort eval --pack size --dimension size --truth-json truth/size.json
```

## Reading the report

- **coverage**: the share of beads with truth that got a label. The rest were unsure.
- **accuracy** and per-value **precision / recall / F1**: over the covered beads only.
- **confidence bands**: accuracy within each band of the pack's own confidence. If accuracy is flat across bands, the thresholds are not doing anything; if it climbs, raise the acting threshold until the top band is as accurate as you need.
- **review yield**: of the beads left unsure, how many the forced top answer would have got wrong. High yield means the uncertainty is real.
- **disagreements**: highest confidence first. The confident mistakes are where to look for a missing `not_for` or a misleading example in the criteria.

Change one thing at a time: a criterion, a threshold, an example. Bump the pack `version` when the questions change so cached answers are re-asked; threshold changes need no re-run.

## Converting an older triage's verdicts

`examples/truth-from-bead-triage.py` turns a bead-triage `state.json` into the truth files above, keeping only beads whose text has not changed since the verdict was recorded.
