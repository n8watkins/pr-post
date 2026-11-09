#!/usr/bin/env python3
"""Who to follow: find builders worth following, research each once, and score the fit.

Usage:
    python3 scripts/follow.py [--paper DIR] run [--max-new N] [--preview]
    python3 scripts/follow.py [--paper DIR] report <login> [--refresh]
    python3 scripts/follow.py [--paper DIR] draft <login>

run      Finds candidates, writes a mini report for up to --max-new new ones
         (default [follow] max_new), scores everyone not yet followed, and
         picks this week's suggestions. --preview picks without recording
         them, so a trial run does not start anyone's cooldown.
report   Prints one person's mini report, researching them first if there is
         no cached report (or --refresh is given).
draft    The "Add to PR Post" step: drafts their watch-list entry and profile
         into the paper with research_person.py, for a pull request.

Settings live under [follow] in watchlist.toml (docs/who-to-follow.md).
Everything is read-only public GitHub data plus one visit to the website a
person lists on their profile. Nothing is ever followed, starred or written
on anyone's account. Needs GITHUB_TOKEN (any token; it is used for reading
public data and GraphQL, and never printed).

Files, all inside the paper:
    follow/people/<login>.json   one cached mini report per person, never redone unless asked
    follow/state.json            who was seen, suggested and when, and how they were found
    follow/latest.json           the last run: scores, breakdowns and this week's picks
"""

import argparse
import datetime as dt
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import follow_lib as fl  # noqa: E402
import research_person as rp  # noqa: E402

API = "https://api.github.com"
UA = "pr-post who-to-follow (+https://github.com/n8watkins/pr-post)"
BOT_RE = re.compile(r"(\[bot\]$|[-_]bot$|^bot[-_]|^(dependabot|renovate|github-actions|copilot|codecov|vercel|netlify|changeset-bot|ghost)$)", re.I)
PERSON_SECONDS = 30   # wall-clock budget per person researched
PERSON_FETCHES = 2    # web pages per person: their site and its feed
PULSE_BATCH = 20      # people per GraphQL request when refreshing scores


class OutOfBudget(Exception):
    pass


class GitHub:
    """A read-only GitHub client that counts requests and stops before the rate limit does."""

    def __init__(self, token, budget, reserve=150, opener=urllib.request.urlopen):
        self.token, self.budget, self.reserve, self.used, self.open = token, budget, reserve, 0, opener
        self.remaining = None

    def _call(self, req):
        if self.used >= self.budget:
            raise OutOfBudget(f"used the run's budget of {self.budget} GitHub requests")
        if self.remaining is not None and self.remaining < self.reserve:
            raise OutOfBudget(f"GitHub rate limit nearly spent ({self.remaining} left)")
        for attempt in (1, 2):
            self.used += 1
            try:
                with self.open(req, timeout=30) as r:
                    rem = r.headers.get("X-RateLimit-Remaining")
                    # The search API has its own small limit, paced separately.
                    if rem and rem.isdigit() and r.headers.get("X-RateLimit-Resource") != "search":
                        self.remaining = int(rem)
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                wait = e.headers.get("Retry-After")
                if e.code in (403, 429) and (wait or e.headers.get("X-RateLimit-Remaining") == "0"):
                    if attempt == 1 and wait and wait.isdigit() and int(wait) <= 60:
                        time.sleep(int(wait))
                        continue
                    raise OutOfBudget(f"GitHub rate limit hit ({e.code})") from e
                if e.code >= 500 and attempt == 1:
                    time.sleep(2)
                    continue
                if e.code == 422:
                    return None  # a search GitHub cannot run; skip it
                raise
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt == 1:
                    time.sleep(2)
                    continue
                raise OutOfBudget(f"GitHub unreachable: {e}") from e
        return None

    def headers(self):
        h = {"Accept": "application/vnd.github+json", "User-Agent": UA}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def rest(self, path, params=None):
        url = f"{API}{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
        return self._call(urllib.request.Request(url, headers=self.headers()))

    def graphql(self, query, variables=None):
        if not self.token:
            raise OutOfBudget("GraphQL needs GITHUB_TOKEN")
        body = json.dumps({"query": query, "variables": variables or {}}).encode()
        data = self._call(urllib.request.Request(f"{API}/graphql", data=body, headers={**self.headers(), "Content-Type": "application/json"}))
        return (data or {}).get("data") or {}


