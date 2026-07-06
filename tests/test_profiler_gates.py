from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from _util import clean_env


REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILER = REPO_ROOT / "tools" / "profiler" / "conversate_loading_profiler.py"

REQUIRED_OPERATIONS = {
    "skill_load_common_path",
    "cli_help",
    "cli_init",
    "cli_list",
    "cli_upsert",
    "cli_rebuild_index",
    "cli_regen_refs",
    "codex_hook",
}
DIRECT_VERBS = {"save", "list", "resume"}
COMMON_PATH_GATES = {"file_count": 2.0, "bytes": 5120.0, "rough_tokens": 900.0, "broad_file_reads": 0.0}
RUNTIME_COVERAGE_OPERATIONS = {"cli_list", "cli_upsert", "cli_rebuild_index", "cli_regen_refs"}
RUNTIME_COVERAGE_RECORDS = 100


def load_profiler() -> ModuleType:
    spec = importlib.util.spec_from_file_location("conversate_loading_profiler_under_test", PROFILER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_profiler(args: list[object], *, cwd: Path, home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(PROFILER), *map(str, args)],
        cwd=str(cwd),
        env=clean_env(home=home),
        capture_output=True,
        text=True,
    )


def result_files() -> set[Path]:
    results = REPO_ROOT / "tools" / "profiler" / "results"
    return set(results.rglob("*")) if results.exists() else set()


def write_permissive_budget_file(path: Path) -> Path:
    budgets = {name: {"median_ms": 60_000, "max_ms": 60_000} for name in REQUIRED_OPERATIONS}
    budgets["skill_load_common_path"].update(COMMON_PATH_GATES)
    path.write_text(json.dumps(budgets), encoding="utf-8")
    return path


def test_runtime_budget_file_enforces_stated_gates() -> None:
    profiler = load_profiler()
    budgets = profiler.load_budgets(profiler.DEFAULT_BUDGET_FILE, 1.0)

    assert profiler.DEFAULT_RECORDS == RUNTIME_COVERAGE_RECORDS
    assert profiler.MIN_RUNTIME_COVERAGE_RECORDS == RUNTIME_COVERAGE_RECORDS
    assert set(profiler.RUNTIME_COVERAGE_METRICS) == RUNTIME_COVERAGE_OPERATIONS
    assert profiler.build_parser().parse_args([]).records == RUNTIME_COVERAGE_RECORDS
    for metric, allowed in COMMON_PATH_GATES.items():
        assert budgets["skill_load_common_path"][metric] == allowed
    for name in [operation for operation in REQUIRED_OPERATIONS if operation.startswith("cli_")]:
        assert budgets[name]["median_ms"] == 100.0
        assert budgets[name]["max_ms"] == 125.0
    assert budgets["codex_hook"]["median_ms"] == 50.0
    assert budgets["codex_hook"]["max_ms"] == 75.0


def test_common_path_loader_checks_direct_verbs_before_first_action() -> None:
    profiler = load_profiler()
    detail = profiler.load_common_skill_files()

    assert set(detail["verbs"]) == DIRECT_VERBS
    assert detail["max_file_count"] <= COMMON_PATH_GATES["file_count"]
    assert detail["max_bytes"] <= COMMON_PATH_GATES["bytes"]
    assert detail["max_rough_tokens"] <= COMMON_PATH_GATES["rough_tokens"]
    assert detail["broad_file_reads"] == 0
    assert detail["missing_files"] == []
    assert detail["missing_first_actions"] == []
    assert detail["pre_action_broad_mentions"] == {}
    for verb in DIRECT_VERBS:
        verb_detail = detail["verbs"][verb]
        assert len(verb_detail["files"]) == 1
        assert verb_detail["files"][0]["path"] == f"plugins/conv/skills/{verb}/SKILL.md"
        assert "python ~/.conversate/scripts/conv_cli.py" in verb_detail["first_useful_action"]
        assert verb_detail["pre_action_broad_mentions"] == []


