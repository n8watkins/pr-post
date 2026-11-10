#!/usr/bin/env python3
"""Move a private paper between its Cloudflare storage and a working folder.

A private paper served by the Worker keeps nothing in git: files live in R2 and
the watch list, profiles, Who to follow state, action log, emailed reports and
email suppressions live in D1
(worker/store.js, docs/private-mode.md). The engine's scripts work on a plain
folder, so a job pulls one, runs them, and pushes back what changed:

    python3 scripts/store.py pull work/              # watchlist.toml, profiles/, files, follow state, action log
    python3 scripts/collect.py --paper work/ ...     # any engine script
    python3 scripts/store.py push work/              # upload only what changed
    python3 scripts/store.py site dist/              # publish the built site (uploads changes, removes stale pages)
    python3 scripts/store.py put work/logs/x.log logs/x.log   # one file
    python3 scripts/store.py request ID work/.request          # a job's payload from the Worker (see worker/jobs.js)
    python3 scripts/store.py publish                         # ask the Worker to start the publish job

A job may add people as drafts for the owner to review: new [[people]] blocks
in watchlist.toml (follow.py draft writes them) are sent as drafts with their
profiles, and ids listed in discard.json remove drafts. Existing people are
only ever changed on the site.

PR_POST_API is the Worker's address and PR_POST_TOKEN its JOBS_TOKEN or
WRITER_TOKEN; the Worker decides what each may read and write. Output is
counts only, never file names or people, because Actions logs on a public
repository are public; --verbose lists paths for local use. In GitHub Actions,
pull also masks every person's id, username and name and the email address,
so a stray line from another script cannot show them either.
No dependencies.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Must match worker/store.js.
PAPER_DIRS = ("data", "stats", "briefs", "reports", "editions", "summaries", "follow", "notes", "logs", "previews", "backups")
IN_D1 = {"follow/state.json", "follow/actions.jsonl"}
SNAPSHOT = ".store.json"
CHUNK = 1_500_000  # bytes of follow state per request

PEOPLE_ORDER = ("github", "id", "name", "ticker", "starred", "self", "orgs", "known_for", "youtube_channel_id", "avatar", "links")
SECTION_ORDER = ("site", "writer", "delivery", "email", "links", "follow")
BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


class StoreError(RuntimeError):
    pass


# ---------- watchlist.toml from D1 ----------

def _key(k):
    return k if BARE_KEY.match(k) else json.dumps(k, ensure_ascii=False)


def _value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)  # JSON string escapes are valid TOML basic strings
    if isinstance(v, list):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_key(k)} = {_value(x)}" for k, x in v.items()) + " }"
    raise StoreError(f"cannot write {type(v).__name__} to TOML")


def _table(name, data, out):
    scalars = {k: v for k, v in data.items() if not isinstance(v, dict)}
    tables = {k: v for k, v in data.items() if isinstance(v, dict)}
    out.append(f"[{name}]")
    out.extend(f"{_key(k)} = {_value(v)}" for k, v in scalars.items())
    out.append("")
    for k, v in tables.items():
        _table(f"{name}.{_key(k)}", v, out)


def _person_lines(entry):
    p = dict(entry)
    github = p.get("github") or ""
    if github and p.get("id") == github.lower():
        p.pop("id")
    keys = [k for k in PEOPLE_ORDER if k in p] + [k for k in p if k not in PEOPLE_ORDER]
    lines = ["[[people]]"]
    for k in keys:
        v = p[k]
        if v in (None, "", False, [], {}):
            continue
        if k == "links":
            lines.append("links = [")
            lines.extend(f"  {_value(link)}," for link in v)
            lines.append("]")
        else:
            lines.append(f"{_key(k)} = {_value(v)}")
    return lines + [""]


def render_watchlist(settings, people):
    """watchlist.toml text for these settings ({section: table}) and people entries, in order."""
    out = ["# Rendered from the paper's database by scripts/store.py. Edit people and settings",
           "# on the site's People page; changes made here are not kept.", ""]
    for name in [s for s in SECTION_ORDER if s in settings] + sorted(s for s in settings if s not in SECTION_ORDER):
        if not isinstance(settings[name], dict):
            raise StoreError(f"setting {name} is not a table")
        _table(_key(name), settings[name], out)
    for entry in people:
        out.extend(_person_lines(entry))
    return "\n".join(out).rstrip("\n") + "\n"


# ---------- the API ----------

class Api:
    def __init__(self, base=None, token=None, verbose=False):
        self.base = (base or os.environ.get("PR_POST_API") or "").rstrip("/")
        self.token = token or os.environ.get("PR_POST_TOKEN") or ""
        self.verbose = verbose
        if not self.base.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise StoreError("Set PR_POST_API to the Worker's https:// address.")
        if not self.token:
            raise StoreError("Set PR_POST_TOKEN to the Worker's JOBS_TOKEN or WRITER_TOKEN.")

    def call(self, method, path, body=None, headers=None, ok=(200,)):
        data = body if body is None or isinstance(body, bytes) else json.dumps(body).encode()
        hdrs = {"Authorization": f"Bearer {self.token}", "User-Agent": "pr-post-store", **(headers or {})}
        if body is not None and not isinstance(body, bytes):
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=hdrs)
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as e:
                if e.code in ok:
                    return e.code, e.read()
                if e.code >= 500 and attempt < 3:
                    continue
                try:
                    why = json.loads(e.read()).get("error", "")
                except ValueError:
                    why = ""
                # The path can name a person, so it is left out unless --verbose.
                where = f" {path}" if self.verbose else ""
                raise StoreError(f"{method}{where} failed: {e.code} {why}".rstrip()) from None
            except urllib.error.URLError as e:
                if attempt < 3:
                    continue
                raise StoreError(f"Could not reach the Worker: {e.reason}") from None
        raise StoreError("unreachable")

    def json(self, method, path, body=None, ok=(200,)):
        status, raw = self.call(method, path, body, ok=ok)
        return status, (json.loads(raw) if raw else None)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def quote(path):
    return urllib.parse.quote(path, safe="/")


def local_files(root, dirs):
    root = Path(root)
    for d in dirs:
        base = root / d
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*")):
            rel = f.relative_to(root).as_posix()
            if f.is_file() and rel not in IN_D1 and not any(part.startswith(".") for part in rel.split("/")):
                yield rel, f


def canon(obj):
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, sort_keys=True)


def load_snapshot(work):
    try:
        return json.loads((Path(work) / SNAPSHOT).read_text())
    except (OSError, ValueError):
        return {}


def mask(wl, env=None):
    """In GitHub Actions, tell the runner to hide every watched person and the email address in all later log lines."""
    env = os.environ if env is None else env
    if env.get("GITHUB_ACTIONS") != "true":
        return
    values = set()
    for p in wl.get("people", []):
        e = p.get("entry", {})
        values.update([p.get("id"), e.get("github"), e.get("name"), e.get("id")])
    values.add((wl.get("settings", {}).get("email") or {}).get("to"))
    for v in sorted(x for x in values if isinstance(x, str) and len(x) >= 3):
        print(f"::add-mask::{v}")


# ---------- pull ----------

def pull(api, work, say=print):
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    snap = {"files": {}, "profiles": {}, "follow": None, "actions": None, "emailed": None}

    _, wl = api.json("GET", "/api/paper/watchlist")
    mask(wl)
    active = [p["entry"] for p in wl["people"] if p["status"] == "active"]
    snap["people"] = [p["id"] for p in wl["people"]]
    (work / "watchlist.toml").write_text(render_watchlist(wl["settings"], active))

    _, prof = api.json("GET", "/api/paper/profiles")
    (work / "profiles").mkdir(exist_ok=True)
    for pid, text in prof["profiles"].items():
        (work / "profiles" / f"{pid}.md").write_text(text)
        snap["profiles"][pid] = sha256(text.encode())

    _, man = api.json("GET", "/api/paper/manifest")
    fetched = 0
    for f in man["files"]:
        dest = work / f["path"]
        if dest.is_file() and sha256(dest.read_bytes()) == f["sha256"]:
            snap["files"][f["path"]] = f["sha256"]
            continue
        _, data = api.call("GET", "/api/paper/files/" + quote(f["path"]))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        snap["files"][f["path"]] = sha256(data)
        fetched += 1

    status, raw = api.call("GET", "/api/paper/follow", ok=(200, 403, 404))
    if status == 200:
        state = json.loads(raw)
        (work / "follow").mkdir(exist_ok=True)
        (work / "follow" / "state.json").write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n")
        snap["follow"] = {
            "meta": {k: canon(v) for k, v in state.items() if k != "people"},
            "people": {login: sha256(canon(v).encode()) for login, v in state.get("people", {}).items()},
        }
    elif status == 404:
        snap["follow"] = {"meta": {}, "people": {}}

    status, raw = api.call("GET", "/api/paper/emailed", ok=(200, 403))
    if status == 200:
        items = json.loads(raw)["items"]
        (work / "emailed.json").write_text(json.dumps(items, indent=1) + "\n")
        snap["emailed"] = items
    status, raw = api.call("GET", "/api/paper/suppressed", ok=(200, 403))
    if status == 200:
        (work / "suppressed.json").write_text(json.dumps(json.loads(raw)["suppressed"], indent=1) + "\n")

    status, raw = api.call("GET", "/api/paper/actions", ok=(200, 403))
    if status == 200:
        (work / "follow").mkdir(exist_ok=True)
        (work / "follow" / "actions.jsonl").write_bytes(raw)
        snap["actions"] = [json.loads(line)["nonce"] for line in raw.decode().splitlines() if line.strip()]

    (work / SNAPSHOT).write_text(json.dumps(snap))
    say(f"Pulled {len(active)} people, {len(snap['profiles'])} profiles, {len(man['files'])} files ({fetched} downloaded)"
        + (f", {len(snap['follow']['people'])} follow candidates" if snap["follow"] else "")
        + (f", {len(snap['actions'])} logged actions" if snap["actions"] is not None else "") + ".")
    return snap


# ---------- push ----------

def push_files(api, work, dirs, delete=False, verbose=False):
    _, man = api.json("GET", "/api/paper/manifest")
    remote = {f["path"]: f["sha256"] for f in man["files"]}
    sent = removed = 0
    seen = set()
    for rel, f in local_files(work, dirs):
        seen.add(rel)
        data = f.read_bytes()
        digest = sha256(data)
        if remote.get(rel) == digest:
            continue
        api.call("PUT", "/api/paper/files/" + quote(rel), data, {"X-Content-Sha256": digest, "Content-Type": "application/octet-stream"})
        sent += 1
        if verbose:
            print(f"  put {rel}")
    if delete:
        for rel in remote:
            if rel.split("/")[0] in dirs and rel not in seen and rel not in IN_D1:
                api.call("DELETE", "/api/paper/files/" + quote(rel))
                removed += 1
                if verbose:
                    print(f"  delete {rel}")
    return sent, removed


def push_profiles(api, work, snap):
    sent = 0
    folder = Path(work) / "profiles"
    if not folder.is_dir():
        return 0
    for f in sorted(folder.glob("*.md")):
        text = f.read_text()
        if snap.get("profiles", {}).get(f.stem) == sha256(text.encode()):
            continue
        api.call("PUT", "/api/paper/profiles/" + quote(f.stem), text.encode(), {"Content-Type": "text/markdown; charset=utf-8"})
        sent += 1
    return sent


def push_follow(api, work, snap):
    path = Path(work) / "follow" / "state.json"
    if snap.get("follow") is None or not path.is_file():
        return 0, 0
    state = json.loads(path.read_text())
    before = snap["follow"]
    meta = {k: json.dumps(v, ensure_ascii=False) for k, v in state.items() if k != "people" and before["meta"].get(k) != canon(v)}
    people = state.get("people", {})
    changed = {login: v for login, v in people.items() if before["people"].get(login) != sha256(canon(v).encode())}
    gone = [login for login in before["people"] if login not in people]
    batches, batch, size = [], {}, 0
    for login, v in changed.items():
        n = len(canon(v)) + len(login) + 8
        if batch and size + n > CHUNK:
            batches.append(batch)
            batch, size = {}, 0
        batch[login] = v
        size += n
    if batch:
        batches.append(batch)
    if not batches and (meta or gone):
        batches.append({})
    for i, b in enumerate(batches):
        api.json("POST", "/api/paper/follow", {"meta": meta if i == 0 else {}, "upsert": b, "delete": gone if i == 0 else []})
    return len(changed), len(gone)


def push_actions(api, work, snap):
    path = Path(work) / "follow" / "actions.jsonl"
    if snap.get("actions") is None or not path.is_file():
        return 0
    known = set(snap["actions"])
    sent = 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get("nonce") in known:
            continue
        status, _ = api.json("POST", "/api/paper/actions", e, ok=(200, 409))
        known.add(e.get("nonce"))
        sent += status == 200
    return sent


def push_emailed(api, work, snap):
    path = Path(work) / "emailed.json"
    if snap.get("emailed") is None or not path.is_file():
        return 0
    new = [i for i in json.loads(path.read_text()) if i not in set(snap["emailed"])]
    if new:
        api.json("POST", "/api/paper/emailed", {"items": new})
    return len(new)


def push_drafts(api, work, snap):
    """New [[people]] in watchlist.toml become drafts; ids in discard.json lose theirs. Returns (added, discarded)."""
    work = Path(work)
    known = set(snap.get("people") or [])
    added = discarded = 0
    path = work / "watchlist.toml"
    if path.is_file() and snap.get("people") is not None:
        for entry in tomllib.loads(path.read_text()).get("people", []):
            pid = (entry.get("id") or entry.get("github") or "").lower()
            if not pid or pid in known:
                continue
            prof = work / "profiles" / f"{pid}.md"
            note = "Drafted by a job; work through the profile's \"Still to check\" list before approving."
            status, _ = api.json("POST", "/api/paper/people/drafts",
                                 {"entry": entry, "profile": prof.read_text() if prof.is_file() else "", "note": note}, ok=(200, 409))
            added += status == 200
            known.add(pid)
            snap.setdefault("profiles", {})[pid] = sha256(prof.read_bytes()) if prof.is_file() else ""
    discard = work / "discard.json"
    if discard.is_file():
        for pid in json.loads(discard.read_text()):
            status, _ = api.json("DELETE", "/api/paper/people/drafts/" + quote(str(pid)), ok=(200, 404))
            discarded += status == 200
        discard.unlink()
    snap["people"] = sorted(known)
    return added, discarded


def push(api, work, dirs=PAPER_DIRS, delete=False, verbose=False, say=print):
    snap = load_snapshot(work)
    if not snap:
        raise StoreError(f"{work} was not pulled with store.py, so there is nothing to compare with.")
    files, removed = push_files(api, work, dirs, delete, verbose)
    drafts, discarded = push_drafts(api, work, snap)
    profiles = push_profiles(api, work, snap)
    follow, gone = push_follow(api, work, snap)
    actions = push_actions(api, work, snap)
    emailed = push_emailed(api, work, snap)
    say(f"Pushed {files} files" + (f" (removed {removed})" if delete else "") + f", {profiles} profiles, "
        f"{follow} follow candidates ({gone} removed), {actions} logged actions" + (f", {emailed} emailed reports" if emailed else "")
        + (f", {drafts} new drafts" if drafts else "") + (f", {discarded} drafts discarded" if discarded else "") + ".")
    # What was just pushed is the new baseline.
    new = pull_snapshot_after_push(work, snap)
    (Path(work) / SNAPSHOT).write_text(json.dumps(new))


def pull_snapshot_after_push(work, snap):
    work = Path(work)
    new = dict(snap)
    folder = work / "profiles"
    new["profiles"] = {f.stem: sha256(f.read_bytes()) for f in folder.glob("*.md")} if folder.is_dir() else {}
    state_path = work / "follow" / "state.json"
    if snap.get("follow") is not None and state_path.is_file():
        state = json.loads(state_path.read_text())
        new["follow"] = {"meta": {k: canon(v) for k, v in state.items() if k != "people"},
                         "people": {login: sha256(canon(v).encode()) for login, v in state.get("people", {}).items()}}
    emailed = work / "emailed.json"
    if snap.get("emailed") is not None and emailed.is_file():
        new["emailed"] = json.loads(emailed.read_text())
    log = work / "follow" / "actions.jsonl"
    if snap.get("actions") is not None and log.is_file():
        new["actions"] = [json.loads(line)["nonce"] for line in log.read_text().splitlines() if line.strip()]
    return new


# ---------- the built site ----------

def publish_site(api, dist, verbose=False, say=print):
    dist = Path(dist)
    if not (dist / "index.html").is_file():
        raise StoreError(f"{dist} has no index.html; build the site first.")
    _, man = api.json("GET", "/api/site/manifest")
    remote = {f["path"]: f["sha256"] for f in man["files"]}
    local = {f.relative_to(dist).as_posix(): f for f in sorted(dist.rglob("*")) if f.is_file()}
    sent = 0
    for rel, f in local.items():
        data = f.read_bytes()
        digest = sha256(data)
        if remote.get(rel) == digest:
            continue
        api.call("PUT", "/api/site/files/" + quote(rel), data, {"X-Content-Sha256": digest, "Content-Type": "application/octet-stream"})
        sent += 1
        if verbose:
            print(f"  put {rel}")
    # Upload first, then remove what the new build no longer has, so no page is missing mid-publish.
    stale = [rel for rel in remote if rel not in local]
    for rel in stale:
        api.call("DELETE", "/api/site/files/" + quote(rel))
        if verbose:
            print(f"  delete {rel}")
    say(f"Site: {len(local)} files, {sent} uploaded, {len(stale)} removed.")


def put_one(api, local, remote_path):
    data = Path(local).read_bytes()
    api.call("PUT", "/api/paper/files/" + quote(remote_path), data, {"X-Content-Sha256": sha256(data), "Content-Type": "application/octet-stream"})
    print("Stored 1 file.")


def fetch_request(api, rid, out):
    """Write a job's payload (worker/jobs.js) to `out`, and print only its kind."""
    if not re.fullmatch(r"[0-9a-f]{32}", rid or ""):
        raise StoreError("Not a request id.")
    _, body = api.json("GET", f"/api/jobs/requests/{rid}")
    Path(out).write_text(body["payload"])
    print(f"Request: {body['kind']}.")
    return body["kind"]


