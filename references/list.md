# Relay list

Use this for `relay:list`, "what's open", and recent conversation requests.

## Commands

- Active, parked, then recent closed:
  `~/.relay/bin/relay list --limit 10`
- JSON for further filtering:
  `~/.relay/bin/relay list --json --limit 50`
- Filter one status:
  `~/.relay/bin/relay list --status active --limit 20`

The list command automatically ensures freshness by snapshotting the archive, reusing metadata-matching cache rows, and parsing only changed or new records. It safely repairs derived cache state (such as the `open` count derived from `## qa`) on the fly when possible, while retaining `index.jsonl` as a compatibility export.
