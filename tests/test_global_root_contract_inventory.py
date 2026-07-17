"""Cross-cutting inventory checks for the global root contract."""
from __future__ import annotations

import re
import sys
from pathlib import Path


TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent

if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from _util import clean_env, run_cli  # noqa: E402
import test_agent_facing_text_contract as text_contract  # noqa: E402


REQUIRED_CLI_SUBCOMMANDS = {
    "init",
    "rebuild-index",
    "regen-refs",
    "sidekick",
    "upsert",
    "continue",
    "return",
    "set-status",
    "list",
    "search",
    "show",
    "context",
    "import",
    "doctor",
    "hook",
}

FORBIDDEN_HELP_TERMS = (
    "conversation store",
    "runtime store",
    "data root",
    "active store",
    "payload",
    "bundle",
    "<cwd>/.relay",
    "RELAY_ROOT",
    "BRAIN_CONV",
)


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def expected_agent_text_surfaces() -> set[str]:
    surfaces = {
        "README.md",
        "SKILL.md",
        "hooks/README.md",
        ".claude-plugin/plugin.json",
        ".claude-plugin/marketplace.json",
        ".codex-plugin/plugin.json",
    }
    surfaces.update(rel(path) for path in (ROOT / "references").glob("*.md"))
    surfaces.update(rel(path) for path in (ROOT / "skills").glob("*/SKILL.md"))
    return surfaces


def cli_subcommands() -> set[str]:
    proc = run_cli_help("--help")
    return set(re.findall(r"^  ([a-z-]+)\s+", proc.stdout, re.MULTILINE))


def run_cli_help(*args: str) -> subprocess.CompletedProcess:
    return run_cli(args, cwd=ROOT, env=clean_env())


def normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def test_agent_text_contract_inventory_includes_all_residual_surfaces() -> None:
    expected = expected_agent_text_surfaces()
    missing_files = sorted(surface for surface in expected if not (ROOT / surface).is_file())
    assert not missing_files, f"global-root text inventory points at missing files: {missing_files}"

    covered = {rel(path) for path in text_contract.covered_files()}
    missing_coverage = sorted(expected - covered)
    assert not missing_coverage, f"text contract does not cover: {missing_coverage}"


def test_cli_help_inventory_exercises_every_subcommand() -> None:
    discovered = cli_subcommands()
    assert discovered == REQUIRED_CLI_SUBCOMMANDS

    help_args = [("--help",), *((command, "--help") for command in sorted(discovered))]
    for args in help_args:
        proc = run_cli_help(*args)
        assert proc.returncode == 0, proc.stderr + proc.stdout
        output = normalized(proc.stdout)
        assert "Plugin installation root" in output
        assert "~/.relay" in output
        lowered = output.lower()
        for term in FORBIDDEN_HELP_TERMS:
            assert term.lower() not in lowered, f"{args} help contains {term!r}"
