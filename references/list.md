# Relay list

Use this for `relay:list`, "what's open", and recent conversation requests.

## Commands

- Active, parked, then recent closed:
  `~/.relay/bin/relay list --limit 10`
- JSON for further filtering:
  `~/.relay/bin/relay list --json --limit 50`
- Filter one status:
  `~/.relay/bin/relay list --status active --limit 20`

The list command reads only the derived index at `~/.relay/index.jsonl`. It does not
read Relay archive markdown files directly. The `open` column is a derived cache
count rebuilt from `## qa` whenever the index is rebuilt.
