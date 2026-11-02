#!/usr/bin/env python3
"""Collect public activity for everyone in a paper's watchlist.toml as JSON.

Usage:
    python3 scripts/collect.py [--paper DIR] [--since YYYY-MM-DD]        # everyone -> <paper>/data/<id>.json
    python3 scripts/collect.py [--paper DIR] --only <id> [--out FILE]   # one person

Sources:
    GitHub   pull requests, issues, commits, events, repos, releases and the
             contribution graph. Uses GITHUB_TOKEN when set (Actions provides
             one); otherwise runs unauthenticated (60 requests an hour, and the
             contribution graph is skipped because GraphQL needs a token).
    YouTube  the channel's public RSS feed for anyone with youtube_channel_id.
             No key.

A source that fails is recorded under "errors" in that person's file so one
flaky feed does not sink the whole run. A YouTube feed that fails keeps the
videos from the previous run (stats.py still drops any older than the week),
so a flaky feed never blanks someone's page. feed.py --check fails the build
if the result has nothing in it at all. No dependencies.
"""

import argparse
import base64
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import youtube  # noqa: E402

API = "https://api.github.com"
BODY_LIMIT = 800
MAX_SEARCH_PAGES = 5
README_REPOS = 6
README_LIMIT = 4000
RELEASE_REPOS = 6
DEFAULT_DAYS = 8


def get(path, params=None):
    url = path if path.startswith("http") else f"{API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "pr-post"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            # Search API allows only 10 unauthenticated requests per minute.
            if e.code in (403, 429) and attempt < 2:
                time.sleep(int(e.headers.get("Retry-After", 30)))
                continue
            if e.code == 404:
                return None
            detail = e.read().decode(errors="replace")[:300]
            sys.exit(f"GitHub API {e.code} on {url}: {detail} (rate limit remaining: {e.headers.get('X-RateLimit-Remaining')}, server: {e.headers.get('Server')})")


