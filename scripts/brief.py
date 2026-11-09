#!/usr/bin/env python3
"""Write a short brief per person for an AI writer: <paper>/briefs/<id>.md.

Usage:
    python3 scripts/brief.py [--paper DIR]        # after collect.py and stats.py

A writer (docs/writers.md) used to explore data/<id>.json, which runs to
hundreds of kilobytes per busy person, with many jq calls. Every byte it reads
is paid for in tokens, again on every later turn of the session. The brief
holds what a report needs in a few kilobytes: the numbers from stats/ (copied,
never computed here), the busiest public repos, merged pull requests with the
start of their descriptions, releases, new and archived repos, videos, and the
README opening of busy repos the profile does not mention yet. The writer opens
data/ only when the brief is not enough.

Private repositories never appear by name; their work is a count, as on the site.
No AI and no network. No dependencies.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402

LIMITS = {"repos": 8, "prs": 25, "pr_bodies": 12, "body": 260, "open_prs": 8, "commits": 10, "releases": 8, "readme": 700, "readmes": 2}


def one_line(text, limit):
    text = re.sub(r"<!--.*?-->", " ", str(text or ""), flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)|<[^>]+>", " ", text)  # images and HTML tags
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 3].rsplit(" ", 1)[0] + "..."


def readme_opening(text, limit):
    """The first real paragraphs of a README: no badges, images, HTML or headings-only lines."""
    lines = [l for l in str(text or "").splitlines() if l.strip() and not re.match(r"^\s*(\[!\[|!\[|<|#|---|\|)", l)]
    return one_line(" ".join(lines), limit)


def brief(person, data, st, profile_text, reports):
    name, pid = person["name"], person["id"]
    out = [f"# Brief: {name} ({pid})", ""]
    gh = (data or {}).get("github") or {}
    yt = (data or {}).get("youtube") or {}
    out.append(f"Collected {(data or {}).get('collected_at', 'never')}; data covers items since {(data or {}).get('since', '?')}.")
    if st:
        out.append(f"Stats window {st['window']['start']} to {st['window']['end']} (stats/ is the only source for numbers).")
    if reports:
        weekly = next((r for r in reports if r.endswith("-weekly")), None)
        out.append(f"Newest report: {reports[0]}" + (f"; newest weekly: {weekly}" if weekly else "; no weekly yet") + ".")
    else:
        out.append("No reports yet.")
    out.append(f"Profile: {'profiles/' + pid + '.md' if profile_text else 'missing, write one from this brief and the READMEs'}.")
    if (data or {}).get("errors"):
        out.append(f"Collection errors: {'; '.join(map(str, data['errors']))[:300]}.")

    if st:
        out += ["", "## Numbers (copied from stats/)",
                f"- {st['public_merged_prs']} merged PRs, {st['opened_prs']} opened, {st['commits']}{'+' if st.get('commits_capped') else ''} commits, "
                f"{st['repos_touched']} repos touched, {len(st['releases'])} releases, {len(st['videos'])} videos."]
        c = st.get("contributions") or {}
        if c.get("public") is not None:
            out.append(f"- Contributions: {c['public']} public" + (f", {c['private']} private (count only, never describe it)" if c.get("private") else "") + ".")
    private = {r["repo"] for r in (st or {}).get("repos", []) if r.get("private")}
    public_repos = [r for r in (st or {}).get("repos", []) if not r.get("private")]
    if public_repos:
        out += ["", "## Busiest public repos"]
        out += [f"- {r['repo']}: {r['merged_prs']} merged PRs, {r['commits']} commits" for r in public_repos[: LIMITS["repos"]]]
        if len(public_repos) > LIMITS["repos"]:
            out.append(f"- plus {len(public_repos) - LIMITS['repos']} smaller repos")
    if private:
        out.append(f"- plus {len(private)} private repos (names and work unknown; counts only)")

    prs = [p for p in gh.get("pull_requests", []) if p["repo"] not in private]
    merged = sorted((p for p in prs if p.get("merged")), key=lambda p: p["merged"], reverse=True)
    if merged:
        out += ["", f"## Merged PRs, newest first ({len(merged)})"]
        for i, p in enumerate(merged[: LIMITS["prs"]]):
            body = one_line(p.get("body"), LIMITS["body"]) if i < LIMITS["pr_bodies"] else ""
            out.append(f"- {p['merged']} {p['repo']}#{p['number']} {one_line(p['title'], 140)} <{p['url']}>" + (f"\n  {body}" if body else ""))
        if len(merged) > LIMITS["prs"]:
            out.append(f"- plus {len(merged) - LIMITS['prs']} more in data/{pid}.json")
    still_open = [p for p in prs if p.get("state") == "open"]
    if still_open:
        out += ["", f"## Open PRs ({len(still_open)})"]
        out += [f"- {p['created']} {p['repo']}#{p['number']} {one_line(p['title'], 120)} <{p['url']}>" for p in still_open[: LIMITS["open_prs"]]]

    pr_repos = {p["repo"] for p in merged}
    lone = [c for c in gh.get("commits", []) if c["repo"] not in pr_repos and c["repo"] not in private]
    if lone:
        out += ["", "## Commits to repos with no merged PR (newest first)"]
        out += [f"- {c['date']} {c['repo']}: {one_line(c['message'].splitlines()[0] if c.get('message') else '', 110)} <{c['url']}>"
                for c in lone[: LIMITS["commits"]]]
        if len(lone) > LIMITS["commits"]:
            out.append(f"- plus {len(lone) - LIMITS['commits']} more")

    for title, items, fmt in (
        ("Releases", [r for r in (st or {}).get("releases", [])][: LIMITS["releases"]], lambda r: f"- {r['date']} {r['repo']} {r['name'] or r['tag']} <{r['url']}>"),
        ("New repos", (st or {}).get("births", []), lambda r: f"- {r.get('date', '')} {r['repo']}: {one_line(r.get('description'), 140)} <{r['url']}>"),
        ("Archived or gone", (st or {}).get("deaths", []), lambda r: f"- {r['repo']} <{r.get('url', '')}>"),
        ("Videos", (st or {}).get("videos", []), lambda v: f"- {v['date']} {one_line(v['title'], 140)} <{v['url']}>"),
    ):
        if items:
            out += ["", f"## {title}"] + [fmt(x) for x in items]

    readmes = gh.get("readmes") or {}
    known = (profile_text or "").lower()
    fresh = [(r, t) for r, t in readmes.items() if r not in private and r.split("/")[-1].lower() not in known][: LIMITS["readmes"]]
    if fresh:
        out += ["", "## READMEs of busy repos the profile does not mention"]
        out += [f"- {r}: {readme_opening(t, LIMITS['readme'])}" for r, t in fresh]
    if not yt.get("videos") and (person.get("youtube_channel_id") and not (st or {}).get("videos")):
        out += ["", "No new videos."]
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper", help="folder holding watchlist.toml (default: $PR_POST_PAPER or the repo root)")
    args = ap.parse_args()
    paper = config.paper_dir(args.paper)
    try:
        cfg = config.load(paper)
    except config.ConfigError as e:
        sys.exit(f"Config error: {e}")
    snaps = sorted((paper / "stats").glob("*.json"))
    people_stats = json.loads(snaps[-1].read_text())["people"] if snaps else {}
    folder = paper / "briefs"
    folder.mkdir(exist_ok=True)
    total = 0
    for p in cfg["people"]:
        pid = p["id"]
        try:
            data = json.loads((paper / "data" / f"{pid}.json").read_text())
        except (OSError, ValueError):
            data = None
        prof = paper / "profiles" / f"{pid}.md"
        reports = sorted((f.stem for f in (paper / "reports" / pid).glob("*.md")), reverse=True)
        text = brief(p, data, people_stats.get(pid), prof.read_text() if prof.exists() else "", reports)
        (folder / f"{pid}.md").write_text(text)
        total += len(text.encode())
    for stale in folder.glob("*.md"):
        if stale.stem not in {p["id"] for p in cfg["people"]}:
            stale.unlink()
    print(f"Wrote {len(cfg['people'])} briefs into {folder} ({total // 1024} KB)")


if __name__ == "__main__":
    main()
