"""Slice s-store-layout / ticket t-conversate-layout.

Contract: the conv root IS the `.conversate/` directory. `init` creates the records dir
`convs/`, the derived `index.jsonl`, the `.conv-root` sentinel, and a `.gitignore` that
keeps derived/cache artifacts out of version control while records stay trackable.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _util import load_json, run_cli  # noqa: E402


class StoreLayoutTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)

    def test_init_creates_full_conversate_layout(self) -> None:
        proc = run_cli(["init"], cwd=self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)
        root = self.tmp / ".conversate"
        self.assertEqual(Path(out["conv_root"]), root)
        self.assertTrue((root / "convs").is_dir(), "records dir convs/ must exist")
        self.assertTrue((root / "index.jsonl").is_file(), "derived index must exist")
        self.assertTrue((root / ".conv-root").is_file(), "sentinel must exist")
        self.assertTrue((root / ".semble").is_dir(), "semantic cache dir must exist")
        self.assertTrue((root / ".gitignore").is_file(), ".gitignore must exist")

    def test_init_gitignore_covers_derived_artifacts(self) -> None:
        run_cli(["init", "--conv-root", self.tmp / ".conversate"], cwd=self.tmp)
        gitignore = (self.tmp / ".conversate" / ".gitignore").read_text(encoding="utf-8")
        for pattern in (".semble/", "index.jsonl", "__pycache__/"):
            self.assertIn(pattern, gitignore, f"{pattern!r} must be ignored")

    def test_init_output_names_convs_dir(self) -> None:
        proc = run_cli(["init", "--conv-root", self.tmp / ".conversate"], cwd=self.tmp)
        out = load_json(proc)
        self.assertEqual(Path(out["convs"]), self.tmp / ".conversate" / "convs")

    def test_records_are_written_under_convs(self) -> None:
        root = self.tmp / ".conversate"
        payload = (
            '{"topic": "layout check", "sections": '
            '{"summary": "s", "dict": "- **t** - m", "qa": "- **Q:** q? **A:** a."}}'
        )
        run_cli(["init", "--conv-root", root], cwd=self.tmp)
        proc = run_cli(["upsert", "--stdin", "--conv-root", root], cwd=self.tmp, input=payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rel = load_json(proc)["file"]
        self.assertTrue(rel.startswith("convs/"), rel)
        self.assertTrue((root / rel).is_file())


if __name__ == "__main__":
    unittest.main()