REPO_FIELDS = """nameWithOwner name url description stargazerCount pushedAt isArchived primaryLanguage { name }
  languages(first: 4, orderBy: {field: SIZE, direction: DESC}) { nodes { name } }
  repositoryTopics(first: 10) { nodes { topic { name } } }"""

USER_QUERY = f"""query($login: String!, $from: DateTime!, $to: DateTime!) {{ user(login: $login) {{
  login name bio company location websiteUrl twitterUsername avatarUrl(size: 160) createdAt
  followers {{ totalCount }}
  socialAccounts(first: 10) {{ nodes {{ provider url }} }}
  organizations(first: 20) {{ nodes {{ login }} }}
  pinnedItems(first: 6, types: REPOSITORY) {{ nodes {{ ... on Repository {{ {REPO_FIELDS} latestRelease {{ name tagName publishedAt url }} }} }} }}
  top: repositories(first: 10, ownerAffiliations: OWNER, isFork: false, privacy: PUBLIC, orderBy: {{field: STARGAZERS, direction: DESC}}) {{
    totalCount nodes {{ {REPO_FIELDS} latestRelease {{ name tagName publishedAt url }} }} }}
  recent: repositories(first: 10, ownerAffiliations: OWNER, isFork: false, privacy: PUBLIC, orderBy: {{field: PUSHED_AT, direction: DESC}}) {{
    nodes {{ {REPO_FIELDS} latestRelease {{ name tagName publishedAt url }} }} }}
  contributed: repositoriesContributedTo(first: 12, contributionTypes: [COMMIT, PULL_REQUEST, PULL_REQUEST_REVIEW], includeUserRepositories: false,
    orderBy: {{field: STARGAZERS, direction: DESC}}) {{ nodes {{ {REPO_FIELDS} }} }}
  contributionsCollection(from: $from, to: $to) {{ contributionCalendar {{ weeks {{ contributionDays {{ contributionCount }} }} }} }}
}} }}"""

MY_QUERY = f"""query($login: String!) {{ user(login: $login) {{
  repositories(first: 100, ownerAffiliations: OWNER, isFork: false, privacy: PUBLIC, orderBy: {{field: PUSHED_AT, direction: DESC}}) {{
    nodes {{ {REPO_FIELDS} }} }} }} }}"""

COLLAB_QUERY = """query($authored: String!, $reviewed: String!) {
  authored: search(query: $authored, type: ISSUE, first: 30) { nodes { ... on PullRequest { url reviews(first: 10) { nodes { author { login __typename } } } } } }
  reviewed: search(query: $reviewed, type: ISSUE, first: 30) { nodes { ... on PullRequest { url author { login __typename } } } }
}"""


def is_bot(login, kind="User"):
    return kind != "User" or bool(BOT_RE.search(login or ""))


# ---------- discovery ----------

