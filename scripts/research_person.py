#!/usr/bin/env python3
"""Draft a profile for someone you are about to follow.

Usage:
    python3 scripts/research_person.py <github-username> [--youtube UC...] [--org ORG ...]
                                       [--paper DIR] [--out FILE] [--no-check]

It gathers what can be found mechanically and writes a Markdown draft for a
person to review before anything is added to a paper:

    GitHub    name, bio, website, X, social accounts, public orgs, pinned repos
              (needs GITHUB_TOKEN), most-starred and recently pushed repos, and
              outside repos they work in (candidates for `orgs`).
    Website   links on their site to known social networks, plus RSS/Atom feeds
              and rel="me" links.
    YouTube   the channel title for --youtube, or the channel id behind a
              youtube.com/@handle link found above.
    Checks    every link is fetched once; the draft says whether it resolved.
              X and LinkedIn refuse most automated requests, so those are marked
              for a manual check instead of failing.

What it cannot do is search the web: the draft ends with a checklist (LinkedIn,
newsletter, Bluesky, Mastodon, talks) for the person or agent doing the intake.
Everything fetched was written by someone else, so only http(s) links are kept
and nothing from a page is treated as an instruction. No dependencies; the token
is read from GITHUB_TOKEN and never printed.
"""

import argparse
import datetime as dt
import html.parser
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import youtube  # noqa: E402

API = "https://api.github.com"
UA = "pr-post research (+https://github.com/n8watkins/pr-post)"
MAX_PAGE = 2_000_000
CHANNEL_RE = re.compile(r"\bUC[A-Za-z0-9_-]{22}\b")

# host (without www.) -> label. Matched on the host or any parent domain.
SOCIAL_HOSTS = {
    "x.com": "X", "twitter.com": "X", "linkedin.com": "LinkedIn", "youtube.com": "YouTube",
    "youtu.be": "YouTube", "bsky.app": "Bluesky", "github.com": "GitHub", "substack.com": "Substack",
    "threads.net": "Threads", "threads.com": "Threads", "instagram.com": "Instagram",
    "twitch.tv": "Twitch", "discord.gg": "Discord", "discord.com": "Discord",
    "medium.com": "Medium", "mastodon.social": "Mastodon", "hachyderm.io": "Mastodon",
    "fosstodon.org": "Mastodon", "tiktok.com": "TikTok", "reddit.com": "Reddit",
    "facebook.com": "Facebook", "patreon.com": "Patreon", "buttondown.email": "Newsletter",
    "buttondown.com": "Newsletter", "beehiiv.com": "Newsletter", "convertkit.com": "Newsletter",
    "egghead.io": "egghead",
}
# What a link to someone's own account looks like on each network. Anything else
# (a tweet, a share button, a company page) is not a profile.
PROFILE_PATHS = {
    "X": r"/[A-Za-z0-9_]{1,15}/?", "LinkedIn": r"/in/[^/]+/?", "Bluesky": r"/profile/[^/]+/?",
    "YouTube": r"/(@[^/]+|channel/UC[A-Za-z0-9_-]{22}|c/[^/]+|user/[^/]+)/?", "GitHub": r"/[A-Za-z0-9-]+/?",
    "Instagram": r"/[A-Za-z0-9_.]+/?", "Threads": r"/@?[A-Za-z0-9_.]+/?", "TikTok": r"/@[A-Za-z0-9_.]+/?",
    "Twitch": r"/[A-Za-z0-9_]+/?", "Facebook": r"/[A-Za-z0-9.]+/?", "Reddit": r"/(u|user)/[^/]+/?",
    "Medium": r"(/@[^/]+)?/?", "Substack": r"(/@[^/]+)?/?", "Mastodon": r"/@[A-Za-z0-9_]+/?",
}
NOT_A_PROFILE = re.compile(r"^/(share|intent|sharer|hashtag|search|home|login|signup|explore|notes)\b", re.IGNORECASE)
# Sites that answer automated requests with a login wall or an app shell, so a
# fetch says nothing about whether the account exists.
BOT_WALLED = ("x.com", "twitter.com", "linkedin.com", "instagram.com", "threads.net", "threads.com",
              "facebook.com", "tiktok.com", "medium.com", "reddit.com")


def host_of(url):
    return urllib.parse.urlparse(url).netloc.lower().split(":")[0].removeprefix("www.")


