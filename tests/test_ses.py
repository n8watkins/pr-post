"""scripts/ses.py: SigV4 signing against AWS's published examples, and the SendEmail request."""

import datetime as dt
import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import config  # noqa: E402
import ses  # noqa: E402

# The example credentials and expected signatures from AWS's Signature Version 4 test suite.
EXAMPLE = ("AKIDEXAMPLE", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "")
WHEN = dt.datetime(2015, 8, 30, 12, 36, tzinfo=dt.timezone.utc)


def sig(headers):
    return headers["Authorization"].rsplit("Signature=", 1)[1]


class SigV4(unittest.TestCase):
    def test_aws_examples(self):
        for method, url, expected in [
            ("GET", "https://example.amazonaws.com/", "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"),
            ("POST", "https://example.amazonaws.com/", "5da7c1a2acd57cee7505fc6676e4e544621c30862966e37dddb68e92efbe5d6b"),
            ("GET", "https://example.amazonaws.com/?Param2=value2&Param1=value1", "b97d918cfa904a5beff61c982a1b6f458b799221646efd99d3219ec94cdf2500"),
        ]:
            with self.subTest(method=method, url=url):
                h = ses.sign(method, url, {}, b"", "us-east-1", "service", EXAMPLE, WHEN)
                self.assertEqual(sig(h), expected)
                self.assertIn("Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, SignedHeaders=host;x-amz-date,", h["Authorization"])

    def test_session_token_is_signed(self):
        h = ses.sign("POST", "https://email.us-east-1.amazonaws.com/v2/email/outbound-emails", {"content-type": "application/json"},
                     b"{}", "us-east-1", "ses", ("AKID", "secret", "session-token"), WHEN)
        self.assertEqual(h["x-amz-security-token"], "session-token")
        self.assertIn("SignedHeaders=content-type;host;x-amz-date;x-amz-security-token,", h["Authorization"])


class Send(unittest.TestCase):
    def fake(self, status=200, body=b'{"MessageId": "m-1"}'):
        seen = {}

        def opener(req, timeout):
            seen["req"] = req
            if status != 200:
                raise urllib.error.HTTPError(req.full_url, status, "no", {}, io.BytesIO(body))
            return io.BytesIO(body)
        return seen, opener

    def test_sends_one_signed_request(self):
        seen, opener = self.fake()
        mid = ses.send("PR Post <paper@post.example.dev>", "me@example.com", "Hi", "<p>hi</p>", "hi", "us-east-1", "pr-post",
                       ("AKID", "secret", "tok"), WHEN, opener)
        self.assertEqual(mid, "m-1")
        req = seen["req"]
        self.assertEqual(req.full_url, "https://email.us-east-1.amazonaws.com/v2/email/outbound-emails")
        self.assertEqual(req.get_method(), "POST")
        body = json.loads(req.data)
        self.assertEqual(body["Destination"], {"ToAddresses": ["me@example.com"]})
        self.assertEqual(body["ConfigurationSetName"], "pr-post")
        self.assertEqual(body["Content"]["Simple"]["Body"]["Text"]["Data"], "hi")
        self.assertTrue(req.get_header("Authorization").startswith("AWS4-HMAC-SHA256 Credential=AKID/20150830/us-east-1/ses/aws4_request"))
        self.assertEqual(req.get_header("X-amz-security-token"), "tok")

    def test_refusal_and_missing_credentials(self):
        _, opener = self.fake(400, b'{"message": "Email address is not verified."}')
        with self.assertRaisesRegex(ses.SesError, "400.*not verified"):
            ses.send("a@b.c", "d@e.f", "s", "h", "t", "us-east-1", creds=EXAMPLE, opener=opener)
        self.assertIsNone(ses.credentials({}))
        self.assertEqual(ses.credentials({"AWS_ACCESS_KEY_ID": "a", "AWS_SECRET_ACCESS_KEY": "b"}), ("a", "b", ""))


class EmailConfig(unittest.TestCase):
    def test_defaults_and_validation(self):
        self.assertEqual(config.parse("")["email"]["via"], "resend")
        cfg = config.parse('[email]\nvia = "ses"\nregion = "eu-west-2"\nconfiguration_set = "pr-post"\n')["email"]
        self.assertEqual((cfg["via"], cfg["region"], cfg["configuration_set"]), ("ses", "eu-west-2", "pr-post"))
        for bad in ['via = "smtp"', 'region = "mars"', 'configuration_set = "a b"']:
            with self.subTest(bad=bad), self.assertRaises(config.ConfigError):
                config.parse(f"[email]\n{bad}\n")


if __name__ == "__main__":
    unittest.main()
