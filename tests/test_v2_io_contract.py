"""Deterministic complexity gates backed by Relay v2 debug I/O telemetry."""
from __future__ import annotations

import json
import shutil
import subprocess
import tomllib
import time
from collections import Counter
from pathlib import Path

from _util import RUST_BINARY, clean_env, load_json, run_cli


NOW = "2026-01-16T12:00:00Z"


def test_v2_removes_regex_without_adding_general_runtime_dependencies() -> None:
    cargo = tomllib.loads((Path(__file__).resolve().parent.parent / "Cargo.toml").read_text(encoding="utf-8"))
    assert set(cargo["dependencies"]) == {"serde_json", "toml"}


def body(cid: str, topic: str, refs: list[dict[str, str]] | None = None) -> str:
    return json.dumps(
        {
            "id": cid,
            "topic": topic,
            "created": NOW,
            "updated": NOW,
            "refs": refs or [],
            "sections": {
                "summary": f"{topic} summary",
                "dict": "- **trace** - deterministic I/O event",
                "qa": "- **Q:** traced? **A:** yes.",
            },
        }
    )


def traced_env(home: Path, trace: Path, **overrides: str) -> dict[str, str]:
    return clean_env(
        home=home,
        RELAY_TEST_MODE="1",
        RELAY_TEST_NOW=NOW,
        RELAY_TEST_TRACE_IO=trace,
        **overrides,
    )


def events(trace: Path) -> list[dict]:
    if not trace.exists():
        return []
    return [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines() if line.strip()]


def reset_trace(trace: Path) -> None:
    trace.unlink(missing_ok=True)


def upsert(root: Path, cwd: Path, env: dict[str, str], payload: str) -> None:
    proc = run_cli(["upsert", "--stdin", "--relay-root", root], cwd=cwd, env=env, input=payload)
    assert proc.returncode == 0, proc.stderr


def test_help_has_no_store_io_events_or_root_side_effects(tmp_path: Path) -> None:
    home = tmp_path / "home"
    trace = tmp_path / "help-trace.jsonl"
    proc = run_cli(["--help"], cwd=tmp_path, env=traced_env(home, trace))

    assert proc.returncode == 0, proc.stderr
    assert events(trace) == []
    assert not (home / ".relay").exists()


def test_missing_platform_home_errors_without_cwd_root_fallback(tmp_path: Path) -> None:
    env = clean_env()
    env.pop("HOME", None)
    env.pop("USERPROFILE", None)
    proc = run_cli(["list", "--json"], cwd=tmp_path, env=env)

    assert proc.returncode == 2
    assert "home" in proc.stderr.lower()
    assert not (tmp_path / ".relay").exists()


