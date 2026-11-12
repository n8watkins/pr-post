"""scripts/writer_usage.py: the Claude routine's usage record. Every person here is invented."""

import datetime as dt
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import writer_usage  # noqa: E402


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


class WriterUsage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paper = Path(self.tmp.name)
        files = {"briefs/wren-invented.md": "# Brief: Wren\n", "briefs/moss-invented.md": "# Brief: Moss, a little longer\n",
                 "reports/README.md": "# Reports\n", "reports/wren-invented/2026-11-07-weekly.md": "# Wren: earlier\n"}
        for rel, text in files.items():
            (self.paper / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.paper / rel).write_text(text)
        (self.paper / "profiles").mkdir()
        (self.paper / "profiles" / "wren-invented.md").write_text("# Wren\n")
        snap = {"files": {rel: digest(text) for rel, text in files.items()}, "profiles": {"wren-invented": digest("# Wren\n")}}
        (self.paper / ".store.json").write_text(json.dumps(snap))
        # What the run writes: one new report, the list, an edition and one profile line.
        (self.paper / "reports" / "wren-invented" / "2026-11-11-update.md").write_text("# Wren update: 2026-11-07 to 2026-11-11\n")
        (self.paper / "reports" / "README.md").write_text("# Reports\n- new\n")
        (self.paper / "editions").mkdir()
        (self.paper / "editions" / "2026-11-11-update.json").write_text("{}\n")
        (self.paper / "profiles" / "wren-invented.md").write_text("# Wren\n- 2026-11-11: started kiln\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_record_counts_reads_and_finds_what_was_written(self):
        now = dt.datetime(2026, 11, 11, 12, 20, 0, tzinfo=dt.timezone.utc)
        rec = writer_usage.record(self.paper, "2026-11-11T12:00:00Z", 14,
                                  ["briefs/wren-invented.md", "briefs/moss-invented.md", "briefs/wren-invented.md", "profiles/gone.md"], now=now)
        self.assertEqual(rec["date"], "2026-11-11")
        self.assertEqual(rec["kind"], "update")
        self.assertEqual((rec["started_at"], rec["finished_at"], rec["wall_s"]), ("2026-11-11T12:00:00+00:00", "2026-11-11T12:20:00+00:00", 1200))
        self.assertEqual((rec["turns"], rec["requests"]), (14, 14))
        self.assertEqual([f["path"] for f in rec["files_read"]], ["briefs/wren-invented.md", "briefs/moss-invented.md", "profiles/gone.md"])
        self.assertIsNone(rec["files_read"][2]["bytes"])
        self.assertEqual(rec["bytes_read"], len("# Brief: Wren\n") + len("# Brief: Moss, a little longer\n"))
        self.assertEqual(sorted(w["path"] for w in rec["files_written"]),
                         ["editions/2026-11-11-update.json", "profiles/wren-invented.md", "reports/README.md", "reports/wren-invented/2026-11-11-update.md"])
        self.assertEqual(rec["reports_written"], 1)
        self.assertEqual((rec["input_tokens"], rec["output_tokens"], rec["usd"], rec["model"]), (None, None, None, None))

    def test_command_writes_the_log_file(self):
        out = subprocess.run([sys.executable, str(ROOT / "scripts" / "writer_usage.py"), str(self.paper), "--started", "2026-11-11T12:00:00Z",
                              "--turns", "9", "--read", "briefs/wren-invented.md", "--model", "an invented model", "--input-tokens", "1200",
                              "--output-tokens", "300"], check=True, capture_output=True, text=True).stdout
        files = list((self.paper / "logs" / "writer-usage").glob("*-claude.json"))
        self.assertEqual(len(files), 1)
        rec = json.loads(files[0].read_text())
        self.assertEqual((rec["model"], rec["input_tokens"], rec["output_tokens"], rec["turns"]), ("an invented model", 1200, 300, 9))
        self.assertIn("9 turns, 1 files read", out)

    def test_a_bad_start_time_is_a_clear_error(self):
        run = subprocess.run([sys.executable, str(ROOT / "scripts" / "writer_usage.py"), str(self.paper), "--started", "noon", "--turns", "1"],
                             capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("writer_usage.py:", run.stderr)


if __name__ == "__main__":
    unittest.main()