def on_host(url, hosts):
    h = host_of(url)
    return any(h == x or h.endswith("." + x) for x in hosts)


def classify(url):
    """The network a link to someone's own account points at, or None for anything else."""
    if not config.is_http_url(url):
        return None
    host, path = host_of(url), urllib.parse.urlparse(url).path
    if NOT_A_PROFILE.search(path):
        return None
    for known, label in SOCIAL_HOSTS.items():
        if not (host == known or host.endswith("." + known)):
            continue
        if label in ("Substack", "Medium"):
            if host != known:
                return label if path in ("", "/") else None  # name.substack.com, not one post
            return label if re.fullmatch(r"/@[^/]+/?", path) else None
        shape = PROFILE_PATHS.get(label, r"/.+")
        return label if re.fullmatch(shape, path) else None
    if re.fullmatch(PROFILE_PATHS["Mastodon"], path):
        return "Mastodon"  # any instance: https://host/@user
    return None


def handle_of(url):
    """The account name inside a profile link, lowercased letters and digits only."""
    host, path = host_of(url), urllib.parse.urlparse(url).path.strip("/")
    if host.endswith((".substack.com", ".medium.com")) and not path:
        part = host.split(".")[0]
    elif host.endswith("bsky.app"):
        part = path.split("/")[-1].split(".")[0]
    else:
        part = path.split("/")[-1] if path else host.split(".")[0]
    return re.sub(r"[^a-z0-9]", "", part.lower())


def looks_like_theirs(url, tokens):
    """True when the account name in a link matches their login, X handle or name.

    A personal site often also links an employer's or a project's accounts;
    those are listed for review but never proposed for the watch list.
    """
    h = handle_of(url)
    return bool(h) and any(t in h or (len(h) >= 4 and h in t) for t in tokens)


def name_tokens(*names):
    tokens = set()
    for n in names:
        if not n:
            continue
        words = [w.lower() for w in re.findall(r"[A-Za-z0-9]+", n)]
        tokens.add("".join(words))
        tokens.update(w for w in words if len(w) >= 4)
    return {t for t in tokens if t}


def clean_name(name):
    """A display name without emoji or other decoration that GitHub names often carry."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s.'\-]", "", name)).strip()


def normalize(url):
    url = url.strip().replace("://twitter.com/", "://x.com/").replace("://www.twitter.com/", "://x.com/")
    return url.split("#", 1)[0]


def same(a, b):
    key = lambda u: normalize(u).lower().rstrip("/").split("://", 1)[-1].removeprefix("www.")
    return key(a) == key(b)


class LinkParser(html.parser.HTMLParser):
    """Collects <a href>, <link rel="me"> and RSS/Atom <link rel="alternate"> from a page."""

    def __init__(self, base):
        super().__init__()
        self.base, self.links, self.feeds, self.me = base, [], [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        href = (a.get("href") or "").strip()
        if not href:
            return
        url = urllib.parse.urljoin(self.base, href)
        if not config.is_http_url(url):
            return
        rel = (a.get("rel") or "").lower().split()
        if tag == "link" and "alternate" in rel and (a.get("type") or "").lower() in ("application/rss+xml", "application/atom+xml", "application/feed+json"):
            self.feeds.append(url)
        elif "me" in rel:
            self.me.append(url)
        elif tag == "a":
            self.links.append(url)


def site_links(page_html, base):
    """Social profiles, feeds and rel=me links found on a page, deduplicated, in page order."""
    p = LinkParser(base)
    p.feed(page_html)
    p.close()
    socials = []
    for url in p.me + p.links:
        label = classify(url)
        if label and not any(same(url, s["url"]) for s in socials):
            socials.append({"label": label, "url": normalize(url), "source": "rel=me" if url in p.me else "site"})
    feeds = []
    for f in p.feeds:
        if f not in feeds:
            feeds.append(f)
    return {"socials": socials, "feeds": feeds}


def fetch(url, timeout=20):
    """(status, final_url, text) for a page; status 0 when the request failed outright."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(MAX_PAGE)
            charset = r.headers.get_content_charset() or "utf-8"
            return r.status, r.geturl(), body.decode(charset, errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, url, ""
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return 0, url, ""


def check(url):
    """'ok', 'manual' (a site whose answer proves nothing) or 'broken (<status>)'."""
    if on_host(url, BOT_WALLED):
        return "manual"
    status, _, _ = fetch(url)
    if 200 <= status < 400:
        return "ok"
    return f"broken ({status or 'no response'})"


def gh(path, params=None):
    url = f"{API}{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": UA}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        sys.exit(f"GitHub API {e.code} on {path} (rate limit remaining: {e.headers.get('X-RateLimit-Remaining')})")


