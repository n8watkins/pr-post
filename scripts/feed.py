#!/usr/bin/env python3
"""feed.json: what the people you follow shipped, as public facts other sites can embed.

Usage:
    python3 scripts/feed.py --check dist/feed.json    # validate a built feed; exits non-zero if it is empty or invalid

build_site.py writes the feed with build(). The format is documented in
docs/feed.md and pinned by docs/feed.schema.json. Facts come only from
stats/; an optional summary from summaries/ is carried separately and
labelled with who wrote it. Private counts are never included. No dependencies.
"""

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402

SCHEMA_VERSION = 1
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "docs" / "feed.schema.json"
SUMMARY_LIMIT = 800
SOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _clean(text, limit):
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _url(u):
    return u.strip() if config.is_http_url(u) else None


def load_summaries(paper, window_start):
    """The newest <paper>/summaries/<date>.json written inside the current window, or {}.

    Shape: {"source": "claude", "written_at": "...", "people": {"<id>": "One paragraph."}}
    Older files are ignored so a stale summary never sits on top of new facts.
    """
    files = sorted((paper / "summaries").glob("*.json"))
    for f in reversed(files):
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", f.stem) or f.stem < window_start:
            continue
        try:
            raw = json.loads(f.read_text())
        except json.JSONDecodeError as e:
            raise SystemExit(f"{f} is not valid JSON: {e}") from e
        source = str(raw.get("source", "")).strip().lower()
        if not SOURCE_RE.match(source):
            raise SystemExit(f'{f}: "source" must name who wrote it, like "claude", "gemini-3.5-flash-lite" or "human"')
        people = raw.get("people") or {}
        return {pid: {"text": _clean(text, SUMMARY_LIMIT), "source": source,
                      "written_at": str(raw.get("written_at") or f.stem)[:25]}
                for pid, text in people.items() if isinstance(text, str) and text.strip()}
    return {}


def person_url(site, pid):
    return f"{site['url']}/p/{pid}/" if site["url"] else None


def build(cfg, snapshot, summaries=None):
    """The feed dict for the newest stats snapshot. Pure: no I/O."""
    site = cfg["site"]
    summaries = summaries or {}
    people = []
    for p in cfg["people"]:
        st = snapshot["people"].get(p["id"])
        if not st:
            continue
        c = st.get("contributions") or {}
        s = summaries.get(p["id"])
        people.append({
            "id": p["id"],
            "who": {
                "name": p["name"],
                "github": p["github"],
                "avatar": _url(st.get("avatar")),
                "known_for": p["known_for"] or None,
                "url": person_url(site, p["id"]),
                "links": ([{"label": "GitHub", "url": f"https://github.com/{p['github']}"}] if p["github"] else [])
                         + [{"label": l["label"], "url": l["url"]} for l in st["links"] if _url(l["url"])],
            },
            "week": {
                "start": st["window"]["start"],
                "end": st["window"]["end"],
                "public_prs_merged": st["public_merged_prs"] if st["has_github"] else None,
                "days_active": c.get("days_active_public") if c else None,
                "releases": len(st["releases"]),
                "new_repos": len(st["births"]),
                "videos": len(st["videos"]),
            },
            "recent": [
                {"type": i["type"], "title": _clean(i["title"], 300), "url": i["url"], "date": i["date"], "repo": i["repo"]}
                for i in st["recent"] if _url(i["url"])
            ],
            "summary": s["text"] if s else None,
            "summary_source": s["source"] if s else None,
            "summary_is_ai": (s["source"] != "human") if s else False,
            "summary_written_at": s["written_at"] if s else None,
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": snapshot["generated_at"],
        "generator": "pr-post",
        "title": site["title"],
        "home_page_url": f"{site['url']}/" if site["url"] else None,
        "feed_url": f"{site['url']}/feed.json" if site["url"] else None,
        "people": people,
    }


# ---------- a small JSON Schema validator (the subset docs/feed.schema.json uses) ----------

TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _is_type(value, name):
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, TYPES[name])


def validate(value, schema, path="$"):
    """Return a list of error strings; empty means valid."""
    errors = []
    types = schema.get("type")
    if types:
        types = [types] if isinstance(types, str) else types
        if not any(_is_type(value, t) for t in types):
            return [f"{path}: expected {' or '.join(types)}, got {type(value).__name__}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")
    if isinstance(value, str):
        if len(value) > schema.get("maxLength", 10**9):
            errors.append(f"{path}: longer than {schema['maxLength']}")
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: shorter than {schema['minLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: does not match {schema['pattern']}")
        fmt = schema.get("format")
        if fmt == "uri" and not config.is_http_url(value):
            errors.append(f"{path}: not an http(s) URL")
        if fmt == "date":
            try:
                dt.date.fromisoformat(value)
            except ValueError:
                errors.append(f"{path}: not a YYYY-MM-DD date")
        if fmt == "date-time":
            try:
                dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                errors.append(f"{path}: not an ISO 8601 date-time")
    if _is_type(value, "integer") and value < schema.get("minimum", -10**18):
        errors.append(f"{path}: below {schema['minimum']}")
    if isinstance(value, list):
        if len(value) > schema.get("maxItems", 10**9):
            errors.append(f"{path}: more than {schema['maxItems']} items")
        for i, item in enumerate(value):
            errors += validate(item, schema.get("items", {}), f"{path}[{i}]")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing {key}")
        props = schema.get("properties", {})
        for key, item in value.items():
            if key in props:
                errors += validate(item, props[key], f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected key {key}")
    return errors


def private_keys(value, path="$"):
    """Every key that mentions private work, anywhere in the feed. Must be empty."""
    found = []
    if isinstance(value, dict):
        for k, v in value.items():
            if "private" in k.lower() or "restricted" in k.lower():
                found.append(f"{path}.{k}")
            found += private_keys(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            found += private_keys(v, f"{path}[{i}]")
    return found


def check(feed, schema=None):
    """Problems that should fail a deploy: schema errors, private keys, or an empty feed."""
    schema = schema or json.loads(SCHEMA_PATH.read_text())
    problems = validate(feed, schema)
    problems += [f"{k}: private counts must never be in the feed" for k in private_keys(feed)]
    people = feed.get("people") or []
    if not people:
        problems.append("the feed has no people")
    elif not any(p.get("recent") for p in people):
        problems.append("the feed has people but not one recent item; collection probably failed")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", metavar="FEED", required=True, help="path to a built feed.json")
    args = ap.parse_args()
    feed = json.loads(Path(args.check).read_text())
    problems = check(feed)
    if problems:
        for p in problems:
            print(f"::error::feed.json: {p}", file=sys.stderr)
        sys.exit(1)
    items = sum(len(p["recent"]) for p in feed["people"])
    print(f"feed.json OK: schema v{feed['schema_version']}, {len(feed['people'])} people, {items} recent items")
    for p in feed["people"]:
        print(f"  {p['id']}: {len(p['recent'])} recent, {p['week']['public_prs_merged']} public PRs merged, {p['week']['videos']} videos")


if __name__ == "__main__":
    main()