def test_common_path_gate_fails_deterministic_fake_metrics() -> None:
    profiler = load_profiler()
    gate = profiler.evaluate_gate(
        {
            "skill_load_common_path": {
                "summary": {"runs": 1, "median_ms": 1.0, "max_ms": 2.0},
                "detail": {
                    "max_file_count": 3,
                    "max_bytes": 6000,
                    "max_rough_tokens": 901,
                    "broad_file_reads": 1,
                    "missing_files": [],
                    "missing_first_actions": ["resume"],
                    "pre_action_broad_mentions": {"save": ["~/.conversate/references/"]},
                    "loaded_broad_files": ["references/save.md"],
                },
            }
        },
        {
            "skill_load_common_path": {
                "median_ms": 10,
                "max_ms": 50,
                **COMMON_PATH_GATES,
            }
        },
    )

    metrics = {failure["metric"] for failure in gate["failures"]}
    assert gate["passed"] is False
    assert {"file_count", "bytes", "rough_tokens", "broad_file_reads", "common_path_contract"} <= metrics


def test_gate_reports_time_failures_in_operation_and_metric_order() -> None:
    profiler = load_profiler()
    summary = {"runs": 1, "median_ms": 2.0}
    budgets = {
        "cli_help": {"median_ms": 1.0, "max_ms": 1.0},
        "cli_init": {"median_ms": 1.0, "max_ms": 1.0},
    }

    gate = profiler.evaluate_gate(
        {
            "cli_init": {"summary": summary},
            "cli_help": {"summary": summary},
        },
        budgets,
    )

    assert [(failure["operation"], failure["metric"]) for failure in gate["failures"]] == [
        ("cli_help", "median_ms"),
        ("cli_help", "max_ms"),
        ("cli_init", "median_ms"),
        ("cli_init", "max_ms"),
    ]


def test_gate_fails_missing_median_or_max_metrics() -> None:
    profiler = load_profiler()
    budgets = {
        "cli_help": {"median_ms": 100.0, "max_ms": 100.0},
        "cli_init": {"median_ms": 100.0, "max_ms": 100.0},
    }

    gate = profiler.evaluate_gate(
        {
            "cli_help": {"summary": {"runs": 1, "max_ms": 1.0}},
            "cli_init": {"summary": {"runs": 1, "median_ms": 1.0}},
        },
        budgets,
    )

    missing = {
        (failure["operation"], failure["metric"], failure.get("reason"))
        for failure in gate["failures"]
    }
    assert gate["passed"] is False
    assert ("cli_help", "median_ms", "missing metric") in missing
    assert ("cli_init", "max_ms", "missing metric") in missing


def test_runtime_coverage_gate_fails_missing_or_low_record_counts() -> None:
    profiler = load_profiler()
    summary = {"runs": 1, "median_ms": 1.0, "max_ms": 2.0}
    budgets = {name: {"median_ms": 60_000, "max_ms": 60_000} for name in RUNTIME_COVERAGE_OPERATIONS}
    gate = profiler.evaluate_gate(
        {
            "cli_list": {"summary": summary},
            "cli_upsert": {
                "summary": summary,
                "coverage": {
                    "records_requested": 99,
                    "min_records": RUNTIME_COVERAGE_RECORDS,
                    "record_files_before": 99,
                    "index_records_before": 99,
                    "record_files_after": 100,
                    "index_records_after": 100,
                },
            },
            "cli_rebuild_index": {
                "summary": summary,
                "coverage": {
                    "records_requested": RUNTIME_COVERAGE_RECORDS,
                    "min_records": RUNTIME_COVERAGE_RECORDS,
                    "record_files_before": 0,
                    "record_files_after": RUNTIME_COVERAGE_RECORDS,
                    "index_records_after": RUNTIME_COVERAGE_RECORDS,
                },
            },
            "cli_regen_refs": {
                "summary": summary,
                "coverage": {
                    "records_requested": RUNTIME_COVERAGE_RECORDS,
                    "min_records": RUNTIME_COVERAGE_RECORDS,
                    "record_files_before": RUNTIME_COVERAGE_RECORDS,
                    "record_files_after": RUNTIME_COVERAGE_RECORDS,
                    "index_records_after": 0,
                },
            },
        },
        budgets,
    )

    coverage_failures = [failure for failure in gate["failures"] if failure["metric"] == "record_coverage"]
    assert gate["passed"] is False
    assert {failure["operation"] for failure in coverage_failures} == RUNTIME_COVERAGE_OPERATIONS
    assert any(failure.get("reason") == "missing coverage metadata" for failure in coverage_failures)
    assert any(failure.get("coverage_metric") == "records_requested" for failure in coverage_failures)
    assert any(failure.get("coverage_metric") == "record_files_before" for failure in coverage_failures)
    assert any(failure.get("coverage_metric") == "index_records_after" for failure in coverage_failures)


