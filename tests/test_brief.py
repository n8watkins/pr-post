"""scripts/brief.py cuts each brief to the person's report window. Every person here is invented."""

import datetime as dt
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import brief  # noqa: E402
from writers.gemini import prepare  # noqa: E402

WEDNESDAY = dt.date(2026, 11, 11)
SATURDAY = dt.date(2026, 11, 14)
DAYS = ["2026-11-02", "2026-11-04", "2026-11-07", "2026-11-09", "2026-11-11", "2026-11-13", "2026-11-14"]


def person_data(pid, today, days=DAYS):
    """data/<id>.json and the stats entry for an invented person busy on each of `days` up to `today`."""
    days = [d for d in days if d <= today.isoformat()]
    repo = f"{pid}/kiln"
    prs = [{"repo": repo, "number": n, "title": f"Glaze step {d}", "state": "merged", "created": f"{d}T09:00:00Z", "merged": f"{d}T10:00:00Z",
            "url": f"https://github.com/{repo}/pull/{n}", "body": f"Work from {d}."} for n, d in enumerate(days, start=1)]
    prs.append({"repo": repo, "number": 90, "title": "Old draft still open", "state": "open", "created": f"{days[0]}T09:00:00Z", "merged": None,
                "url": f"https://github.com/{repo}/pull/90", "body": ""})
    commits = [{"repo": f"{pid}/notes", "date": f"{d}T08:00:00Z", "message": f"Note for {d}", "url": f"https://github.com/{pid}/notes/commit/{d}"} for d in days]
    data = {"collected_at": f"{today.isoformat()}T11:00:00+00:00", "since": "2026-10-30",
            "github": {"pull_requests": prs, "commits": commits, "readmes": {}}, "youtube": {}}
    st = {"window": {"start": (today - dt.timedelta(days=6)).isoformat(), "end": today.isoformat()},
          "public_merged_prs": len(prs) - 1, "opened_prs": len(prs), "commits": len(commits), "repos_touched": 2,
          "releases": [{"date": d, "repo": repo, "name": f"v{d}", "tag": f"v{d}", "url": f"https://github.com/{repo}/releases/tag/v{d}"} for d in days],
          "videos": [], "contributions": {"public": 12, "private": 0},
          "repos": [{"repo": repo, "merged_prs": len(prs) - 1, "commits": 0, "private": False}],
          "births": [{"date": days[0], "repo": f"{pid}/old-idea", "description": "An older experiment", "url": f"https://github.com/{pid}/old-idea"},
                     {"date": days[-1], "repo": f"{pid}/new-idea", "description": "A fresh experiment", "url": f"https://github.com/{pid}/new-idea"}],
          "deaths": []}
    return data, st


def dated_lines(text):
    return [line for line in text.splitlines() if line.startswith("- 2026-")]


class Window(unittest.TestCase):
    def test_midweek_starts_at_the_newest_report_of_either_kind(self):
        # The 2026-11-11 bug: the newest report was the 2026-11-07 weekly, yet the update went back to 2026-11-03.
        win = brief.window(["2026-11-07-weekly", "2026-10-31-weekly"], WEDNESDAY)
        self.assertEqual(win, {"start": "2026-11-07", "end": "2026-11-11", "kind": "update", "why": "since the 2026-11-07 weekly"})
        self.assertEqual(brief.window(["2026-11-09-update", "2026-11-07-weekly"], WEDNESDAY)["start"], "2026-11-09")

    def test_weekly_starts_at_the_newest_weekly_and_skips_updates(self):
        win = brief.window(["2026-11-11-update", "2026-11-07-weekly"], SATURDAY)
        self.assertEqual((win["start"], win["end"], win["kind"], win["why"]), ("2026-11-07", "2026-11-14", "weekly", "since the 2026-11-07 weekly"))

    def test_fallbacks_when_there_is_no_earlier_report(self):
        self.assertEqual(brief.window([], SATURDAY)["start"], "2026-11-07")
        self.assertEqual(brief.window([], SATURDAY)["why"], "no earlier weekly, so the last 7 days")
        self.assertEqual(brief.window([], WEDNESDAY)["start"], "2026-11-07")
        self.assertEqual(brief.window([], WEDNESDAY)["why"], "no earlier report, so the last 4 days")
        self.assertEqual(brief.window(["2026-11-09-update"], SATURDAY)["start"], "2026-11-07")  # updates never start a weekly

    def test_reports_dated_today_or_later_do_not_move_the_window(self):
        self.assertEqual(brief.window(["2026-11-11-update", "2026-11-07-weekly"], WEDNESDAY)["start"], "2026-11-07")
        self.assertEqual(brief.window(["README", "notes", "2026-11-04-update"], WEDNESDAY)["start"], "2026-11-04")

    def test_an_asked_for_kind_overrides_the_day(self):
        self.assertEqual(brief.window(["2026-11-09-update", "2026-11-01-weekly"], WEDNESDAY, "weekly")["start"], "2026-11-01")

    def test_the_gemini_writer_computes_the_same_window(self):
        cases = [(WEDNESDAY, []), (SATURDAY, []), (WEDNESDAY, ["2026-11-07-weekly"]), (SATURDAY, ["2026-11-11-update", "2026-11-07-weekly"]),
                 (WEDNESDAY, ["2026-11-09-update", "2026-10-31-weekly"]), (SATURDAY, ["2026-11-14-weekly", "2026-11-09-update"]),
                 (dt.date(2026, 11, 16), ["2026-11-14-weekly"])]
        for today, reports in cases:
            with self.subTest(today=today, reports=reports):
                ours = brief.window(reports, today)
                pairs = [tuple(r.rsplit("-", 1)) for r in reports if r[:10] < today.isoformat()]
                start, end = prepare.window(prepare.kind_for(today), today, pairs)
                self.assertEqual((ours["start"], ours["end"]), (start.isoformat(), end.isoformat()))


