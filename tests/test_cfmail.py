"""scripts/cfmail.py: the Cloudflare Email Service REST request, with no network."""

import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cfmail  # noqa: E402
import config  # noqa: E402
import email_lib  # noqa: E402

CREDS = ("acct123", "tok_secret_value")


def fake(status=200, body=None):
    seen = {}
    body = json.dumps(body if body is not None else {"success": True, "errors": [], "result": {"delivered": ["me@example.com"], "permanent_bounces": [], "queued": []}}).encode()

    def opener(req, timeout):
        seen["req"] = req
        if status != 200:
            raise urllib.error.HTTPError(req.full_url, status, "no", {}, io.BytesIO(body))
        return io.BytesIO(body)

    return seen, opener


class Send(unittest.TestCase):
    def test_sends_one_authenticated_request(self):
        seen, opener = fake()
        out = cfmail.send("PR Post <prpost@n8builds.dev>", "me@example.com", "Hi", "<p>hi</p>", "hi", CREDS, opener)
        self.assertEqual(out, "delivered")
        req = seen["req"]
        self.assertEqual(req.full_url, "https://api.cloudflare.com/client/v4/accounts/acct123/email/sending/send")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Authorization"), "Bearer tok_secret_value")
        body = json.loads(req.data)
        self.assertEqual(body["from"], {"address": "prpost@n8builds.dev", "name": "PR Post"})
        self.assertEqual((body["to"], body["subject"], body["html"], body["text"]), (["me@example.com"], "Hi", "<p>hi</p>", "hi"))

    def test_queued_counts_as_accepted(self):
        _, opener = fake(body={"success": True, "result": {"delivered": [], "permanent_bounces": [], "queued": ["me@example.com"]}})
        self.assertEqual(cfmail.send("a@b.co", "me@example.com", "s", "h", "t", CREDS, opener), "queued")

    def test_refusal_carries_the_reason_but_never_the_token_or_address(self):
        _, opener = fake(400, {"success": False, "errors": [{"code": 1000, "message": "Sender domain not verified"}], "result": None})
        with self.assertRaises(cfmail.CloudflareMailError) as ctx:
            cfmail.send("a@b.co", "me@example.com", "s", "h", "t", CREDS, opener)
        msg = str(ctx.exception)
        self.assertIn("400", msg)
        self.assertIn("Sender domain not verified", msg)
        self.assertNotIn("tok_secret_value", msg)
        self.assertNotIn("me@example.com", msg)

    def test_a_permanent_bounce_is_an_error(self):
        _, opener = fake(body={"success": True, "result": {"delivered": [], "permanent_bounces": ["me@example.com"], "queued": []}})
        with self.assertRaisesRegex(cfmail.CloudflareMailError, "permanent bounce"):
            cfmail.send("a@b.co", "me@example.com", "s", "h", "t", CREDS, opener)

    def test_credentials_need_both_values(self):
        self.assertIsNone(cfmail.credentials({}))
        self.assertIsNone(cfmail.credentials({"CLOUDFLARE_EMAIL_TOKEN": "t"}))
        self.assertIsNone(cfmail.credentials({"CLOUDFLARE_ACCOUNT_ID": "a"}))
        self.assertEqual(cfmail.credentials({"CLOUDFLARE_ACCOUNT_ID": "a", "CLOUDFLARE_EMAIL_TOKEN": "t"}), ("a", "t"))

    def test_parse_from(self):
        self.assertEqual(cfmail.parse_from('"PR Post" <p@n8builds.dev>'), {"address": "p@n8builds.dev", "name": "PR Post"})
        self.assertEqual(cfmail.parse_from("p@n8builds.dev"), {"address": "p@n8builds.dev"})


class Config(unittest.TestCase):
    def test_via_cloudflare_is_accepted_and_described_without_values(self):
        watch = config.parse('[email]\nvia = "cloudflare"\nto = "me@example.com"\nfrom = "PR Post <prpost@n8builds.dev>"\n')
        self.assertEqual(watch["email"]["via"], "cloudflare")
        self.assertEqual(email_lib.transport(watch), ("cloudflare", None))
        self.assertEqual(email_lib.describe(watch), "Cloudflare Email Service (CLOUDFLARE_EMAIL_TOKEN and CLOUDFLARE_ACCOUNT_ID not set)")
        with self.assertRaises(config.ConfigError):
            config.parse('[email]\nvia = "mailgun"\n')


if __name__ == "__main__":
    unittest.main()
