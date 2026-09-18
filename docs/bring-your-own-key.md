# Bring your own key

beadsort calls the model provider's API with a key you own. It never bundles a key, never proxies one, and never writes one to disk.

## Getting a key

1. Sign in at <https://console.typesafe.ai/login> (Google, or an emailed one-time code).
2. Create a key at <https://console.typesafe.ai/settings/keys>.
3. Put it in your environment:

   ```sh
   export TYPESAFE_API_KEY=...
   ```

   Or, if you prefer beads to hold it for a repo (the value lands in the beads database, not in a committed file):

   ```sh
   bd config set custom.beadsort.api_key ...
   ```

4. Check it:

   ```sh
   beadsort doctor --online
   ```

Billing is prepaid credit on the provider's side. beadsort's dry run prints the input-token count and an estimate of the cost before you apply anything. A backlog of a few hundred beads is a small fraction of a cent per run.

## Where the key is read from

In order:

1. `TYPESAFE_API_KEY` in the environment.
2. `bd config get custom.beadsort.api_key` in the current repo.

`.beadsort/config.yaml` is refused if it contains an `api_key` entry, because that file is meant to be committed.

## What beadsort never does with the key

- It never prints it. `doctor` reports only where it came from.
- It never sets the SDK's log level. The provider's SDK can log request bodies at `debug`; beadsort leaves that off and does not recommend turning it on around real bead text.
- It never puts it in the cache, the JSON envelopes, the audit records or the playground share links.
