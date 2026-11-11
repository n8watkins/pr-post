"""Send one email through Cloudflare Email Service (the REST send API), with the standard library only.

Credentials come from two environment variables: CLOUDFLARE_EMAIL_TOKEN, an API
token with only the "Email Sending: Edit" permission, and CLOUDFLARE_ACCOUNT_ID.
The sender's domain must be onboarded for Email Sending on that account.
docs/email.md has the setup.
"""

import json
import os
import re
import urllib.error
import urllib.request

ENDPOINT = "https://api.cloudflare.com/client/v4/accounts/{account}/email/sending/send"


class CloudflareMailError(RuntimeError):
    pass


def credentials(env=None):
    """(account id, API token) from the environment, or None when either is missing."""
    env = os.environ if env is None else env
    account, token = env.get("CLOUDFLARE_ACCOUNT_ID", "").strip(), env.get("CLOUDFLARE_EMAIL_TOKEN", "").strip()
    return (account, token) if account and token else None


def parse_from(sender):
    """'Name <a@b.c>' or 'a@b.c' as the API's {address, name} (name left out when there is none)."""
    m = re.fullmatch(r"\s*(.*?)\s*<\s*([^<>\s]+)\s*>\s*", sender)
    if not m:
        return {"address": sender.strip()}
    name = m.group(1).strip().strip('"')
    return {"address": m.group(2), **({"name": name} if name else {})}


def send(sender, to, subject, page, text, creds, opener=urllib.request.urlopen):
    """Send one email. Returns "delivered" or "queued" (never the address, because the caller prints it into
    public Actions logs); raises CloudflareMailError with Cloudflare's reason when it refuses."""
    account, token = creds
    payload = {"from": parse_from(sender), "to": [to], "subject": subject, "html": page, "text": text}
    req = urllib.request.Request(
        ENDPOINT.format(account=account),
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": "pr-post"},
        method="POST",
    )
    try:
        with opener(req, timeout=30) as r:
            body = json.load(r)
    except urllib.error.HTTPError as e:
        raise CloudflareMailError(f"Cloudflare refused the email ({e.code}): {_reason(e.read())}") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise CloudflareMailError(f"Cloudflare could not be reached: {type(e).__name__}") from None
    if not body.get("success"):
        raise CloudflareMailError(f"Cloudflare refused the email: {_errors(body)}")
    result = body.get("result") or {}
    if result.get("permanent_bounces"):
        raise CloudflareMailError("Cloudflare reported a permanent bounce for the recipient.")
    return "delivered" if result.get("delivered") else "queued"


def _errors(body):
    return "; ".join(str(e.get("message", e.get("code", "error"))) for e in body.get("errors") or [] if isinstance(e, dict))[:500] or "unknown error"


def _reason(raw):
    try:
        return _errors(json.loads(raw))
    except ValueError:
        return raw.decode(errors="replace")[:500]