def discover(gh, people, fcfg, today, log=print):
    """{login_lower: signals} from every configured source. Stops a source early when out of budget."""
    since = today - dt.timedelta(days=90)
    found = {}

    def sig(login):
        return found.setdefault(login.lower(), {"login": login, "followed_by": [], "collab": [], "stack": {}, "similar": []})

    builders = [p for p in people if p["github"] and not p["self"]]
    try:
        for b in builders:
            for page in range(1, fcfg["following_pages"] + 1):
                rows = gh.rest(f"/users/{b['github']}/following", {"per_page": 100, "page": page}) or []
                for u in rows:
                    if not is_bot(u.get("login"), u.get("type")) and b["id"] not in sig(u["login"])["followed_by"]:
                        sig(u["login"])["followed_by"].append(b["id"])
                if len(rows) < 100:
                    break
        log(f"  following lists of {len(builders)} builders: {len(found)} people")

        for b in builders:
            q = f"is:pr is:public created:>{since.isoformat()}"
            data = gh.graphql(COLLAB_QUERY, {"authored": f"author:{b['github']} {q}", "reviewed": f"reviewed-by:{b['github']} -author:{b['github']} {q}"})
            for pr in ((data.get("authored") or {}).get("nodes") or []):
                for rv in ((pr or {}).get("reviews") or {}).get("nodes") or []:
                    a = (rv or {}).get("author") or {}
                    if a.get("login") and a["login"].lower() != b["github"].lower() and not is_bot(a["login"], a.get("__typename")):
                        add_collab(sig(a["login"]), b["id"], "reviewed their pull request", pr["url"])
            for pr in ((data.get("reviewed") or {}).get("nodes") or []):
                a = (pr or {}).get("author") or {}
                if a.get("login") and not is_bot(a["login"], a.get("__typename")):
                    add_collab(sig(a["login"]), b["id"], "had a pull request reviewed by them", pr["url"])
        log(f"  reviews with builders: {len(found)} people so far")

        for repo in fcfg["stack_repos"]:
            rows = gh.rest(f"/repos/{repo}/commits", {"since": f"{since.isoformat()}T00:00:00Z", "per_page": 100}) or []
            for c in rows:
                a = (c or {}).get("author") or {}
                if a.get("login") and not is_bot(a["login"], a.get("type")):
                    s = sig(a["login"])["stack"]
                    s[repo] = s.get(repo, 0) + 1
        log(f"  committers to {len(fcfg['stack_repos'])} stack repos: {len(found)} people so far")

        for query in fcfg["similar_queries"]:
            res = gh.rest("/search/repositories", {"q": f"{query} pushed:>{since.isoformat()}", "sort": "stars", "per_page": 20}) or {}
            for r in res.get("items") or []:
                o = r.get("owner") or {}
                if o.get("login") and not is_bot(o["login"], o.get("type")):
                    s = sig(o["login"])["similar"]
                    if len(s) < 3 and not any(x["repo"] == r["full_name"] for x in s):
                        s.append({"repo": r["full_name"], "url": r["html_url"], "query": query})
            time.sleep(2.1)  # the search API allows 30 requests a minute
        log(f"  similar projects from {len(fcfg['similar_queries'])} searches: {len(found)} people in all")
    except OutOfBudget as e:
        log(f"  discovery stopped early: {e}")
    return found


def add_collab(s, builder, how, url):
    if len(s["collab"]) < 6 and config.is_http_url(url) and not any(c["url"] == url for c in s["collab"]):
        s["collab"].append({"with": builder, "how": how, "url": url})


def prior(sg):
    """A cheap ranking from discovery alone, to choose whom to research first."""
    return (3 * min(len(sg.get("followed_by") or []), 5) + 3 * min(len({c["with"] for c in sg.get("collab") or []}), 3)
            + sum(2 * math.log2(1 + n) for n in (sg.get("stack") or {}).values()) + 2 * min(len(sg.get("similar") or []), 2))


def choose(keys, signals, n):
    """Whom to research this run: the best by prior() from each source in turn, so one busy source cannot crowd out the rest."""
    def has(source):
        return lambda k: bool(signals[k].get(source)) or (source == "followed_by" and bool(signals[k].get("collab")))
    buckets = [sorted(filter(has(src), keys), key=lambda k: (-prior(signals[k]), k)) for src in ("followed_by", "stack", "similar")]
    out = []
    while len(out) < n and any(buckets):
        for b in buckets:
            while b and b[0] in out:
                b.pop(0)
            if b and len(out) < n:
                out.append(b.pop(0))
    return out


def merge_signals(old, new):
    out = {"followed_by": sorted(set(old.get("followed_by", [])) | set(new.get("followed_by", []))),
           "collab": (new.get("collab") or []) + [c for c in old.get("collab", []) if c["url"] not in {x["url"] for x in new.get("collab") or []}],
           "stack": {**old.get("stack", {}), **new.get("stack", {})},
           "similar": new.get("similar") or old.get("similar", [])}
    out["collab"] = out["collab"][:6]
    return out


