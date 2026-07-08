#!/usr/bin/env python3
"""Profile and gate common conversate runtime paths.

The harness measures the real CLI and hook entrypoints through subprocesses,
but it always uses temporary Plugin installation roots and never writes to the
user's real ~/.conversate database. In gate mode it compares each operation's
median and max elapsed time against JSON budgets.
"""
from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import math
import os
import pstats
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


REPO = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
PROFILER_DIR = REPO / "tools" / "profiler"
DEFAULT_BUDGET_FILE = PROFILER_DIR / "runtime_budgets.json"
RESULTS_DIR = PROFILER_DIR / "results"
DEFAULT_RECORDS = 100
MIN_RUNTIME_COVERAGE_RECORDS = 100
DEFAULT_SUBPROCESS_WARMUPS = 1

OPERATION_ORDER = (
    "skill_load_common_path",
    "cli_help",
    "cli_init",
    "cli_list",
    "cli_upsert",
    "cli_rebuild_index",
    "cli_regen_refs",
    "codex_hook",
)

TIME_BUDGET_METRICS = ("median_ms", "max_ms")
COMMON_PATH_BUDGET_METRICS = {
    "file_count": "max_file_count",
    "bytes": "max_bytes",
    "rough_tokens": "max_rough_tokens",
    "broad_file_reads": "broad_file_reads",
}
RUNTIME_COVERAGE_METRICS: dict[str, tuple[str, ...]] = {
    "cli_list": (
        "records_requested",
        "record_files_before",
        "index_records_before",
        "record_files_after",
        "index_records_after",
    ),
    "cli_upsert": (
        "records_requested",
        "record_files_before",
        "index_records_before",
        "record_files_after",
        "index_records_after",
    ),
    "cli_rebuild_index": (
        "records_requested",
        "record_files_before",
        "record_files_after",
        "index_records_after",
    ),
    "cli_regen_refs": (
        "records_requested",
        "record_files_before",
        "record_files_after",
        "index_records_after",
    ),
}
UPSERT_MEASURED_RECORD_START = 10_000
UPSERT_MEASURED_COVERAGE_METRICS = ("measured_record_files_after", "measured_index_records_after")

BUILTIN_BUDGETS: dict[str, dict[str, float]] = {
    "skill_load_common_path": {
        "median_ms": 10.0,
        "max_ms": 50.0,
        "file_count": 2.0,
        "bytes": 5_120.0,
        "rough_tokens": 900.0,
        "broad_file_reads": 0.0,
    },
    "cli_help": {"median_ms": 100.0, "max_ms": 125.0},
    "cli_init": {"median_ms": 100.0, "max_ms": 125.0},
    "cli_list": {"median_ms": 100.0, "max_ms": 125.0},
    "cli_upsert": {"median_ms": 140.0, "max_ms": 175.0},
    "cli_rebuild_index": {"median_ms": 140.0, "max_ms": 175.0},
    "cli_regen_refs": {"median_ms": 140.0, "max_ms": 175.0},
    "codex_hook": {"median_ms": 50.0, "max_ms": 75.0},
}


@dataclass(frozen=True)
class Config:
    runs: int
    records: int
    command_timeout: int
    profile: bool
    profile_dir: Path
    subprocess_warmups: int


def now_stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def ms_since(start: float) -> float:
    return (time.perf_counter() - start) * 1000


def summarize(samples: list[float]) -> dict[str, float]:
    if not samples:
        return {}
    return {
        "runs": len(samples),
        "min_ms": round(min(samples), 3),
        "median_ms": round(statistics.median(samples), 3),
        "avg_ms": round(statistics.mean(samples), 3),
        "max_ms": round(max(samples), 3),
    }


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_")


