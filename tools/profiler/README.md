# Relay Runtime Profiler

`relay_loading_profiler.py` measures the common runtime paths that affect
skill startup and command latency:

- common direct skill-file loading for `relay:save`, `relay:list`, and `relay:resume`
- CLI subprocesses for `--help`, `init`, `list`, `upsert`, warm/full
  `rebuild-index`, `regen-refs`, indexed search, and context-pack rendering
- the Codex turn-counter hook under a copied temporary plugin layout

The runtime gate uses 100 synthetic records by default and requires at least
100-record coverage for every archive runtime workload.
Those operations run against populated temporary roots, and the JSON report
includes before/after coverage counts so empty-root or low-record profiles fail
the gate instead of silently passing.
`upsert` also records whether the measured runs created their expected new
records, separate from warmup activity.

The common-path gate verifies each direct verb loads only narrow skill files,
does not front-load broad root or reference docs before the first CLI action,
and stays under the checked-in file-count, byte, and rough-token budgets.

The profiler never writes to the real `~/.relay` database. CLI commands use
temporary Plugin installation roots, and the hook profiler uses an isolated temp
counter directory.

Run a budget gate:

```powershell
python tools/profiler/relay_loading_profiler.py --gate --binary <release-relay> --budget-profile v2
```

Run the required runtime coverage explicitly:

```powershell
python tools/profiler/relay_loading_profiler.py --gate --binary <release-relay> --budget-profile v2 --runs 21 --records 100
```

Limit a run to selected operations:

```powershell
python tools/profiler/relay_loading_profiler.py --gate --binary <release-relay> --budget-profile v2 --only cli_list,cli_upsert
```

An empty `--only` selection fails the gate.

Write a report somewhere explicit:

```powershell
python tools/profiler/relay_loading_profiler.py --gate --out $env:TEMP\relay-profile.json
```

Collect `cProfile` data for the skill loader, CLI help, and Codex hook:

```powershell
python tools/profiler/relay_loading_profiler.py --profile
```

Write `cProfile` data to an explicit directory:

```powershell
python tools/profiler/relay_loading_profiler.py --profile --profile-dir $env:TEMP\relay-profiles
```

Versioned budgets live in `runtime_budgets.m1.json` and
`runtime_budgets.v2.json`; gate mode requires one explicitly (or an explicit budget
file) and an explicit release binary. Every subprocess gets two warmups and cloned
pre-state per sample. Reports include provenance, fixture metadata, structural I/O,
coverage, resources, and all failed/timed-out attempts. Scale gates default to the
10,000-record/21-run profile and require an explicit release binary and budget profile:

```powershell
python tools/profiler/relay_loading_profiler.py --scale-gates --binary <release-relay> --budget-profile v2
```

Use `--runs` for deterministic integration-test overrides; such reports are not
promotion eligible unless they retain 21 measured runs. Budget values and `--budget-scale` must be
finite numbers. Reports and `.prof` files under
`tools/profiler/results/` are ignored so timestamped profiler artifacts do not
become tracked source files.