def test_runtime_coverage_uses_global_minimum_and_cli_list_after_counts() -> None:
    profiler = load_profiler()
    gate = profiler.evaluate_gate(
        {
            "cli_list": {
                "summary": {"runs": 1, "median_ms": 1.0, "max_ms": 2.0},
                "coverage": {
                    "records_requested": RUNTIME_COVERAGE_RECORDS,
                    "min_records": 0,
                    "record_files_before": RUNTIME_COVERAGE_RECORDS,
                    "index_records_before": RUNTIME_COVERAGE_RECORDS,
                    "record_files_after": 0,
                    "index_records_after": 0,
                },
            }
        },
        {"cli_list": {"median_ms": 60_000, "max_ms": 60_000}},
    )

    failures = [failure for failure in gate["failures"] if failure["metric"] == "record_coverage"]
    assert gate["passed"] is False
    assert {failure["coverage_metric"] for failure in failures} == {"record_files_after", "index_records_after"}
    assert all(failure["min_records"] == RUNTIME_COVERAGE_RECORDS for failure in failures)
    assert all(failure["reported_min_records"] == 0 for failure in failures)


def test_cli_upsert_gate_requires_measured_record_creation() -> None:
    profiler = load_profiler()
    gate = profiler.evaluate_gate(
        {
            "cli_upsert": {
                "summary": {"runs": 2, "median_ms": 1.0, "max_ms": 2.0},
                "coverage": {
                    "records_requested": RUNTIME_COVERAGE_RECORDS,
                    "min_records": RUNTIME_COVERAGE_RECORDS,
                    "record_files_before": RUNTIME_COVERAGE_RECORDS,
                    "index_records_before": RUNTIME_COVERAGE_RECORDS,
                    "record_files_after": RUNTIME_COVERAGE_RECORDS,
                    "index_records_after": RUNTIME_COVERAGE_RECORDS,
                    "measured_record_files_after": 1,
                    "measured_index_records_after": 0,
                },
            }
        },
        {"cli_upsert": {"median_ms": 60_000, "max_ms": 60_000}},
    )

    failures = [failure for failure in gate["failures"] if failure["metric"] == "upsert_measured_records"]
    assert gate["passed"] is False
    assert {failure["coverage_metric"] for failure in failures} == {
        "measured_record_files_after",
        "measured_index_records_after",
    }
    assert all(failure["expected_records"] == 2 for failure in failures)


def test_warmup_failures_fail_gate_without_timing_samples() -> None:
    profiler = load_profiler()

    result = profiler.time_repeated_command(
        "cli_help",
        1,
        lambda i: [
            sys.executable,
            "-c",
            "import sys; sys.exit(1 if int(sys.argv[1]) < 0 else 0)",
            str(i),
        ],
        warmups=1,
        timeout=10,
    )
    gate = profiler.evaluate_gate({"cli_help": result}, {"cli_help": {"median_ms": 60_000, "max_ms": 60_000}})

    assert result["summary"]["runs"] == 1
    assert len(result["samples_ms"]) == 1
    assert result["warmup_runs"][0]["returncode"] == 1
    assert result["command_runs"][0]["returncode"] == 0
    assert gate["passed"] is False
    assert any(failure["metric"] == "subprocess" for failure in gate["failures"])


def test_load_budgets_rejects_non_finite_budget_values(tmp_path: Path) -> None:
    profiler = load_profiler()

    for index, literal in enumerate(("NaN", "Infinity", "-Infinity")):
        path = tmp_path / f"budget-{index}.json"
        path.write_text(
            f'{{"cli_help": {{"median_ms": {literal}, "max_ms": 1.0}}}}',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="non-finite"):
            profiler.load_budgets(path, 1.0)


@pytest.mark.parametrize("scale", ["nan", "inf", "-inf"])
def test_profiler_rejects_non_finite_budget_scale(scale: str, tmp_path: Path) -> None:
    proc = run_profiler(
        ["--budget-scale", scale, "--only", "cli_help"],
        cwd=tmp_path,
        home=tmp_path / "home",
    )

    assert proc.returncode == 2
    assert "--budget-scale" in proc.stderr


