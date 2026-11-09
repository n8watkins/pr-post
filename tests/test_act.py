"""One-click action links: signed, expiring, single use, and limited to a few follows a week."""

import datetime as dt
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401  (puts scripts/ on the path)
import act_lib

KEY = "test-key-0123456789abcdef"
HERE = Path(__file__).resolve().parent


class Tokens(unittest.TestCase):
    def test_round_trip(self):
        t = act_lib.sign(KEY, "follow", "Jane-Doe", now=1000)
        p = act_lib.verify(KEY, t, now=1000)
        self.assertEqual((p["a"], p["l"], p["e"]), ("follow", "Jane-Doe", 1000 + act_lib.LIFETIME))
        self.assertNotIn(KEY, t)

    def test_refuses_tampering_expiry_and_other_keys(self):
        t = act_lib.sign(KEY, "follow", "jane", now=1000)
        body, sig = t.split(".")
        forged = act_lib._b64(json.dumps({"a": "follow", "l": "mallory", "e": 10**10, "n": "0" * 32}).encode())
        i = len(t) - 10
        flipped = t[:i] + ("B" if t[i] == "A" else "A") + t[i + 1:]
        for bad in (f"{forged}.{sig}", flipped, t + "x", "nope", "", None, f"{body}.{sig}.x"):
            with self.assertRaises(act_lib.TokenError, msg=bad):
                act_lib.verify(KEY, bad, now=1000)
        with self.assertRaises(act_lib.TokenError):
            act_lib.verify("another-key", t, now=1000)
        with self.assertRaisesRegex(act_lib.TokenError, "expired"):
            act_lib.verify(KEY, t, now=1000 + act_lib.LIFETIME + 1)

    def test_only_known_actions_and_real_logins(self):
        for action, login in (("star", "jane"), ("follow", "../x"), ("follow", "-jane"), ("follow", "a" * 40)):
            with self.assertRaises(act_lib.TokenError):
                act_lib.sign(KEY, action, login)

    def test_matches_the_worker(self):
        """worker/lib.js checks the links Python signs."""
        t = act_lib.sign(KEY, "add", "jane")
        script = (f"import {{ verifyAction }} from {json.dumps(str(HERE.parent / 'worker' / 'lib.js'))};"
                  f"const p = await verifyAction({json.dumps(KEY)}, {json.dumps(t)});"
                  "console.log(JSON.stringify(p));")
        out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out)["l"], "jane")


class Log(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.paper = Path(tmp.name)
        self.now = dt.datetime(2026, 11, 9, 12, tzinfo=dt.timezone.utc)

    def payload(self, action="follow", login="jane"):
        return act_lib.verify(KEY, act_lib.sign(KEY, action, login))

    def test_single_use(self):
        p = self.payload()
        self.assertIsNone(act_lib.refusal(act_lib.read_log(self.paper), p, 5, self.now))
        act_lib.append(self.paper, act_lib.entry(p, "ok", now=self.now))
        self.assertIn("already used", act_lib.refusal(act_lib.read_log(self.paper), p, 5, self.now))

    def test_weekly_follow_limit(self):
        for i in range(5):
            act_lib.append(self.paper, act_lib.entry(self.payload(login=f"u{i}"), "ok", now=self.now - dt.timedelta(days=i)))
        self.assertIn("limit is 5", act_lib.refusal(act_lib.read_log(self.paper), self.payload(), 5, self.now))
        # Unfollows and adds are not limited, and follows older than a week no longer count.
        self.assertIsNone(act_lib.refusal(act_lib.read_log(self.paper), self.payload("unfollow"), 5, self.now))
        self.assertIsNone(act_lib.refusal(act_lib.read_log(self.paper), self.payload(), 5, self.now + dt.timedelta(days=5)))


if __name__ == "__main__":
    unittest.main()
