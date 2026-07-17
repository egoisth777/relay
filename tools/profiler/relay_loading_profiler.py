#!/usr/bin/env python3
"""Profile and gate common relay runtime paths.

The harness measures the real CLI and hook entrypoints through subprocesses,
but it always uses temporary Plugin installation roots and never writes to the
user's real ~/.relay database. In gate mode it compares each operation's
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
import threading


def _process_resources(pid: int) -> tuple[int, int]:
    if os.name == "nt":
        try:
            import ctypes

            class Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.c_ulong),
                    ("faults", ctypes.c_ulong),
                    ("peak", ctypes.c_size_t),
                    ("working", ctypes.c_size_t),
                    ("quota_peak_paged", ctypes.c_size_t),
                    ("quota_paged", ctypes.c_size_t),
                    ("quota_peak_nonpaged", ctypes.c_size_t),
                    ("quota_nonpaged", ctypes.c_size_t),
                    ("pagefile", ctypes.c_size_t),
                    ("peak_pagefile", ctypes.c_size_t),
                    ("private_usage", ctypes.c_size_t),
                ]

            handle = ctypes.windll.kernel32.OpenProcess(0x0410, False, pid)
            if not handle:
                return 0, 0
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
            handles = ctypes.c_ulong()
            ctypes.windll.kernel32.GetProcessHandleCount(handle, ctypes.byref(handles))
            ctypes.windll.kernel32.CloseHandle(handle)
            return int(counters.peak), int(handles.value)
        except (AttributeError, OSError, TypeError):
            return 0, 0
    if sys.platform == "darwin":
        try:
            import ctypes
            import resource

            rss = int(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
            libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
            buffer = ctypes.create_string_buffer(8 * 4096)
            size = libproc.proc_pidinfo(pid, 1, 0, buffer, len(buffer))
            return rss, max(0, int(size) // 8)
        except (OSError, AttributeError, TypeError, ValueError):
            return 0, 0
    try:
        peak = 0
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmHWM:"):
                peak = int(line.split()[1]) * 1024
                break
        return peak, len(list(Path(f"/proc/{pid}/fd").iterdir()))
    except (OSError, ValueError):
        return 0, 0


def run_scale_cmd(
    cmd: list[str],
    *,
    input_text: str | None = None,
    timeout: int = 120,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    start = time.perf_counter()
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd or REPO),
            stdin=subprocess.PIPE if input_text is not None else None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        return {
            "cmd": printable_cmd(cmd),
            "returncode": None,
            "timed_out": False,
            "elapsed_ms": round(ms_since(start), 3),
            "stdout_bytes": 0,
            "stderr_bytes": len(str(exc)),
            "stdout_head": "",
            "stderr_head": str(exc)[:500],
        }
    output: dict[str, Any] = {}

    def communicate() -> None:
        stdout, stderr = proc.communicate(input=input_text)
        output["stdout"] = stdout
        output["stderr"] = stderr
        output["finished_at"] = time.perf_counter()

    thread = threading.Thread(target=communicate, daemon=True)
    thread.start()
    peak_rss = peak_handles = 0
    deadline = time.perf_counter() + timeout
    while thread.is_alive():
        rss, handles = _process_resources(proc.pid)
        peak_rss = max(peak_rss, rss)
        peak_handles = max(peak_handles, handles)
        if time.perf_counter() >= deadline:
            proc.kill()
            thread.join(timeout=5)
            return {
                "cmd": printable_cmd(cmd),
                "returncode": None,
                "timed_out": True,
                "elapsed_ms": round((output.get("finished_at", time.perf_counter()) - start) * 1000, 3),
                "stdout_bytes": len(output.get("stdout", "").encode("utf-8", "replace")),
                "stderr_bytes": len(output.get("stderr", "").encode("utf-8", "replace")),
                "stdout_head": output.get("stdout", "")[:500],
                "stderr_head": output.get("stderr", "")[:500],
                "query_hits_observed": _query_hits(output.get("stdout", "")),
                "resource": {"peak_rss_bytes": peak_rss, "peak_open_handles": peak_handles},
            }
        time.sleep(0.01)
    thread.join()
    stdout = output.get("stdout", "")
    stderr = output.get("stderr", "")
    return {
        "cmd": printable_cmd(cmd),
        "timed_out": False,
        "returncode": proc.returncode,
        "elapsed_ms": round((output.get("finished_at", time.perf_counter()) - start) * 1000, 3),
        "stdout_bytes": len(stdout.encode("utf-8", "replace")),
        "stderr_bytes": len(stderr.encode("utf-8", "replace")),
        "stdout_head": stdout[:500],
        "stderr_head": stderr[:500],
        "query_hits_observed": _query_hits(stdout),
        "resource": {"peak_rss_bytes": peak_rss, "peak_open_handles": peak_handles},
    }
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable



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


REPO = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
RUST_BINARY = REPO / "target" / "debug" / ("relay.exe" if os.name == "nt" else "relay")
ACTIVE_BINARY = RUST_BINARY
PROFILER_DIR = REPO / "tools" / "profiler"
DEFAULT_BUDGET_FILE = PROFILER_DIR / "runtime_budgets.json"
M1_BUDGET_FILE = PROFILER_DIR / "runtime_budgets.m1.json"
V2_BUDGET_FILE = PROFILER_DIR / "runtime_budgets.v2.json"
RESULTS_DIR = PROFILER_DIR / "results"
DEFAULT_RECORDS = 100
MIN_RUNTIME_COVERAGE_RECORDS = 100
DEFAULT_SUBPROCESS_WARMUPS = 2
SCALE_RECORDS = 10_000
SCALE_RUNS = 21
PARALLEL_COLD_REBUILD_MAX_RATIO = 0.85
SCALING_RECORD_COUNTS = (1_000, 10_000, 100_000)
SCALE_BUDGETS = {
    "cli_list": {"median_ms": 150.0, "max_ms": 200.0},
    "cli_upsert": {"median_ms": 200.0, "max_ms": 250.0},
    "cli_rebuild_index": {"median_ms": 150.0, "max_ms": 200.0},
    "cli_rebuild_index_full": {"median_ms": 2000.0, "max_ms": 3000.0},
    "cli_search": {"median_ms": 200.0, "max_ms": 250.0},
    "cli_search_body_fallback": {"median_ms": 1500.0, "max_ms": 2200.0},
    "cli_context": {"median_ms": 250.0, "max_ms": 325.0},
    "cli_regen_refs": {"median_ms": 1200.0, "max_ms": 1800.0},
}
RESOURCE_GATES_100K = {
    "peak_rss_bytes": 768 * 1024 * 1024,
    "derived_bytes": 512 * 1024 * 1024,
    "peak_open_handles": 64,
    "scan_workers": 8,
}
REQUIRED_PROVENANCE_FIELDS = (
    "commit", "binary_path", "binary_size", "binary_sha256", "cargo_lock_sha256",
    "rustc_version", "cargo_version", "target_triple", "profile", "build_flags",
    "logical_cpus", "ram_bytes", "os_image", "os_build", "storage_volume",
    "storage_filesystem", "runner_image", "power_policy", "av_policy",
    "fixture_manifest_sha256", "base_commit_report_sha256",
)
REQUIRED_SCALE_METRICS = (
    "archive_bytes", "records", "record_opens", "record_writes", "cache_read_bytes",
    "cache_write_bytes", "compat_write_bytes", "postings_read_bytes", "postings_write_bytes",
    "derived_bytes", "peak_rss_bytes", "peak_open_handles", "scan_workers",
    "configured_workers", "actual_workers", "query_hits", "cache_generation_before",
    "cache_generation_after",
)

OPERATION_ORDER = (
    "skill_load_common_path",
    "cli_help",
    "cli_init",
    "cli_list",
    "cli_upsert",
    "cli_rebuild_index",
    "cli_rebuild_index_full",
    "cli_regen_refs",
    "cli_search",
    "cli_context",
    "codex_hook",
)

SCALE_OPERATIONS = tuple(SCALE_BUDGETS)

@dataclass(frozen=True)
class Config:
    runs: int
    records: int
    command_timeout: int
    profile: bool
    profile_dir: Path
    subprocess_warmups: int
    scale_gates: bool = False

STRUCTURAL_GATES = {
    "cli_help": {"archive_enumerations": 0, "record_opens": 0, "record_writes": 0},
    "cli_list": {"archive_enumerations": 1, "record_opens": 0, "record_writes": 0, "cache_publishes": 0},
    "cli_upsert": {
        "archive_enumerations": 1, "record_opens": 1, "record_writes": 1,
        "journal_publishes": 1, "cache_publishes": 1, "compat_index_publishes": 1,
    },
    "cli_rebuild_index": {"archive_enumerations": 1, "record_opens": 0, "record_writes": 0, "cache_publishes": 0},
    "cli_search": {"archive_enumerations": 1, "record_opens": 0, "record_writes": 0},
    "cli_context": {"archive_enumerations": 1, "record_opens": 3, "record_writes": 0},
}

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
    "cli_rebuild_index_full": (
        "records_requested", "record_files_before", "index_records_before",
        "record_files_after", "index_records_after",
    ),
    "cli_regen_refs": (
        "records_requested",
        "record_files_before",
        "record_files_after",
        "index_records_after",
    ),
    "cli_search": (
        "records_requested", "record_files_before", "index_records_before",
        "record_files_after", "index_records_after",
    ),
    "cli_context": (
        "records_requested", "record_files_before", "index_records_before",
        "record_files_after", "index_records_after",
    ),
}
UPSERT_MEASURED_RECORD_START = 10_000
UPSERT_MEASURED_COVERAGE_METRICS = ("measured_record_count_stable", "measured_topic_changed")

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
    "cli_upsert": {"median_ms": 60.0, "max_ms": 80.0},
    "cli_rebuild_index": {"median_ms": 60.0, "max_ms": 80.0},
    "cli_rebuild_index_full": {"median_ms": 250.0, "max_ms": 350.0},
    "cli_regen_refs": {"median_ms": 60.0, "max_ms": 80.0},
    "cli_search": {"median_ms": 60.0, "max_ms": 80.0},
    "cli_context": {"median_ms": 100.0, "max_ms": 125.0},
    "codex_hook": {"median_ms": 50.0, "max_ms": 75.0},
}



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




def tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()
    files = sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.relative_to(root).as_posix())
    for path in files:
        relative = path.relative_to(root).as_posix()
        if relative == ".semble/write.lock":
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def cache_generation(root: Path) -> int | None:
    try:
        manifest = json.loads((root / ".semble" / "index-v2" / "manifest.json").read_text(encoding="utf-8"))
        return int(manifest["generation"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


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
    attempts = []
    for phase, phase_runs in (("warmup", warmup_runs), ("measured", command_runs)):
        for ordinal, run in enumerate(phase_runs):
            attempts.append({
                **run,
                "phase": phase,
                "ordinal": ordinal,
                "clone_id": f"{label}-{phase}-{ordinal}",
                "pre_state_sha256": tree_sha256(root) if root else hashlib.sha256(label.encode()).hexdigest(),
                "pre_generation": cache_generation(root) if root else None,
            })
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
        "attempts": attempts,
    }


def time_cloned_command(
    label: str,
    config: Config,
    source: Path,
    make_cmd: Callable[[Path, int], list[str]],
    *,
    make_input: Callable[[int], str | None] | None = None,
    env_factory: Callable[[Path, str, int], dict[str, str] | None] | None = None,
    runner: Callable[..., dict[str, Any]] = run_cmd,
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    warmup_runs: list[dict[str, Any]] = []
    command_runs: list[dict[str, Any]] = []
    samples: list[float] = []
    last_root = source
    attempt_base = source.parent / f"{safe_name(label)}-attempts"
    for phase, count in (("warmup", config.subprocess_warmups), ("measured", config.runs)):
        for ordinal in range(count):
            clone_id = f"{label}-{phase}-{ordinal}"
            clone = attempt_base / clone_id
            shutil.copytree(source, clone)
            env = env_factory(clone, phase, ordinal) if env_factory else None
            result = runner(
                make_cmd(clone, ordinal),
                input_text=make_input(ordinal) if make_input else None,
                timeout=config.command_timeout,
                env=env,
            )
            attempt = {
                **result,
                "phase": phase,
                "ordinal": ordinal,
                "clone_id": clone_id,
                "pre_state_sha256": tree_sha256(source),
                "pre_generation": cache_generation(source),
                "post_state_sha256": tree_sha256(clone),
                "post_generation": cache_generation(clone),
                "clone_path": str(clone),
                **({"trace_path": env["RELAY_TEST_TRACE_IO"]} if env and env.get("RELAY_TEST_TRACE_IO") else {}),
            }
            attempts.append(attempt)
            if phase == "warmup":
                warmup_runs.append(result)
            else:
                command_runs.append(result)
                if result["returncode"] == 0 and not result["timed_out"]:
                    samples.append(float(result["elapsed_ms"]))
                last_root = clone
    return {
        "label": label,
        "kind": "subprocess",
        "root": str(source),
        "summary": summarize(samples),
        "samples_ms": [round(sample, 3) for sample in samples],
        "warmup_runs": warmup_runs,
        "returncodes": sorted({run["returncode"] for run in command_runs}, key=lambda value: str(value)),
        "timed_out": any(run["timed_out"] for run in command_runs),
        "command_runs": command_runs,
        "attempts": attempts,
        "_last_root": last_root,
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


def rust_binary_path() -> Path:
    """Return the checkout's debug Rust binary path, including cross-platform fallback."""
    if ACTIVE_BINARY.is_file():
        return ACTIVE_BINARY
    candidates = (
        RUST_BINARY,
        REPO / "target" / "debug" / ("relay" if RUST_BINARY.name.endswith(".exe") else "relay.exe"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return RUST_BINARY


def ensure_rust_binary(timeout: int) -> Path:
    """Build the debug binary as setup, never as part of measured samples."""
    binary = rust_binary_path()
    if binary.is_file():
        return binary
    build = run_cmd(["cargo", "build", "--bin", "relay"], timeout=timeout, cwd=REPO)
    binary = rust_binary_path()
    if build["returncode"] != 0 or build["timed_out"] or not binary.is_file():
        raise RuntimeError(f"could not build debug relay binary: {build}")
    return binary


def relay_cmd(*args: object) -> list[str]:
    return [str(ACTIVE_BINARY), *map(str, args)]

def profiler_record_id(i: int) -> str:
    return f"conv_profiler_{i:04d}"


def conversation_payload(i: int, *, ref_previous: bool = False, topic: str | None = None) -> str:
    data: dict[str, Any] = {
        "id": profiler_record_id(i),
        "topic": topic or f"profiler conversation {i:04d}",
        "status": "active",
        "tags": ["profiler", "synthetic"],
        "sections": {
            "summary": f"synthetic profiler summary {i}",
            "dict": "- **profiler** - synthetic measurement record",
            "qa": "- **Q:** What is this? **A:** A profiling fixture.",
            "decisions": "1. Use temporary roots only.",
        },
        "resume": {
            "goal": "measure relay latency gates",
            "next_steps": ["collect timings", "compare budgets"],
            "open_questions": [],
            "suggested_skills": ["relay:save"],
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
    result = run_cmd(relay_cmd("init", "--relay-root", root), timeout=timeout)
    if result["returncode"] != 0:
        raise RuntimeError(f"could not initialize profiler root {root}: {result}")


def upsert_record(root: Path, i: int, timeout: int, *, ref_previous: bool = False) -> None:
    result = run_cmd(
        relay_cmd("upsert", "--stdin", "--relay-root", root),
        input_text=conversation_payload(i, ref_previous=ref_previous),
        timeout=timeout,
    )
    if result["returncode"] != 0:
        raise RuntimeError(f"could not seed profiler record {i} in {root}: {result}")


def record_file_count(root: Path) -> int:
    return len(list((root / "convs").rglob("*.md")))


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


def indexed_topic(root: Path, record_id: str) -> str | None:
    try:
        for line in (root / "index.jsonl").read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("id") == record_id:
                return str(row.get("topic"))
    except (OSError, json.JSONDecodeError):
        return None
    return None


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
FIRST_USEFUL_ACTION_MARKER = "~/.relay/bin/relay"
BROAD_COMMON_PATH_FILES = {
    "SKILL.md",
    "skills/relay/SKILL.md",
    "references/branching.md",
    "references/cli.md",
    "references/list.md",
    "references/resume.md",
    "references/save.md",
}
FORBIDDEN_PRE_ACTION_TEXT = (
    "~/.relay/references/",
    "~/.relay/SKILL.md",
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
        lambda _i: relay_cmd("--help"),
        warmups=config.subprocess_warmups,
        timeout=config.command_timeout,
    )


def op_cli_init(config: Config, base: Path, cache: dict[str, Any]) -> dict[str, Any]:
    del cache
    root_base = base / "cli-init"
    return time_repeated_command(
        "cli_init",
        config.runs,
        lambda i: relay_cmd("init", "--relay-root", root_base / f"root-{i}"),
        warmups=config.subprocess_warmups,
        timeout=config.command_timeout,
        root=root_base,
    )


def op_cli_list(config: Config, base: Path, cache: dict[str, Any]) -> dict[str, Any]:
    source = operation_dataset_root(base, config, cache, "cli-list")
    before = coverage_snapshot(source, config)
    result = time_cloned_command(
        "cli_list", config, source,
        lambda root, _i: relay_cmd("list", "--json", "--limit", "10", "--relay-root", root),
    )
    last = result.pop("_last_root")
    return attach_runtime_coverage(result, root=last, config=config, before=before)


def op_cli_upsert(config: Config, base: Path, cache: dict[str, Any]) -> dict[str, Any]:
    source = operation_dataset_root(base, config, cache, "cli-upsert")
    before = coverage_snapshot(source, config)
    measured_id = profiler_record_id(0)
    topic_before = indexed_topic(source, measured_id)
    result = time_cloned_command(
        "cli_upsert", config, source,
        lambda root, _i: relay_cmd("upsert", "--stdin", "--relay-root", root),
        make_input=lambda _i: conversation_payload(0, topic="profile update v2"),
    )
    last = result.pop("_last_root")
    result = attach_runtime_coverage(result, root=last, config=config, before=before)
    result["coverage"].update(
        {
            "measured_record_id": measured_id,
            "measured_record_files_before": measured_profiler_record_file_count(source, start=0, count=1),
            "measured_index_records_before": measured_profiler_index_record_count(source, start=0, count=1),
            "measured_record_files_after": measured_profiler_record_file_count(
                last, start=0, count=1,
            ),
            "measured_index_records_after": measured_profiler_index_record_count(
                last, start=0, count=1,
            ),
            "measured_topic_before": topic_before,
            "measured_topic_after": indexed_topic(last, measured_id),
        }
    )
    return result


def op_cli_rebuild_index(config: Config, base: Path, cache: dict[str, Any]) -> dict[str, Any]:
    source = operation_dataset_root(base, config, cache, "cli-rebuild-index")
    before = coverage_snapshot(source, config)
    result = time_cloned_command(
        "cli_rebuild_index", config, source,
        lambda root, _i: relay_cmd("rebuild-index", "--relay-root", root),
    )
    last = result.pop("_last_root")
    return attach_runtime_coverage(result, root=last, config=config, before=before)


def op_cli_rebuild_index_full(config: Config, base: Path, cache: dict[str, Any]) -> dict[str, Any]:
    source = operation_dataset_root(base, config, cache, "cli-rebuild-index-full")
    before = coverage_snapshot(source, config)
    result = time_cloned_command(
        "cli_rebuild_index_full", config, source,
        lambda root, _i: relay_cmd("rebuild-index", "--full", "--relay-root", root),
    )
    last = result.pop("_last_root")
    return attach_runtime_coverage(result, root=last, config=config, before=before)


def op_cli_regen_refs(config: Config, base: Path, cache: dict[str, Any]) -> dict[str, Any]:
    source = operation_dataset_root(base, config, cache, "cli-regen-refs")
    before = coverage_snapshot(source, config)
    result = time_cloned_command(
        "cli_regen_refs", config, source,
        lambda root, _i: relay_cmd("regen-refs", "--relay-root", root),
    )
    last = result.pop("_last_root")
    return attach_runtime_coverage(result, root=last, config=config, before=before)


def op_cli_search(config: Config, base: Path, cache: dict[str, Any]) -> dict[str, Any]:
    source = operation_dataset_root(base, config, cache, "cli-search")
    before = coverage_snapshot(source, config)
    result = time_cloned_command(
        "cli_search", config, source,
        lambda root, _i: relay_cmd("search", "profiler conversation 0001", "--relay-root", root),
    )
    last = result.pop("_last_root")
    return attach_runtime_coverage(result, root=last, config=config, before=before)


def op_cli_search_body_fallback(config: Config, base: Path, cache: dict[str, Any]) -> dict[str, Any]:
    source = operation_dataset_root(base, config, cache, "cli-search-body-fallback")
    before = coverage_snapshot(source, config)
    result = time_cloned_command(
        "cli_search_body_fallback", config, source,
        lambda root, _i: relay_cmd(
            "search", "profiler conversation 0001", "--no-semble", "--relay-root", root
        ),
    )
    last = result.pop("_last_root")
    return attach_runtime_coverage(result, root=last, config=config, before=before)


def op_cli_context(config: Config, base: Path, cache: dict[str, Any]) -> dict[str, Any]:
    source = operation_dataset_root(base, config, cache, "cli-context")
    before = coverage_snapshot(source, config)
    target = profiler_record_id(1 if config.records > 2 else 0)
    result = time_cloned_command(
        "cli_context", config, source,
        lambda root, _i: relay_cmd("context", target, "--json", "--relay-root", root),
    )
    last = result.pop("_last_root")
    return attach_runtime_coverage(result, root=last, config=config, before=before)


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


def prepare_codex_hook(base: Path, cache: dict[str, Any], timeout: int) -> tuple[Path, dict[str, str]]:
    cached = cache.get("codex-hook")
    if cached:
        return cached
    plugin_root = base / "codex-hook-plugin"
    binary = plugin_root / "bin" / rust_binary_path().name
    binary.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ensure_rust_binary(timeout), binary)
    (plugin_root / "convs").mkdir(parents=True, exist_ok=True)
    counter_tmp = base / "codex-hook-tmp"
    counter_tmp.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({"TMP": str(counter_tmp), "TEMP": str(counter_tmp), "TMPDIR": str(counter_tmp)})
    cache["codex-hook"] = (binary, env)
    return binary, env


def op_codex_hook(config: Config, base: Path, cache: dict[str, Any]) -> dict[str, Any]:
    binary, env = prepare_codex_hook(base, cache, config.command_timeout)
    plugin_root = binary.parents[1]
    return time_repeated_command(
        "codex_hook",
        config.runs,
        lambda _i: [str(binary), "hook", "--agent", "codex"],
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
    "cli_rebuild_index_full": op_cli_rebuild_index_full,
    "cli_regen_refs": op_cli_regen_refs,
    "cli_search": op_cli_search,
    "cli_search_body_fallback": op_cli_search_body_fallback,
    "cli_context": op_cli_context,
    "codex_hook": op_codex_hook,
}

def _read_trace(path: str | Path | None) -> list[dict[str, Any]]:
    if not path or not Path(path).is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                events.append(value)
    return events


def _scale_env(clone: Path, label: str, phase: str, ordinal: int, *, threads: int | None = None) -> dict[str, str]:
    env = dict(os.environ)
    trace = clone.parent / f"{safe_name(label)}-{phase}-{ordinal}.jsonl"
    env.update({"RELAY_TEST_MODE": "1", "RELAY_TEST_TRACE_IO": str(trace)})
    if threads is not None:
        env["RELAY_SCAN_THREADS"] = str(threads)
    return env


def _query_hits(stdout_head: str) -> int | None:
    try:
        value: Any = json.loads(stdout_head)
    except (TypeError, ValueError):
        return None
    if isinstance(value, dict):
        for key in ("hits", "results", "records"):
            candidate = value.get(key)
            if isinstance(candidate, list):
                return len(candidate)
    if isinstance(value, list):
        return len(value)
    return None


def scale_telemetry(result: dict[str, Any], source: Path) -> dict[str, Any]:
    """Extract scale evidence from traced attempts and observed child resources."""
    attempts = [attempt for attempt in result.get("attempts", []) if attempt.get("phase") == "measured"]
    events = [event for attempt in attempts for event in _read_trace(attempt.get("trace_path"))]
    record_files = [path for path in source.joinpath("convs").rglob("*.md") if path.is_file()]
    archive_bytes = sum(path.stat().st_size for path in record_files)
    cache_reads = [event for event in events if event.get("event") == "cache_read"]
    cache_writes = [event for event in events if event.get("event") == "cache_write"]
    compat_events = [event for event in events if event.get("event") == "compat_index_publish"]
    derived_values: list[int] = []
    compat_values: list[int] = []
    for attempt in attempts:
        clone = Path(attempt.get("clone_path", ""))
        if not clone.is_dir():
            trace_path = attempt.get("trace_path")
            candidate = (
                source.parent / f"{safe_name(result.get('label', 'scale'))}-attempts" / attempt["clone_id"]
                if trace_path
                else Path()
            )
            clone = candidate if candidate.is_dir() else Path()
        if clone.is_dir():
            derived_values.append(
                sum(path.stat().st_size for path in clone.rglob("*") if path.is_file() and ".semble" in path.parts)
            )
            if (clone / "index.jsonl").is_file():
                compat_values.append((clone / "index.jsonl").stat().st_size)
    resource = [attempt.get("resource", {}) for attempt in attempts]
    rss = [value.get("peak_rss_bytes") for value in resource if value.get("peak_rss_bytes")]
    handles = [value.get("peak_open_handles") for value in resource if value.get("peak_open_handles")]
    configured = [event.get("workers") for event in events if event.get("event") == "scan_start"]
    actual = [event.get("workers_started") for event in events if event.get("event") == "scan_end"]
    hit_values = [
        attempt.get("query_hits_observed")
        if attempt.get("query_hits_observed") is not None
        else _query_hits(attempt.get("stdout_head", ""))
        for attempt in attempts
    ]
    hits = next((value for value in hit_values if value is not None), None)
    before = [attempt.get("pre_generation") for attempt in attempts if attempt.get("pre_generation") is not None]
    after = [attempt.get("post_generation") for attempt in attempts if attempt.get("post_generation") is not None]
    return {
        "archive_bytes": archive_bytes or None,
        "records": len(record_files) or None,
        "record_opens": sum(1 for event in events if event.get("event") == "record_open"),
        "record_writes": sum(1 for event in events if event.get("event") == "record_write"),
        "cache_read_bytes": sum(int(event.get("bytes", 0)) for event in cache_reads if isinstance(event.get("bytes"), int)),
        "cache_write_bytes": sum(int(event.get("bytes", 0)) for event in cache_writes if isinstance(event.get("bytes"), int)),
        "compat_write_bytes": sum(compat_values) if compat_events and compat_values else None,
        "postings_read_bytes": sum(
            int(event.get("bytes", 0))
            for event in cache_reads
            if str(event.get("artifact", "")).startswith("postings") and isinstance(event.get("bytes"), int)
        ),
        "postings_write_bytes": sum(
            int(event.get("bytes", 0))
            for event in cache_writes
            if str(event.get("artifact", "")).startswith("postings") and isinstance(event.get("bytes"), int)
        ),
        "derived_bytes": max(derived_values) if derived_values else None,
        "peak_rss_bytes": max(rss) if rss else None,
        "peak_open_handles": max(handles) if handles else None,
        "scan_workers": max(actual) if actual else None,
        "configured_workers": max(configured) if configured else None,
        "actual_workers": max(actual) if actual else None,
        "query_hits": hits,
        "cache_generation_before": min(before) if before else None,
        "cache_generation_after": max(after) if after else None,
    }


def scale_operation(
    name: str,
    config: Config,
    base: Path,
    cache: dict[str, Any],
    make_cmd: Callable[[Path, int], list[str]],
    *,
    make_input: Callable[[int], str | None] | None = None,
) -> dict[str, Any]:
    source = operation_dataset_root(base, config, cache, f"scale-{name}")
    result = time_cloned_command(
        name,
        config,
        source,
        make_cmd,
        make_input=make_input,
        env_factory=lambda clone, phase, ordinal: _scale_env(clone, name, phase, ordinal),
        runner=run_scale_cmd,
    )
    result.pop("_last_root", None)
    result["telemetry"] = scale_telemetry(result, source)
    return result


def time_paired_cold_rebuild(config: Config, source: Path) -> dict[str, Any]:
    arms = {
        "serial": {"samples_ms": [], "command_runs": [], "warmup_runs": [], "attempts": []},
        "parallel": {"samples_ms": [], "command_runs": [], "warmup_runs": [], "attempts": []},
    }
    pairs: list[dict[str, Any]] = []
    orders: list[list[str]] = []
    attempt_base = source.parent / "cli-rebuild-index-full-paired-attempts"
    for phase, count in (("warmup", config.subprocess_warmups), ("measured", config.runs)):
        for ordinal in range(count):
            order = ["serial", "parallel"] if ordinal % 2 == 0 else ["parallel", "serial"]
            orders.append(order)
            pair: dict[str, Any] = {"phase": phase, "ordinal": ordinal, "order": order, "arms": {}}
            pre_hash = tree_sha256(source)
            for arm in order:
                clone_id = f"{arm}-{phase}-{ordinal}"
                clone = attempt_base / clone_id
                shutil.copytree(source, clone)
                threads = 1 if arm == "serial" else 8
                env = _scale_env(clone, f"cli-rebuild-index-full-{arm}", phase, ordinal, threads=threads)
                run = run_scale_cmd(
                    relay_cmd("rebuild-index", "--full", "--relay-root", clone),
                    timeout=config.command_timeout,
                    env=env,
                )
                attempt = {
                    **run,
                    "phase": phase,
                    "ordinal": ordinal,
                    "arm": arm,
                    "clone_id": clone_id,
                    "pre_state_sha256": pre_hash,
                    "pre_generation": cache_generation(source),
                    "post_state_sha256": tree_sha256(clone),
                    "post_generation": cache_generation(clone),
                    "configured_threads": threads,
                    "trace_path": env["RELAY_TEST_TRACE_IO"],
                    "clone_path": str(clone),
                }
                arms[arm]["attempts"].append(attempt)
                pair["arms"][arm] = attempt
                if phase == "warmup":
                    arms[arm]["warmup_runs"].append(run)
                else:
                    arms[arm]["command_runs"].append(run)
                    if run["returncode"] == 0 and not run["timed_out"]:
                        arms[arm]["samples_ms"].append(float(run["elapsed_ms"]))
            pair["byte_identical"] = (
                pair["arms"].get("serial", {}).get("post_state_sha256")
                == pair["arms"].get("parallel", {}).get("post_state_sha256")
            )
            pairs.append(pair)
    for arm in arms.values():
        arm["summary"] = summarize(arm["samples_ms"])
        arm["samples_ms"] = [round(value, 3) for value in arm["samples_ms"]]
        arm["returncodes"] = sorted({run["returncode"] for run in arm["command_runs"]}, key=lambda value: str(value))
        arm["timed_out"] = any(run["timed_out"] for run in arm["command_runs"])
    return {
        "label": "cli_rebuild_index_full",
        "kind": "paired_subprocess",
        "root": str(source),
        "arms": arms,
        "pairs": pairs,
        "orders": orders,
        "attempts": [attempt for arm in arms.values() for attempt in arm["attempts"]],
        "telemetry": scale_telemetry({"attempts": arms["parallel"]["attempts"], "root": str(source), "label": "cli-rebuild-index-full"}, source),
    }



def structural_run(name: str, config: Config, base: Path, cache: dict[str, Any]) -> dict[str, Any]:
    trace = base / f"structural-{safe_name(name)}.jsonl"
    env = dict(os.environ)
    env.update({"RELAY_TEST_MODE": "1", "RELAY_TEST_TRACE_IO": str(trace)})
    clone_id = f"structural-{name}"
    source: Path | None = None
    root: Path | None = None
    input_text = None
    if name == "cli_help":
        command = relay_cmd("--help")
    else:
        source = operation_dataset_root(base, config, cache, name.replace("_", "-"))
        root = base / "structural-clones" / safe_name(name)
        root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, root)
        if name == "cli_list":
            command = relay_cmd("list", "--json", "--relay-root", root)
        elif name == "cli_upsert":
            command = relay_cmd("upsert", "--stdin", "--relay-root", root)
            input_text = conversation_payload(0, topic="profile structural update")
        elif name == "cli_rebuild_index":
            command = relay_cmd("rebuild-index", "--relay-root", root)
        elif name == "cli_search":
            command = relay_cmd("search", "profiler conversation 0001", "--no-semble", "--relay-root", root)
        elif name == "cli_context":
            command = relay_cmd("context", profiler_record_id(1), "--json", "--relay-root", root)
        else:
            raise ValueError(f"no structural command for {name}")
    executed = run_cmd(command, input_text=input_text, timeout=config.command_timeout, env=env)
    events = []
    if trace.is_file():
        for line in trace.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    names = [str(event.get("event")) for event in events]
    io = {
        "archive_enumerations": names.count("snapshot"),
        "record_opens": names.count("record_open"),
        "record_writes": names.count("record_write"),
        "journal_publishes": names.count("journal_publish"),
        "cache_publishes": names.count("cache_publish"),
        "compat_index_publishes": names.count("compat_index_publish"),
    }
    return {
        "excluded_from_timing": True,
        "clone_id": clone_id,
        "pre_state_sha256": tree_sha256(source) if source else hashlib.sha256(name.encode()).hexdigest(),
        "pre_generation": cache_generation(source) if source else None,
        "returncode": executed["returncode"],
        "timed_out": executed["timed_out"],
        "io": io,
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


def profile_command(
    label: str,
    command: list[str],
    *,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
    profile_dir: Path,
    timeout: int,
) -> dict[str, Any]:
    """Profile the Python subprocess launcher while executing the Rust command directly."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    profile_path = profile_dir / f"{safe_name(label)}.prof"
    profiler = cProfile.Profile()
    profiler.enable()
    result = run_cmd(command, input_text=input_text, env=env, timeout=timeout)
    profiler.disable()
    profiler.dump_stats(str(profile_path))
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
            profile_command(
                "cli_help",
                relay_cmd("--help"),
                profile_dir=config.profile_dir,
                timeout=config.command_timeout,
            )
        )
    if "codex_hook" in selected:
        binary, env = prepare_codex_hook(base, cache, config.command_timeout)
        profiles.append(
            profile_command(
                "codex_hook",
                [str(binary), "hook", "--agent", "codex"],
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
                    stable = (
                        coverage.get("measured_record_files_before") == coverage.get("measured_record_files_after")
                        and coverage.get("measured_index_records_before") == coverage.get("measured_index_records_after")
                        and coverage.get("measured_record_files_before") == 1
                        and coverage.get("measured_index_records_before") == 1
                    )
                    if not stable:
                        failures.append({
                            "operation": name,
                            "metric": "upsert_measured_update",
                            "coverage_metric": "measured_record_count_stable",
                        })
                    if coverage.get("measured_topic_before") == coverage.get("measured_topic_after"):
                        failures.append({
                            "operation": name,
                            "metric": "upsert_measured_update",
                            "coverage_metric": "measured_topic_changed",
                        })
        if name in STRUCTURAL_GATES and "structural_run" in result:
            actual_io = result.get("structural_run", {}).get("io", {})
            for metric, allowed in STRUCTURAL_GATES[name].items():
                if actual_io.get(metric) != allowed:
                    failures.append({
                        "operation": name,
                        "metric": "structural_io",
                        "io_metric": metric,
                        "actual": actual_io.get(metric),
                        "expected": allowed,
                    })
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
    for name in RUNTIME_COVERAGE_METRICS:
        if operations and name in budgets and name not in operations:
            failures.append({
                "operation": name,
                "metric": "record_coverage",
                "reason": "missing coverage metadata",
                "min_records": MIN_RUNTIME_COVERAGE_RECORDS,
            })
    return {"passed": not failures, "failures": failures}


def evaluate_scale_gate(
    operations: dict[str, dict[str, Any]],
    telemetry: dict[str, Any],
    *,
    records: int = SCALE_RECORDS,
    budgets: dict[str, dict[str, float]] | None = None,
    selected: list[str] | None = None,
) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    expected = set(SCALE_OPERATIONS)
    actual = set(selected if selected is not None else operations)
    if actual != expected:
        failures.append({
            "operation": "selection",
            "metric": "scale_operations",
            "reason": "scale evidence must execute the complete SCALE_BUDGETS set",
            "missing": sorted(expected - actual),
            "unexpected": sorted(actual - expected),
        })
    active_budgets = budgets or SCALE_BUDGETS

    def check_summary(name: str, summary: Any, budget: Any) -> None:
        if not isinstance(summary, dict) or not summary:
            failures.append({"operation": name, "metric": "summary", "reason": "missing samples"})
            return
        for metric in TIME_BUDGET_METRICS:
            try:
                actual_value = float(summary[metric])
                allowed = float(budget[metric])
            except (KeyError, TypeError, ValueError):
                failures.append({"operation": name, "metric": metric, "reason": "missing or non-numeric metric"})
                continue
            if not math.isfinite(actual_value) or not math.isfinite(allowed):
                failures.append({"operation": name, "metric": metric, "reason": "non-finite metric"})
            elif actual_value > allowed:
                failures.append({
                    "operation": name,
                    "metric": metric,
                    "actual_ms": round(actual_value, 3),
                    "budget_ms": round(allowed, 3),
                })

    for name in SCALE_OPERATIONS:
        result = operations.get(name)
        if not isinstance(result, dict):
            failures.append({"operation": name, "metric": "operation", "reason": "missing operation result"})
            continue
        budget = active_budgets.get(name)
        if not isinstance(budget, dict):
            failures.append({"operation": name, "metric": "budget", "reason": "missing budget"})
            continue
        if name == "cli_rebuild_index_full":
            arms = result.get("arms")
            if not isinstance(arms, dict):
                failures.append({"operation": name, "metric": "paired_arms", "reason": "missing paired arms"})
            else:
                for arm in ("serial", "parallel"):
                    arm_result = arms.get(arm, {})
                    check_summary(f"{name}:{arm}", arm_result.get("summary"), budget)
                    bad = [
                        attempt for attempt in arm_result.get("attempts", [])
                        if attempt.get("returncode") != 0 or attempt.get("timed_out")
                    ]
                    if bad:
                        failures.append({"operation": name, "metric": f"{arm}_subprocess", "runs": bad})
            for pair in result.get("pairs", []):
                if pair.get("phase") == "measured" and not pair.get("byte_identical"):
                    failures.append({"operation": name, "metric": "byte_identical", "reason": "paired outputs differ"})
            serial = (arms or {}).get("serial", {}).get("summary", {})
            parallel = (arms or {}).get("parallel", {}).get("summary", {})
            try:
                serial_median = float(serial["median_ms"])
                parallel_median = float(parallel["median_ms"])
                ratio = parallel_median / serial_median
                if not math.isfinite(ratio):
                    raise ValueError
                if ratio > PARALLEL_COLD_REBUILD_MAX_RATIO:
                    failures.append({
                        "operation": name,
                        "metric": "parallel_ratio",
                        "actual_ratio": round(ratio, 4),
                        "max_ratio": PARALLEL_COLD_REBUILD_MAX_RATIO,
                    })
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                failures.append({"operation": name, "metric": "parallel_ratio", "reason": "missing paired medians"})
        else:
            check_summary(name, result.get("summary"), budget)
            bad = [
                run for run in [*(result.get("warmup_runs") or []), *(result.get("command_runs") or [])]
                if run.get("returncode") != 0 or run.get("timed_out")
            ]
            if bad:
                failures.append({"operation": name, "metric": "subprocess", "runs": bad})

    for metric in REQUIRED_SCALE_METRICS:
        value = telemetry.get(metric)
        if value is None or isinstance(value, bool):
            failures.append({"operation": "scale", "metric": metric, "reason": "missing telemetry"})
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            failures.append({"operation": "scale", "metric": metric, "reason": "non-numeric telemetry"})
            continue
        if not math.isfinite(numeric):
            failures.append({"operation": "scale", "metric": metric, "reason": "non-finite telemetry"})
    try:
        measured_records = int(telemetry.get("records"))
    except (TypeError, ValueError):
        measured_records = -1
    if measured_records != records:
        failures.append({
            "operation": "scale",
            "metric": "records",
            "actual": measured_records,
            "expected": records,
            "reason": "scale corpus record count mismatch",
        })

    if records == 100_000:
        for metric, allowed in RESOURCE_GATES_100K.items():
            value = telemetry.get(metric)
            try:
                actual_value = float(value)
                allowed_value = float(allowed)
            except (TypeError, ValueError):
                failures.append({"operation": "scale_100k", "metric": metric, "reason": "missing resource telemetry"})
                continue
            if actual_value > allowed_value:
                failures.append({
                    "operation": "scale_100k",
                    "metric": metric,
                    "actual": actual_value,
                    "budget": allowed_value,
                })
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
    return RESULTS_DIR / f"relay-loading-profile-{now_stamp()}.json"


def write_report(report: dict[str, Any], out: Path | None, *, stdout_json: bool) -> str | None:
    if stdout_json:
        print(json.dumps(report, indent=2))
        return None
    if out is None:
        out = default_out_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return str(out)


def command_text(command: list[str]) -> str:
    try:
        return subprocess.run(command, cwd=str(REPO), capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def dataset_report(root: Path, records: int) -> dict[str, Any]:
    files = sorted((root / "convs").rglob("*.md")) if root.exists() else []
    archive_bytes = sum(path.stat().st_size for path in files)
    return {
        "manifest_version": 1,
        "seed": 20260716,
        "archive_sha256": tree_sha256(root),
        "records": records,
        "files": len(files),
        "bytes": archive_bytes,
        "size_histogram": {},
        "tag_histogram": {},
        "ref_degree_histogram": {},
        "nested_records": sum(1 for path in files if len(path.relative_to(root / "convs").parts) > 1),
        "queries": {"metadata": "profiler conversation 0001", "body": "synthetic profiler summary"},
    }


def provenance_report(binary: Path, dataset: dict[str, Any]) -> dict[str, Any]:
    binary_bytes = binary.read_bytes() if binary.is_file() else b""
    cargo_lock = (REPO / "Cargo.lock").read_bytes()
    rustc = command_text(["rustc", "-vV"])
    cargo = command_text(["cargo", "-V"])
    host = next((line.removeprefix("host: ") for line in rustc.splitlines() if line.startswith("host: ")), "")
    commit = command_text(["git", "rev-parse", "HEAD"])
    values = {
        "commit": commit,
        "binary_path": str(binary.resolve()),
        "binary_size": len(binary_bytes),
        "binary_sha256": hashlib.sha256(binary_bytes).hexdigest(),
        "cargo_lock_sha256": hashlib.sha256(cargo_lock).hexdigest(),
        "rustc_version": rustc,
        "cargo_version": cargo,
        "target_triple": host,
        "profile": "release" if "release" in {part.lower() for part in binary.parts} else "debug",
        "build_flags": "--release --locked --target" if "release" in {part.lower() for part in binary.parts} else "debug",
        "logical_cpus": os.cpu_count() or 1,
        "ram_bytes": 0,
        "os_image": sys.platform,
        "os_build": command_text(["cmd", "/c", "ver"]) if os.name == "nt" else "",
        "storage_volume": binary.anchor,
        "storage_filesystem": "unknown",
        "runner_image": os.environ.get("ImageOS", "local"),
        "power_policy": os.environ.get("RELAY_POWER_POLICY", "unknown"),
        "av_policy": os.environ.get("RELAY_AV_POLICY", "unknown"),
        "fixture_manifest_sha256": str(dataset.get("archive_sha256", "")),
        "base_commit_report_sha256": "",
    }
    return values


def aggregate_scale_telemetry(operations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    values = [
        result.get("telemetry", {})
        for result in operations.values()
        if isinstance(result.get("telemetry"), dict)
    ]
    aggregate: dict[str, Any] = {}
    for metric in REQUIRED_SCALE_METRICS:
        observed = [value.get(metric) for value in values if value.get(metric) is not None]
        if not observed:
            aggregate[metric] = None
        elif metric in {"archive_bytes", "records"}:
            aggregate[metric] = max(observed)
        elif metric in {"cache_generation_before"}:
            aggregate[metric] = min(observed)
        elif metric in {"cache_generation_after", "query_hits"}:
            aggregate[metric] = max(observed)
        else:
            aggregate[metric] = max(observed)
    return aggregate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Profile relay skill loading, CLI commands, and the Codex hook with optional budget gates."
    )
    parser.add_argument("--gate", action="store_true", help="exit non-zero when any selected operation exceeds budget")
    parser.add_argument("--runs", type=int, default=5, help="timing samples per operation (default: 5, or 21 with --scale-gates)")
    parser.add_argument(
        "--records",
        type=int,
        default=DEFAULT_RECORDS,
        help="synthetic records for covered runtime paths; gate requires at least 100",
    )
    parser.add_argument("--only", action="append", help="operation name or comma-list; repeatable")
    parser.add_argument("--binary", type=Path, help="explicit relay release binary used by gate runs")
    parser.add_argument("--budget-profile", choices=("m1", "v2"), help="versioned built-in budget profile")
    parser.add_argument("--budget-file", type=Path, help="JSON budget override; defaults to tools/profiler/runtime_budgets.json")
    parser.add_argument("--budget-scale", type=float, default=1.0, help="multiply loaded budgets, useful for smoke tests")
    parser.add_argument("--command-timeout", type=int, default=120, help="subprocess timeout in seconds")
    parser.add_argument("--out", type=str, help="report path; use '-' to print full JSON to stdout")
    parser.add_argument("--profile", action="store_true", help="also write cProfile data for skill load, CLI help, and Codex hook")
    parser.add_argument("--profile-dir", type=Path, help="directory for .prof files; default is ignored profiler results")
    parser.add_argument("--list-operations", action="store_true", help="print available operation names and exit")
    parser.add_argument("--scale-gates", action="store_true", help="enable the 10k scale and paired parallel rebuild gates")
    return parser
def main(argv: list[str] | None = None) -> int:
    global ACTIVE_BINARY
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list_operations:
        for name in OPERATION_ORDER:
            print(name)
        return 0
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    scale_mode = bool(args.scale_gates)
    has_runs_override = any(value == "--runs" or value.startswith("--runs=") for value in raw_argv)
    has_records_override = any(value == "--records" or value.startswith("--records=") for value in raw_argv)
    runs = args.runs if not scale_mode or has_runs_override else SCALE_RUNS
    records = args.records if not scale_mode or has_records_override else SCALE_RECORDS
    if runs < 1:
        parser.error("--runs must be >= 1")
    if records < 1:
        parser.error("--records must be >= 1")
    gated = bool(args.gate or scale_mode)
    if gated and args.binary is None:
        parser.error("--scale-gates/--gate requires an explicit --binary release artifact")
    if gated and args.budget_file is None and args.budget_profile is None:
        parser.error("--scale-gates/--gate requires --budget-profile m1|v2 or --budget-file")
    if args.binary is not None:
        ACTIVE_BINARY = args.binary.resolve()
        if not ACTIVE_BINARY.is_file():
            parser.error(f"--binary does not exist: {ACTIVE_BINARY}")
        if gated and "release" not in {part.lower() for part in ACTIVE_BINARY.parts}:
            parser.error("--binary must be a release-profile artifact")
    else:
        ACTIVE_BINARY = RUST_BINARY

    try:
        requested = expand_only(args.only)
        if scale_mode:
            selected = list(SCALE_OPERATIONS) if args.only is None else requested
            if set(selected) != set(SCALE_OPERATIONS):
                raise ValueError("--scale-gates requires the complete SCALE_BUDGETS operation set; partial --only is rejected")
            selected = list(SCALE_OPERATIONS)
        else:
            selected = requested
        budget_path = args.budget_file
        if budget_path is None and args.budget_profile:
            budget_path = M1_BUDGET_FILE if args.budget_profile == "m1" else V2_BUDGET_FILE
        loaded_budgets = load_budgets(budget_path, args.budget_scale)
        scale_budgets = {
            name: {
                metric: round(value * args.budget_scale, 3)
                for metric, value in budget.items()
            }
            for name, budget in SCALE_BUDGETS.items()
        }
    except Exception as exc:
        print(f"profiler: {exc}", file=sys.stderr)
        return 2

    out_path = None if not args.out else (None if args.out == "-" else Path(args.out))
    stdout_json = args.out == "-"
    profile_dir = args.profile_dir or (RESULTS_DIR / f"profiles-{now_stamp()}")
    config = Config(
        runs=runs,
        records=records,
        command_timeout=args.command_timeout,
        profile=args.profile,
        profile_dir=profile_dir,
        subprocess_warmups=DEFAULT_SUBPROCESS_WARMUPS,
        scale_gates=scale_mode,
    )
    if not scale_mode and any(name != "skill_load_common_path" for name in selected):
        try:
            ensure_rust_binary(config.command_timeout)
        except RuntimeError as exc:
            print(f"profiler: {exc}", file=sys.stderr)
            return 2

    started = time.perf_counter()
    cache: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="conv-prof-gate-") as tmp:
        base = Path(tmp).resolve()
        operations: dict[str, dict[str, Any]] = {}
        if scale_mode:
            scale_commands: dict[str, tuple[Callable[[Path, int], list[str]], Callable[[int], str | None] | None]] = {
                "cli_list": (lambda root, _i: relay_cmd("list", "--json", "--limit", "10", "--relay-root", root), None),
                "cli_upsert": (
                    lambda root, _i: relay_cmd("upsert", "--stdin", "--relay-root", root),
                    lambda _i: conversation_payload(0, topic="profile scale update"),
                ),
                "cli_rebuild_index": (lambda root, _i: relay_cmd("rebuild-index", "--relay-root", root), None),
                "cli_search": (
                    lambda root, _i: relay_cmd("search", "profiler conversation 0001", "--limit", "10", "--relay-root", root),
                    None,
                ),
                "cli_search_body_fallback": (
                    lambda root, _i: relay_cmd(
                        "search", "profiler conversation 0001", "--limit", "10", "--no-semble", "--relay-root", root
                    ),
                    None,
                ),
                "cli_context": (
                    lambda root, _i: relay_cmd("context", profiler_record_id(1), "--json", "--relay-root", root),
                    None,
                ),
                "cli_regen_refs": (lambda root, _i: relay_cmd("regen-refs", "--relay-root", root), None),
            }
            for name in selected:
                if name == "cli_rebuild_index_full":
                    source = operation_dataset_root(base, config, cache, "scale-cli-rebuild-index-full")
                    operations[name] = time_paired_cold_rebuild(config, source)
                else:
                    command, make_input = scale_commands[name]
                    operations[name] = scale_operation(name, config, base, cache, command, make_input=make_input)
            telemetry = aggregate_scale_telemetry(operations)
            gate = evaluate_scale_gate(
                operations,
                telemetry,
                records=config.records,
                budgets=scale_budgets,
                selected=selected,
            )
        else:
            for name in selected:
                operations[name] = OPERATIONS[name](config, base, cache)
                if name in STRUCTURAL_GATES:
                    operations[name]["structural_run"] = structural_run(name, config, base, cache)
            telemetry = None
            gate = evaluate_gate(operations, loaded_budgets)
        profiles = run_profiles(selected, config, base, cache) if args.profile and not scale_mode else []
        dataset_root_path = dataset_root(base, config, cache) if (scale_mode or any(name in RUNTIME_COVERAGE_METRICS for name in selected)) else base
        dataset = dataset_report(dataset_root_path, config.records)
        provenance = provenance_report(ACTIVE_BINARY, dataset)
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
                "budget_profile": args.budget_profile,
                "binary": str(ACTIVE_BINARY),
                "scale_gates": scale_mode,
            },
            "budgets": scale_budgets if scale_mode else {name: loaded_budgets[name] for name in selected if name in loaded_budgets},
            "operations": operations,
            "profiles": profiles,
            "gate": gate,
            "scale": {"telemetry": telemetry, "required_metrics": list(REQUIRED_SCALE_METRICS)} if scale_mode else None,
            "dataset": dataset,
            "provenance": provenance,
            "promotion_eligible": config.runs == SCALE_RUNS,
            "elapsed_ms": round(ms_since(started), 3),
        }

    report_path = write_report(report, out_path, stdout_json=stdout_json)
    if not stdout_json:
        status = "PASSED" if report["gate"]["passed"] else "FAILED"
        print(json.dumps({"gate": status, "report": report_path, "failures": report["gate"]["failures"]}, indent=2))
    if gated and not report["gate"]["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