def test_profiler_gate_fails_empty_only_selection(tmp_path: Path) -> None:
    out = tmp_path / "empty-only-profile.json"

    proc = run_profiler(
        ["--gate", "--only", ",", "--out", out],
        cwd=tmp_path,
        home=tmp_path / "home",
    )

    assert proc.returncode == 1
    assert "FAILED" in proc.stdout
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["config"]["selected"] == []
    assert report["gate"]["failures"] == [
        {"operation": "selection", "metric": "selection", "reason": "no operations selected"}
    ]


def test_profiler_only_selection_ignores_empty_items_and_dedupes_in_requested_order() -> None:
    profiler = load_profiler()

    selected = profiler.expand_only([",cli_list,,cli_help", "cli_list", "skill_load_common_path,cli_help"])

    assert selected == ["cli_list", "cli_help", "skill_load_common_path"]


def test_budget_scale_only_scales_time_budgets_not_structural_gates(tmp_path: Path) -> None:
    profiler = load_profiler()
    budget = write_permissive_budget_file(tmp_path / "budgets.json")

    budgets = profiler.load_budgets(budget, 10.0)
    common_budget = budgets["skill_load_common_path"]

    assert common_budget["median_ms"] == 600_000.0
    assert common_budget["max_ms"] == 600_000.0
    for metric, allowed in COMMON_PATH_GATES.items():
        assert common_budget[metric] == allowed

    gate = profiler.evaluate_gate(
        {
            "skill_load_common_path": {
                "summary": {"runs": 1, "median_ms": 1.0, "max_ms": 2.0},
                "detail": {
                    "max_file_count": COMMON_PATH_GATES["file_count"] + 1,
                    "max_bytes": COMMON_PATH_GATES["bytes"],
                    "max_rough_tokens": COMMON_PATH_GATES["rough_tokens"],
                    "broad_file_reads": COMMON_PATH_GATES["broad_file_reads"],
                },
            }
        },
        budgets,
    )

    assert gate["passed"] is False
    assert any(
        failure["operation"] == "skill_load_common_path" and failure["metric"] == "file_count"
        for failure in gate["failures"]
    )