def graphql(query, variables):
    headers = {"Content-Type": "application/json", "User-Agent": "pr-post"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    else:
        return None  # GitHub's GraphQL API requires a token
    req = urllib.request.Request(f"{API}/graphql", data=json.dumps({"query": query, "variables": variables}).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.load(r)
    if body.get("errors"):
        sys.exit(f"GitHub GraphQL error: {body['errors'][:2]}")
    return body["data"]


CONTRIBUTIONS = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions totalPullRequestContributions totalIssueContributions
      totalPullRequestReviewContributions restrictedContributionsCount hasAnyRestrictedContributions
      contributionCalendar { weeks { contributionDays { date contributionCount } } }
    }
  }
}"""


def contributions(user, since):
    """Daily totals and public/private split from the contribution graph.

    Private work shows up only as a count, and only for people who chose to
    show private contributions on their profile.
    """
    now = dt.datetime.now(dt.timezone.utc)
    data = graphql(CONTRIBUTIONS, {"login": user, "from": f"{since.isoformat()}T00:00:00Z", "to": now.isoformat(timespec="seconds")})
    if not data or not data.get("user"):
        return None
    c = data["user"]["contributionsCollection"]
    by_day = contributions_by_day(user, since)
    days = {d["date"]: d["contributionCount"] for w in c["contributionCalendar"]["weeks"] for d in w["contributionDays"]}
    public = (c["totalCommitContributions"] + c["totalPullRequestContributions"]
              + c["totalIssueContributions"] + c["totalPullRequestReviewContributions"])
    return {
        "public": public,
        "private": c["restrictedContributionsCount"],
        "private_visible": c["hasAnyRestrictedContributions"],
        "commits": c["totalCommitContributions"],
        "pull_requests": c["totalPullRequestContributions"],
        "issues": c["totalIssueContributions"],
        "reviews": c["totalPullRequestReviewContributions"],
        "days": {d: n for d, n in sorted(days.items()) if d >= since.isoformat()},
        "by_day": by_day,
    }


SOCIAL_LABELS = {"twitter": "X", "youtube": "YouTube", "bluesky": "Bluesky", "mastodon": "Mastodon",
                 "linkedin": "LinkedIn", "instagram": "Instagram", "twitch": "Twitch", "reddit": "Reddit",
                 "facebook": "Facebook", "hometown": "Website", "npm": "npm"}


def social_links(user):
    """The website and social accounts a person lists on their GitHub profile."""
    profile = get(f"/users/{user}") or {}
    links = []

    def add(label, url):
        key = url.lower().rstrip("/").replace("://twitter.com/", "://x.com/").split("://", 1)[-1].removeprefix("www.")
        if url and all(l["key"] != key for l in links):
            links.append({"label": label, "url": url, "key": key})

    if profile.get("twitter_username"):
        add("X", f"https://x.com/{profile['twitter_username']}")
    for acct in get(f"/users/{user}/social_accounts") or []:
        provider, url = acct.get("provider", "generic"), acct.get("url", "")
        if provider == "generic":
            add(urllib.parse.urlparse(url).netloc.removeprefix("www.") or "Link", url)
        else:
            add(SOCIAL_LABELS.get(provider, provider.title()), url.replace("://twitter.com/", "://x.com/"))
    if blog := (profile.get("blog") or "").strip():
        url = blog if blog.startswith("http") else f"https://{blog}"
        add(urllib.parse.urlparse(url).netloc.removeprefix("www.") or "Website", url)
    return {"bio": profile.get("bio"), "avatar": profile.get("avatar_url"),
            "links": [{"label": l["label"], "url": l["url"]} for l in links]}


DAY_FIELDS = """
      totalCommitContributions totalPullRequestContributions totalIssueContributions
      totalPullRequestReviewContributions restrictedContributionsCount
      pullRequestContributionsByRepository(maxRepositories: 25) { repository { nameWithOwner } contributions { totalCount } }"""


def contributions_by_day(user, start, days=7):
    """Public work by type and private work as one count, for each UTC day.

    GitHub itemises public commits, PRs, issues and reviews, but private work
    is a single number per day with no type or repo.
    """
    dates = [start + dt.timedelta(days=i) for i in range(days)]
    fields = "".join(
        f'd{i}: contributionsCollection(from: "{d}T00:00:00Z", to: "{d}T23:59:59Z") {{{DAY_FIELDS}}}\n'
        for i, d in enumerate(dates)
    )
    data = graphql(f'query($login: String!) {{ user(login: $login) {{ {fields} }} }}', {"login": user})
    if not data or not data.get("user"):
        return None
    out = []
    for i, d in enumerate(dates):
        c = data["user"][f"d{i}"]
        out.append({
            "date": d.isoformat(),
            "public": {"commits": c["totalCommitContributions"], "pull_requests": c["totalPullRequestContributions"],
                       "issues": c["totalIssueContributions"], "reviews": c["totalPullRequestReviewContributions"]},
            "private": c["restrictedContributionsCount"],
            "pr_repos": {r["repository"]["nameWithOwner"]: r["contributions"]["totalCount"]
                         for r in c["pullRequestContributionsByRepository"]},
        })
    return out


def search(kind, query):
    items, total = [], 0
    for page in range(1, MAX_SEARCH_PAGES + 1):
        data = get(f"/search/{kind}", {"q": query, "per_page": 100, "page": page, "sort": "updated" if kind == "issues" else "committer-date"})
        items += data["items"]
        total = data["total_count"]
        if len(items) >= total or not data["items"]:
            break
    return items, total


def repo_of(repository_url):
    # https://api.github.com/repos/OWNER/NAME -> OWNER/NAME
    return repository_url.split("/repos/", 1)[1]



def collect_github(user, orgs, since, previous=None):
    since_iso = since.isoformat()

    prs = []
    pr_items, pr_total = search("issues", f"author:{user} type:pr updated:>={since_iso}")
    for it in pr_items:
        pr = it.get("pull_request") or {}
        prs.append({
            "repo": repo_of(it["repository_url"]),
            "number": it["number"],
            "title": it["title"],
            "state": "merged" if pr.get("merged_at") else it["state"],
            "created": it["created_at"][:10],
            "merged": (pr.get("merged_at") or "")[:10] or None,
            "url": it["html_url"],
            "body": (it.get("body") or "")[:BODY_LIMIT],
        })

    issues = [
        {"repo": repo_of(it["repository_url"]), "number": it["number"], "title": it["title"],
         "state": it["state"], "created": it["created_at"][:10], "url": it["html_url"]}
        for it in search("issues", f"author:{user} type:issue created:>={since_iso}")[0]
    ]

    commit_items, commit_total = search("commits", f"author:{user} committer-date:>={since_iso}")
    commits = [
        {"repo": c["repository"]["full_name"], "date": c["commit"]["committer"]["date"][:10],
         "message": c["commit"]["message"].split("\n")[0], "url": c["html_url"]}
        for c in commit_items
    ]

    # Recent events catch activity search misses (reviews, comments, branch work).
    events_by_repo = {}
    for page in range(1, 4):
        batch = get(f"/users/{user}/events/public", {"per_page": 100, "page": page}) or []
        fresh = [e for e in batch if e["created_at"][:10] >= since_iso]
        for e in fresh:
            counts = events_by_repo.setdefault(e["repo"]["name"], {})
            counts[e["type"]] = counts.get(e["type"], 0) + 1
        if len(fresh) < len(batch) or not batch:
            break

    # Repos the person or their orgs own. Births and deaths come only from
    # these, never from repos they merely starred or commented on.
    owner_list = [("users", user)] + [("orgs", o) for o in orgs]
    repos, owned = {}, set()
    for kind, owner in owner_list:
        for r in get(f"/{kind}/{owner}/repos", {"per_page": 100, "sort": "pushed"}) or []:
            repos[r["full_name"]] = r
            owned.add(r["full_name"])
    active = {p["repo"] for p in prs} | {c["repo"] for c in commits} | set(events_by_repo)
    for name in active - repos.keys():
        if r := get(f"/repos/{name}"):
            repos[name] = r

    def meta(r):
        return {"description": r.get("description"), "language": r.get("language"),
                "stars": r.get("stargazers_count"), "created": r["created_at"][:10],
                "pushed": (r.get("pushed_at") or "")[:10], "archived": r.get("archived"),
                "fork": r.get("fork"), "private": r.get("private"), "url": r["html_url"]}

    # READMEs of the busiest repos, so the report writer can explain them.
    activity = {}
    for item in prs + commits:
        activity[item["repo"]] = activity.get(item["repo"], 0) + 1
    readmes = {}
    for name in sorted(activity, key=activity.get, reverse=True)[:README_REPOS]:
        if (r := get(f"/repos/{name}/readme")) and r.get("content"):
            readmes[name] = base64.b64decode(r["content"]).decode(errors="replace")[:README_LIMIT]

    # Archiving leaves no event, so a death is a repo that was not archived in
    # the previous snapshot and is now. The first snapshot reports none.
    previous = previous or {}
    states = {n: bool(repos[n].get("archived")) for n in sorted(owned)}
    died = [n for n, archived in states.items() if archived and previous.get(n) is False]

    # Releases on the busiest repos they or their orgs own.
    releases = []
    busiest_owned = [n for n in sorted(activity, key=activity.get, reverse=True) if n in owned][:RELEASE_REPOS]
    for name in busiest_owned:
        for rel in get(f"/repos/{name}/releases", {"per_page": 5}) or []:
            published = (rel.get("published_at") or "")[:10]
            if rel.get("draft") or not published or published < since_iso:
                continue
            releases.append({"repo": name, "tag": rel.get("tag_name"), "name": rel.get("name") or rel.get("tag_name"),
                             "published": published, "prerelease": bool(rel.get("prerelease")), "url": rel.get("html_url")})

    return {
        "user": user,
        "orgs": orgs,
        "since": since_iso,
        "pull_requests": prs,
        "issues_opened": issues,
        "commits": commits,
        "events_by_repo": events_by_repo,
        "active_repos": {n: meta(repos[n]) for n in sorted(active) if n in repos},
        "new_repos": {n: meta(repos[n]) for n in sorted(owned) if repos[n]["created_at"][:10] >= since_iso and not repos[n].get("fork")},
        "readmes": readmes,
        "releases": releases,
        "archived_repos": {n: meta(repos[n]) for n in died},
        "repo_states": states,
        "totals": {"pull_requests": pr_total, "commits": commit_total, "commits_capped": commit_total > len(commits)},
        # Always the last 7 days, so weeks compare like for like.
        "contributions": contributions(user, dt.date.today() - dt.timedelta(days=6)),
        "profile": social_links(user),
    }


def collect_person(person, since, previous=None):
    """One person's data file: GitHub and YouTube blocks, either of which may be null.

    `previous` is this person's data file from the last run, if any: it carries
    repository states forward (to spot archiving) and stands in for a YouTube
    feed that fails this time.
    """
    previous = previous or {}
    out = {
        "id": person["id"],
        "since": since.isoformat(),
        "collected_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "github": None,
        "youtube": None,
        "errors": [],
    }
    if person["github"]:
        out["github"] = collect_github(person["github"], person["orgs"], since, (previous.get("github") or {}).get("repo_states"))
    if person["youtube_channel_id"]:
        try:
            out["youtube"] = youtube.collect(person["youtube_channel_id"], since)
        except youtube.FeedError as e:
            out["errors"].append({"source": "youtube", "message": str(e)})
            last = previous.get("youtube")
            if last and last.get("channel_id") == person["youtube_channel_id"]:
                out["youtube"] = {**last, "carried_over_from": last.get("carried_over_from") or previous.get("collected_at")}
            kept = " (kept the previous run's videos)" if out["youtube"] else ""
            print(f"::warning::YouTube feed for {person['id']} failed: {e}{kept}", file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper", help="folder holding watchlist.toml (default: $PR_POST_PAPER or the repo root)")
    ap.add_argument("--only", help="collect one person by id")
    ap.add_argument("--since", help=f"YYYY-MM-DD, default {DEFAULT_DAYS} days ago")
    ap.add_argument("--out", help="with --only: write here instead of stdout")
    args = ap.parse_args()

    paper = config.paper_dir(args.paper)
    try:
        cfg = config.load(paper)
    except config.ConfigError as e:
        sys.exit(f"Config error: {e}")
    since = dt.date.fromisoformat(args.since) if args.since else dt.date.today() - dt.timedelta(days=DEFAULT_DAYS)

    if args.only:
        person = next((p for p in cfg["people"] if p["id"] == args.only.lower()), None)
        if not person:
            sys.exit(f"{args.only} is not in {paper / 'watchlist.toml'}")
        data = json.dumps(collect_person(person, since), indent=1, ensure_ascii=False)
        if args.out:
            Path(args.out).write_text(data)
        else:
            sys.stdout.write(data)
        return

    if not cfg["people"]:
        sys.exit("watchlist.toml has no [[people]] to follow")
    (paper / "data").mkdir(exist_ok=True)
    for person in cfg["people"]:
        out = paper / "data" / f"{person['id']}.json"
        previous = json.loads(out.read_text()) if out.exists() else None
        out.write_text(json.dumps(collect_person(person, since, previous), indent=1, ensure_ascii=False))
        print("Wrote", out)


if __name__ == "__main__":
    main()