class Brief(unittest.TestCase):
    def make(self, today, reports, kind=None, pid="wren-invented"):
        data, st = person_data(pid, today)
        return brief.brief({"id": pid, "name": "Wren Invented"}, data, st, "", reports, kind)

    def test_midweek_brief_holds_only_items_since_the_last_report(self):
        text = self.make(WEDNESDAY, ["2026-11-07-weekly", "2026-10-31-weekly"])
        self.assertEqual(text.splitlines()[2],
                         "Window: 2026-11-07 to 2026-11-11 (since the 2026-11-07 weekly), for a midweek update. Every dated item below is inside it.")
        days = {line[2:12] for line in dated_lines(text)}
        self.assertEqual(days, {"2026-11-07", "2026-11-09", "2026-11-11"})
        self.assertNotIn("Glaze step 2026-11-04", text)
        self.assertNotIn("Old draft still open", text)  # opened before the window
        self.assertNotIn("old-idea", text)
        self.assertIn("## Merged PRs since 2026-11-07 (3, newest first)", text)
        self.assertIn("## Releases since 2026-11-07", text)
        self.assertIn("## New repos since 2026-11-07", text)
        self.assertIn("## Numbers for 2026-11-05 to 2026-11-11 (copied from stats/)", text)
        self.assertNotIn("Nothing dated inside the window", text)

    def test_weekly_brief_covers_the_week_since_the_last_weekly(self):
        text = self.make(SATURDAY, ["2026-11-11-update", "2026-11-07-weekly"])
        self.assertIn("Window: 2026-11-07 to 2026-11-14 (since the 2026-11-07 weekly), for a weekly report.", text)
        self.assertEqual({line[2:12] for line in dated_lines(text)}, {"2026-11-07", "2026-11-09", "2026-11-11", "2026-11-13", "2026-11-14"})

    def test_a_quiet_person_says_so(self):
        data, st = person_data("moss-invented", WEDNESDAY, days=["2026-11-02"])
        text = brief.brief({"id": "moss-invented", "name": "Moss Invented"}, data, st, "", ["2026-11-07-weekly"])
        self.assertEqual(dated_lines(text), [])
        self.assertIn("Nothing dated inside the window: quiet since 2026-11-07.", text)

    def test_the_gemini_writer_trims_nothing_more_from_the_brief(self):
        """Both writers read the same items: the Gemini writer's own trim keeps every dated line of the brief."""
        for today, reports in ((WEDNESDAY, ["2026-11-07-weekly"]), (SATURDAY, ["2026-11-11-update", "2026-11-07-weekly"]), (WEDNESDAY, [])):
            with self.subTest(today=today, reports=reports):
                text = self.make(today, reports)
                pairs = [tuple(r.rsplit("-", 1)) for r in reports]
                start, end = prepare.window(prepare.kind_for(today), today, pairs, text)
                trimmed, items = prepare.trim(text, start, end)
                self.assertEqual(len(items), len(dated_lines(text)))
                self.assertEqual(len([line for line in trimmed.splitlines() if line.startswith("- [i")]), len(dated_lines(text)))
                self.assertEqual(prepare.numbers_line(text), prepare.numbers_line(trimmed))
                self.assertIsNotNone(prepare.collected_at(text))


class Command(unittest.TestCase):
    def test_writes_windowed_briefs_into_the_paper(self):
        with tempfile.TemporaryDirectory() as tmp:
            paper = Path(tmp)
            (paper / "watchlist.toml").write_text(
                '[site]\ntitle = "The Invented Times"\nmode = "private"\n\n[writer]\nkind = "claude-routine"\nreader = "a friend"\n\n'
                '[[people]]\nid = "wren-invented"\ngithub = "wren-invented"\nname = "Wren Invented"\n')
            data, st = person_data("wren-invented", WEDNESDAY)
            (paper / "data").mkdir()
            (paper / "data" / "wren-invented.json").write_text(json.dumps(data))
            (paper / "stats").mkdir()
            (paper / "stats" / "2026-11-11.json").write_text(json.dumps({"people": {"wren-invented": st}}))
            (paper / "reports" / "wren-invented").mkdir(parents=True)
            (paper / "reports" / "wren-invented" / "2026-11-07-weekly.md").write_text("# Wren Invented: earlier\n")
            (paper / "briefs").mkdir()
            (paper / "briefs" / "gone-invented.md").write_text("stale")
            run = lambda *extra: subprocess.run([sys.executable, str(ROOT / "scripts" / "brief.py"), "--paper", str(paper), *extra],
                                                check=True, capture_output=True, text=True)
            run()
            text = (paper / "briefs" / "wren-invented.md").read_text()
            self.assertIn("Window: 2026-11-07 to 2026-11-11 (since the 2026-11-07 weekly), for a midweek update.", text)
            self.assertFalse((paper / "briefs" / "gone-invented.md").exists())
            self.assertEqual(sorted(p.name for p in (paper / "briefs").iterdir()), ["wren-invented.md"])  # no untrimmed copy
            run("--kind", "weekly")
            self.assertIn("Window: 2026-11-07 to 2026-11-11 (since the 2026-11-07 weekly), for a weekly report.",
                          (paper / "briefs" / "wren-invented.md").read_text())


if __name__ == "__main__":
    unittest.main()
