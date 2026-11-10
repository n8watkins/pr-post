"""The jobs of a paper kept in Cloudflare: act.py run, send_email.py --unsent, and store.py's drafts and masks."""

import contextlib
import datetime as dt
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401  (puts scripts/ on the path)
import act
import act_lib
import send_email
import store
from fixture_paper import write_paper

KEY = "k" * 32


class ActRun(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.paper = write_paper(Path(tmp.name) / "paper")
        self.link = Path(tmp.name) / "link"

    def run_act(self, action, login="jane", env=None):
        self.link.write_text(act_lib.sign(KEY, action, login))
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true", **(env or {})}), contextlib.redirect_stdout(out):
            code = act.run(self.paper, KEY, self.link)
        return code, out.getvalue(), act_lib.read_log(self.paper)

    def test_follow_is_done_logged_and_masked(self):
        with mock.patch.object(act, "follow_api") as api:
            code, out, log = self.run_act("follow")
        api.assert_called_once_with("follow", "jane")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("::add-mask::jane\n"))
        self.assertEqual([(e["action"], e["login"], e["result"]) for e in log], [("follow", "jane", "ok")])

    def test_a_used_link_is_refused_and_logged(self):
        with mock.patch.object(act, "follow_api"):
            self.run_act("follow")
            payload = act_lib.verify(KEY, self.link.read_text())
            with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), contextlib.redirect_stdout(io.StringIO()):
                code = act.run(self.paper, KEY, self.link)
        self.assertEqual(code, 1)
        log = act_lib.read_log(self.paper)
        self.assertEqual([e["result"] for e in log if e["nonce"] == payload["n"]], ["ok", "refused"])

    def test_a_failure_is_logged_as_failed(self):
        with mock.patch.object(act, "follow_api", side_effect=SystemExit("GitHub refused to follow jane (403).")):
            code, _, log = self.run_act("follow")
        self.assertEqual(code, 1)
        self.assertEqual(log[-1]["result"], "failed")
        self.assertIn("403", log[-1]["detail"])

    def test_add_drafts_and_undo_lists_the_draft_for_discarding(self):
        import follow
        with mock.patch.object(follow, "draft") as draft:
            code, _, _ = self.run_act("add")
        self.assertEqual(code, 0)
        self.assertEqual(draft.call_args.args[2], "jane")
        code, _, _ = self.run_act("unadd", "Jane")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads((self.paper / "discard.json").read_text()), ["jane"])

    def test_a_bad_link_does_nothing(self):
        self.link.write_text("not-a-link")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(act.run(self.paper, KEY, self.link), 1)
        self.assertEqual(act_lib.read_log(self.paper), [])


class Unsent(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.paper = write_paper(Path(tmp.name) / "paper", 'mode = "private"\nurl = "https://paper.example"')
        with open(self.paper / "watchlist.toml", "a") as f:
            f.write('\n[email]\nto = "reader@example.com"\nvia = "ses"\n\n[delivery]\nemail = true\n')
        self.today = dt.date.today()

    def report(self, pid, days_ago, kind="weekly"):
        p = self.paper / "reports" / pid / f"{(self.today - dt.timedelta(days=days_ago)).isoformat()}-{kind}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# {pid}: a week\n\n## TL;DR\n- shipped\n")
        return p.relative_to(self.paper).as_posix()

    def main(self, *args):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["send_email.py", "--paper", str(self.paper), *args]), \
                mock.patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "AKID", "AWS_SECRET_ACCESS_KEY": "s"}), contextlib.redirect_stdout(out):
            send_email.main()
        return out.getvalue()

    def test_sends_each_new_issue_once_and_records_it(self):
        old = self.report("octo", 30)
        sent = self.report("octo", 3, "update")
        a = self.report("octo", 1)
        b = self.report("tuber", 1)
        (self.paper / "emailed.json").write_text(json.dumps([sent]))
        with mock.patch("email_lib.deliver", return_value="m-1") as deliver:
            out = self.main("--unsent")
        self.assertEqual(deliver.call_count, 1)  # one issue: today's weekly, both people in one email
        self.assertIn("1 issue(s) with new reports.", out)
        self.assertEqual(json.loads((self.paper / "emailed.json").read_text()), sorted([sent, a, b]))
        self.assertNotIn(old, (self.paper / "emailed.json").read_text())  # too old to email now
        with mock.patch("email_lib.deliver") as deliver:
            self.assertIn("No new reports to email.", self.main("--unsent"))
        deliver.assert_not_called()

    def test_dry_run_records_nothing(self):
        self.report("octo", 1)
        (self.paper / "emailed.json").write_text("[]")
        with mock.patch("email_lib.deliver") as deliver:
            out = self.main("--unsent", "--dry-run")
        deliver.assert_not_called()
        self.assertIn("through Amazon SES", out)
        self.assertEqual(json.loads((self.paper / "emailed.json").read_text()), [])

    def test_needs_the_emailed_list(self):
        self.report("octo", 1)
        with self.assertRaises(SystemExit):
            self.main("--unsent")


class StoreParts(unittest.TestCase):
    def test_masks_people_and_the_address_only_in_actions(self):
        wl = {"people": [{"id": "ada", "entry": {"github": "AdaL", "name": "Ada Lovelace"}}], "settings": {"email": {"to": "me@example.com"}}}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            store.mask(wl, {"GITHUB_ACTIONS": "true"})
            store.mask(wl, {})
        self.assertEqual(out.getvalue().splitlines(), ["::add-mask::Ada Lovelace", "::add-mask::AdaL", "::add-mask::ada", "::add-mask::me@example.com"])

    def test_new_people_become_drafts_and_discards_are_sent(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work = Path(tmp.name)
        (work / "watchlist.toml").write_text(store.render_watchlist({}, [{"github": "ada", "name": "Ada"}, {"github": "Jane", "name": "Jane"}]))
        (work / "profiles").mkdir()
        (work / "profiles" / "jane.md").write_text("# Jane\n")
        (work / "discard.json").write_text('["old"]')
        api = mock.Mock()
        api.json.return_value = (200, {})
        snap = {"people": ["ada", "bob"], "profiles": {}}
        self.assertEqual(store.push_drafts(api, work, snap), (1, 1))
        calls = [c.args[:2] for c in api.json.call_args_list]
        self.assertEqual(calls, [("POST", "/api/paper/people/drafts"), ("DELETE", "/api/paper/people/drafts/old")])
        self.assertEqual(api.json.call_args_list[0].args[2]["profile"], "# Jane\n")
        self.assertFalse((work / "discard.json").exists())
        self.assertIn("jane", snap["people"])
        self.assertIn("jane", snap["profiles"])  # so push_profiles does not send it again

    def test_a_drafted_profile_with_windows_line_endings_is_sent_once(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work = Path(tmp.name)
        (work / "watchlist.toml").write_text(store.render_watchlist({}, [{"github": "Jane", "name": "Jane"}]))
        (work / "profiles").mkdir()
        (work / "profiles" / "jane.md").write_bytes(b"# Jane\r\nBio line\r\n")
        api = mock.Mock()
        api.json.return_value = (200, {})
        snap = {"people": [], "profiles": {}}
        store.push_drafts(api, work, snap)
        self.assertEqual(api.json.call_args.args[2]["profile"], "# Jane\r\nBio line\r\n")
        self.assertEqual(store.push_profiles(api, work, snap), 0)


if __name__ == "__main__":
    unittest.main()