def publish(api):
    status, body = api.json("POST", "/api/jobs/publish", ok=(202, 503))
    print("The publish job is starting." if status == 202 else f"Not started: {body.get('error')} {body.get('fallback', '')}".strip())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--verbose", action="store_true", help="list file paths (not for public logs)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pull").add_argument("work")
    pp = sub.add_parser("push")
    pp.add_argument("work")
    pp.add_argument("--only", help="comma-separated folders to push files from (default: all)")
    pp.add_argument("--delete", action="store_true", help="remove stored files missing from those folders")
    pp.add_argument("--no-files", action="store_true", help="push only the database parts (drafts, profiles, follow state, action log, emailed)")
    sub.add_parser("site").add_argument("dist")
    put = sub.add_parser("put")
    put.add_argument("local")
    put.add_argument("remote")
    req = sub.add_parser("request")
    req.add_argument("id")
    req.add_argument("out")
    sub.add_parser("publish")
    args = ap.parse_args(argv)
    try:
        api = Api(verbose=args.verbose)
        if args.cmd == "pull":
            pull(api, args.work)
        elif args.cmd == "push":
            dirs = () if args.no_files else tuple(d for d in (args.only.split(",") if args.only else PAPER_DIRS) if d)
            bad = [d for d in dirs if d not in PAPER_DIRS]
            if bad:
                raise StoreError(f"Not a paper folder: {', '.join(bad)}")
            push(api, args.work, dirs, args.delete, args.verbose)
        elif args.cmd == "site":
            publish_site(api, args.dist, args.verbose)
        elif args.cmd == "put":
            put_one(api, args.local, args.remote)
        elif args.cmd == "request":
            fetch_request(api, args.id, args.out)
        elif args.cmd == "publish":
            publish(api)
    except StoreError as e:
        sys.exit(f"store.py: {e}")


if __name__ == "__main__":
    main()