def read_json_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def normalize_budget(raw: dict[str, Any], *, source: str) -> dict[str, dict[str, float]]:
    budgets: dict[str, dict[str, float]] = {}
    for name, value in raw.items():
        if not isinstance(value, dict):
            raise ValueError(f"{source}: budget for {name!r} must be an object")
        for metric in TIME_BUDGET_METRICS:
            if metric not in value:
                raise ValueError(f"{source}: budget for {name!r} missing {metric!r}")
        normalized: dict[str, float] = {}
        for metric, metric_value in value.items():
            try:
                normalized_value = float(metric_value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{source}: budget for {name!r} has non-numeric {metric!r}") from exc
            if not math.isfinite(normalized_value):
                raise ValueError(f"{source}: budget for {name!r} has non-finite {metric!r}")
            normalized[str(metric)] = normalized_value
        budgets[str(name)] = normalized
    return budgets


def load_budgets(path: Path | None, scale: float) -> dict[str, dict[str, float]]:
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("--budget-scale must be finite and > 0")
    budgets = {name: dict(value) for name, value in BUILTIN_BUDGETS.items()}
    if path is None:
        path = DEFAULT_BUDGET_FILE if DEFAULT_BUDGET_FILE.is_file() else None
    if path is not None:
        budgets.update(normalize_budget(read_json_object(path), source=str(path)))
    return {
        name: {
            metric: round(metric_value * scale, 3) if metric in TIME_BUDGET_METRICS else metric_value
            for metric, metric_value in value.items()
        }
        for name, value in budgets.items()
    }


def printable_cmd(cmd: list[str]) -> list[str]:
    out = []
    for item in cmd:
        try:
            path = Path(item)
            if path.is_absolute():
                try:
                    out.append(str(path.relative_to(REPO)))
                    continue
                except ValueError:
                    pass
        except OSError:
            pass
        out.append(item)
    return out


def run_cmd(
    cmd: list[str],
    *,
    input_text: str | None = None,
    timeout: int = 120,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd or REPO),
            env=env,
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        return {
            "cmd": printable_cmd(cmd),
            "returncode": proc.returncode,
            "timed_out": False,
            "elapsed_ms": round(ms_since(start), 3),
            "stdout_bytes": len(proc.stdout.encode("utf-8", "replace")),
            "stderr_bytes": len(proc.stderr.encode("utf-8", "replace")),
            "stdout_head": proc.stdout[:500],
            "stderr_head": proc.stderr[:500],
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        return {
            "cmd": printable_cmd(cmd),
            "returncode": None,
            "timed_out": True,
            "elapsed_ms": round(ms_since(start), 3),
            "stdout_bytes": len(stdout.encode("utf-8", "replace")),
            "stderr_bytes": len(stderr.encode("utf-8", "replace")),
            "stdout_head": stdout[:500],
            "stderr_head": stderr[:500],
        }


def time_repeated_command(
    label: str,
    runs: int,
    make_cmd: Callable[[int], list[str]],
    *,
    make_input: Callable[[int], str | None] | None = None,
    warmups: int = 0,
    timeout: int,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    warmup_runs = []
    for i in range(warmups):
        sample_index = i - warmups
        warmup_runs.append(
            run_cmd(
                make_cmd(sample_index),
                input_text=make_input(sample_index) if make_input else None,
                timeout=timeout,
                cwd=cwd,
                env=env,
            )
        )
    command_runs = []
    samples = []
    for i in range(runs):
        result = run_cmd(
            make_cmd(i),
            input_text=make_input(i) if make_input else None,
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
        command_runs.append(result)
        samples.append(float(result["elapsed_ms"]))
    return {
        "label": label,
        "kind": "subprocess",
        "root": str(root) if root else None,
        "summary": summarize(samples),
        "samples_ms": [round(sample, 3) for sample in samples],
        "warmup_runs": warmup_runs,
        "returncodes": sorted({run["returncode"] for run in command_runs}, key=lambda value: str(value)),
        "timed_out": any(run["timed_out"] for run in command_runs),
        "command_runs": command_runs,
    }


def time_repeated_callable(label: str, runs: int, func: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    samples = []
    last_detail: dict[str, Any] = {}
    for _ in range(runs):
        start = time.perf_counter()
        last_detail = func()
        samples.append(ms_since(start))
    return {
        "label": label,
        "kind": "in_process",
        "root": None,
        "summary": summarize(samples),
        "samples_ms": [round(sample, 3) for sample in samples],
        "detail": last_detail,
    }


def conv_cli(*args: object) -> list[str]:
    return [PYTHON, str(REPO / "scripts" / "conv_cli.py"), *map(str, args)]


def profiler_record_id(i: int) -> str:
    return f"conv_profiler_{i:04d}"


def conversation_payload(i: int, *, ref_previous: bool = False) -> str:
    data: dict[str, Any] = {
        "id": profiler_record_id(i),
        "topic": f"profiler conversation {i:04d}",
        "status": "active",
        "tags": ["profiler", "synthetic"],
        "sections": {
            "summary": f"synthetic profiler summary {i}",
            "dict": "- **profiler** - synthetic measurement record",
            "qa": "- **Q:** What is this? **A:** A profiling fixture.",
            "decisions": "1. Use temporary roots only.",
        },
        "resume": {
            "goal": "measure conversate latency gates",
            "next_steps": ["collect timings", "compare budgets"],
            "open_questions": [],
            "suggested_skills": ["conversate:save"],
        },
        "user_instructions": ["do not mutate the real conversation database"],
        "condensed_transcript": [
            {"u": "profile this", "a": "created temporary synthetic data"}
        ],
    }
    if ref_previous and i > 0:
        data["refs"] = [{"id": profiler_record_id(i - 1), "rel": "informed-by"}]
    return json.dumps(data)


def init_root(root: Path, timeout: int) -> None:
    result = run_cmd(conv_cli("init", "--conv-root", root), timeout=timeout)
    if result["returncode"] != 0:
        raise RuntimeError(f"could not initialize profiler root {root}: {result}")


def upsert_record(root: Path, i: int, timeout: int, *, ref_previous: bool = False) -> None:
    result = run_cmd(
        conv_cli("upsert", "--stdin", "--conv-root", root),
        input_text=conversation_payload(i, ref_previous=ref_previous),
        timeout=timeout,
    )
    if result["returncode"] != 0:
        raise RuntimeError(f"could not seed profiler record {i} in {root}: {result}")


def record_file_count(root: Path) -> int:
    return len(list((root / "convs").glob("*.md")))


def index_record_count(root: Path) -> int:
    path = root / "index.jsonl"
    if not path.is_file():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def measured_profiler_record_file_count(root: Path, *, start: int, count: int) -> int:
    return sum(1 for i in range(start, start + count) if (root / "convs" / f"{profiler_record_id(i)}.md").is_file())


def measured_profiler_index_record_count(root: Path, *, start: int, count: int) -> int:
    path = root / "index.jsonl"
    if not path.is_file():
        return 0
    expected = {profiler_record_id(i) for i in range(start, start + count)}
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("id") in expected:
            seen.add(str(record["id"]))
    return len(seen)


def coverage_snapshot(root: Path, config: Config) -> dict[str, int]:
    return {
        "records_requested": config.records,
        "min_records": MIN_RUNTIME_COVERAGE_RECORDS,
        "record_files": record_file_count(root),
        "index_records": index_record_count(root),
    }


def attach_runtime_coverage(
    result: dict[str, Any],
    *,
    root: Path,
    config: Config,
    before: dict[str, int],
) -> dict[str, Any]:
    after = coverage_snapshot(root, config)
    result["coverage"] = {
        "records_requested": before["records_requested"],
        "min_records": before["min_records"],
        "record_files_before": before["record_files"],
        "index_records_before": before["index_records"],
        "record_files_after": after["record_files"],
        "index_records_after": after["index_records"],
    }
    return result


def dataset_root(base: Path, config: Config, cache: dict[str, Any]) -> Path:
    key = f"dataset-root-{config.records}"
    cached = cache.get(key)
    if isinstance(cached, Path):
        return cached
    root = base / f"dataset-{config.records}"
    init_root(root, config.command_timeout)
    for i in range(config.records):
        upsert_record(root, i, config.command_timeout, ref_previous=True)
    cache[key] = root
    return root


def operation_dataset_root(base: Path, config: Config, cache: dict[str, Any], name: str) -> Path:
    key = f"operation-dataset-root-{name}-{config.records}"
    cached = cache.get(key)
    if isinstance(cached, Path):
        return cached
    source = dataset_root(base, config, cache)
    root = base / f"{safe_name(name)}-dataset-{config.records}"
    shutil.copytree(source, root)
    cache[key] = root
    return root


DIRECT_COMMON_PATH_SKILLS = {
    "save": (REPO / "skills" / "save" / "SKILL.md",),
    "list": (REPO / "skills" / "list" / "SKILL.md",),
    "resume": (REPO / "skills" / "resume" / "SKILL.md",),
}
FIRST_USEFUL_ACTION_MARKER = "python ~/.conversate/scripts/conv_cli.py"
BROAD_COMMON_PATH_FILES = {
    "SKILL.md",
    "skills/conversate/SKILL.md",
    "references/branching.md",
    "references/cli.md",
    "references/list.md",
    "references/resume.md",
    "references/save.md",
}
FORBIDDEN_PRE_ACTION_TEXT = (
    "~/.conversate/references/",
    "~/.conversate/SKILL.md",
    "follow it exactly",
)


def repo_rel(path: Path) -> str:
    return str(path.relative_to(REPO)).replace("\\", "/")


def rough_token_count(text: str) -> int:
    return (len(text.encode("utf-8")) + 3) // 4


def first_useful_action(text: str) -> tuple[str | None, str]:
    offset = text.find(FIRST_USEFUL_ACTION_MARKER)
    if offset < 0:
        return None, text
    start = text.rfind("\n", 0, offset) + 1
    end = text.find("\n", offset)
    if end < 0:
        end = len(text)
    return text[start:end].strip(" `\t"), text[:offset]


def load_direct_skill_files(paths: tuple[Path, ...]) -> tuple[list[dict[str, Any]], str, int, int]:
    files = []
    combined_text = []
    total_bytes = 0
    total_tokens = 0
    for path in paths:
        text = path.read_text(encoding="utf-8")
        encoded = text.encode("utf-8")
        total_bytes += len(encoded)
        total_tokens += rough_token_count(text)
        combined_text.append(text)
        files.append(
            {
                "path": repo_rel(path),
                "bytes": len(encoded),
                "rough_tokens": rough_token_count(text),
                "lines": len(text.splitlines()),
            }
        )
    return files, "\n".join(combined_text), total_bytes, total_tokens


def load_common_skill_files() -> dict[str, Any]:
    verbs: dict[str, Any] = {}
    loaded_files: list[str] = []
    missing_files: list[str] = []
    missing_first_actions: list[str] = []
    pre_action_broad_mentions: dict[str, list[str]] = {}
    sha = hashlib.sha1()

    for verb, paths in DIRECT_COMMON_PATH_SKILLS.items():
        missing = [repo_rel(path) for path in paths if not path.is_file()]
        if missing:
            missing_files.extend(missing)
            verbs[verb] = {"files": [], "missing_files": missing}
            continue

        files, text, total_bytes, total_tokens = load_direct_skill_files(paths)
        action, pre_action_text = first_useful_action(text)
        if action is None:
            missing_first_actions.append(verb)
        mentions = [pattern for pattern in FORBIDDEN_PRE_ACTION_TEXT if pattern in pre_action_text]
        if mentions:
            pre_action_broad_mentions[verb] = mentions
        for file_detail in files:
            loaded_files.append(file_detail["path"])
            sha.update(file_detail["path"].encode("utf-8"))
            sha.update(b"\0")
            sha.update(text.encode("utf-8"))
        verbs[verb] = {
            "file_count": len(files),
            "bytes": total_bytes,
            "rough_tokens": total_tokens,
            "first_useful_action": action,
            "pre_action_broad_mentions": mentions,
            "files": files,
        }

    loaded_broad_files = sorted(path for path in loaded_files if path in BROAD_COMMON_PATH_FILES)
    verb_values = [value for value in verbs.values() if "file_count" in value]
    return {
        "verb_count": len(verb_values),
        "max_file_count": max((value["file_count"] for value in verb_values), default=0),
        "max_bytes": max((value["bytes"] for value in verb_values), default=0),
        "max_rough_tokens": max((value["rough_tokens"] for value in verb_values), default=0),
        "broad_file_reads": len(loaded_broad_files),
        "loaded_broad_files": loaded_broad_files,
        "missing_files": missing_files,
        "missing_first_actions": missing_first_actions,
        "pre_action_broad_mentions": pre_action_broad_mentions,
        "sha1": sha.hexdigest()[:12],
        "verbs": verbs,
    }


def op_skill_load_common_path(config: Config, base: Path, cache: dict[str, Any]) -> dict[str, Any]:
    del base, cache
    return time_repeated_callable(
        "skill_load_common_path",
        config.runs,
        load_common_skill_files,
    )


def op_cli_help(config: Config, base: Path, cache: dict[str, Any]) -> dict[str, Any]:
    del base, cache
    return time_repeated_command(
        "cli_help",
        config.runs,
        lambda _i: conv_cli("--help"),
        warmups=config.subprocess_warmups,
        timeout=config.command_timeout,
    )


def op_cli_init(config: Config, base: Path, cache: dict[str, Any]) -> dict[str, Any]:
    del cache
    root_base = base / "cli-init"
    return time_repeated_command(
        "cli_init",
        config.runs,
        lambda i: conv_cli("init", "--conv-root", root_base / f"root-{i}"),
        warmups=config.subprocess_warmups,
        timeout=config.command_timeout,
        root=root_base,
    )


def op_cli_list(config: Config, base: Path, cache: dict[str, Any]) -> dict[str, Any]:
    root = operation_dataset_root(base, config, cache, "cli-list")
    before = coverage_snapshot(root, config)
    result = time_repeated_command(
        "cli_list",
        config.runs,
        lambda _i: conv_cli("list", "--json", "--limit", "10", "--conv-root", root),
        warmups=config.subprocess_warmups,
        timeout=config.command_timeout,
        root=root,
    )
    return attach_runtime_coverage(result, root=root, config=config, before=before)


def op_cli_upsert(config: Config, base: Path, cache: dict[str, Any]) -> dict[str, Any]:
    root = operation_dataset_root(base, config, cache, "cli-upsert")
    before = coverage_snapshot(root, config)
    result = time_repeated_command(
        "cli_upsert",
        config.runs,
        lambda _i: conv_cli("upsert", "--stdin", "--conv-root", root),
        make_input=lambda i: conversation_payload(UPSERT_MEASURED_RECORD_START + i),
        warmups=config.subprocess_warmups,
        timeout=config.command_timeout,
        root=root,
    )
    result = attach_runtime_coverage(result, root=root, config=config, before=before)
    result["coverage"].update(
        {
            "measured_records_expected": config.runs,
            "measured_record_files_after": measured_profiler_record_file_count(
                root,
                start=UPSERT_MEASURED_RECORD_START,
                count=config.runs,
            ),
            "measured_index_records_after": measured_profiler_index_record_count(
                root,
                start=UPSERT_MEASURED_RECORD_START,
                count=config.runs,
            ),
        }
    )
    return result


def op_cli_rebuild_index(config: Config, base: Path, cache: dict[str, Any]) -> dict[str, Any]:
    root = operation_dataset_root(base, config, cache, "cli-rebuild-index")
    before = coverage_snapshot(root, config)
    result = time_repeated_command(
        "cli_rebuild_index",
        config.runs,
        lambda _i: conv_cli("rebuild-index", "--conv-root", root),
        warmups=config.subprocess_warmups,
        timeout=config.command_timeout,
        root=root,
    )
    return attach_runtime_coverage(result, root=root, config=config, before=before)


def op_cli_regen_refs(config: Config, base: Path, cache: dict[str, Any]) -> dict[str, Any]:
    root = operation_dataset_root(base, config, cache, "cli-regen-refs")
    before = coverage_snapshot(root, config)
    result = time_repeated_command(
        "cli_regen_refs",
        config.runs,
        lambda _i: conv_cli("regen-refs", "--conv-root", root),
        warmups=config.subprocess_warmups,
        timeout=config.command_timeout,
        root=root,
    )
    return attach_runtime_coverage(result, root=root, config=config, before=before)


def hook_payload(base: Path) -> str:
    return json.dumps(
        {
            "session_id": f"profiler-session-{base.name}",
            "cwd": str(REPO),
            "hook_event_name": "UserPromptSubmit",
            "model": "profiler",
            "permission_mode": "read-only",
            "turn_id": "turn-profiler",
            "prompt": "profile hook startup",
        }
    )


def prepare_codex_hook(base: Path, cache: dict[str, Any]) -> tuple[Path, dict[str, str]]:
    cached = cache.get("codex-hook")
    if cached:
        return cached
    plugin_root = base / "codex-hook-plugin"
    hook = plugin_root / "hooks" / "codex" / "conv_turn_counter.py"
    hook.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO / "hooks" / "codex" / "conv_turn_counter.py", hook)
    (plugin_root / "convs").mkdir(parents=True, exist_ok=True)
    counter_tmp = base / "codex-hook-tmp"
    counter_tmp.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({"TMP": str(counter_tmp), "TEMP": str(counter_tmp), "TMPDIR": str(counter_tmp)})
    cache["codex-hook"] = (hook, env)
    return hook, env


def op_codex_hook(config: Config, base: Path, cache: dict[str, Any]) -> dict[str, Any]:
    hook, env = prepare_codex_hook(base, cache)
    plugin_root = hook.parents[2]
    return time_repeated_command(
        "codex_hook",
        config.runs,
        lambda _i: [PYTHON, str(hook)],
        make_input=lambda _i: hook_payload(base),
        warmups=config.subprocess_warmups,
        timeout=config.command_timeout,
        env=env,
        root=plugin_root,
    )


Operation = Callable[[Config, Path, dict[str, Any]], dict[str, Any]]

OPERATIONS: dict[str, Operation] = {
    "skill_load_common_path": op_skill_load_common_path,
    "cli_help": op_cli_help,
    "cli_init": op_cli_init,
    "cli_list": op_cli_list,
    "cli_upsert": op_cli_upsert,
    "cli_rebuild_index": op_cli_rebuild_index,
    "cli_regen_refs": op_cli_regen_refs,
    "codex_hook": op_codex_hook,
}


def top_profile_functions(profile_path: Path, limit: int = 12) -> list[dict[str, Any]]:
    stats = pstats.Stats(str(profile_path))
    stats.strip_dirs().sort_stats("cumtime")
    rows = []
    for func in (stats.fcn_list or [])[:limit]:
        primitive, total, self_time, cum_time, _callers = stats.stats[func]
        file_name, line_no, func_name = func
        rows.append(
            {
                "function": f"{file_name}:{line_no}:{func_name}",
                "primitive_calls": primitive,
                "total_calls": total,
                "self_s": round(self_time, 6),
                "cum_s": round(cum_time, 6),
            }
        )
    return rows


def profile_python_command(
    label: str,
    script_args: list[str],
    *,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
    profile_dir: Path,
    timeout: int,
) -> dict[str, Any]:
    profile_dir.mkdir(parents=True, exist_ok=True)
    profile_path = profile_dir / f"{safe_name(label)}.prof"
    result = run_cmd(
        [PYTHON, "-m", "cProfile", "-o", str(profile_path), *script_args],
        input_text=input_text,
        env=env,
        timeout=timeout,
    )
    return {
        "label": label,
        "run": result,
        "profile_path": str(profile_path),
        "top_cumulative": top_profile_functions(profile_path) if result["returncode"] == 0 else [],
    }


def profile_skill_loader(profile_dir: Path) -> dict[str, Any]:
    profile_dir.mkdir(parents=True, exist_ok=True)
    profile_path = profile_dir / "skill_load_common_path.prof"
    profiler = cProfile.Profile()
    profiler.enable()
    detail = load_common_skill_files()
    profiler.disable()
    profiler.dump_stats(str(profile_path))
    return {
        "label": "skill_load_common_path",
        "detail": detail,
        "profile_path": str(profile_path),
        "top_cumulative": top_profile_functions(profile_path),
    }


def run_profiles(
    selected: list[str],
    config: Config,
    base: Path,
    cache: dict[str, Any],
) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    if "skill_load_common_path" in selected:
        profiles.append(profile_skill_loader(config.profile_dir))
    if "cli_help" in selected:
        profiles.append(
            profile_python_command(
                "cli_help",
                [str(REPO / "scripts" / "conv_cli.py"), "--help"],
                profile_dir=config.profile_dir,
                timeout=config.command_timeout,
            )
        )
    if "codex_hook" in selected:
        hook, env = prepare_codex_hook(base, cache)
        profiles.append(
            profile_python_command(
                "codex_hook",
                [str(hook)],
                input_text=hook_payload(base),
                env=env,
                profile_dir=config.profile_dir,
                timeout=config.command_timeout,
            )
        )
    return profiles


def operation_order_key(name: str) -> tuple[int, str]:
    try:
        return OPERATION_ORDER.index(name), name
    except ValueError:
        return len(OPERATION_ORDER), name


def evaluate_gate(
    operations: dict[str, dict[str, Any]],
    budgets: dict[str, dict[str, float]],
) -> dict[str, Any]:
    failures = []
    if not operations:
        failures.append({"operation": "selection", "metric": "selection", "reason": "no operations selected"})
    for name in sorted(operations, key=operation_order_key):
        result = operations[name]
        summary = result.get("summary") or {}
        budget = budgets.get(name)
        if not budget:
            failures.append({"operation": name, "metric": "budget", "reason": "missing budget"})
            continue
        if not isinstance(summary, dict) or not summary:
            failures.append({"operation": name, "metric": "summary", "reason": "missing samples"})
            continue
        for metric in TIME_BUDGET_METRICS:
            if metric not in summary:
                failures.append({"operation": name, "metric": metric, "reason": "missing metric"})
                continue
            if metric not in budget:
                failures.append(
                    {
                        "operation": name,
                        "metric": "budget",
                        "budget_metric": metric,
                        "reason": "missing budget metric",
                    }
                )
                continue
            try:
                actual = float(summary[metric])
            except (TypeError, ValueError):
                failures.append({"operation": name, "metric": metric, "reason": "non-numeric metric"})
                continue
            try:
                allowed = float(budget[metric])
            except (TypeError, ValueError):
                failures.append(
                    {
                        "operation": name,
                        "metric": "budget",
                        "budget_metric": metric,
                        "reason": "non-numeric budget metric",
                    }
                )
                continue
            if not math.isfinite(actual):
                failures.append({"operation": name, "metric": metric, "reason": "non-finite metric"})
                continue
            if not math.isfinite(allowed):
                failures.append(
                    {
                        "operation": name,
                        "metric": "budget",
                        "budget_metric": metric,
                        "reason": "non-finite budget metric",
                    }
                )
                continue
            if actual > allowed:
                failures.append(
                    {
                        "operation": name,
                        "metric": metric,
                        "actual_ms": round(actual, 3),
                        "budget_ms": round(allowed, 3),
                    }
                )
        if name == "skill_load_common_path":
            detail = result.get("detail") or {}
            for budget_metric, detail_metric in COMMON_PATH_BUDGET_METRICS.items():
                if budget_metric not in budget:
                    continue
                if detail_metric not in detail:
                    failures.append({"operation": name, "metric": budget_metric, "reason": "missing detail metric"})
                    continue
                try:
                    actual = float(detail[detail_metric])
                    allowed = float(budget[budget_metric])
                except (TypeError, ValueError):
                    failures.append({"operation": name, "metric": budget_metric, "reason": "non-numeric metric"})
                    continue
                if not math.isfinite(actual) or not math.isfinite(allowed):
                    failures.append({"operation": name, "metric": budget_metric, "reason": "non-finite metric"})
                    continue
                if actual > allowed:
                    failures.append(
                        {
                            "operation": name,
                            "metric": budget_metric,
                            "actual": round(actual, 3),
                            "budget": round(allowed, 3),
                        }
                    )
            contract_errors = {
                key: detail.get(key)
                for key in ("missing_files", "missing_first_actions", "pre_action_broad_mentions", "loaded_broad_files")
                if detail.get(key)
            }
            if contract_errors:
                failures.append(
                    {
                        "operation": name,
                        "metric": "common_path_contract",
                        "errors": contract_errors,
                    }
                )
        if name in RUNTIME_COVERAGE_METRICS:
            coverage = result.get("coverage")
            if not isinstance(coverage, dict):
                failures.append(
                    {
                        "operation": name,
                        "metric": "record_coverage",
                        "reason": "missing coverage metadata",
                        "min_records": MIN_RUNTIME_COVERAGE_RECORDS,
                    }
                )
            else:
                required = MIN_RUNTIME_COVERAGE_RECORDS
                reported_min_records = coverage.get("min_records")
                for metric in RUNTIME_COVERAGE_METRICS[name]:
                    try:
                        actual = int(coverage.get(metric, -1))
                    except (TypeError, ValueError):
                        actual = -1
                    if actual < required:
                        failures.append(
                            {
                                "operation": name,
                                "metric": "record_coverage",
                                "coverage_metric": metric,
                                "actual": actual,
                                "min_records": required,
                                "reported_min_records": reported_min_records,
                            }
                        )
                if name == "cli_upsert":
                    try:
                        expected = int(summary.get("runs", 0))
                    except (TypeError, ValueError):
                        expected = 0
                    if expected < 1:
                        failures.append(
                            {
                                "operation": name,
                                "metric": "upsert_measured_records",
                                "reason": "missing measured run count",
                            }
                        )
                    else:
                        for metric in UPSERT_MEASURED_COVERAGE_METRICS:
                            try:
                                actual = int(coverage.get(metric, -1))
                            except (TypeError, ValueError):
                                actual = -1
                            if actual < expected:
                                failures.append(
                                    {
                                        "operation": name,
                                        "metric": "upsert_measured_records",
                                        "coverage_metric": metric,
                                        "actual": actual,
                                        "expected_records": expected,
                                    }
                                )
        command_runs = result.get("command_runs") or []
        bad_runs = [
            {
                "cmd": run.get("cmd"),
                "returncode": run.get("returncode"),
                "timed_out": run.get("timed_out"),
                "stderr_head": run.get("stderr_head"),
            }
            for run in [*(result.get("warmup_runs") or []), *command_runs]
            if run.get("returncode") != 0 or run.get("timed_out")
        ]
        if bad_runs:
            failures.append({"operation": name, "metric": "subprocess", "runs": bad_runs})
    return {"passed": not failures, "failures": failures}


def expand_only(values: list[str] | None) -> list[str]:
    if not values:
        return list(OPERATION_ORDER)
    selected: list[str] = []
    for value in values:
        for item in value.split(","):
            name = item.strip()
            if not name:
                continue
            if name not in OPERATIONS:
                raise ValueError(f"unknown operation {name!r}; choose from {', '.join(OPERATION_ORDER)}")
            if name not in selected:
                selected.append(name)
    return selected


def default_out_path() -> Path:
    return RESULTS_DIR / f"conversate-loading-profile-{now_stamp()}.json"


def write_report(report: dict[str, Any], out: Path | None, *, stdout_json: bool) -> str | None:
    if stdout_json:
        print(json.dumps(report, indent=2))
        return None
    if out is None:
        out = default_out_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return str(out)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Profile conversate skill loading, CLI commands, and the Codex hook with optional budget gates."
    )
    parser.add_argument("--gate", action="store_true", help="exit non-zero when any selected operation exceeds budget")
    parser.add_argument("--runs", type=int, default=5, help="timing samples per operation")
    parser.add_argument(
        "--records",
        type=int,
        default=DEFAULT_RECORDS,
        help="synthetic records for covered runtime paths; gate requires at least 100",
    )
    parser.add_argument("--only", action="append", help="operation name or comma-list; repeatable")
    parser.add_argument("--budget-file", type=Path, help="JSON budget override; defaults to tools/profiler/runtime_budgets.json")
    parser.add_argument("--budget-scale", type=float, default=1.0, help="multiply loaded budgets, useful for smoke tests")
    parser.add_argument("--command-timeout", type=int, default=120, help="subprocess timeout in seconds")
    parser.add_argument("--out", type=str, help="report path; use '-' to print full JSON to stdout")
    parser.add_argument("--profile", action="store_true", help="also write cProfile data for skill load, CLI help, and Codex hook")
    parser.add_argument("--profile-dir", type=Path, help="directory for .prof files; default is ignored profiler results")
    parser.add_argument("--list-operations", action="store_true", help="print available operation names and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list_operations:
        for name in OPERATION_ORDER:
            print(name)
        return 0
    if args.runs < 1:
        parser.error("--runs must be >= 1")
    if args.records < 1:
        parser.error("--records must be >= 1")

    try:
        selected = expand_only(args.only)
        budgets = load_budgets(args.budget_file, args.budget_scale)
    except Exception as exc:
        print(f"profiler: {exc}", file=sys.stderr)
        return 2

    out_path = None if not args.out else (None if args.out == "-" else Path(args.out))
    stdout_json = args.out == "-"
    profile_dir = args.profile_dir or (RESULTS_DIR / f"profiles-{now_stamp()}")
    config = Config(
        runs=args.runs,
        records=args.records,
        command_timeout=args.command_timeout,
        profile=args.profile,
        profile_dir=profile_dir,
        subprocess_warmups=DEFAULT_SUBPROCESS_WARMUPS,
    )

    started = time.perf_counter()
    cache: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="conv-prof-gate-") as tmp:
        base = Path(tmp).resolve()
        operations = {}
        for name in selected:
            operations[name] = OPERATIONS[name](config, base, cache)
        profiles = run_profiles(selected, config, base, cache) if args.profile else []
        gate = evaluate_gate(operations, budgets)
        report = {
            "schema_version": 1,
            "repo": str(REPO),
            "python": sys.version,
            "started_at": now_stamp(),
            "config": {
                "runs": config.runs,
                "records": config.records,
                "selected": selected,
                "command_timeout": config.command_timeout,
                "profile": config.profile,
                "profile_dir": str(config.profile_dir),
                "subprocess_warmups": config.subprocess_warmups,
            },
            "budgets": {name: budgets[name] for name in selected if name in budgets},
            "operations": operations,
            "profiles": profiles,
            "gate": gate,
            "elapsed_ms": round(ms_since(started), 3),
        }

    report_path = write_report(report, out_path, stdout_json=stdout_json)
    if not stdout_json:
        status = "PASSED" if report["gate"]["passed"] else "FAILED"
        print(json.dumps({"gate": status, "report": report_path, "failures": report["gate"]["failures"]}, indent=2))
    if args.gate and not report["gate"]["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