def test_doctor_fix_never_falls_back_to_compile_time_source_checkout(tmp_path: Path) -> None:
    root = tmp_path / "isolated root" / ".relay"
    env = clean_env(home=tmp_path / "home")
    assert run_cli(["init", "--relay-root", root], cwd=tmp_path, env=env).returncode == 0
    isolated_dir = tmp_path / "isolated-bin"
    isolated_dir.mkdir()
    isolated_binary = isolated_dir / RUST_BINARY.name
    shutil.copy2(RUST_BINARY, isolated_binary)

    proc = subprocess.run(
        [str(isolated_binary), "doctor", "--fix", "--relay-root", str(root)],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    repair = load_json(proc)["fix"]["installer_repair"]
    assert repair["available"] is False
    assert "Plugin installation root" in repair["reason"]


def test_warm_list_has_one_snapshot_and_zero_record_or_publish_io(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = home / ".relay"
    trace = tmp_path / "warm-list-trace.jsonl"
    env = traced_env(home, trace)
    assert run_cli(["init", "--relay-root", root], cwd=tmp_path, env=env).returncode == 0
    for index in range(5):
        upsert(root, tmp_path, env, body(f"conv_26010{index}_trace-{index}", f"trace {index}"))

    reset_trace(trace)
    listed = run_cli(["list", "--json", "--relay-root", root], cwd=tmp_path, env=env)
    assert listed.returncode == 0, listed.stderr
    names = Counter(event["event"] for event in events(trace))
    assert names["snapshot"] == 1
    snapshot_end = [event for event in events(trace) if event["event"] == "snapshot_end"]
    assert len(snapshot_end) == 1
    assert snapshot_end[0]["files"] == 5
    assert sum(event["bytes"] for event in events(trace) if event["event"] == "cache_read") > 0
    for name in ("record_open", "record_write", "journal_publish", "cache_publish", "compat_index_publish"):
        assert names[name] == 0


def test_single_record_update_has_bounded_io_and_one_publication(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = home / ".relay"
    trace = tmp_path / "update-trace.jsonl"
    env = traced_env(home, trace)
    assert run_cli(["init", "--relay-root", root], cwd=tmp_path, env=env).returncode == 0
    cid = "conv_260110_one-record"
    upsert(root, tmp_path, env, body(cid, "before"))

    reset_trace(trace)
    upsert(root, tmp_path, env, body(cid, "after"))
    captured = events(trace)
    names = Counter(event["event"] for event in captured)
    assert names["snapshot"] == 1
    assert names["record_write"] == 1
    assert names["journal_publish"] == 1
    assert names["cache_publish"] == 1
    assert names["compat_index_publish"] == 1
    opened = Counter(event["path"] for event in captured if event["event"] == "record_open")
    assert sum(opened.values()) == 1 and max(opened.values()) == 1


def test_ref_retarget_writes_only_child_and_two_neighbors(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = home / ".relay"
    trace = tmp_path / "refs-trace.jsonl"
    env = traced_env(home, trace)
    assert run_cli(["init", "--relay-root", root], cwd=tmp_path, env=env).returncode == 0
    parent_a = "conv_260111_parent-a"
    parent_b = "conv_260112_parent-b"
    child = "conv_260113_child"
    upsert(root, tmp_path, env, body(parent_a, "parent a"))
    upsert(root, tmp_path, env, body(parent_b, "parent b"))
    upsert(root, tmp_path, env, body("conv_260114_unrelated", "unrelated"))
    upsert(root, tmp_path, env, body(child, "child", [{"id": parent_a, "rel": "spawned-from"}]))
    expected = {
        path.relative_to(root).as_posix()
        for path in (root / "convs").glob("*.md")
        if any(f'id = "{cid}"' in path.read_text(encoding="utf-8") for cid in (parent_a, parent_b, child))
    }

    reset_trace(trace)
    upsert(root, tmp_path, env, body(child, "child moved", [{"id": parent_b, "rel": "spawned-from"}]))
    captured = events(trace)
    written = {event["path"] for event in captured if event["event"] == "record_write"}
    assert written == expected
    assert Counter(event["event"] for event in captured)["cache_publish"] == 1


def test_full_rebuild_reports_configured_parallel_worker_count(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = home / ".relay"
    trace = tmp_path / "workers-trace.jsonl"
    env = traced_env(home, trace)
    assert run_cli(["init", "--relay-root", root], cwd=tmp_path, env=env).returncode == 0
    for index in range(20):
        upsert(root, tmp_path, env, body(f"conv_2602{index:02d}_worker-{index}", f"worker {index}"))

    reset_trace(trace)
    rebuilt = run_cli(
        ["rebuild-index", "--full", "--relay-root", root],
        cwd=tmp_path,
        env=traced_env(home, trace, RELAY_SCAN_THREADS="8"),
    )
    assert rebuilt.returncode == 0, rebuilt.stderr
    starts = [event for event in events(trace) if event["event"] == "scan_start"]
    assert len(starts) == 1
    assert starts[0]["workers"] == 8
    ended = [event for event in events(trace) if event["event"] == "scan_end"]
    assert len(ended) == 1
    assert ended[0]["workers_started"] == 8
    assert ended[0]["max_active"] >= 2
    worker_ids = {event["worker_id"] for event in events(trace) if event["event"] == "record_open"}
    assert len(worker_ids) >= 2
def test_reader_rechecks_journal_after_waiting_for_writer(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = home / ".relay"
    trace = tmp_path / "reader-race-trace.jsonl"
    env = traced_env(home, trace)
    assert run_cli(["init", "--relay-root", root], cwd=tmp_path, env=env).returncode == 0
    parent_a = "conv_260215_parent-a"
    parent_b = "conv_260216_parent-b"
    child = "conv_260217_child"
    upsert(root, tmp_path, env, body(parent_a, "parent a"))
    upsert(root, tmp_path, env, body(parent_b, "parent b"))
    upsert(
        root,
        tmp_path,
        env,
        body(child, "child", [{"id": parent_a, "rel": "spawned-from"}]),
    )
    reset_trace(trace)

    barrier = tmp_path / "writer-lock"
    writer_env = traced_env(
        home,
        trace,
        RELAY_TEST_BARRIER_AFTER_LOCK=barrier,
        RELAY_TEST_CRASH_AT="after_record:1",
    )
    writer = subprocess.Popen(
        [
            str(RUST_BINARY),
            "upsert",
            "--stdin",
            "--relay-root",
            str(root),
        ],
        cwd=str(tmp_path),
        env=writer_env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    reader: subprocess.Popen | None = None
    try:
        assert writer.stdin is not None
        writer.stdin.write(
            body(child, "child moved", [{"id": parent_b, "rel": "spawned-from"}])
        )
        writer.stdin.close()
        ready = Path(f"{barrier}.{writer.pid}.ready")
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "writer did not reach the lock barrier"

        reader = subprocess.Popen(
            [
                str(RUST_BINARY),
                "list",
                "--json",
                "--relay-root",
                str(root),
            ],
            cwd=str(tmp_path),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if any(
                event["event"] == "lock_wait" and event["mode"] == "shared"
                for event in events(trace)
            ):
                break
            time.sleep(0.01)
        else:
            raise AssertionError("reader did not wait for the writer's exclusive lock")

        (tmp_path / "writer-lock.release").write_text("release", encoding="utf-8")
        writer_result = writer.communicate(timeout=10)
        reader_result = reader.communicate(timeout=10)
    finally:
        if writer.poll() is None:
            writer.kill()
            writer.wait()
        if reader is not None and reader.poll() is None:
            reader.kill()
            reader.wait()

    assert writer_result[0] == ""
    assert writer.returncode != 0
    assert reader_result[1] == ""
    assert reader.returncode == 0, reader_result[1]
    rows = {row["id"]: row for row in json.loads(reader_result[0])}
    assert rows[child]["refs"] == [{"id": parent_b, "rel": "spawned-from"}]
    assert {"id": child, "rel": "spawned-to"} not in rows[parent_a]["refs"]
    assert {"id": child, "rel": "spawned-to"} in rows[parent_b]["refs"]
    assert not (root / ".semble" / "txn.pending").exists()
    shared_acquires = [
        event
        for event in events(trace)
        if event["event"] == "lock_acquire" and event["mode"] == "shared"
    ]
    assert len(shared_acquires) >= 2
