"""scripts/email_lib.py: the jobs' email through the paper's Worker (EMAIL_VIA=worker), end to end against the real Worker code."""

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import config  # noqa: E402
import email_lib as E  # noqa: E402

JOBS = "j" * 40


def watch(to="owner@example.com", via="resend"):
    return config.parse(f'[email]\nto = "{to}"\nvia = "{via}"\n[delivery]\nemail = true\n')


class Choice(unittest.TestCase):
    def test_email_via_overrides_the_watchlist(self):
        with mock.patch.dict(os.environ, {"EMAIL_VIA": "worker", "PR_POST_API": "https://paper.example", "PR_POST_TOKEN": JOBS}):
            self.assertEqual(E.transport(watch()), ("worker", True))
            self.assertEqual(E.describe(watch()), "the paper's Worker (PR_POST_API and PR_POST_TOKEN set)")
        with mock.patch.dict(os.environ, {"EMAIL_VIA": "", "RESEND_API_KEY": ""}):
            self.assertEqual(E.transport(watch(via="worker"))[0], "worker")
            self.assertEqual(E.transport(watch())[0], "resend")
        with mock.patch.dict(os.environ, {"EMAIL_VIA": "carrier-pigeon"}):
            with self.assertRaises(SystemExit):
                E.transport(watch())

    def test_without_the_worker_settings_it_skips(self):
        with mock.patch.dict(os.environ, {"EMAIL_VIA": "worker", "PR_POST_API": "", "PR_POST_TOKEN": ""}):
            self.assertEqual(E.check_delivery(watch(), dry_run=False), "PR_POST_API or PR_POST_TOKEN not set; skipping email.")
            self.assertIsNone(E.check_delivery(watch(), dry_run=True))

    def test_the_key_is_stable(self):
        a = E.send_key("digest", "o@example.com", "S", "T")
        self.assertEqual(a, E.send_key("digest", "o@example.com", "S", "T"))
        self.assertNotEqual(a, E.send_key("digest", "o@example.com", "S", "T2"))
        self.assertRegex(a, r"^digest-[0-9a-f]{40}$")


@unittest.skipUnless(shutil.which("node"), "needs node")
class EndToEnd(unittest.TestCase):
    def setUp(self):
        env = {**os.environ, "JOBS_TOKEN": JOBS, "WRITER_TOKEN": "w" * 40, "SITE_PASSWORD": "p" * 16, "FAKE_MAILROOM": "1"}
        self.server = subprocess.Popen(["node", str(HERE / "serve_worker.mjs")], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, text=True)
        line = self.server.stdout.readline()
        self.assertTrue(line.startswith("listening"), line)
        self.env = mock.patch.dict(os.environ, {"EMAIL_VIA": "worker", "PR_POST_API": f"http://127.0.0.1:{line.split()[1]}", "PR_POST_TOKEN": JOBS})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.server.kill()
        self.server.wait()
        self.server.stdout.close()

    def test_a_digest_and_a_follow_email_go_through_the_worker(self):
        key = E.send_key("digest", "owner@example.com", "Subject", "Text")
        self.assertEqual(E.deliver(watch(), "Subject", "<p>Page</p>", "Text"), f"mr-digest-{key}")
        self.assertTrue(E.deliver(watch(), "Picks", "<p>Page</p>", "Text", kind="follow").startswith("mr-follow-follow-"))

    def test_a_refusal_stops_the_job_with_the_reason(self):
        with self.assertRaises(SystemExit) as stop:
            E.deliver(watch(to="capped@example.com"), "Subject", "<p>Page</p>", "Text")
        self.assertIn("cap_app_day", str(stop.exception))


if __name__ == "__main__":
    unittest.main()
