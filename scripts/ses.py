"""Send one email through Amazon SES (the v2 SendEmail API), with the standard library only.

Credentials come from the usual AWS environment variables: AWS_ACCESS_KEY_ID,
AWS_SECRET_ACCESS_KEY and, for the short-lived credentials GitHub OIDC hands
out (aws-actions/configure-aws-credentials), AWS_SESSION_TOKEN. Requests are
signed with Signature Version 4, implemented here so the engine keeps no
dependencies; tests/test_ses.py checks it against AWS's published example.

docs/email.md has the setup: a verified sender domain, a configuration set
that reports bounces and complaints, and a send-only IAM role.
"""

import datetime as dt
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request

SERVICE = "ses"


class SesError(RuntimeError):
    pass


def _h(data):
    return hashlib.sha256(data).hexdigest()


def _hmac(key, msg):
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def credentials(env=None):
    """(access key, secret key, session token or "") from the environment, or None when not set."""
    env = os.environ if env is None else env
    key, secret = env.get("AWS_ACCESS_KEY_ID", ""), env.get("AWS_SECRET_ACCESS_KEY", "")
    if not key or not secret:
        return None
    return key, secret, env.get("AWS_SESSION_TOKEN", "")


def sign(method, url, headers, body, region, service, creds, now):
    """The headers for a SigV4-signed request: `headers` plus x-amz-date, a session token if any, and Authorization."""
    key, secret, token = creds
    u = urllib.parse.urlsplit(url)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    day = amz_date[:8]
    out = {**headers, "host": u.netloc, "x-amz-date": amz_date}
    if token:
        out["x-amz-security-token"] = token
    lower = {k.lower(): " ".join(str(v).strip().split()) for k, v in out.items()}
    names = sorted(lower)
    query = "&".join(
        f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}"
        for k, v in sorted(urllib.parse.parse_qsl(u.query, keep_blank_values=True))
    )
    canonical = "\n".join([
        method,
        urllib.parse.quote(u.path or "/", safe="/-_.~"),
        query,
        "".join(f"{n}:{lower[n]}\n" for n in names),
        ";".join(names),
        _h(body),
    ])
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, _h(canonical.encode())])
    k = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret).encode(), day), region), service), "aws4_request")
    signature = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    out["Authorization"] = f"AWS4-HMAC-SHA256 Credential={key}/{scope}, SignedHeaders={';'.join(names)}, Signature={signature}"
    return out


def message(sender, to, subject, html, text, configuration_set=""):
    body = {
        "FromEmailAddress": sender,
        "Destination": {"ToAddresses": [to]},
        "Content": {"Simple": {
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Html": {"Data": html, "Charset": "UTF-8"}, "Text": {"Data": text, "Charset": "UTF-8"}},
        }},
    }
    if configuration_set:
        body["ConfigurationSetName"] = configuration_set
    return body


def send(sender, to, subject, html, text, region, configuration_set="", creds=None, now=None, opener=urllib.request.urlopen):
    """Send one email. Returns SES's MessageId; raises SesError with SES's reason on refusal."""
    creds = creds or credentials()
    if not creds:
        raise SesError("No AWS credentials in the environment.")
    url = f"https://email.{region}.amazonaws.com/v2/email/outbound-emails"
    body = json.dumps(message(sender, to, subject, html, text, configuration_set)).encode()
    headers = sign("POST", url, {"content-type": "application/json"}, body, region, SERVICE, creds, now or dt.datetime.now(dt.timezone.utc))
    headers.pop("host")
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with opener(req, timeout=30) as r:
            return json.load(r).get("MessageId")
    except urllib.error.HTTPError as e:
        try:
            why = json.loads(e.read()).get("message", "")
        except ValueError:
            why = ""
        raise SesError(f"SES refused the email ({e.code}): {why[:300]}") from None
    except urllib.error.URLError as e:
        raise SesError(f"Could not reach SES: {e.reason}") from None
