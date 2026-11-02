#!/usr/bin/env python3
"""Compute every fact and number the paper prints, from <paper>/data/, into <paper>/stats/<date>.json.

Usage:
    python3 scripts/stats.py [--paper DIR]

Runs right after collect.py. The site, feed.json and any writer read their
numbers from here, so nothing printed is ever made up or estimated. Items from
private repositories never reach "recent", "releases", "births" or "deaths";
private work survives only as GitHub's own per-day count, which only a private
site that opts in ever prints. No dependencies.
"""

import argparse
import datetime as dt
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402

WINDOW_DAYS = 7
RECENT_CAPS = {"release": 5, "video": 5, "repo": 5, "pr": 10}
TITLE_LIMIT = 300


def clean(text, limit=TITLE_LIMIT):
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def merge_links(*groups):
    seen, out = set(), []
    for group in groups:
        for link in group:
            if not config.is_http_url(link.get("url")):
                continue
            key = link["url"].lower().rstrip("/").split("://", 1)[-1].removeprefix("www.").replace("twitter.com/", "x.com/")
            if key not in seen:
                seen.add(key)
                out.append({"label": clean(link.get("label"), 40) or "Link", "url": link["url"].strip()})
    return out


def github_stats(g, start):
    """Numbers and public items from a GitHub block, for the window starting at `start`."""
    in_window = lambda day: bool(day) and day >= start
    private_repo = lambda r: bool((g["active_repos"].get(r) or {}).get("private"))

    merged = [p for p in g["pull_requests"] if p["state"] == "merged" and in_window(p["merged"])]
    opened = [p for p in g["pull_requests"] if in_window(p["created"])]
    commits = [c for c in g["commits"] if in_window(c["date"])]
    by_repo = Counter(p["repo"] for p in merged)
    commits_by_repo = Counter(c["repo"] for c in commits)
    repos = sorted(set(by_repo) | set(commits_by_repo), key=lambda r: (-by_repo[r], -commits_by_repo[r], r))

    c = g.get("contributions") or {}
    by_day = [day for day in (c.get("by_day") or []) if day["date"] >= start]
    days = {day: n for day, n in (c.get("days") or {}).items() if day >= start}
    public_days = {d["date"]: sum(d["public"].values()) for d in by_day}

    releases = [
        {"repo": r["repo"], "tag": clean(r.get("tag"), 80), "name": clean(r.get("name") or r.get("tag"), 120),
         "date": r["published"], "url": r["url"], "prerelease": bool(r.get("prerelease"))}
        for r in g.get("releases", [])
        if in_window(r.get("published")) and not private_repo(r["repo"]) and config.is_http_url(r.get("url"))
    ]
    births = [
        {"repo": n, "description": clean(m.get("description"), 200), "url": m["url"], "date": m["created"]}
        for n, m in sorted(g.get("new_repos", {}).items()) if not m.get("private") and not m.get("fork") and not m.get("archived")
    ]
    deaths = [{"repo": n, "url": m["url"]} for n, m in sorted(g.get("archived_repos", {}).items()) if not m.get("private")]

    recent = [{"type": "pr", "title": clean(p["title"]), "url": p["url"], "date": p["merged"], "repo": p["repo"]}
              for p in sorted(merged, key=lambda p: p["merged"], reverse=True) if not private_repo(p["repo"]) and config.is_http_url(p["url"])]
    recent += [{"type": "release", "title": r["name"] or r["tag"], "url": r["url"], "date": r["date"], "repo": r["repo"]} for r in releases]
    recent += [{"type": "repo", "title": b["description"] or b["repo"].split("/")[-1], "url": b["url"], "date": b["date"], "repo": b["repo"]} for b in births]

    return {
        "merged_prs": len(merged),
        "public_merged_prs": sum(1 for p in merged if not private_repo(p["repo"])),
        "opened_prs": len(opened),
        "commits": len(commits),
        "commits_capped": g.get("totals", {}).get("commits_capped", False),
        "repos_touched": len(repos),
        "repos": [{"repo": r, "merged_prs": by_repo[r], "commits": commits_by_repo[r], "private": private_repo(r)} for r in repos[:12]],
        # Contribution graph: public work is itemised; private work is a single count GitHub shares per day.
        "contributions": {
            "by_day": [
                {**day, "prs": [{"repo": p["repo"], "number": p["number"], "title": clean(p["title"]), "url": p["url"], "state": p["state"]}
                                for p in g["pull_requests"] if p["created"] == day["date"] and not private_repo(p["repo"])][:40]}
                for day in by_day
            ],
            "total": sum(days.values()),
            "public": c.get("public"),
            "private": c.get("private") if c.get("private_visible") else None,
            "days": days,
            "days_active": sum(1 for n in days.values() if n > 0),
            "days_active_public": sum(1 for n in public_days.values() if n > 0),
        } if c else None,
        "releases": releases,
        "births": births,
        "deaths": deaths,
        "recent": recent,
        "profile_links": (g.get("profile") or {}).get("links", []),
        "avatar": (g.get("profile") or {}).get("avatar"),
    }


