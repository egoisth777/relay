# Conversate Runtime Profiler

`conversate_loading_profiler.py` measures the common runtime paths that affect
skill startup and command latency:

- common direct skill-file loading for `conv:save`, `conv:list`, and `conv:resume`
- CLI subprocesses for `--help`, `init`, `list`, `upsert`, `rebuild-index`, and `regen-refs`
- the Codex turn-counter hook under a copied temporary plugin layout

The runtime gate uses 100 synthetic records by default and requires at least
100-record coverage for `list`, `upsert`, `rebuild-index`, and `regen-refs`.
Those operations run against populated temporary roots, and the JSON report
includes before/after coverage counts so empty-root or low-record profiles fail
the gate instead of silently passing.
`upsert` also records whether the measured runs created their expected new
records, separate from warmup activity.

The common-path gate verifies each direct verb loads only narrow skill files,
does not front-load broad root or reference docs before the first CLI action,
and stays under the checked-in file-count, byte, and rough-token budgets.

The profiler never writes to the real `~/.conversate` database. CLI commands use
temporary Plugin installation roots, and the hook profiler uses an isolated temp
counter directory.

Run a budget gate:

```powershell
python tools/profiler/conversate_loading_profiler.py --gate
```

Run the required runtime coverage explicitly:

```powershell
python tools/profiler/conversate_loading_profiler.py --gate --runs 1 --records 100
```

Limit a run to selected operations:

```powershell
python tools/profiler/conversate_loading_profiler.py --gate --only cli_list,cli_upsert
```

An empty `--only` selection fails the gate.

Write a report somewhere explicit:

```powershell
python tools/profiler/conversate_loading_profiler.py --gate --out $env:TEMP\conv-profile.json
```

Collect `cProfile` data for the skill loader, CLI help, and Codex hook:

```powershell
python tools/profiler/conversate_loading_profiler.py --profile
```

Write `cProfile` data to an explicit directory:

```powershell
python tools/profiler/conversate_loading_profiler.py --profile --profile-dir $env:TEMP\conv-profiles
```

Default budgets live in `runtime_budgets.json`. The latency gates are 100 ms
median / 125 ms max for CLI subprocesses and 50 ms median / 75 ms max for the
Codex hook. Budget values and `--budget-scale` must be finite numbers. Reports and `.prof` files under
`tools/profiler/results/` are ignored so timestamped profiler artifacts do not
become tracked source files.
