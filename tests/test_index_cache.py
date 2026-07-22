"""Relay v2 archive/cache/transaction quality gates.

These are forward gates for docs/specs/relay-v2-performance-and-fidelity.md. They are
intentionally not xfailed: the pre-v2 CLI must stay red until the corresponding
milestone is implemented.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from _util import RUST_BINARY, clean_env, load_json, run_cli


NOW = "2026-01-16T12:00:00Z"


def payload(cid: str, topic: str, *, refs: list[dict[str, str]] | None = None) -> str:
    return json.dumps(
        {
            "id": cid,
            "topic": topic,
            "status": "active",
            "tags": ["v2", topic.split()[0]],
            "refs": refs or [],
            "created": NOW,
            "updated": NOW,
            "sections": {
                "summary": f"{topic} summary",
                "glossary": f"- **{topic.split()[0]}** - deterministic fixture",
                "qa": "- **Q:** stable? **A:** yes.",
            },
            "resume": {
                "goal": f"resume {topic}",
                "next_steps": ["run the next deterministic step"],
            },
        }
    )


def init_root(tmp_path: Path, name: str = "root") -> tuple[Path, dict[str, str]]:
    home = tmp_path / f"{name}-home"
    root = home / ".relay"
    env = clean_env(home=home, RELAY_TEST_MODE="1", RELAY_TEST_NOW=NOW)
    proc = run_cli(["init", "--relay-root", root], cwd=tmp_path, env=env)
    assert proc.returncode == 0, proc.stderr
    return root, env


def upsert(root: Path, env: dict[str, str], cwd: Path, body: str) -> None:
    proc = run_cli(
        ["upsert", "--stdin", "--relay-root", root],
        cwd=cwd,
        env=env,
        input=body,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr


def record_snapshot(root: Path) -> list[tuple[str, bytes]]:
    convs = root / "convs"
    return [
        (path.relative_to(convs).as_posix(), path.read_bytes())
        for path in sorted(convs.rglob("*.md"), key=lambda item: item.relative_to(convs).as_posix())
    ]


def manifest(root: Path) -> dict:
    return json.loads((root / ".semble" / "index-v2" / "manifest.json").read_text(encoding="utf-8"))


def normalized_result(proc, *paths: Path) -> tuple[int, str, str]:
    stdout, stderr = proc.stdout, proc.stderr
    for path in sorted(paths, key=lambda value: len(str(value)), reverse=True):
        candidates = (str(path), str(path).replace("\\", "/"))
        for candidate in candidates:
            escaped = json.dumps(candidate)[1:-1]
            stdout = stdout.replace(candidate, "<PATH>").replace(escaped, "<PATH>")
            stderr = stderr.replace(candidate, "<PATH>").replace(escaped, "<PATH>")
    return proc.returncode, stdout, stderr


def make_nested_legacy_source(tmp_path: Path, name: str) -> Path:
    source, env = init_root(tmp_path, f"{name}-legacy")
    upsert(source, env, tmp_path, payload("conv_260116_imported", "nested imported"))
    original = next((source / "convs").glob("*.md"))
    nested = source / "convs" / "2026" / "01" / original.name
    nested.parent.mkdir(parents=True)
    original.replace(nested)
    return source


def run_equivalence_sequence(root: Path, source: Path, cwd: Path, *, no_cache: bool) -> list[tuple[int, str, str]]:
    env = clean_env(
        home=root.parent,
        RELAY_TEST_MODE="1",
        RELAY_TEST_NOW=NOW,
        **({"RELAY_NO_CACHE": "1"} if no_cache else {}),
    )
    commands: list[tuple[list[object], str | None]] = [
        (
            ["sidekick", "conv_260101_parent", "branch topic", "--id", "conv_260102_branch", "--relay-root", root],
            None,
        ),
        (
            ["return", "conv_260102_branch", "--digest", "branch complete", "--relay-root", root],
            None,
        ),
        (
            ["continue", "conv_260101_parent", "--topic", "continued topic", "--id", "conv_260103_continued", "--relay-root", root],
            None,
        ),
        (["set-status", "conv_260103_continued", "parked", "--relay-root", root], None),
        (["import", "--from", source, "--relay-root", root], None),
        (["regen-refs", "--relay-root", root], None),
        (["rebuild-index", "--relay-root", root], None),
        (["list", "--json", "--relay-root", root], None),
        (["search", "branch", "--no-semble", "--relay-root", root], None),
        (["show", "conv_260101_parent", "--relay-root", root], None),
        (["context", "conv_260101_parent", "--json", "--relay-root", root], None),
    ]
    results = []
    for args, stdin in commands:
        proc = run_cli(args, cwd=cwd, env=env, input=stdin, timeout=30)
        results.append(normalized_result(proc, root, source))
        assert proc.returncode == 0, proc.stderr
    return results


def test_recursive_import_is_indexed_and_searchable(tmp_path: Path) -> None:
    root, env = init_root(tmp_path)
    source = make_nested_legacy_source(tmp_path, "recursive")

    imported = run_cli(["import", "--from", source, "--relay-root", root], cwd=tmp_path, env=env)
    assert imported.returncode == 0, imported.stderr
    assert load_json(imported)["records"] == 1

    listed = run_cli(["list", "--json", "--relay-root", root], cwd=tmp_path, env=env)
    searched = run_cli(["search", "nested imported", "--no-semble", "--relay-root", root], cwd=tmp_path, env=env)
    assert [row["id"] for row in load_json(listed)] == ["conv_260116_imported"]
    assert [row["id"] for row in load_json(searched)] == ["conv_260116_imported"]
    row = json.loads((root / "index.jsonl").read_text(encoding="utf-8"))
    assert row["file"].startswith("convs/2026/01/")


def test_compat_index_jsonl_keeps_exact_sorted_line_contract(tmp_path: Path) -> None:
    root, env = init_root(tmp_path, "compat-index")
    upsert(root, env, tmp_path, payload("conv_260116_contract", "compat contract"))
    raw = (root / "index.jsonl").read_text(encoding="utf-8")
    assert raw.endswith("\n") and not raw.endswith("\n\n")
    line = raw.removesuffix("\n")
    row = json.loads(line)
    assert list(row) == sorted(row)
    assert set(row) == {"created", "file", "id", "open", "refs", "status", "tags", "topic", "updated"}
    assert line == json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def test_manifest_schema_names_one_complete_generation(tmp_path: Path) -> None:
    root, env = init_root(tmp_path, "manifest-schema")
    upsert(root, env, tmp_path, payload("conv_260116_manifest-schema", "manifest schema"))
    current = manifest(root)
    assert set(current) == {
        "version",
        "generation",
        "record_count",
        "records_file",
        "records_hash",
        "postings_base_generation",
        "postings_base",
        "postings_base_directory_hash",
        "postings_deltas",
        "compat_hash",
    }
    assert current["version"] == 2
    assert current["record_count"] == 1
    assert current["records_file"] == f'records.{current["generation"]}.jsonl'
    assert current["postings_base"] == f'postings.base.{current["postings_base_generation"]}.bin'
    assert [delta["generation"] for delta in current["postings_deltas"]] == sorted(
        delta["generation"] for delta in current["postings_deltas"]
    )
    index_v2 = root / ".semble" / "index-v2"
    records_raw = (index_v2 / current["records_file"]).read_text(encoding="utf-8")
    assert records_raw.endswith("\n") and not records_raw.endswith("\n\n")
    record_row = json.loads(records_raw)
    assert list(record_row) == sorted(record_row)
    assert set(record_row) == {
        "id",
        "topic",
        "status",
        "tags",
        "refs",
        "created",
        "updated",
        "file",
        "open",
        "size",
        "mtime_ns",
        "fp",
    }
    named_files = [current["records_file"], current["postings_base"]] + [
        delta["file"] for delta in current["postings_deltas"]
    ]
    assert all(Path(name).name == name and (index_v2 / name).is_file() for name in named_files)
    for key in ("records_hash", "postings_base_directory_hash", "compat_hash"):
        assert len(current[key]) == 16 and set(current[key]) <= set("0123456789abcdef")
    for delta in current["postings_deltas"]:
        assert set(delta) == {"generation", "file", "directory_hash"}
        assert delta["file"] == f'postings.delta.{delta["generation"]}.bin'
        assert len(delta["directory_hash"]) == 16


def test_duplicate_ids_fail_with_all_paths_in_deterministic_order(tmp_path: Path) -> None:
    root, env = init_root(tmp_path, "duplicates-target")
    source, source_env = init_root(tmp_path, "duplicates-source")
    duplicate_id = "conv_260116_duplicate"
    upsert(source, source_env, tmp_path, payload(duplicate_id, "duplicate source"))
    original = next((source / "convs").glob("*.md"))
    first = source / "convs" / "a" / "first.md"
    second = source / "convs" / "z" / "second.md"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    original.replace(first)
    shutil.copy2(first, second)
    imported = run_cli(["import", "--from", source, "--relay-root", root], cwd=tmp_path, env=env)
    assert imported.returncode == 0, imported.stderr

    listed = run_cli(["list", "--json", "--relay-root", root], cwd=tmp_path, env=env)
    assert listed.returncode == 0, listed.stderr
    assert duplicate_id not in {row["id"] for row in load_json(listed)}

    rebuilt = run_cli(["rebuild-index", "--full", "--relay-root", root], cwd=tmp_path, env=env)
    assert rebuilt.returncode == 2
    assert "duplicate" in rebuilt.stderr.lower()
    first_path = "convs/a/first.md"
    second_path = "convs/z/second.md"
    assert first_path in rebuilt.stderr
    assert second_path in rebuilt.stderr
    assert rebuilt.stderr.index(first_path) < rebuilt.stderr.index(second_path)


def test_cache_and_no_cache_command_sequences_are_publicly_identical(tmp_path: Path) -> None:
    base, env = init_root(tmp_path, "base")
    upsert(base, env, tmp_path, payload("conv_260101_parent", "parent topic"))

    cached = tmp_path / "cached" / ".relay"
    uncached = tmp_path / "uncached" / ".relay"
    shutil.copytree(base, cached)
    shutil.copytree(base, uncached)
    cached_source = make_nested_legacy_source(tmp_path, "cached")
    uncached_source = make_nested_legacy_source(tmp_path, "uncached")

    cached_results = run_equivalence_sequence(cached, cached_source, tmp_path, no_cache=False)
    uncached_results = run_equivalence_sequence(uncached, uncached_source, tmp_path, no_cache=True)

    assert cached_results == uncached_results
    assert record_snapshot(cached) == record_snapshot(uncached)
    assert (cached / "index.jsonl").read_bytes() == (uncached / "index.jsonl").read_bytes()


def test_no_cache_mode_bypasses_and_does_not_rewrite_v2_generation(tmp_path: Path) -> None:
    root, env = init_root(tmp_path, "no-cache")
    upsert(root, env, tmp_path, payload("conv_260101_cached", "cached record"))
    manifest_path = root / ".semble" / "index-v2" / "manifest.json"
    before = manifest_path.read_bytes()

    no_cache_env = dict(env)
    no_cache_env["RELAY_NO_CACHE"] = "1"
    upsert(root, no_cache_env, tmp_path, payload("conv_260102_uncached", "uncached record"))
    assert manifest_path.read_bytes() == before
    uncached_list = run_cli(["list", "--json", "--relay-root", root], cwd=tmp_path, env=no_cache_env)
    assert {row["id"] for row in load_json(uncached_list)} == {"conv_260101_cached", "conv_260102_uncached"}

    cached_list = run_cli(["list", "--json", "--relay-root", root], cwd=tmp_path, env=env)
    assert {row["id"] for row in load_json(cached_list)} == {"conv_260101_cached", "conv_260102_uncached"}
    assert manifest_path.read_bytes() != before


def test_thread_count_is_deterministic_and_override_is_validated(tmp_path: Path) -> None:
    base, env = init_root(tmp_path, "thread-base")
    for index in range(24):
        upsert(base, env, tmp_path, payload(f"conv_2601{index:02d}_thread-{index}", f"thread record {index}"))
    one = tmp_path / "thread-one" / ".relay"
    eight = tmp_path / "thread-eight" / ".relay"
    shutil.copytree(base, one)
    shutil.copytree(base, eight)

    one_env = clean_env(home=one.parent, RELAY_SCAN_THREADS="1")
    eight_env = clean_env(home=eight.parent, RELAY_SCAN_THREADS="8")
    one_result = run_cli(["rebuild-index", "--full", "--relay-root", one], cwd=tmp_path, env=one_env)
    eight_result = run_cli(["rebuild-index", "--full", "--relay-root", eight], cwd=tmp_path, env=eight_env)
    assert normalized_result(one_result, one) == normalized_result(eight_result, eight)
    assert (one / "index.jsonl").read_bytes() == (eight / "index.jsonl").read_bytes()

    for value in ("0", "-1", "many", "65"):
        invalid = run_cli(
            ["rebuild-index", "--full", "--relay-root", one],
            cwd=tmp_path,
            env=clean_env(home=one.parent, RELAY_SCAN_THREADS=value),
        )
        assert invalid.returncode == 2
        assert "RELAY_SCAN_THREADS" in invalid.stderr


def test_corrupt_cache_layers_self_heal_without_partial_results(tmp_path: Path) -> None:
    root, env = init_root(tmp_path, "corrupt")
    for index in range(3):
        upsert(root, env, tmp_path, payload(f"conv_26011{index}_cache-{index}", f"cache topic {index}"))
    expected_list = run_cli(["list", "--json", "--relay-root", root], cwd=tmp_path, env=env).stdout
    expected_index = (root / "index.jsonl").read_bytes()

    first = manifest(root)
    index_v2 = root / ".semble" / "index-v2"
    (index_v2 / first["records_file"]).write_text('{"id":"partial"}\n{bad\n', encoding="utf-8")
    repaired = run_cli(["list", "--json", "--relay-root", root], cwd=tmp_path, env=env)
    assert repaired.returncode == 0, repaired.stderr
    assert repaired.stdout == expected_list
    second = manifest(root)
    assert second["generation"] > first["generation"]
    assert len((index_v2 / second["records_file"]).read_text(encoding="utf-8").splitlines()) == 3

    record_rows = [json.loads(line) for line in (index_v2 / second["records_file"]).read_text(encoding="utf-8").splitlines()]
    record_rows[0]["topic"] = "valid JSON but corrupt cache data"
    (index_v2 / second["records_file"]).write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in record_rows),
        encoding="utf-8",
    )
    valid_json_repaired = run_cli(["list", "--json", "--relay-root", root], cwd=tmp_path, env=env)
    assert valid_json_repaired.returncode == 0, valid_json_repaired.stderr
    assert valid_json_repaired.stdout == expected_list
    third = manifest(root)
    assert third["generation"] > second["generation"]

    (index_v2 / third["postings_base"]).write_bytes(b"corrupt postings base")
    search = run_cli(["search", "cache topic", "--no-semble", "--relay-root", root], cwd=tmp_path, env=env)
    assert search.returncode == 0, search.stderr
    assert {row["id"] for row in load_json(search)} == {f"conv_26011{i}_cache-{i}" for i in range(3)}

    generation_before_export_repair = manifest(root)["generation"]
    (root / "index.jsonl").write_text("{bad\n", encoding="utf-8")
    export_repaired = run_cli(["list", "--json", "--relay-root", root], cwd=tmp_path, env=env)
    assert export_repaired.returncode == 0, export_repaired.stderr
    assert export_repaired.stdout == expected_list
    assert (root / "index.jsonl").read_bytes() == expected_index
    assert manifest(root)["generation"] == generation_before_export_repair


@pytest.mark.parametrize(
    "damage",
    [
        "missing_manifest",
        "malformed_manifest",
        "missing_records",
        "missing_postings_base",
        "missing_compat_index",
    ],
)
def test_missing_or_malformed_cache_artifact_repairs_from_source(tmp_path: Path, damage: str) -> None:
    root, env = init_root(tmp_path, f"repair-{damage}")
    for index in range(3):
        upsert(root, env, tmp_path, payload(f"conv_26014{index}_matrix-{index}", f"matrix signal {index}"))
    expected = run_cli(
        ["search", "matrix signal", "--no-semble", "--relay-root", root],
        cwd=tmp_path,
        env=env,
    )
    assert expected.returncode == 0, expected.stderr
    current = manifest(root)
    index_v2 = root / ".semble" / "index-v2"
    targets = {
        "missing_manifest": index_v2 / "manifest.json",
        "missing_records": index_v2 / current["records_file"],
        "missing_postings_base": index_v2 / current["postings_base"],
        "missing_compat_index": root / "index.jsonl",
    }
    if damage == "malformed_manifest":
        (index_v2 / "manifest.json").write_text('{"version":2,"generation":', encoding="utf-8")
    else:
        targets[damage].unlink()

    repaired = run_cli(
        ["search", "matrix signal", "--no-semble", "--relay-root", root],
        cwd=tmp_path,
        env=env,
    )
    assert repaired.returncode == 0, repaired.stderr
    assert repaired.stdout == expected.stdout
    after = manifest(root)
    assert (index_v2 / after["records_file"]).is_file()
    assert (index_v2 / after["postings_base"]).is_file()
    assert (root / "index.jsonl").is_file()


def test_manifest_path_substitution_is_rejected_without_outside_read(tmp_path: Path) -> None:
    root, env = init_root(tmp_path, "manifest-path")
    upsert(root, env, tmp_path, payload("conv_260116_manifest", "manifest path"))
    manifest_path = root / ".semble" / "index-v2" / "manifest.json"
    bad = json.loads(manifest_path.read_text(encoding="utf-8"))
    bad["records_file"] = "../../outside.jsonl"
    manifest_path.write_text(json.dumps(bad), encoding="utf-8")
    outside = root / ".semble" / "outside.jsonl"
    outside.write_text('{"topic":"must never be read"}\n', encoding="utf-8")
    trace = tmp_path / "manifest-path-trace.jsonl"
    traced_env = dict(env)
    traced_env["RELAY_TEST_TRACE_IO"] = str(trace)

    listed = run_cli(["list", "--json", "--relay-root", root], cwd=tmp_path, env=traced_env)
    assert listed.returncode == 0, listed.stderr
    assert [row["id"] for row in load_json(listed)] == ["conv_260116_manifest"]
    captured = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert all("outside.jsonl" not in event.get("path", "") for event in captured)
    assert manifest(root)["records_file"].startswith("records.")


def test_changed_malformed_source_never_serves_stale_cached_row(tmp_path: Path) -> None:
    root, env = init_root(tmp_path, "malformed-source")
    bad_id = "conv_260116_will-break"
    good_id = "conv_260117_stays-good"
    upsert(root, env, tmp_path, payload(bad_id, "stale marker topic"))
    upsert(root, env, tmp_path, payload(good_id, "healthy topic"))
    broken = next(path for path in (root / "convs").glob("*.md") if f'id = "{bad_id}"' in path.read_text(encoding="utf-8"))
    broken.write_text("+++\nid = this is not TOML\n+++\nbroken\n", encoding="utf-8")

    listed = run_cli(["list", "--json", "--relay-root", root], cwd=tmp_path, env=env)
    assert listed.returncode == 0, listed.stderr
    assert [row["id"] for row in load_json(listed)] == [good_id]
    searched = run_cli(["search", "stale marker", "--no-semble", "--relay-root", root], cwd=tmp_path, env=env)
    assert searched.returncode == 0, searched.stderr
    assert load_json(searched) == []

    for args in (
        ["show", bad_id],
        ["context", bad_id, "--json"],
        ["rebuild-index", "--full"],
        ["regen-refs"],
    ):
        strict = run_cli([*args, "--relay-root", root], cwd=tmp_path, env=env)
        assert strict.returncode == 2
    doctor = run_cli(["doctor", "--relay-root", root], cwd=tmp_path, env=env)
    assert doctor.returncode == 0, doctor.stderr
    errors = load_json(doctor)["parse_errors"]
    assert len(errors) == 1
    assert Path(errors[0]["file"]).name == broken.name


def test_same_stat_out_of_band_edit_needs_explicit_full_repair(tmp_path: Path) -> None:
    root, env = init_root(tmp_path, "same-stat")
    cid = "conv_260116_same-stat"
    upsert(root, env, tmp_path, payload(cid, "alpha topic"))
    record = next((root / "convs").glob("*.md"))
    before_stat = record.stat()
    original = record.read_bytes()
    changed = original.replace(b"alpha", b"bravo")
    assert len(changed) == before_stat.st_size and changed != original
    record.write_bytes(changed)
    os.utime(record, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))
    after_stat = record.stat()
    assert (after_stat.st_size, after_stat.st_mtime_ns) == (before_stat.st_size, before_stat.st_mtime_ns)

    stale = run_cli(["list", "--json", "--relay-root", root], cwd=tmp_path, env=env)
    assert stale.returncode == 0, stale.stderr
    assert load_json(stale)[0]["topic"] == "alpha topic"
    no_cache_env = dict(env)
    no_cache_env["RELAY_NO_CACHE"] = "1"
    uncached = run_cli(["list", "--json", "--relay-root", root], cwd=tmp_path, env=no_cache_env)
    assert uncached.returncode == 0, uncached.stderr
    assert load_json(uncached)[0]["topic"] == "bravo topic"
    repaired = run_cli(["rebuild-index", "--full", "--relay-root", root], cwd=tmp_path, env=env)
    assert repaired.returncode == 0, repaired.stderr
    refreshed = run_cli(["list", "--json", "--relay-root", root], cwd=tmp_path, env=env)
    assert load_json(refreshed)[0]["topic"] == "bravo topic"


def test_interrupted_neighbor_transaction_replays_before_read(tmp_path: Path) -> None:
    root, env = init_root(tmp_path, "journal")
    parent_a = "conv_260110_parent-a"
    parent_b = "conv_260111_parent-b"
    child = "conv_260112_child"
    upsert(root, env, tmp_path, payload(parent_a, "parent a"))
    upsert(root, env, tmp_path, payload(parent_b, "parent b"))
    upsert(root, env, tmp_path, payload(child, "child", refs=[{"id": parent_a, "rel": "spawned-from"}]))

    failing_env = dict(env)
    failing_env["RELAY_TEST_CRASH_AT"] = "after_record:1"
    failed = run_cli(
        ["upsert", "--stdin", "--relay-root", root],
        cwd=tmp_path,
        env=failing_env,
        input=payload(child, "child moved", refs=[{"id": parent_b, "rel": "spawned-from"}]),
        timeout=30,
    )
    assert failed.returncode != 0
    assert (root / ".semble" / "txn.pending").is_file()
    shutil.rmtree(root / ".semble" / "index-v2", ignore_errors=True)

    recovered = run_cli(["list", "--json", "--relay-root", root], cwd=tmp_path, env=env, timeout=30)
    assert recovered.returncode == 0, recovered.stderr
    assert not (root / ".semble" / "txn.pending").exists()
    parent_a_row = load_json(run_cli(["show", parent_a, "--relay-root", root], cwd=tmp_path, env=env))
    parent_b_row = load_json(run_cli(["show", parent_b, "--relay-root", root], cwd=tmp_path, env=env))
    child_row = load_json(run_cli(["show", child, "--relay-root", root], cwd=tmp_path, env=env))
    assert {"id": child, "rel": "spawned-to"} not in parent_a_row["refs"]
    assert {"id": child, "rel": "spawned-to"} in parent_b_row["refs"]
    assert child_row["refs"] == [{"id": parent_b, "rel": "spawned-from"}]


@pytest.mark.parametrize(
    "phase",
    [
        "after_journal",
        "after_record:1",
        "after_records_cache",
        "after_postings",
        "after_compat",
        "after_manifest",
        "after_journal_unlink",
    ],
)
def test_every_durability_boundary_recovers_complete_state(tmp_path: Path, phase: str) -> None:
    root, env = init_root(tmp_path, "durability")
    cid = "conv_260116_durable"
    upsert(root, env, tmp_path, payload(cid, "old complete state"))
    crash_env = dict(env)
    crash_env["RELAY_TEST_CRASH_AT"] = phase
    crashed = run_cli(
        ["upsert", "--stdin", "--relay-root", root],
        cwd=tmp_path,
        env=crash_env,
        input=payload(cid, "new complete state"),
        timeout=30,
    )
    assert crashed.returncode != 0

    restarted = run_cli(["show", cid, "--relay-root", root], cwd=tmp_path, env=env, timeout=30)
    assert restarted.returncode == 0, restarted.stderr
    assert load_json(restarted)["topic"] == "new complete state"
    assert manifest(root)["record_count"] == 1
    assert not (root / ".semble" / "txn.pending").exists()


def test_unreadable_journal_blocks_commands_and_is_never_deleted(tmp_path: Path) -> None:
    root, env = init_root(tmp_path, "bad-journal")
    upsert(root, env, tmp_path, payload("conv_260116_blocked", "blocked record"))
    journal = root / ".semble" / "txn.pending"
    corrupt = b"not a Relay transaction journal\x00\xff"
    journal.write_bytes(corrupt)

    for args in (
        ["list", "--json"],
        ["show", "conv_260116_blocked"],
        ["rebuild-index", "--full"],
        ["doctor"],
        ["doctor", "--fix"],
    ):
        proc = run_cli([*args, "--relay-root", root], cwd=tmp_path, env=env)
        assert proc.returncode == 2
        assert "journal" in proc.stderr.lower()
        assert journal.read_bytes() == corrupt


def test_import_preserves_arbitrary_bytes_and_recovers_all_staged_copies(tmp_path: Path) -> None:
    scratch, scratch_env = init_root(tmp_path, "import-scratch")
    upsert(scratch, scratch_env, tmp_path, payload("conv_260116_import-good", "import good"))
    good_bytes = next((scratch / "convs").glob("*.md")).read_bytes()
    source = tmp_path / "legacy-arbitrary"
    fixtures = {
        "a/good.md": good_bytes,
        "b/malformed.md": b"+++\nid = not valid TOML\n+++\nmalformed\n",
        "c/invalid-utf8.md": b"+++\nid = \"invalid\"\n+++\n\xff\xfe\n",
        "z/collision.md": b"legacy collision bytes\n",
    }
    for relative, content in fixtures.items():
        path = source / "convs" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    classified, classified_env = init_root(tmp_path, "import-classified")
    classified_collision = classified / "convs" / "z" / "collision.md"
    classified_collision.parent.mkdir(parents=True)
    classified_collision.write_bytes(b"existing collision bytes\n")
    imported = run_cli(["import", "--from", source, "--relay-root", classified], cwd=tmp_path, env=classified_env)
    assert imported.returncode == 0, imported.stderr
    classification = load_json(imported)
    assert classification["copied"] == sorted(classification["copied"])
    assert classification["unchanged"] == sorted(classification["unchanged"])
    assert classification["collisions"] == ["z/collision.md"]

    root, env = init_root(tmp_path, "import-target")
    collision = root / "convs" / "z" / "collision.md"
    collision.parent.mkdir(parents=True)
    collision.write_bytes(b"existing collision bytes\n")
    source_before = {path.relative_to(source).as_posix(): path.read_bytes() for path in source.rglob("*.md")}
    crash_env = dict(env)
    crash_env["RELAY_TEST_CRASH_AT"] = "after_record:1"
    crashed = run_cli(["import", "--from", source, "--relay-root", root], cwd=tmp_path, env=crash_env)
    assert crashed.returncode != 0

    recovered = run_cli(["doctor", "--relay-root", root], cwd=tmp_path, env=env)
    assert recovered.returncode == 0, recovered.stderr
    for relative, content in fixtures.items():
        target = root / "convs" / relative
        if relative == "z/collision.md":
            assert target.read_bytes() == b"existing collision bytes\n"
        else:
            assert target.read_bytes() == content
    assert {path.relative_to(source).as_posix(): path.read_bytes() for path in source.rglob("*.md")} == source_before


def test_parallel_readers_and_writers_publish_complete_generations(tmp_path: Path) -> None:
    root, env = init_root(tmp_path, "hammer")
    upsert(root, env, tmp_path, payload("conv_260100_anchor", "anchor"))

    def write(index: int) -> None:
        upsert(root, env, tmp_path, payload(f"conv_2602{index:02d}_writer-{index}", f"writer {index}"))

    def read(_: int) -> None:
        for args in (["list", "--json"], ["search", "anchor", "--no-semble"]):
            proc = run_cli([*args, "--relay-root", root], cwd=tmp_path, env=env, timeout=30)
            assert proc.returncode == 0, proc.stderr
            assert isinstance(load_json(proc), list)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(write, index) for index in range(6)]
        futures += [pool.submit(read, index) for index in range(12)]
        for future in futures:
            future.result(timeout=60)

    listed = load_json(run_cli(["list", "--limit", "100", "--json", "--relay-root", root], cwd=tmp_path, env=env))
    assert {row["id"] for row in listed} == {"conv_260100_anchor", *(f"conv_2602{i:02d}_writer-{i}" for i in range(6))}
    current = manifest(root)
    index_v2 = root / ".semble" / "index-v2"
    assert (index_v2 / current["records_file"]).is_file()
    assert (index_v2 / current["postings_base"]).is_file()


def test_shared_readers_overlap_and_waiting_writer_progresses(tmp_path: Path) -> None:
    root, env = init_root(tmp_path, "lock-barrier")
    upsert(root, env, tmp_path, payload("conv_260100_anchor", "anchor"))
    barrier = tmp_path / "store-lock"
    barrier_env = dict(env)
    barrier_env["RELAY_TEST_BARRIER_AFTER_LOCK"] = str(barrier)

    def launch(args: list[object], stdin: str | None = None) -> subprocess.Popen[str]:
        proc = subprocess.Popen(
            [str(RUST_BINARY), *map(str, args)],
            cwd=str(tmp_path),
            env=barrier_env,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if stdin is not None:
            assert proc.stdin is not None
            proc.stdin.write(stdin)
            proc.stdin.close()
            proc.stdin = None
        return proc

    readers = [
        launch(["list", "--json", "--relay-root", root]),
        launch(["search", "anchor", "--no-semble", "--relay-root", root]),
    ]
    deadline = time.monotonic() + 5
    while len(list(tmp_path.glob("store-lock.*.ready"))) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    reader_ready_count = len(list(tmp_path.glob("store-lock.*.ready")))
    if reader_ready_count != 2:
        Path(f"{barrier}.release").write_text("release", encoding="utf-8")
        for proc in readers:
            proc.communicate(timeout=15)
    assert reader_ready_count == 2, "two shared readers must reach the lock barrier together"

    writer = launch(
        ["upsert", "--stdin", "--relay-root", root],
        payload("conv_260101_writer", "writer"),
    )
    time.sleep(0.2)
    writer_waited = writer.poll() is None
    ready_before_release = len(list(tmp_path.glob("store-lock.*.ready")))

    Path(f"{barrier}.release").write_text("release", encoding="utf-8")
    for proc in [*readers, writer]:
        stdout, stderr = proc.communicate(timeout=15)
        assert proc.returncode == 0, stderr + stdout
    assert writer_waited, "writer must wait while the shared readers hold the lock"
    assert ready_before_release == 2

    listed = load_json(run_cli(["list", "--limit", "100", "--json", "--relay-root", root], cwd=tmp_path, env=env))
    assert "conv_260101_writer" in {row["id"] for row in listed}