PINNED = """query($login: String!) { user(login: $login) { pinnedItems(first: 6, types: REPOSITORY) {
  nodes { ... on Repository { nameWithOwner description stargazerCount url } } } } }"""


def pinned(user):
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return None
    req = urllib.request.Request(f"{API}/graphql", data=json.dumps({"query": PINNED, "variables": {"login": user}}).encode(),
                                 headers={"Authorization": f"Bearer {token}", "User-Agent": UA, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r).get("data") or {}
    except (urllib.error.URLError, TimeoutError):
        return None
    return [{"repo": n["nameWithOwner"], "description": n.get("description") or "", "stars": n["stargazerCount"], "url": n["url"]}
            for n in ((data.get("user") or {}).get("pinnedItems") or {}).get("nodes", []) if n]


def repo_row(r):
    return {"repo": r["full_name"], "description": r.get("description") or "", "stars": r["stargazers_count"],
            "pushed": (r.get("pushed_at") or "")[:10], "url": r["html_url"], "fork": r.get("fork", False)}


def outside_repos(events, user):
    """Repos outside their own account that they pushed to or opened PRs in, busiest first."""
    counts = {}
    for e in events or []:
        name = (e.get("repo") or {}).get("name", "")
        if e.get("type") in ("PushEvent", "PullRequestEvent", "CreateEvent", "ReleaseEvent") and name and not name.lower().startswith(user.lower() + "/"):
            counts[name] = counts.get(name, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


def github_facts(user):
    profile = gh(f"/users/{user}")
    if not profile:
        sys.exit(f"No GitHub user named {user}")
    repos = [repo_row(r) for r in gh(f"/users/{user}/repos", {"per_page": 100, "sort": "pushed"}) or []]
    events = []
    for page in (1, 2, 3):
        batch = gh(f"/users/{user}/events/public", {"per_page": 100, "page": page}) or []
        events += batch
        if len(batch) < 100:
            break
    links = []

    def add(label, url, source):
        if url and config.is_http_url(url) and not any(same(url, l["url"]) for l in links):
            links.append({"label": label, "url": normalize(url), "source": source})

    if profile.get("twitter_username"):
        add("X", f"https://x.com/{profile['twitter_username']}", "the X field on their GitHub profile")
    for acct in gh(f"/users/{user}/social_accounts") or []:
        url = acct.get("url", "")
        add(classify(url) or host_of(url) or "Link", url, "listed on their GitHub profile")
    blog = (profile.get("blog") or "").strip()
    website = (blog if blog.startswith("http") else f"https://{blog}") if blog else None
    return {
        "login": profile["login"], "name": profile.get("name") or profile["login"], "bio": (profile.get("bio") or "").strip(),
        "company": profile.get("company") or "", "location": profile.get("location") or "", "followers": profile.get("followers", 0),
        "created": (profile.get("created_at") or "")[:10], "website": website, "links": links,
        "orgs": [o["login"] for o in gh(f"/users/{user}/orgs") or []],
        "pinned": pinned(user),
        "top": sorted((r for r in repos if not r["fork"]), key=lambda r: -r["stars"])[:6],
        "recent": [r for r in repos if not r["fork"]][:6],
        "outside": outside_repos(events, user)[:8],
    }


def youtube_from_link(url):
    """The UC... channel id behind a YouTube link, read from the channel page."""
    if m := CHANNEL_RE.search(url):
        return m.group(0)
    _, _, page = fetch(url)
    if not page:
        return None
    m = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[A-Za-z0-9_-]{22})"', page) or \
        re.search(r'"externalId":"(UC[A-Za-z0-9_-]{22})"', page)
    return m.group(1) if m else None


def youtube_title(channel_id):
    try:
        return youtube.parse(youtube.fetch(channel_id, attempts=2, timeout=20))["channel"]["title"]
    except youtube.FeedError:
        return None


def suggest_ticker(name, login, taken):
    """A free Commit Index symbol from their surname, initials or login."""
    words = re.findall(r"[A-Za-z0-9]+", name)
    handle = re.sub(r"[^A-Za-z0-9]", "", login)
    cands = [words[-1][:4], words[0][0] + words[-1][:3]] if words else []
    for cand in cands + [handle[:4], handle[:6]]:
        cand = cand.upper()
        if cand and config.TICKER_RE.match(cand) and cand not in taken:
            return cand
    return (handle[:5] or "NEW").upper() + "X"


def research(user, channel=None, orgs=(), paper=None, do_check=True):
    facts = github_facts(user)
    facts["name"] = clean_name(facts["name"]) or facts["login"]
    x_handles = [urllib.parse.urlparse(l["url"]).path.strip("/") for l in facts["links"] if l["label"] == "X"]
    tokens = name_tokens(facts["login"], facts["name"], *x_handles)
    links = [{**l, "theirs": True} for l in facts["links"]]  # they listed these themselves
    site = {"socials": [], "feeds": [], "status": None}
    # A repository used as the website field is not a personal site worth scraping.
    if facts["website"] and not on_host(facts["website"], ("github.com",)):
        status, final, page = fetch(facts["website"])
        site = {**site_links(page, final), "status": status}
        for s in site["socials"]:
            if s["label"] != "GitHub" and not any(same(s["url"], l["url"]) for l in links):
                links.append({**s, "source": "linked from their website" + (" with rel=me" if s["source"] == "rel=me" else ""), "theirs": looks_like_theirs(s["url"], tokens)})
    yt = {"id": channel, "title": None, "source": "--youtube" if channel else None}
    if not channel:
        for l in links:
            if l["label"] == "YouTube" and l["theirs"] and (cid := youtube_from_link(l["url"])):
                yt = {"id": cid, "title": None, "source": l["url"]}
                break
    if yt["id"]:
        yt["title"] = youtube_title(yt["id"])
    if do_check:
        for l in links:
            l["check"] = check(l["url"])
        site["feed_checks"] = {f: check(f) for f in site["feeds"]}
    taken = set()
    if paper:
        try:
            taken = {p["ticker"] for p in config.load(Path(paper))["people"] if p.get("ticker")}
        except (config.ConfigError, OSError):
            pass
    return {"facts": facts, "site": site, "links": links, "youtube": yt, "orgs": list(orgs),
            "ticker": suggest_ticker(facts["name"], facts["login"], taken),
            "researched": dt.date.today().isoformat()}


def toml_str(s):
    return json.dumps(s, ensure_ascii=False)


CHECKED = {"ok": "resolves", "manual": "this site blocks automated checks, open it by hand", None: "not checked"}


def link_line(l):
    return f"- {l['label']}: {l['url']} ({l['source']}; {CHECKED.get(l.get('check'), l.get('check'))})."


def render(r):
    """The draft: profile Markdown, a watchlist block and the manual checklist."""
    f, yt, site = r["facts"], r["youtube"], r["site"]
    name, login = f["name"], f["login"]
    orgs = r["orgs"] or []
    out = [f"# {name} ({login})", "",
           f"GitHub: [{login}](https://github.com/{login}), {f['followers']} followers, joined {f['created']}.",
           f"Bio: {f['bio']}" if f["bio"] else "Bio: none on GitHub."]
    if f["company"] or f["location"]:
        out.append(" ".join(x for x in (f"Company: {f['company']}." if f["company"] else "", f"Location: {f['location']}." if f["location"] else "") if x))
    out.append(f"Public org memberships: {', '.join(f['orgs']) or 'none'}.")
    if orgs:
        out.append("Orgs followed for this paper: " + ", ".join(f"[{o}](https://github.com/{o})" for o in orgs) + ".")
    out += ["", "Why I follow them: TODO (one line; it becomes `known_for`).", "", "## Where to find them", ""]
    if f["website"]:
        ok = (site.get("status") or 0) in range(200, 400) or site.get("status") is None
        out.append(f"- Website: {f['website']} (their GitHub website field; {'resolves' if ok else 'did not resolve'}).")
    theirs = [l for l in r["links"] if l["theirs"]]
    others = [l for l in r["links"] if not l["theirs"]]
    out += [link_line(l) for l in theirs]
    if yt["id"]:
        title = f" ({yt['title']})" if yt["title"] else " (its feed did not load)"
        out.append(f"- YouTube channel id: `{yt['id']}`{title} (from {yt['source']}).")
    if not (f["website"] or theirs or yt["id"]):
        out.append("- Nothing on their GitHub profile; every link has to come from the web search below.")
    for feed in site["feeds"]:
        out.append(f"- Feed: {feed} ({CHECKED.get(site.get('feed_checks', {}).get(feed), 'not checked')}).")
    if others:
        out += ["", "Also linked from their website, but the account name does not match theirs (probably an employer or a project):", ""]
        out += [link_line(l) for l in others]
    out += ["", "## Notable projects", ""]
    seen = set()
    for row in (f["pinned"] or []) + f["top"]:
        if row["repo"] not in seen:
            seen.add(row["repo"])
            out.append(f"- [{row['repo']}]({row['url']}) ({row['stars']} stars): {row['description'] or 'no description'}")
    out += ["", "Recently pushed: " + (", ".join(f"[{x['repo']}]({x['url']}) ({x['pushed']})" for x in f["recent"]) or "nothing public") + "."]
    if f["outside"]:
        out += ["", "Outside repos in their recent public activity (candidates for `orgs`): " + ", ".join(f"{repo} ({n})" for repo, n in f["outside"]) + "."]
    found = {l["label"] for l in theirs} | ({"YouTube"} if yt["id"] else set())
    todo = [label for label in ("LinkedIn", "X", "YouTube", "Bluesky", "Mastodon", "Substack") if label not in found]
    out += ["", "## Still to check (web search)", "",
            *(f"- {label}: search for \"{name}\" {label}; keep it only if it links back to their GitHub or website, or they link to it." for label in todo),
            "- A newsletter or blog feed, if none is listed above.",
            "- Open every \"open it by hand\" link and confirm it is them (same name and photo, or a link back).",
            "", f"_Draft generated {r['researched']} by research_person.py. Review before committing._", ""]
    out += ["<!-- watchlist.toml draft. Links on their GitHub profile are added automatically, so only others are listed.", "",
            *watchlist_block(r), "-->", ""]
    return "\n".join(out)


def watchlist_block(r, known_for="TODO"):
    """The [[people]] entry for watchlist.toml, as lines."""
    f, yt = r["facts"], r["youtube"]
    block = ["[[people]]", f"github = {toml_str(f['login'])}", f"ticker = {toml_str(r['ticker'])}", f"name = {toml_str(f['name'])}"]
    if r["orgs"]:
        block.append("orgs = [" + ", ".join(toml_str(o) for o in r["orgs"]) + "]")
    if yt["id"]:
        block.append(f"youtube_channel_id = {toml_str(yt['id'])}")
    block.append(f"known_for = {toml_str(known_for)}")
    extra = [l for l in r["links"] if l["theirs"] and "GitHub profile" not in l["source"]
             and not (l.get("check") or "").startswith("broken")]
    if yt["id"] and not any(l["label"] == "YouTube" and l["theirs"] for l in r["links"]):
        extra.append({"label": "YouTube", "url": f"https://www.youtube.com/channel/{yt['id']}"})
    if extra:
        block.append("links = [")
        block += [f"  {{ label = {toml_str(l['label'])}, url = {toml_str(l['url'])} }}," for l in extra]
        block.append("]")
    return block


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("user", help="GitHub username")
    ap.add_argument("--youtube", help="their YouTube channel id (UC...)")
    ap.add_argument("--org", action="append", default=[], help="an organization where their work lands (repeatable)")
    ap.add_argument("--paper", help="paper folder, to avoid tickers already taken")
    ap.add_argument("--out", help="write the draft here instead of stdout")
    ap.add_argument("--no-check", action="store_true", help="skip fetching each link to verify it")
    ap.add_argument("--json", action="store_true", help="print the raw findings as JSON instead")
    a = ap.parse_args()
    if a.youtube and not CHANNEL_RE.fullmatch(a.youtube):
        sys.exit("--youtube must be a channel id starting with UC")
    r = research(a.user, a.youtube, a.org, a.paper, not a.no_check)
    text = json.dumps(r, indent=1) if a.json else render(r)
    if a.out:
        Path(a.out).write_text(text)
        print(f"wrote {a.out}", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
