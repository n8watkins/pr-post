"""One-click actions from the Who to follow email: signed, single-use, expiring links.

A link is <site>/act/<token>. The token is

    base64url(JSON {"a": action, "l": login, "e": expiry, "n": nonce}) "." base64url(HMAC-SHA256)

signed with ACTION_SIGNING_KEY, a random secret held by the Worker (to check
links) and by Actions (to sign them for the email, and to check them again
before acting). The token carries no other secret. worker/lib.js has the same
format; tests/test_act.py and tests/worker.test.mjs keep the two in step.

Opening a link never acts: the Worker shows a page that acts through a POST,
so email scanners that fetch every link change nothing. The act.yml workflow
in the paper checks the token again, refuses a nonce already in the log, keeps
to [follow] max_follows_per_week, acts, and appends a line to
<paper>/follow/actions.jsonl.
"""

import base64
import datetime as dt
import hashlib
import hmac
import json
import re
import secrets
import time
from pathlib import Path

ACTIONS = ("follow", "unfollow", "add", "unadd")
UNDO = {"follow": "unfollow", "add": "unadd"}
LIFETIME = 7 * 24 * 3600
LOGIN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
NONCE_RE = re.compile(r"[0-9a-f]{32}")


class TokenError(ValueError):
    pass


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(key, body):
    return hmac.new(key.encode(), body.encode(), hashlib.sha256).digest()


def sign(key, action, login, now=None, lifetime=LIFETIME, nonce=None):
    if not key:
        raise TokenError("no signing key")
    if action not in ACTIONS or not LOGIN_RE.fullmatch(login or ""):
        raise TokenError(f"cannot sign {action!r} for {login!r}")
    payload = {"a": action, "l": login, "e": int(now if now is not None else time.time()) + lifetime,
               "n": nonce or secrets.token_hex(16)}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body}.{_b64(_mac(key, body))}"


def verify(key, token, now=None):
    """The token's payload {"a", "l", "e", "n"}, or TokenError saying why not."""
    if not key:
        raise TokenError("no signing key")
    if not isinstance(token, str) or len(token) > 400 or token.count(".") != 1:
        raise TokenError("not a token")
    body, sig = token.split(".")
    try:
        good = hmac.compare_digest(_unb64(sig), _mac(key, body))
        payload = json.loads(_unb64(body)) if good else None
    except (ValueError, TypeError):
        raise TokenError("not a token") from None
    if not good:
        raise TokenError("bad signature")
    if (not isinstance(payload, dict) or payload.get("a") not in ACTIONS or not LOGIN_RE.fullmatch(str(payload.get("l", "")))
            or not isinstance(payload.get("e"), int) or not NONCE_RE.fullmatch(str(payload.get("n", "")))):
        raise TokenError("not a token")
    if payload["e"] < int(now if now is not None else time.time()):
        raise TokenError("expired")
    return payload


def url(site_url, token):
    return f"{site_url.rstrip('/')}/act/{token}"


# ---------- the log ----------

def log_path(paper):
    return Path(paper) / "follow" / "actions.jsonl"


def read_log(paper):
    try:
        lines = log_path(paper).read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def refusal(log, payload, max_follows, now=None):
    """Why this action must not run, or None. Single use, and at most max_follows follows in 7 days."""
    if any(e.get("nonce") == payload["n"] for e in log):
        return "This link was already used."
    if payload["a"] == "follow":
        now = now or dt.datetime.now(dt.timezone.utc)
        since = now - dt.timedelta(days=7)
        done = [e for e in log if e.get("action") == "follow" and e.get("result") == "ok"
                and dt.datetime.fromisoformat(e["at"]) >= since]
        if len(done) >= max_follows:
            return f"Already {len(done)} follows in the last 7 days (the limit is {max_follows})."
    return None


def entry(payload, result, detail="", now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    return {"at": now.isoformat(timespec="seconds"), "action": payload["a"], "login": payload["l"],
            "nonce": payload["n"], "result": result, "detail": detail[:200]}


def append(paper, item):
    path = log_path(paper)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(item, separators=(",", ":")) + "\n")