# ---------- research ----------

def research(gh, login, today, vocab, fetch=rp.fetch):
    """One mini report, within PERSON_SECONDS and PERSON_FETCHES. None when the login is not a person."""
    start = time.monotonic()
    data = gh.graphql(USER_QUERY, {"login": login, **window(today)})
    user = data.get("user")
    if not user:
        return None
    site, socials, posts, fetches = None, [], [], 0
    website = fl.url_or_none(user.get("websiteUrl"))
    if website and not rp.on_host(website, ("github.com",) + rp.BOT_WALLED):
        status, final, page = fetch(website, timeout=10)
        fetches += 1
        if 200 <= status < 400 and page and config.is_http_url(final):
            site = {"url": final, **fl.page_info(page, final)}
            found = rp.site_links(page, final)
            tokens = rp.name_tokens(login, user.get("name") or "", user.get("twitterUsername") or "")
            for s in found["socials"]:
                if s["label"] == "GitHub":
                    continue
                if s["source"] == "rel=me":
                    socials.append({"label": s["label"], "url": s["url"], "verified_by": "rel=me link on their website"})
                elif rp.looks_like_theirs(s["url"], tokens):
                    socials.append({"label": s["label"], "url": s["url"], "verified_by": "linked from their website, handle matches"})
            if found["feeds"] and fetches < PERSON_FETCHES and time.monotonic() - start < PERSON_SECONDS - 10:
                fstatus, _, ftext = fetch(found["feeds"][0], timeout=10)
                if 200 <= fstatus < 400:
                    posts = fl.work_posts(fl.parse_feed(ftext), vocab)
    return fl.mini_report(user, today, site, socials, posts, vocab)


def window(today):
    """The 26 weeks of contributions the score reads. `to` must be set: GitHub otherwise counts a year from `from`."""
    return {"from": f"{(today - dt.timedelta(weeks=26)).isoformat()}T00:00:00Z", "to": f"{today.isoformat()}T23:59:59Z"}


def pulse(gh, logins, today):
    """{login_lower: {followers, weeks}} for many people in few requests, to refresh scores of cached reports."""
    out, w = {}, window(today)
    logins = [x for x in logins if config.GITHUB_RE.match(x)]
    for i in range(0, len(logins), PULSE_BATCH):
        chunk = logins[i:i + PULSE_BATCH]
        q = "query {" + " ".join(
            f'u{j}: user(login: "{x}") {{ login followers {{ totalCount }} contributionsCollection(from: "{w['from']}", to: "{w['to']}") '
            f'{{ contributionCalendar {{ weeks {{ contributionDays {{ contributionCount }} }} }} }} }}' for j, x in enumerate(chunk)) + "}"
        try:
            data = gh.graphql(q)
        except OutOfBudget:
            break
        for node in data.values():
            if node:
                out[node["login"].lower()] = {"followers": node["followers"]["totalCount"],
                                              "weeks": fl.weekly_totals(node["contributionsCollection"]["contributionCalendar"])}
    return out


def following(gh, login):
    """Everyone `login` follows, lowercased. Public data; read-only."""
    out, page = set(), 1
    while True:
        rows = gh.rest(f"/users/{login}/following", {"per_page": 100, "page": page}) or []
        out |= {u["login"].lower() for u in rows}
        if len(rows) < 100 or page >= 50:
            return out
        page += 1


def my_repos(gh, login):
    data = gh.graphql(MY_QUERY, {"login": login})
    return [fl._repo(n) for n in (((data.get("user") or {}).get("repositories") or {}).get("nodes") or []) if n]


# ---------- files ----------

def folder(paper):
    return Path(paper) / "follow"


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=False) + "\n")


def report_path(paper, login):
    return folder(paper) / "people" / f"{login.lower()}.json"