def person_stats(d, person):
    collected = dt.date.fromisoformat(d["collected_at"][:10])
    start = (collected - dt.timedelta(days=WINDOW_DAYS - 1)).isoformat()
    g = github_stats(d["github"], start) if d.get("github") else None
    y = d.get("youtube")
    videos = [
        {"title": clean(v["title"]), "url": v["url"], "date": v["published"], "thumbnail": v.get("thumbnail"),
         "short": bool(v.get("short")), "views": v.get("views")}
        for v in (y or {}).get("videos", []) if v["published"] >= start and config.is_http_url(v.get("url"))
    ]
    recent = (g or {}).get("recent", []) + [{"type": "video", "title": v["title"], "url": v["url"], "date": v["date"], "repo": None} for v in videos]
    # Cap each kind so a hundred merged PRs cannot crowd out a release or a video.
    capped, counts = [], Counter()
    for item in sorted(recent, key=lambda i: i["date"], reverse=True):
        if counts[item["type"]] < RECENT_CAPS[item["type"]]:
            counts[item["type"]] += 1
            capped.append(item)

    own_links = list(person["links"])
    # Add the channel link unless they already list a YouTube link (often a nicer @handle address).
    if person["youtube_channel_id"] and not any(l["label"].lower() == "youtube" for l in own_links):
        own_links.append({"label": "YouTube", "url": f"https://www.youtube.com/channel/{person['youtube_channel_id']}"})
    out = {
        "id": person["id"],
        "github": person["github"],
        "name": person["name"],
        "window": {"start": start, "end": collected.isoformat()},
        "has_github": g is not None,
        "has_youtube": y is not None,
        "avatar": person["avatar"] or (g or {}).get("avatar"),
        "links": merge_links(own_links, (g or {}).get("profile_links", [])),
        "videos": videos,
        "youtube_channel": {"title": y["channel_title"], "url": y["channel_url"]} if y else None,
        "recent": capped,
        "errors": d.get("errors", []),
        "merged_prs": 0, "public_merged_prs": 0, "opened_prs": 0, "commits": 0, "commits_capped": False,
        "repos_touched": 0, "repos": [], "contributions": None, "releases": [], "births": [], "deaths": [],
    }
    if g:
        out.update({k: v for k, v in g.items() if k not in ("recent", "profile_links", "avatar")})
    return out


def build(paper, cfg):
    people = {p["id"]: p for p in cfg["people"]}
    out = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "people": {}}
    for f in sorted((paper / "data").glob("*.json")):
        d = json.loads(f.read_text())
        if d.get("id") in people:
            out["people"][d["id"]] = person_stats(d, people[d["id"]])
    if not out["people"]:
        raise SystemExit(f"No data for anyone in {paper / 'watchlist.toml'}; run collect.py first")
    out["date"] = max(p["window"]["end"] for p in out["people"].values())
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper", help="folder holding watchlist.toml (default: $PR_POST_PAPER or the repo root)")
    args = ap.parse_args()
    paper = config.paper_dir(args.paper)
    try:
        cfg = config.load(paper)
    except config.ConfigError as e:
        sys.exit(f"Config error: {e}")
    out = build(paper, cfg)
    (paper / "stats").mkdir(exist_ok=True)
    path = paper / "stats" / f"{out['date']}.json"
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print("Wrote", path)


if __name__ == "__main__":
    main()
