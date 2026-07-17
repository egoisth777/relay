"""Relay v2 search compatibility and postings quality gates."""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from _util import clean_env, load_json, run_cli


NOW = "2026-01-16T12:00:00Z"


def payload(cid: str, topic: str, *, marker: str, tags: list[str] | None = None) -> str:
    return json.dumps(
        {
            "id": cid,
            "topic": topic,
            "tags": tags or [],
            "created": NOW,
            "updated": NOW,
            "sections": {
                "summary": marker,
                "dict": "- **search** - compatibility cascade",
                "qa": "- **Q:** deterministic? **A:** yes.",
            },
        }
    )


def setup_root(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    home = tmp_path / "home"
    root = home / ".relay"
    env = clean_env(home=home, RELAY_TEST_MODE="1", RELAY_TEST_NOW=NOW)
    init = run_cli(["init", "--relay-root", root], cwd=tmp_path, env=env)
    assert init.returncode == 0, init.stderr
    fixtures = [
        ("conv_260101_needle-file", "ordinary alpha", "alpha body", ["plain"]),
        ("conv_260111_needle-file-longer", "ordinary exact collision", "collision body", ["plain"]),
        ("conv_260102_topic-hit", "needle topic metadata", "beta body", ["violetmetadata"]),
        ("conv_260103_short", "xy short query", "gamma body", ["café-tag"]),
        ("conv_260104_body", "ordinary delta", "bodyonlysignal appears only in the body", ["plain"]),
        ("conv_260105_semble", "semantic decoy", "no body match", ["plain"]),
        ("conv_260108_term-a", "amber metadata", "term a body", ["plain"]),
        ("conv_260109_term-b", "cobalt metadata", "term b body", ["plain"]),
        ("conv_260110_term-both", "amber cobalt metadata", "both terms body", ["plain"]),
        ("conv_260106_body-order-a", "ordinary body order a", "dualbody marker", ["plain"]),
        ("conv_260107_body-order-b", "ordinary body order b", "dualbody marker", ["plain"]),
    ]
    for cid, topic, marker, tags in fixtures:
        proc = run_cli(
            ["upsert", "--stdin", "--relay-root", root],
            cwd=tmp_path,
            env=env,
            input=payload(cid, topic, marker=marker, tags=tags),
        )
        assert proc.returncode == 0, proc.stderr
    return root, env


def search(root: Path, env: dict[str, str], cwd: Path, query: str, *args: object):
    return run_cli(["search", query, *args, "--relay-root", root], cwd=cwd, env=env, timeout=30)


def trace_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def install_fake_semble(tmp_path: Path, env: dict[str, str], output: str) -> tuple[dict[str, str], Path]:
    fake_dir = tmp_path / "fake-bin"
    fake_dir.mkdir()
    marker = tmp_path / "semble-called.json"
    runner = tmp_path / "search" if os.name == "nt" else fake_dir / "semble_runner.py"
    runner.write_text(
        """import json
import os
import sys
from pathlib import Path

Path(os.environ["SEMBLE_CALLED"]).write_text(json.dumps(sys.argv), encoding="utf-8")
print(os.environ["SEMBLE_OUTPUT"])
""",
        encoding="utf-8",
    )
    if os.name == "nt":
        shutil.copy2(sys.executable, fake_dir / "semble.exe")
    else:
        executable = fake_dir / "semble"
        executable.write_text(f"#!{sys.executable}\n" + runner.read_text(encoding="utf-8"), encoding="utf-8")
        executable.chmod(0o755)
    configured = dict(env)
    configured["PATH"] = os.pathsep.join([str(fake_dir), configured.get("PATH", "")])
    configured["SEMBLE_CALLED"] = str(marker)
    configured["SEMBLE_OUTPUT"] = output
    return configured, marker


def test_tier_one_short_circuits_before_topic_metadata_tier(tmp_path: Path) -> None:
    root, env = setup_root(tmp_path)
    proc = search(root, env, tmp_path, "needle", "--no-semble")
    assert proc.returncode == 0, proc.stderr
    rows = load_json(proc)
    assert [row["id"] for row in rows] == ["conv_260101_needle-file"]
    assert rows[0]["layer"] == "fff"


def test_exact_id_probe_beats_longer_substring_collision(tmp_path: Path) -> None:
    root, env = setup_root(tmp_path)
    proc = search(root, env, tmp_path, "conv_260101_needle-file", "--no-semble")
    assert proc.returncode == 0, proc.stderr
    rows = load_json(proc)
    assert [row["id"] for row in rows] == ["conv_260101_needle-file"]
    assert rows[0]["layer"] == "fff"


def test_topic_and_tag_hits_keep_compatibility_layer_label(tmp_path: Path) -> None:
    root, env = setup_root(tmp_path)
    for query in ("metadata", "violetmetadata"):
        proc = search(root, env, tmp_path, query, "--no-semble")
        assert proc.returncode == 0, proc.stderr
        rows = load_json(proc)
        assert [row["id"] for row in rows] == ["conv_260102_topic-hit"]
        assert rows[0]["layer"] == "rg-index-fallback"


def test_short_and_unicode_queries_match_no_cache_reference_bytes(tmp_path: Path) -> None:
    root, env = setup_root(tmp_path)
    for query in ("xy", "café", "260103_short"):
        cached = search(root, env, tmp_path, query, "--no-semble")
        uncached_env = dict(env)
        uncached_env["RELAY_NO_CACHE"] = "1"
        uncached = search(root, uncached_env, tmp_path, query, "--no-semble")
        assert cached.returncode == uncached.returncode == 0
        assert cached.stdout == uncached.stdout
        assert cached.stderr == uncached.stderr


def test_multi_term_postings_union_candidates_before_exact_scoring(tmp_path: Path) -> None:
    root, env = setup_root(tmp_path)
    proc = search(root, env, tmp_path, "amber cobalt", "--no-semble")
    assert proc.returncode == 0, proc.stderr
    rows = load_json(proc)
    assert {row["id"] for row in rows} == {
        "conv_260108_term-a",
        "conv_260109_term-b",
        "conv_260110_term-both",
    }
    assert rows[0]["id"] == "conv_260110_term-both"
    assert rows[0]["score"] == 2
    assert {row["score"] for row in rows[1:]} == {1}


def test_body_fallback_preserves_archive_path_order(tmp_path: Path) -> None:
    root, env = setup_root(tmp_path)
    fake_env, _ = install_fake_semble(tmp_path, env, "")
    proc = search(root, fake_env, tmp_path, "dualbody", "--no-semble")
    assert proc.returncode == 0, proc.stderr
    rows = load_json(proc)
    assert [row["id"] for row in rows] == ["conv_260106_body-order-a", "conv_260107_body-order-b"]
    assert {row["layer"] for row in rows} == {"semble-body-fallback"}


def test_no_semble_never_probes_or_launches_subprocess(tmp_path: Path) -> None:
    root, env = setup_root(tmp_path)
    fake_env, called = install_fake_semble(tmp_path, env, "convs/2026-01-05_semble.md")

    proc = search(root, fake_env, tmp_path, "bodyonlysignal", "--no-semble")
    assert proc.returncode == 0, proc.stderr
    rows = load_json(proc)
    assert [row["id"] for row in rows] == ["conv_260104_body"]
    assert rows[0]["layer"] == "semble-body-fallback"
    assert not called.exists()


def test_semble_full_nested_path_does_not_match_duplicate_basename(tmp_path: Path) -> None:
    root, env = setup_root(tmp_path)
    source = tmp_path / "legacy-source"
    for label, cid in (("a", "conv_260120_nested-a"), ("b", "conv_260121_nested-b")):
        scratch_home = tmp_path / f"scratch-{label}"
        scratch_root = scratch_home / ".relay"
        scratch_env = clean_env(home=scratch_home, RELAY_TEST_MODE="1", RELAY_TEST_NOW=NOW)
        assert run_cli(["init", "--relay-root", scratch_root], cwd=tmp_path, env=scratch_env).returncode == 0
        made = run_cli(
            ["upsert", "--stdin", "--relay-root", scratch_root],
            cwd=tmp_path,
            env=scratch_env,
            input=payload(cid, f"nested {label}", marker=f"nested body {label}"),
        )
        assert made.returncode == 0, made.stderr
        destination = source / "convs" / label / "shared.md"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(next((scratch_root / "convs").glob("*.md")).read_bytes())
    imported = run_cli(["import", "--from", source, "--relay-root", root], cwd=tmp_path, env=env)
    assert imported.returncode == 0, imported.stderr

    fake_env, _ = install_fake_semble(tmp_path, env, "convs/a/shared.md")
    proc = search(root, fake_env, tmp_path, "semanticnestedpath")
    assert proc.returncode == 0, proc.stderr
    assert [row["id"] for row in load_json(proc)] == ["conv_260120_nested-a"]

    fake_env["SEMBLE_OUTPUT"] = "convs\\a\\shared.md"
    windows_style = search(root, fake_env, tmp_path, "semanticnestedpath")
    assert windows_style.returncode == 0, windows_style.stderr
    assert [row["id"] for row in load_json(windows_style)] == ["conv_260120_nested-a"]


def test_zero_limit_short_circuits_before_semble_and_returns_empty(tmp_path: Path) -> None:
    root, env = setup_root(tmp_path)
    fake_env, called = install_fake_semble(tmp_path, env, "convs/2026-01-05_semble.md")
    proc = search(root, fake_env, tmp_path, "bodyonlysignal", "--limit", "0")
    assert proc.returncode == 0, proc.stderr
    assert load_json(proc) == []
    assert not called.exists()


def test_corrupt_postings_fall_back_exactly_and_repair(tmp_path: Path) -> None:
    root, env = setup_root(tmp_path)
    expected = search(root, env, tmp_path, "metadata", "--no-semble")
    assert expected.returncode == 0, expected.stderr
    index_v2 = root / ".semble" / "index-v2"
    before = json.loads((index_v2 / "manifest.json").read_text(encoding="utf-8"))
    (index_v2 / before["postings_base"]).write_bytes(b"corrupt postings base")

    actual = search(root, env, tmp_path, "metadata", "--no-semble")
    assert actual.returncode == 0, actual.stderr
    assert actual.stdout == expected.stdout
    after = json.loads((index_v2 / "manifest.json").read_text(encoding="utf-8"))
    assert after["generation"] > before["generation"]
    assert (index_v2 / after["postings_base"]).stat().st_size > 0


def test_corrupt_postings_delta_falls_back_exactly_and_repairs(tmp_path: Path) -> None:
    root, env = setup_root(tmp_path)
    for index in range(40):
        seeded = run_cli(
            ["upsert", "--stdin", "--relay-root", root],
            cwd=tmp_path,
            env=env,
            input=payload(
                f"conv_2603{index:02d}_delta-seed-{index}",
                f"ordinary delta seed {index}",
                marker=f"seed body {index}",
            ),
        )
        assert seeded.returncode == 0, seeded.stderr
    rebuilt = run_cli(["rebuild-index", "--full", "--relay-root", root], cwd=tmp_path, env=env)
    assert rebuilt.returncode == 0, rebuilt.stderr
    changed = run_cli(
        ["upsert", "--stdin", "--relay-root", root],
        cwd=tmp_path,
        env=env,
        input=payload(
            "conv_260102_topic-hit",
            "deltaonlytoken updated metadata",
            marker="beta body",
            tags=["violetmetadata"],
        ),
    )
    assert changed.returncode == 0, changed.stderr

    index_v2 = root / ".semble" / "index-v2"
    before = json.loads((index_v2 / "manifest.json").read_text(encoding="utf-8"))
    assert before["postings_deltas"], "one update after a full rebuild must publish a delta"
    delta = before["postings_deltas"][-1]
    (index_v2 / delta["file"]).write_bytes(b"corrupt postings delta")

    no_cache_env = dict(env)
    no_cache_env["RELAY_NO_CACHE"] = "1"
    expected = search(root, no_cache_env, tmp_path, "deltaonlytoken", "--no-semble")
    actual = search(root, env, tmp_path, "deltaonlytoken", "--no-semble")
    assert expected.returncode == actual.returncode == 0
    assert actual.stdout == expected.stdout
    after = json.loads((index_v2 / "manifest.json").read_text(encoding="utf-8"))
    assert after["generation"] > before["generation"]
    assert all((index_v2 / item["file"]).read_bytes() != b"corrupt postings delta" for item in after["postings_deltas"])


def test_selective_query_does_not_read_complete_postings_artifacts(tmp_path: Path) -> None:
    root, env = setup_root(tmp_path)
    trace = tmp_path / "postings-read-trace.jsonl"
    traced_env = dict(env)
    traced_env["RELAY_TEST_TRACE_IO"] = str(trace)
    proc = search(root, traced_env, tmp_path, "violetmetadata", "--no-semble")
    assert proc.returncode == 0, proc.stderr
    manifest = json.loads((root / ".semble" / "index-v2" / "manifest.json").read_text(encoding="utf-8"))
    index_v2 = root / ".semble" / "index-v2"
    total_postings_bytes = (index_v2 / manifest["postings_base"]).stat().st_size + sum(
        (index_v2 / delta["file"]).stat().st_size for delta in manifest["postings_deltas"]
    )
    posting_reads = [
        event
        for event in trace_events(trace)
        if event["event"] == "cache_read" and event["artifact"] in {"postings_directory", "postings_block"}
    ]
    assert any(event["artifact"] == "postings_block" for event in posting_reads)
    assert 0 < sum(event["bytes"] for event in posting_reads) < total_postings_bytes


def test_semble_timeout_override_rejects_invalid_values(tmp_path: Path) -> None:
    root, env = setup_root(tmp_path)
    for value in ("0", "-1", "nan", "inf", "later"):
        invalid_env = dict(env)
        invalid_env["RELAY_SEMBLE_TIMEOUT"] = value
        proc = search(root, invalid_env, tmp_path, "bodyonlysignal")
        assert proc.returncode == 2
        assert "RELAY_SEMBLE_TIMEOUT" in proc.stderr