def signal_sources(sg, names):
    """How someone was found, each line with its source URL."""
    out = []
    for b in sg.get("followed_by") or []:
        out.append({"text": f"Followed by {names.get(b, b)}", "url": f"https://github.com/{b}?tab=following"})
    for c in (sg.get("collab") or [])[:3]:
        out.append({"text": f"{c['how'].capitalize()} ({names.get(c['with'], c['with'])})", "url": c["url"]})
    for repo, n in sorted((sg.get("stack") or {}).items(), key=lambda kv: -kv[1]):
        out.append({"text": f"{n} commit{'s' if n != 1 else ''} to {repo} in the last 90 days", "url": f"https://github.com/{repo}/commits?author={sg.get('login', '')}"})
    for s in sg.get("similar") or []:
        out.append({"text": f"Builds {s['repo']}, found by searching {s['query']}", "url": s["url"]})
    return [o for o in out if config.is_http_url(o["url"])]


# ---------- run ----------

def run(paper, cfg, gh, today, max_new=None, preview=False, log=print):
    fcfg = cfg["follow"]
    me = fcfg["me"]
    names = {p["id"]: p["name"] for p in cfg["people"]}
    names.update({p["github"].lower(): p["name"] for p in cfg["people"] if p["github"]})
    watch = {p["github"].lower() for p in cfg["people"] if p["github"]}
    state = load_json(folder(paper) / "state.json", {"version": 1, "people": {}})
    vocab = fl.vocabulary(fcfg["tech"])

    log(f"Reading {me}'s stack and who they follow")
    mine = fl.my_stack(my_repos(gh, me), vocab, today)
    follows = following(gh, me)
    log(f"  {len(follows)} followed; stack: {', '.join(list(mine)[:8])}")

    log("Finding candidates")
    found = discover(gh, cfg["people"], fcfg, today, log)
    for key, sg in found.items():
        st = state["people"].setdefault(key, {"login": sg["login"], "first_seen": today.isoformat()})
        st["signals"] = merge_signals(st.get("signals") or {}, sg)
        st["seen"] = today.isoformat()

    for key, st in state["people"].items():
        if key in follows:
            st["status"] = "followed"
        elif key in watch or key == me.lower():
            st["status"] = "watching"
        elif key in fcfg["ignore"]:
            st["status"] = "ignored"
        elif st.get("status") != "not-a-person":
            st["status"] = "candidate"
    pool = [k for k, st in state["people"].items() if st["status"] == "candidate"]
    cached = {k for k in pool if report_path(paper, k).exists()}
    todo = choose([k for k in pool if k not in cached], {k: state["people"][k].get("signals") or {} for k in pool},
                  fcfg["max_new"] if max_new is None else max_new)
    log(f"{len(pool)} candidates after excluding follows, the watch list, orgs and bots; {len(cached)} already researched, researching {len(todo)}")

    fresh = set()
    for key in todo:
        login = state["people"][key]["login"]
        try:
            rep = research(gh, login, today, vocab)
        except OutOfBudget as e:
            log(f"  stopped researching: {e}")
            break
        if rep is None:
            state["people"][key]["status"] = "not-a-person"
            continue
        write_json(report_path(paper, key), rep)
        fresh.add(key)
        log(f"  researched {login}")

    reports = {k: load_json(report_path(paper, k), None) for k in pool}
    reports = {k: r for k, r in reports.items() if r}
    stale = sorted((k for k in reports if k not in fresh), key=lambda k: -(state["people"][k].get("score") or 0))[:60]
    pulses = pulse(gh, [reports[k]["login"] for k in stale], today) if stale else {}

    scored = []
    for key, rep in reports.items():
        st = state["people"][key]
        sg = {**(st.get("signals") or {}), "login": rep["login"]}
        s = fl.score(rep, sg, mine, fcfg, names, pulses.get(key))
        st["score"], st["scored_at"] = s["score"], today.isoformat()
        scored.append({"login": rep["login"], "name": rep["name"], "avatar": rep.get("avatar"), "profile": rep["profile"],
                       "researched": rep["researched"], **s, "found": signal_sources(sg, names)})
    scored.sort(key=lambda c: (-c["score"], c["login"].lower()))

    if preview:
        picks = fl.pick(scored, json.loads(json.dumps(state["people"])), fcfg, today)
    else:
        picks = fl.pick(scored, state["people"], fcfg, today)
    latest = {"date": today.isoformat(), "me": me, "preview": preview, "stack": list(mine)[:10],
              "picks": picks, "requests": gh.used, "candidates": scored}
    write_json(folder(paper) / "latest.json", latest)
    write_json(folder(paper) / "state.json", state)
    log(f"Scored {len(scored)}; picks: {', '.join(picks) or 'none'}; {gh.used} GitHub requests")
    return latest