def test_profiler_stdout_report_does_not_create_result_artifacts(tmp_path: Path) -> None:
    before = result_files()

    proc = run_profiler(
        ["--only", "cli_help", "--runs", "1", "--out", "-"],
        cwd=tmp_path,
        home=tmp_path / "home",
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert result_files() == before
    report = json.loads(proc.stdout)
    assert report["config"]["selected"] == ["cli_help"]
    assert report["operations"]["cli_help"]["summary"]["runs"] == 1


def test_profiler_gate_covers_required_operations_with_temp_roots(tmp_path: Path) -> None:
    out = tmp_path / "profile.json"
    before = result_files()
    budget = write_permissive_budget_file(tmp_path / "budgets.json")

    proc = run_profiler(
        [
            "--gate",
            "--runs",
            "1",
            "--records",
            str(RUNTIME_COVERAGE_RECORDS),
            "--budget-file",
            budget,
            "--command-timeout",
            "30",
            "--out",
            out,
        ],
        cwd=tmp_path,
        home=tmp_path / "home",
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert result_files() == before
    report = json.loads(out.read_text(encoding="utf-8"))

    assert report["gate"]["passed"] is True
    assert report["config"]["records"] == RUNTIME_COVERAGE_RECORDS
    assert report["config"]["subprocess_warmups"] == 1
    assert set(report["operations"]) == REQUIRED_OPERATIONS
    for name in REQUIRED_OPERATIONS:
        assert report["operations"][name]["summary"]["runs"] == 1
        assert len(report["operations"][name]["samples_ms"]) == 1
        if report["operations"][name]["kind"] == "subprocess":
            assert len(report["operations"][name]["warmup_runs"]) == 1

    for name in RUNTIME_COVERAGE_OPERATIONS:
        coverage = report["operations"][name]["coverage"]
        assert coverage["records_requested"] == RUNTIME_COVERAGE_RECORDS
        assert coverage["min_records"] == RUNTIME_COVERAGE_RECORDS
        assert coverage["record_files_before"] >= RUNTIME_COVERAGE_RECORDS
        assert coverage["index_records_before"] >= RUNTIME_COVERAGE_RECORDS
        assert coverage["record_files_after"] >= RUNTIME_COVERAGE_RECORDS
        assert coverage["index_records_after"] >= RUNTIME_COVERAGE_RECORDS

    upsert_coverage = report["operations"]["cli_upsert"]["coverage"]
    assert upsert_coverage["measured_records_expected"] == 1
    assert upsert_coverage["measured_record_files_after"] == 1
    assert upsert_coverage["measured_index_records_after"] == 1

    repo_resolved = REPO_ROOT.resolve()
    for name in ("cli_init", "cli_list", "cli_upsert", "cli_rebuild_index", "cli_regen_refs", "codex_hook"):
        root = Path(report["operations"][name]["root"]).resolve()
        assert repo_resolved not in [root, *root.parents]


def test_profiler_gate_fails_when_runtime_coverage_uses_too_few_records(tmp_path: Path) -> None:
    out = tmp_path / "low-record-profile.json"
    budget = write_permissive_budget_file(tmp_path / "budgets.json")

    proc = run_profiler(
        [
            "--gate",
            "--only",
            "cli_list",
            "--runs",
            "1",
            "--records",
            "2",
            "--budget-file",
            budget,
            "--command-timeout",
            "30",
            "--out",
            out,
        ],
        cwd=tmp_path,
        home=tmp_path / "home",
    )

    assert proc.returncode == 1
    assert "FAILED" in proc.stdout
    report = json.loads(out.read_text(encoding="utf-8"))
    failures = report["gate"]["failures"]

    assert report["gate"]["passed"] is False
    assert report["operations"]["cli_list"]["coverage"]["records_requested"] == 2
    assert any(
        failure["operation"] == "cli_list"
        and failure["metric"] == "record_coverage"
        and failure.get("coverage_metric") == "records_requested"
        for failure in failures
    )
    assert any(
        failure["operation"] == "cli_list"
        and failure["metric"] == "record_coverage"
        and failure.get("coverage_metric") == "record_files_before"
        for failure in failures
    )
    assert any(
        failure["operation"] == "cli_list"
        and failure["metric"] == "record_coverage"
        and failure.get("coverage_metric") == "record_files_after"
        for failure in failures
    )


def test_profiler_gate_fails_when_budget_is_exceeded(tmp_path: Path) -> None:
    out = tmp_path / "failed-profile.json"
    budget = tmp_path / "budgets.json"
    budget.write_text(
        json.dumps({"cli_help": {"median_ms": 0.0, "max_ms": 0.0}}),
        encoding="utf-8",
    )

    proc = run_profiler(
        [
            "--gate",
            "--only",
            "cli_help",
            "--runs",
            "1",
            "--budget-file",
            budget,
            "--out",
            out,
        ],
        cwd=tmp_path,
        home=tmp_path / "home",
    )

    assert proc.returncode == 1
    assert "FAILED" in proc.stdout
    report = json.loads(out.read_text(encoding="utf-8"))
    failures = report["gate"]["failures"]

    assert report["gate"]["passed"] is False
    assert any(item["operation"] == "cli_help" and item["metric"] == "median_ms" for item in failures)
    assert any(item["operation"] == "cli_help" and item["metric"] == "max_ms" for item in failures)


def test_profiler_honors_profile_dir(tmp_path: Path) -> None:
    out = tmp_path / "profile.json"
    profile_dir = tmp_path / "profiles"

    proc = run_profiler(
        [
            "--only",
            "cli_help",
            "--runs",
            "1",
            "--profile",
            "--profile-dir",
            profile_dir,
            "--out",
            out,
        ],
        cwd=tmp_path,
        home=tmp_path / "home",
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    report = json.loads(out.read_text(encoding="utf-8"))
    profile_path = Path(report["profiles"][0]["profile_path"])
    assert report["config"]["profile_dir"] == str(profile_dir)
    assert profile_path == profile_dir / "cli_help.prof"
    assert profile_path.is_file()


def test_profiler_timestamped_outputs_are_ignored() -> None:
    ignore_text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "tools/profiler/results/" in ignore_text
    assert "tools/profiler/**/*.prof" in ignore_text
