#!/usr/bin/env python3
"""Carry out a one-click action from the Who to follow email (act_lib.py has the design).

Usage, from the paper's act.yml workflow:
    python3 scripts/act.py --paper DIR check TOKEN          # verify, single use, weekly limit; writes GITHUB_OUTPUT
    python3 scripts/act.py --paper DIR github follow LOGIN  # PUT /user/following/LOGIN with FOLLOW_GITHUB_TOKEN
    python3 scripts/act.py --paper DIR github unfollow LOGIN
    python3 scripts/act.py --paper DIR log TOKEN ok|failed|refused [DETAIL]
    python3 scripts/act.py --paper DIR sign ACTION LOGIN    # print a link, for testing
    python3 scripts/act.py --paper DIR run TOKEN_FILE       # a paper pulled by store.py: check, act and log in one go

`run` is for a paper kept in Cloudflare (docs/private-mode.md). It reads the
link from a file (store.py request writes it), so the link never appears in a
public log, masks the login, checks the link and the limits, acts, and appends
the result to follow/actions.jsonl for store.py push. Add drafts the person
into watchlist.toml (follow.py draft), which push sends as a draft for the
People page; Undo of an Add lists them in discard.json. It exits 1 when the
action was refused or failed, after logging it.

ACTION_SIGNING_KEY must be set for check, log and sign. Nothing here prints a
key or a token's signature.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import act_lib  # noqa: E402
import config  # noqa: E402


def output(**values):
    path = os.environ.get("GITHUB_OUTPUT")
    lines = "".join(f"{k}={v}\n" for k, v in values.items())
    if path:
        with open(path, "a") as f:
            f.write(lines)
    else:
        print(lines, end="")


def follow_api(verb, login):
    token = os.environ.get("FOLLOW_GITHUB_TOKEN")
    if not token:
        sys.exit("Follow isn't set up yet: add the FOLLOW_GITHUB_TOKEN secret (SETUP.md).")
    req = urllib.request.Request(
        f"https://api.github.com/user/following/{login}", method="PUT" if verb == "follow" else "DELETE",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "pr-post", "Content-Length": "0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"{verb}: GitHub answered {r.status}")
    except urllib.error.HTTPError as e:
        sys.exit(f"GitHub refused to {verb} {login} ({e.code}). The token needs the Followers: write account permission.")


def run(paper, key, token_file):
    """Check, act and log one link on a pulled paper. Returns the exit code."""
    try:
        payload = act_lib.verify(key, Path(token_file).read_text().strip())
    except (OSError, act_lib.TokenError) as e:
        print(f"Refusing the link: {e}.")
        return 1
    action, login = payload["a"], payload["l"]
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::add-mask::{login}")
    cfg = config.load(paper)
    why = act_lib.refusal(act_lib.read_log(paper), payload, cfg["follow"]["max_follows_per_week"])
    result, detail = ("refused", why) if why else ("ok", "")
    if not why:
        try:
            if action in ("follow", "unfollow"):
                follow_api(action, login)
            elif action == "add":
                import follow  # noqa: PLC0415 (only Add needs the research code)
                follow.draft(paper, cfg, login)
            elif action == "unadd":
                (Path(paper) / "discard.json").write_text(json.dumps([login.lower()]))
        except SystemExit as e:
            result, detail = "failed", str(e.code or "failed")
    act_lib.append(paper, act_lib.entry(payload, result, detail))
    print(f"{action}: {result}" + (f" ({detail})" if detail else ""))
    return 0 if result == "ok" else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("token")
    g = sub.add_parser("github")
    g.add_argument("verb", choices=("follow", "unfollow"))
    g.add_argument("login")
    lg = sub.add_parser("log")
    lg.add_argument("token")
    lg.add_argument("result", choices=("ok", "failed", "refused"))
    lg.add_argument("detail", nargs="?", default="")
    s = sub.add_parser("sign")
    s.add_argument("action", choices=act_lib.ACTIONS)
    s.add_argument("login")
    r = sub.add_parser("run")
    r.add_argument("token_file")
    args = ap.parse_args()

    paper = config.paper_dir(args.paper)
    key = os.environ.get("ACTION_SIGNING_KEY", "")
    if args.cmd == "github":
        if not act_lib.LOGIN_RE.fullmatch(args.login):
            sys.exit(f"Not a GitHub username: {args.login}")
        return follow_api(args.verb, args.login)
    if not key:
        sys.exit("ACTION_SIGNING_KEY is not set.")
    if args.cmd == "run":
        sys.exit(run(paper, key, args.token_file))
    if args.cmd == "sign":
        site = config.load(paper)["site"]["url"]
        print(act_lib.url(site or "https://example.invalid", act_lib.sign(key, args.action, args.login)))
        return
    try:
        # A link checked in time may finish after it expires, so logging ignores expiry.
        payload = act_lib.verify(key, args.token, now=0 if args.cmd == "log" else None)
    except act_lib.TokenError as e:
        sys.exit(f"Refusing the link: {e}.")
    if args.cmd == "log":
        act_lib.append(paper, act_lib.entry(payload, args.result, args.detail))
        print(f"Logged {payload['a']} {payload['l']}: {args.result}")
        return
    cfg = config.load(paper)
    why = act_lib.refusal(act_lib.read_log(paper), payload, cfg["follow"]["max_follows_per_week"])
    output(action=payload["a"], login=payload["l"], refused=why or "")
    print(f"{payload['a']} {payload['l']}: {why or 'allowed'}")


if __name__ == "__main__":
    main()
