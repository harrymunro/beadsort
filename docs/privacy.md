# Privacy

beadsort sends bead text to a third-party model API. Read this before pointing it at a tracker that holds confidential material.

## What is sent

For every bead that is classified, one request carries:

- the bead's id, title, description, acceptance criteria and design notes, trimmed to the limits in your config;
- its notes and comments, merged into one list newest first, each entry trimmed;
- its status, priority and type;
- the parent bead's title and a short excerpt of its description;
- the titles of open beads it is blocked by, and the titles of its children if it is an epic;
- your project brief from `.beadsort/config.yaml` (name, summary, conventions, owner, agent names);
- the people list, as the options of the "who is this waiting on" question: name, aliases and role for each person.

Not sent: labels, timestamps, the assignee, anything outside the repo's beads database.

## What comes back and where it goes

Typed answers: an option, a score or a probability per question. They are stored in `.beadsort/cache.json` (gitignored), written as labels and metadata into the beads database on `--apply`, and summarised in `.beads/interactions.jsonl` through `bd audit record` (gitignored by beads).

## What the provider keeps

That is governed by the provider's terms, not by beadsort. At the time of writing their agreement states that inputs and outputs are processed to run the service and are not added to training data without consent, while telemetry such as logs, hashes and summary statistics may be retained. Zero-data-retention arrangements exist for enterprise customers. Check the current terms yourself; beadsort cannot make promises on the provider's behalf.

## Reducing exposure

- Lower `state.max_description_chars` and `state.max_updates` in the config. Less text is also more accurate for this kind of model.
- Run on a subset: `beadsort run --only ID` or `--status open --type task`.
- Keep the project brief short and free of anything you would not put in a bead.
- `beadsort share` produces a URL that embeds the bead text. Treat the link as you would the bead.
- The provider's SDK can log full request bodies at `debug`. beadsort never enables that; do not set `TYPESAFE_LOG_LEVEL=debug` around real data.