# ---------- draft (Add to PR Post) ----------

def draft(paper, cfg, login, known_for=None):
    """Add `login` to watchlist.toml and write profiles/<id>.md from research_person.py's draft."""
    if not config.GITHUB_RE.match(login):
        sys.exit(f"{login!r} is not a GitHub username")
    if any((p["github"] or "").lower() == login.lower() for p in cfg["people"]):
        sys.exit(f"{login} is already on the watch list")
    r = rp.research(login, paper=paper)
    login = r["facts"]["login"]
    latest = load_json(folder(paper) / "latest.json", {})
    cand = next((c for c in latest.get("candidates", []) if c["login"].lower() == login.lower()), None)
    known_for = fl.clean(known_for or (cand or {}).get("why_follow") or r["facts"]["bio"] or "TODO", 200)
    block = rp.watchlist_block(r, known_for)
    wl = Path(paper) / "watchlist.toml"
    lines = wl.read_text().splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == "[[people]]"]
    at = len(lines)
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(lines)
        if any(re.match(r"\s*self\s*=\s*true", line) for line in lines[start:end]):
            at = start
            break
    new = lines[:at] + block + [""] + lines[at:]
    text = "\n".join(new) + "\n"
    config.parse(text)  # refuses to write an entry that would not load
    wl.write_text(text)
    profile = rp.render(r).split("<!-- watchlist.toml draft")[0].rstrip() + "\n"
    profile = profile.replace("Why I follow them: TODO (one line; it becomes `known_for`).", f"Why I follow them: {known_for}")
    pid = login.lower()
    (Path(paper) / "profiles").mkdir(exist_ok=True)
    (Path(paper) / "profiles" / f"{pid}.md").write_text(profile)
    print(f"Added {login} to watchlist.toml and wrote profiles/{pid}.md; work through its \"Still to check\" list before merging.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--paper", help="folder holding watchlist.toml (default: $PR_POST_PAPER or the repo root)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--max-new", type=int)
    r.add_argument("--preview", action="store_true", help="pick without recording, so nobody's cooldown starts")
    rep = sub.add_parser("report")
    rep.add_argument("login")
    rep.add_argument("--refresh", action="store_true")
    d = sub.add_parser("draft")
    d.add_argument("login")
    d.add_argument("--known-for")
    a = ap.parse_args()

    paper = config.paper_dir(a.paper)
    try:
        cfg = config.load(paper)
    except config.ConfigError as e:
        sys.exit(f"Config error: {e}")
    if a.cmd == "draft":
        return draft(paper, cfg, a.login, a.known_for)
    if not cfg["follow"]["enabled"]:
        print("Who to follow is off: set enabled = true under [follow] in watchlist.toml.")
        return
    today = dt.date.today()
    gh = GitHub(os.environ.get("GITHUB_TOKEN"), cfg["follow"]["max_requests"])
    if a.cmd == "run":
        run(paper, cfg, gh, today, a.max_new, a.preview)
        return
    path = report_path(paper, a.login)
    report = None if a.refresh else load_json(path, None)
    if report is None:
        if not config.GITHUB_RE.match(a.login):
            sys.exit(f"{a.login!r} is not a GitHub username")
        report = research(gh, a.login, today, fl.vocabulary(cfg["follow"]["tech"]))
        if report is None:
            sys.exit(f"{a.login} is not a GitHub user")
        write_json(path, report)
    latest = load_json(folder(paper) / "latest.json", {})
    scored = next((c for c in latest.get("candidates", []) if c["login"].lower() == a.login.lower()), None)
    print(fl.render_md(report, scored))


if __name__ == "__main__":
    main()
