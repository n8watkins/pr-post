#!/usr/bin/env python3
"""Build the paper's web edition and feed.json into dist/.

Usage:
    python3 scripts/build_site.py [--paper DIR] [--out DIR]   # default out: dist/
    python3 -m http.server -d dist 8080                       # preview locally

Everything comes from the paper folder: watchlist.toml (people, order, stars,
site settings) and stats/ (every fact and number). Written material is
optional: editions/, reports/, profiles/ and summaries/ appear when a writer
has produced them. Without them the front page is a facts edition generated
from stats/ alone, with no AI involved. No dependencies.
"""

import argparse
import datetime as dt
import html
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import digest  # noqa: E402
import feed as feedlib  # noqa: E402
import follow_lib  # noqa: E402
from md import blocks, safe_url  # noqa: E402

ROOT = config.ROOT
# Bodoni only sets the masthead and wordmark; Source Serif 4 sets headlines, Inter the text and UI.
FONTS = ("https://fonts.googleapis.com/css2?family=Bodoni+Moda:opsz,wght@6..96,900"
         "&family=Source+Serif+4:opsz,wght@8..60,600..800&family=Inter:opsz,wght@14..32,400..700"
         "&family=JetBrains+Mono:wght@500&display=swap")
ARROW_MIN_DAYS = 4  # compare against a snapshot at least this old
FACTS_SIDE_MAX = 6  # births and obituaries the facts edition prints before "plus N more"
REPO_URL = "https://github.com/n8watkins/pr-post"
MENU_ICON = ('<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">'
             '<path d="M4 7h16M4 12h16M4 17h16"/></svg>')
FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%2317171a'/%3E"
           "%3Ctext x='16' y='23' font-family='Georgia,serif' font-weight='900' font-size='20' text-anchor='middle' fill='%23f8f7f3'%3EP%3C/text%3E%3C/svg%3E")

# Set by configure() from [site] in watchlist.toml.
SITE = dict(config.SITE_DEFAULTS)
PAPER = SITE["title"]
BASE = ""
SHOW_PRIVATE = False
HAS_FOLLOW = False  # set by build() when there is a "Who to follow" page


def people_api():
    """True when the paper is kept in Cloudflare, so the Worker's /api/people (and its drafts) exists."""
    return SITE["mode"] == "private" and SITE["storage"] == "cloudflare"


DRAFTS_API = "/api/people/drafts"


def configure(site):
    global SITE, PAPER, BASE, SHOW_PRIVATE
    SITE, PAPER, BASE, SHOW_PRIVATE = site, site["title"], site["base_path"], config.private_counts(site)


# ---------- small helpers ----------

def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def u(path):
    """A site-relative path with the base path in front, for sites served from a sub-folder."""
    return f"{BASE}{path}"


def ext(url, text, cls=""):
    c = f' class="{cls}"' if cls else ""
    return f'<a{c} href="{esc(safe_url(url))}" target="_blank" rel="noopener">{text}</a>'


def n(x):
    return f"{x:,}" if isinstance(x, int) else "?"


def plural(k, one, many=None):
    return f"{n(k)} {one if k == 1 else (many or one + 's')}"


def day_long(iso):
    d = dt.date.fromisoformat(iso)
    return f"{d:%A}, {d:%B} {d.day}, {d.year}"


def day_short(iso):
    d = dt.date.fromisoformat(iso)
    return f"{d:%b} {d.day}"


def short_repo(repo):
    return (repo or "").split("/")[-1]


def inline(text):
    text = esc(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", lambda m: f'<a href="{esc(safe_url(html.unescape(m[2])))}" target="_blank" rel="noopener">{m[1]}</a>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?!\w)", r"<em>\1</em>", text)
    return text


def render_md(md, skip_h1=True, demote=0):
    return render_blocks([b for b in blocks(md) if not (skip_h1 and b[0] == "h1")], demote)


def render_blocks(parsed, demote=0):
    out = []
    for kind, data in parsed:
        if kind in ("h1", "h2", "h3"):
            level = min(int(kind[1]) + demote, 4)
            out.append(f"<h{level}>{inline(data)}</h{level}>")
        elif kind == "p":
            out.append(f"<p>{inline(data)}</p>")
        elif kind == "hr":
            out.append("<hr>")
        elif kind == "table":
            head, *rows = data
            th = "".join(f"<th>{inline(c)}</th>" for c in head)
            trs = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows)
            out.append(f'<div class="tablewrap"><table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>')
        elif kind == "list":
            ordered_, items = data
            tag = "ol" if ordered_ else "ul"
            html_items, open_nested = [], False
            for nested, text in items:
                if nested and not open_nested:
                    html_items.append("<ul>")
                    open_nested = True
                if not nested and open_nested:
                    html_items.append("</ul>")
                    open_nested = False
                html_items.append(f"<li>{inline(text)}</li>")
            if open_nested:
                html_items.append("</ul>")
            out.append(f"<{tag}>{''.join(html_items)}</{tag}>")
    return "\n".join(out)


def report_title(md, fallback):
    return next((l[2:].strip() for l in md.splitlines() if l.startswith("# ")), fallback)


def split_pct(public, private):
    total = (public or 0) + (private or 0)
    if not total:
        return 0, 0
    return round(100 * (public or 0) / total, 1), round(100 * (private or 0) / total, 1)


def week_total(st):
    """The headline contribution number: everything on a private site that opts in, public work otherwise."""
    c = (st or {}).get("contributions") or {}
    return c.get("total") if SHOW_PRIVATE else c.get("public")


def days_active(st):
    c = (st or {}).get("contributions") or {}
    return c.get("days_active") if SHOW_PRIVATE else c.get("days_active_public")


def visible_repos(st):
    return [r for r in st["repos"] if SHOW_PRIVATE or not r.get("private")]


# ---------- loading ----------

def load(paper, cfg):
    people = cfg["people"]
    snaps = [json.loads(f.read_text()) for f in sorted((paper / "stats").glob("*.json"))]
    editions = []
    for f in sorted((paper / "editions").glob("*.json")):
        e = json.loads(f.read_text())
        e["stem"] = f.stem
        editions.append(e)
    for i, e in enumerate(editions, 1):
        e["number"] = i
    reports, profiles = {}, {}
    for p in people:
        files = sorted((paper / "reports" / p["id"]).glob("*.md"), reverse=True)
        reports[p["id"]] = [(f.stem, f.read_text()) for f in files]
        prof = paper / "profiles" / f"{p['id']}.md"
        if prof.exists():
            profiles[p["id"]] = prof.read_text()
    return snaps, editions, reports, profiles


def latest_stats(snaps, pid):
    for s in reversed(snaps):
        if pid in s["people"]:
            return s["people"][pid]
    return None


def previous_stats(snaps, pid, latest):
    if not latest:
        return None
    cutoff = dt.date.fromisoformat(latest["window"]["end"]) - dt.timedelta(days=ARROW_MIN_DAYS)
    for s in reversed(snaps):
        if pid in s["people"] and dt.date.fromisoformat(s["date"]) <= cutoff:
            return s["people"][pid]
    return None


def ordered(people):
    """Starred people first, each group in watch list order."""
    return [p for p in people if p.get("starred")] + [p for p in people if not p.get("starred")]


def edition_who(item):
    return item.get("id") or (item.get("github") or "").lower()


# ---------- facts edition: a front page from stats alone, no AI ----------

def quote_title(text, limit=90):
    text = text if len(text) <= limit else text[: limit - 3].rsplit(" ", 1)[0].rstrip(",;:.-") + "..."
    return f"“{text}”"


def release_name(r):
    """A release's display name, with the repo in front when the name is just a version."""
    name = r["name"] or r["tag"] or "a release"
    return f"{short_repo(r['repo'])} {name}" if re.match(r"^v?\d", name) else name


def facts_story(p, st):
    """Headline, beat, score and an escaped HTML summary for one person, from facts only.

    The reader's own entry (self = true) is written in the second person, like their reports.
    """
    you = bool(p.get("self"))
    name = "You" if you else p["name"]
    verb = (lambda third, base: base) if you else (lambda third, base: third)
    prs = st["public_merged_prs"]
    rel, vids, births = st["releases"], st["videos"], st["births"]
    pr_repos = [r for r in st["repos"] if r["merged_prs"] and not r.get("private")]
    commits = st["commits"]
    if rel:
        hed = f"{name} {verb('ships', 'ship')} {release_name(rel[0])}" + (f" and {plural(len(rel) - 1, 'more release')}" if len(rel) > 1 else "")
        beat = "Release desk"
    elif prs:
        hed = f"{name} {verb('merges', 'merge')} {plural(prs, 'PR')}" + (f" across {plural(len(pr_repos), 'repo')}" if len(pr_repos) > 1 else f" in {short_repo(pr_repos[0]['repo'])}" if pr_repos else "")
        beat = "GitHub desk"
    elif vids:
        hed = f"{name} {verb('posts', 'post')} " + (quote_title(vids[0]['title'], 70) if len(vids) == 1 else plural(len(vids), 'video'))
        beat = "Video desk"
    elif births:
        hed = f"{name} {verb('starts', 'start')} {short_repo(births[0]['repo'])}"
        beat = "New repos"
    elif commits:
        hed = f"{name} {verb('pushes', 'push')} {plural(commits, 'commit')}"
        beat = "GitHub desk"
    else:
        hed = "A quiet week for you" if you else f"A quiet week for {name}"
        beat = "Quiet desk"

    bits = []
    if pr_repos:
        top = pr_repos[0]
        bits.append(f"Busiest in {ext('https://github.com/' + top['repo'], esc(top['repo']))} with {plural(top['merged_prs'], 'merged PR')}.")
    latest_pr = next((i for i in st["recent"] if i["type"] == "pr"), None)
    if latest_pr:
        bits.append(f"Latest merged: {ext(latest_pr['url'], esc(quote_title(latest_pr['title'])))}.")
    if rel:
        bits.append("Released " + ", ".join(ext(r["url"], esc(release_name(r))) for r in rel[:3]) + (f" and {len(rel) - 3} more" if len(rel) > 3 else "") + ".")
    if vids:
        bits.append(f"Newest video: {ext(vids[0]['url'], esc(quote_title(vids[0]['title'])))}" + (f", one of {len(vids)} this week." if len(vids) > 1 else "."))
    if births:
        bits.append("New repo" + ("s" if len(births) > 1 else "") + ": " + ", ".join(ext(b["url"], esc(short_repo(b["repo"]))) for b in births[:3]) + ".")
    if not bits:
        bits.append("Nothing public landed in the last seven days." if st["has_github"] or st["has_youtube"] else "No sources configured.")
    score = prs + 5 * len(rel) + 4 * len(vids) + 3 * len(births) + min(commits, 50) / 10
    return {"id": p["id"], "beat": beat, "headline": hed, "summary_html": " ".join(bits), "score": score}


def facts_edition(people, stats):
    """An edition dict shaped like a written one, built from the newest stats."""
    rows = [(p, stats[p["id"]]) for p in people if stats.get(p["id"])]
    if not rows:
        return None
    stories = [facts_story(p, st) for p, st in rows]
    by_id = {p["id"]: (p, st) for p, st in rows}
    lead_story = max(stories, key=lambda s: s["score"])
    lp, lst = by_id[lead_story["id"]]
    date = max(st["window"]["end"] for _, st in rows)

    prs = sum(st["public_merged_prs"] for _, st in rows)
    vids = sum(len(st["videos"]) for _, st in rows)
    rels = sum(len(st["releases"]) for _, st in rows)
    weather = f"{plural(prs, 'PR')} merged, {plural(rels, 'release')} and {plural(vids, 'video')} across the beat. Heaviest over {lp['name']}."

    items = lst["recent"][:6]
    body = [lead_story["summary_html"]]
    if items:
        body.append("From the record: " + "; ".join(
            f"{ext(i['url'], esc(quote_title(i['title'], 80)))} ({esc(short_repo(i['repo'])) + ', ' if i['repo'] else ''}{day_short(i['date'])})" for i in items) + ".")
    # Newest first, and only a handful, so the side column stays beside the lead story.
    births = [{"repo": b["repo"], "url": b["url"], "date": b["date"],
               "html": f'{esc(b["description"]) or "No description yet."} <span class="muted">{esc(p["name"])}</span>'}
              for p, st in rows for b in st["births"]]
    births.sort(key=lambda b: b["date"], reverse=True)
    obits = [{"name": short_repo(d["repo"]), "url": d["url"], "html": f"{esc(d['repo'])} was archived."} for _, st in rows for d in st["deaths"]]
    quote_item = next((i for i in lst["recent"] if i["type"] in ("pr", "video")), None)
    quote = {"text": quote_item["title"], "url": quote_item["url"], "by": lp["name"],
             "where": short_repo(quote_item["repo"]) if quote_item["repo"] else "YouTube", "date": day_short(quote_item["date"])} if quote_item else None
    return {
        "facts": True,
        "date": date,
        "kind": "facts",
        "weather": weather,
        "lead": {"id": lp["id"], "headline": lead_story["headline"],
                 "deck_html": f"The week of {day_short(lst['window']['start'])} to {day_short(lst['window']['end'])}, from public GitHub and YouTube activity.",
                 "body_html": body,
                 "sources": [{"label": quote_title(i["title"], 40), "url": i["url"]} for i in items[:3]]},
        "stories": sorted([s for s in stories if s["id"] != lp["id"]], key=lambda s: -s["score"]),
        "births_html": births[:FACTS_SIDE_MAX],
        "births_more": max(0, len(births) - FACTS_SIDE_MAX),
        "obituaries_html": obits[:FACTS_SIDE_MAX],
        "obituaries_more": max(0, len(obits) - FACTS_SIDE_MAX),
        "quote": quote,
        "corrections": [],
        "number": 0,
    }


# ---------- layout ----------

def rail(people, snaps, current=None, page=None):
    def link(p):
        st = latest_stats(snaps, p["id"])
        c = (st or {}).get("contributions") or {}
        if not st:
            meta = "new"
        elif c:
            meta = f'{esc(p["ticker"] or "")}{" · " if p["ticker"] else ""}{n(week_total(st))}'
        else:
            meta = plural(len(st["videos"]), "video")
        cur = ' aria-current="page"' if p["id"] == current else ""
        search = esc(f'{p["name"]} {p["github"] or ""} {p["id"]} {p["ticker"]}'.lower())
        bar = ""
        if SHOW_PRIVATE and c:
            pub, priv = split_pct(c.get("public"), c.get("private"))
            bar = f'<span class="pl-bar" aria-hidden="true"><span style="width:{pub}%"></span><span style="width:{priv}%"></span></span>'
        return (f'<a class="person-link" href="{u("/p/" + esc(p["id"]) + "/")}" data-search="{search}"{cur}>'
                f'<span class="pl-name">{"★ " if p.get("starred") else ""}{esc(p["name"])}</span>'
                f'<span class="pl-meta">{meta}</span>{bar}</a>')

    starred = [p for p in people if p.get("starred")]
    rest = [p for p in people if not p.get("starred")]
    groups = ""
    if starred:
        groups += f'<div class="rail-group"><h2>Starred</h2>{"".join(link(p) for p in starred)}</div>'
    if rest:
        groups += f'<div class="rail-group"><h2>{"Following" if starred else "People"}</h2>{"".join(link(p) for p in rest)}</div>'
    pages = [("front", "/", "Front page"), ("archive", "/archive/", "Back issues")]
    if HAS_FOLLOW:
        pages.append(("follow", "/follow/", "Who to follow"))
    if people_api():
        pages.append(("people", "/people/", "People"))
    elif SITE["mode"] == "private":
        pages.append(("settings", "/settings/", "People and settings"))
    nav = "".join(f'<a href="{u(href)}"{" aria-current=\"page\"" if page == key else ""}>{label}</a>' for key, href, label in pages)
    legend = ('<span class="legend"><span class="sw pub"></span>public</span> &nbsp; <span class="legend"><span class="sw priv"></span>private</span><br>'
              "Numbers are this week's contributions." if SHOW_PRIVATE else "Numbers are this week's public contributions.")
    return f"""<aside class="rail" id="rail" aria-label="Sections and people">
  <a class="rail-brand" href="{u("/")}">{esc(PAPER)}</a>
  <nav class="rail-nav">{nav}</nav>
  <input class="rail-search" id="rail-search" type="search" placeholder="Find a person  ( / )" aria-label="Find a person" autocomplete="off">
  {groups}
  <div class="rail-foot">
    {legend} Press [ and ] to move between people.
  </div>
</aside>"""


def layout(title, body, people, snaps, current=None, page=None, description=None, scripts=()):
    robots = '<meta name="robots" content="noindex,nofollow">' if SITE["mode"] == "private" else ""
    desc = description or f"{PAPER}: what {len(people)} builders shipped this week, from their public GitHub and YouTube activity."
    private_note = " · private work appears only as a count" if SHOW_PRIVATE else ""
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
{robots}
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<link rel="icon" href="{FAVICON}">
<link rel="alternate" type="application/json" title="{esc(PAPER)} feed" href="{u("/feed.json")}">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONTS}">
<link rel="stylesheet" href="{u("/assets/style.css")}">
</head><body>
<div class="shell">
{rail(people, snaps, current, page)}
<div>
  <header class="topbar"><button id="rail-toggle" type="button" aria-controls="rail" aria-expanded="false">{MENU_ICON}<span class="menu-label">Menu</span></button>{"" if page == "front" else f'<a href="{u("/")}">{esc(PAPER)}</a>'}</header>
  <main class="main">
{body}
<footer class="colophon">{esc(PAPER)} · built from public GitHub and YouTube activity{private_note} · <a href="{u("/feed.json")}">feed.json</a> · made with <a href="{REPO_URL}">pr-post</a></footer>
  </main>
</div>
</div>
<script src="{u("/assets/app.js")}" defer></script>{"".join(f'<script src="{u("/assets/" + s)}" defer></script>' for s in scripts)}
</body></html>
"""


# ---------- front page ----------

def ticker(people, snaps):
    items = []
    rows = [(p, latest_stats(snaps, p["id"])) for p in people]
    for p, st in sorted(rows, key=lambda r: -(week_total(r[1]) or 0)):
        if not st:
            continue
        sym = esc(p["ticker"] or p["id"][:4].upper())
        if not st.get("contributions"):
            if st["has_youtube"]:
                items.append(f'<a href="{u("/p/" + esc(p["id"]) + "/")}"><b>{sym}</b> {plural(len(st["videos"]), "video")}</a>')
            continue
        total = week_total(st) or 0
        prev = previous_stats(snaps, p["id"], st)
        prev_total = week_total(prev) if prev else None
        if prev_total:
            pct = round(100 * (total - prev_total) / prev_total)
            move = f'<span class="{"up" if pct >= 0 else "dn"}">{"▲" if pct >= 0 else "▼"} {abs(pct)}%</span>'
        else:
            move = '<span class="muted">new listing</span>'
        items.append(f'<a href="{u("/p/" + esc(p["id"]) + "/")}"><b>{sym}</b> {n(total)} {move}</a>')
    # A still strip: nothing on the page moves on its own. Phones skip it; the box scores carry the same numbers.
    return f'<div class="ticker" role="group" aria-label="Commit Index: contributions this week">{"".join(items)}</div>' if items else ""


def front_page(edition, people, snaps, written_count):
    by_id = {p["id"]: p for p in people}
    stats = {p["id"]: latest_stats(snaps, p["id"]) for p in people}
    merged_total = sum((s or {}).get("public_merged_prs", 0) for s in stats.values())
    date = edition["date"] if edition else max((s["window"]["end"] for s in stats.values() if s), default=dt.date.today().isoformat())
    facts = bool((edition or {}).get("facts"))

    weather = (edition or {}).get("weather") or "Forecast unavailable. Check back after the next run."
    number = "Facts edition" if facts else f'No. {edition["number"] if edition else 0}{" · Extra" if (edition or {}).get("kind") == "update" else ""}'
    head = f"""<div class="ears"><span>Weather: {esc(weather)}</span><span>Price: one (1) merged PR</span></div>
<div class="mast">{esc(PAPER)}</div>
<div class="motto">{esc(SITE["motto"])}</div>
<div class="edline"><span>Vol. I · {number}</span><span>{day_long(date)}</span><span>{plural(len(people), "builder")} · {plural(merged_total, "PR")} merged</span></div>
{ticker(people, snaps)}"""

    if not edition:
        return head + '<p class="deck" style="margin-top:24px">The first edition prints after the next collection run.</p>'

    lead = edition["lead"]
    lid = edition_who(lead)
    lp = by_id.get(lid, {"name": lid})
    sources = " · ".join(ext(s["url"], esc(s["label"])) for s in lead.get("sources", []))
    body = "".join(f"<p>{para}</p>" for para in lead["body_html"]) if facts else "".join(f"<p>{inline(para)}</p>" for para in lead.get("body", []))
    deck = lead["deck_html"] if facts else inline(lead.get("deck", ""))
    byline = (f'By {esc(SITE["desk"])} · generated from public activity, no AI' if facts
              else f'By the {esc(edition.get("source") or "writing")} desk' + (" · AI-written" if edition.get("source") not in (None, "human") else ""))
    lead_html = f"""<div>
  <div class="kicker"><a href="{u("/p/" + esc(lid) + "/")}">{esc(lp.get("name"))}</a></div>
  <h1 class="lead-hed"><a href="{u("/p/" + esc(lid) + "/")}">{esc(lead["headline"])}</a></h1>
  <p class="deck">{deck}</p>
  <div class="byline">{byline}</div>
  <div class="story">{body}</div>
  {f'<div class="sources">Sources: {sources}</div>' if sources else ""}
</div>"""

    if facts:
        more = lambda k, what: f'<p class="muted">Plus {plural(k, what)}; see each person\'s page.</p>' if k else ""
        obits = "".join(f'<p><span class="who-line">{ext(o["url"], esc(o["name"]))}</span>{o["html"]}</p>' for o in edition["obituaries_html"])
        obits += more(edition.get("obituaries_more", 0), "more archived repo")
        births = "".join(f'<p><span class="who-line">{ext(b["url"], esc(short_repo(b["repo"])))}</span>{b["html"]}</p>' for b in edition["births_html"])
        births += more(edition.get("births_more", 0), "more new repo")
    else:
        obits = "".join(f'<p><span class="who-line">{ext(o["url"], esc(o["name"]))}</span>{inline(o["text"])}</p>' for o in edition.get("obituaries", []))
        births = "".join(f'<p><span class="who-line">{ext("https://github.com/" + b["repo"], esc(short_repo(b["repo"])))}</span>{inline(b["text"])}</p>' for b in edition.get("births", []))
    obits = obits or '<p class="muted">No deaths reported this week.</p>'
    births = births or '<p class="muted">No births reported this week.</p>'
    q = edition.get("quote")
    quote = (f'<div class="box"><h3>Quote of the week</h3><div class="quote">{ext(q["url"], esc(chr(8220) + q["text"] + chr(8221)))}</div>'
             f'<div class="cite">{esc(q["by"])}, {esc(q.get("where", ""))}, {esc(q.get("date", ""))}</div></div>') if q else ""
    side = f'<div class="side"><div class="box"><h3>Obituaries</h3>{obits}</div><div class="box"><h3>Births</h3>{births}</div>{quote}</div>'

    stories = sorted(edition.get("stories", []), key=lambda s: not by_id.get(edition_who(s), {}).get("starred"))
    story_html = "".join(
        f'<article><div class="beat">{"<span class=star>★</span> " if by_id.get(edition_who(s), {}).get("starred") else ""}{esc(s.get("beat", ""))}</div>'
        f'<h3><a href="{u("/p/" + esc(edition_who(s)) + "/")}">{esc(s["headline"])}</a></h3>'
        f'<p>{s["summary_html"] if "summary_html" in s else inline(s.get("summary", ""))}</p></article>'
        for s in stories
    )
    stories_block = f'<hr class="section-rule"><h2 class="section-hed">Elsewhere on the beat</h2><div class="stories">{story_html}</div>' if story_html else ""

    rows = []
    for p in sorted(people, key=lambda p: (-((stats[p["id"]] or {}).get("public_merged_prs", 0)), -len((stats[p["id"]] or {}).get("videos", [])))):
        st = stats[p["id"]]
        if not st:
            continue
        c = st.get("contributions") or {}
        gh = st["has_github"]
        dash = '<span class="muted">-</span>'
        commits = f'{n(st["commits"])}{"+" if st.get("commits_capped") else ""}' if gh else dash
        cells = [n(st["public_merged_prs"]) if gh else dash, commits, n(len(st["releases"])) if gh else dash, n(len(st["videos"])) if st["has_youtube"] else dash]
        if SHOW_PRIVATE:
            pub, priv = split_pct(c.get("public"), c.get("private"))
            private = n(c["private"]) if c.get("private") is not None else '<span class="muted" title="Private contributions are not shown on their profile">hidden</span>'
            cells += [n(c.get("public")) if c else dash, private if c else dash]
            split = f'<div class="splitbar" title="{pub:g}% public, {priv:g}% private"><span style="width:{pub}%"></span><span style="width:{priv}%"></span></div>' if c else ""
        cells.append(f"{days_active(st)}/7" if c else dash)
        tail = f'<td class="split">{split}</td>' if SHOW_PRIVATE else ""
        rows.append(f'<tr><td><a href="{u("/p/" + esc(p["id"]) + "/")}">{esc(p["name"])}</a><span class="tk">{esc(p["ticker"])}</span></td>'
                    + "".join(f"<td>{x}</td>" for x in cells) + f"{tail}</tr>")
    heads = ["Builder", "Merged PRs", "Commits", "Releases", "Videos"] + (["Public", "Private"] if SHOW_PRIVATE else []) + ["Days active"] + (["Public / private"] if SHOW_PRIVATE else [])
    scores = f"""<hr class="section-rule"><h2 class="section-hed">Sports page: box scores</h2>
<div class="scores-wrap"><table class="scores"><thead><tr>{"".join(f"<th>{h}</th>" for h in heads)}</tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>"""

    corrections = "".join(f"<p>{inline(c)}</p>" for c in edition.get("corrections", []))
    facts_note = (f'<p class="facts-note">This front page is generated from public GitHub and YouTube activity alone. '
                  f'No AI wrote it. {"" if written_count else "Written editions appear here when a writer is set up."}</p>') if facts else ""
    return f"""{head}
<div class="fp">{lead_html}{side}</div>
{stories_block}
{scores}
{f'<hr class="section-rule"><div class="corrections"><h2 class="section-hed">Corrections</h2>{corrections}</div>' if corrections else ""}
{facts_note}"""


# ---------- person pages ----------

def gist(pid, editions, reports, summary, facts):
    """(label, headline, html) for the top of a person's page, best source first."""
    if summary:
        label = f'AI summary · {esc(summary["source"])}' if summary["source"] != "human" else "Summary"
        return label, None, esc(summary["text"])
    for e in reversed(editions):
        if edition_who(e["lead"]) == pid:
            return f'From No. {e["number"]}, {day_short(e["date"])}', e["lead"]["headline"], inline(e["lead"].get("deck", ""))
        for story in e.get("stories", []):
            if edition_who(story) == pid:
                return f'From No. {e["number"]}, {day_short(e["date"])}', story["headline"], inline(story.get("summary", ""))
    if reports:
        items = [d for k, d in blocks(reports[0][1]) if k == "list"]
        if items:
            text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", items[0][1][0][1])
            text = re.sub(r"\s*\(\s*(,\s*)*\)", "", text)
            text = text if len(text) < 320 else text[:317].rsplit(" ", 1)[0] + "..."
            return f"From the {day_short(reports[0][0][:10])} report", None, inline(text)
    if facts:
        return "Facts desk · no AI", facts["headline"], facts["summary_html"]
    return None, None, None


TYPE_LABELS = {"pr": "Merged PR", "release": "Release", "repo": "New repo", "video": "Video"}


def recent_list(items):
    rows = "".join(
        f'<li><span class="chip">{TYPE_LABELS[i["type"]]}</span>{ext(i["url"], esc(i["title"]))}'
        f'<span class="label">{esc(i["repo"]) + " · " if i["repo"] else ""}{day_short(i["date"])}</span></li>'
        for i in items
    )
    return f'<ul class="recent">{rows}</ul>'


def videos_block(st):
    if not st["videos"] and not st.get("youtube_channel"):
        return ""
    cards = "".join(
        f'<a class="video" href="{esc(safe_url(v["url"]))}" target="_blank" rel="noopener">'
        f'<img src="{esc(safe_url(v["thumbnail"]))}" alt="" loading="lazy" width="320" height="180">'
        f'<span class="v-title">{esc(v["title"])}</span>'
        f'<span class="label">{"Short · " if v["short"] else ""}{day_short(v["date"])}{" · " + n(v["views"]) + " views" if isinstance(v.get("views"), int) else ""}</span></a>'
        for v in st["videos"][:6] if config.is_http_url(v.get("thumbnail"))
    )
    channel = st.get("youtube_channel")
    more = f'<p class="muted">{ext(channel["url"], "All videos on " + esc(channel["title"] or "YouTube"))}</p>' if channel else ""
    body = f'<div class="videos">{cards}</div>' if cards else '<p class="muted">No new videos in the last seven days.</p>'
    return f'<div class="sect">On YouTube</div>{body}{more}'


def person_page(p, st, reports, profile, people, editions, summary, facts):
    pid = p["id"]
    name = p["name"]
    you = bool(p.get("self"))
    star = '<span class="who-star" title="Starred">★</span>' if p.get("starred") else ""
    window = f'Week of {day_short(st["window"]["start"])} to {day_short(st["window"]["end"])}' if st else "Not collected yet"
    rank = {"X": 0, "YouTube": 1, "Twitch": 2, "Bluesky": 3, "Mastodon": 4, "LinkedIn": 5}
    listed = sorted((st or {}).get("links", []) or p["links"], key=lambda l: rank.get(l["label"], 9))
    links = ([("GitHub", f"https://github.com/{p['github']}")] if p["github"] else []) + [(l["label"], l["url"]) for l in listed]
    links += [(f"@{o}", f"https://github.com/{o}") for o in p.get("orgs", [])]
    link_html = "".join(ext(url, esc(label), "slink") for label, url in links)
    avatar = (st or {}).get("avatar")
    avatar_html = f'<img class="avatar" src="{esc(safe_url(avatar))}" alt="" width="64" height="64">' if avatar and config.is_http_url(avatar) else ""

    label, hed, summary_html = gist(pid, editions, reports, summary, facts)
    gist_html = ""
    if summary_html:
        gist_html = (f'<div class="gist"><div class="label">The gist · {label}</div>'
                     f'{f"<h2 class=gist-hed>{esc(hed)}</h2>" if hed else ""}<p>{summary_html}</p></div>')

    card, day_panels = "", ""
    c = (st or {}).get("contributions")
    if st and (c or st["has_youtube"]):
        tiles = []
        if st["has_github"]:
            commits = f'{n(st["commits"])}{"+" if st.get("commits_capped") else ""}'
            tiles += [(n(st["public_merged_prs"]), "PRs merged"), (commits, "commits")]
            if c:
                tiles += [(f"{days_active(st)}/7", "days active"), (n(week_total(st)), "contributions")]
            else:
                tiles += [(n(len(st["releases"])), "releases")]
        if st["has_youtube"]:
            tiles.append((n(len(st["videos"])), "videos this week"))
            if not st["has_github"]:
                latest = (st["videos"] or [None])[0]
                tiles.append((day_short(latest["date"]) if latest else "-", "latest video"))
        tile_html = "".join(f"<div><b>{v}</b><span>{l}</span></div>" for v, l in tiles)
        split_html, spark_html = "", ""
        if c:
            if SHOW_PRIVATE:
                pub_pct, priv_pct = split_pct(c.get("public"), c.get("private"))
                if c.get("private") is None:
                    split_line = f'{n(c.get("public"))} public · private hidden'
                    split_tip = f"{'You have' if you else 'They have'} not made private contributions visible on GitHub, so only public work is counted."
                else:
                    split_line = f'{n(c.get("public"))} public · {n(c["private"])} private'
                    split_tip = "Private work shows only as a count. GitHub never shares what it was or where it happened."
                split_html = f"""<div class="pc-split" title="{esc(split_tip)}">
    <div class="splitbar"><span style="width:{pub_pct}%"></span><span style="width:{priv_pct}%"></span></div>
    <div class="pc-line"><span class="legend"><span class="sw pub"></span><span class="sw priv"></span></span>{split_line}<span class="tip" aria-hidden="true">?</span></div>
  </div>"""
            by_day = c.get("by_day") or []
            priv_of = (lambda d: d["private"] or 0) if SHOW_PRIVATE else (lambda d: 0)
            peak = max([sum(d["public"].values()) + priv_of(d) for d in by_day] or [1]) or 1
            bars, panels = [], []
            for d in by_day:
                pub = sum(d["public"].values())
                priv = priv_of(d)
                day = dt.date.fromisoformat(d["date"])
                did = f"day-{d['date']}"
                lab = f"{day:%a %b} {day.day}: {pub} public" + (f", {priv} private" if SHOW_PRIVATE else "")
                bars.append(
                    f'<button class="sp" type="button" aria-controls="{did}" aria-expanded="false" title="{lab}" aria-label="{lab}">'
                    f'<i class="priv" style="height:{round(100 * priv / peak, 1)}%"></i><i class="pub" style="height:{round(100 * pub / peak, 1)}%"></i>'
                    f'<b>{day:%a}</b></button>'
                )
                parts = [plural(v, one, many) for one, many, v in (
                    ("commit", "commits", d["public"]["commits"]), ("PR", "PRs", d["public"]["pull_requests"]),
                    ("issue", "issues", d["public"]["issues"]), ("review", "reviews", d["public"]["reviews"])) if v]
                prs = "".join(
                    f'<li>{ext(pr["url"], esc(pr["title"]))}<span class="label">{esc(short_repo(pr["repo"]))} #{pr["number"]} · {esc(pr["state"])}</span></li>'
                    for pr in d.get("prs", [])
                )
                private_text = ""
                if SHOW_PRIVATE:
                    private_text = (f'<span><span class="sw priv"></span><b>{n(priv)} private</b>, type and repo unknown</span>' if c.get("private") is not None
                                    else '<span><span class="sw priv"></span>Private work hidden</span>')
                panels.append(f"""<section class="day-panel" id="{did}" hidden>
  <div class="dp-head"><h3>{day:%A, %B} {day.day}</h3><button class="btn icon dp-close" type="button" aria-label="Close">✕</button></div>
  <div class="dp-split"><span><span class="sw pub"></span><b>{n(pub)} public</b>{": " + ", ".join(parts) if parts else ""}</span>{private_text}</div>
  {f'<ul class="dp-prs">{prs}</ul>' if prs else '<p class="muted">No public PRs opened this day.</p>'}
</section>""")
            if bars:
                spark_html = f'<div class="spark" aria-label="Contributions per day. Choose a day for details.">{"".join(bars)}</div><div class="spark-hint">Tap a day for its PRs</div>'
            day_panels = "".join(panels)
        card = f"""<aside class="pcard" aria-label="This week in numbers">
  <div class="pc-stats">{tile_html}</div>
  {split_html}
  {spark_html}
</aside>"""

    ticker_html = f'<span class="tk">{esc(p["ticker"])}</span>' if p["ticker"] else ""
    head = f"""<div class="runhead"><span>{esc(PAPER)} · The Beat</span><span>{window}</span></div>
<div class="phead">
  <div class="phead-main">
    <div class="who-row">{avatar_html}<h1 class="who">{star}{esc(name)}{ticker_html}</h1></div>
    {f'<p class="known">{inline(p["known_for"])}</p>' if p.get("known_for") else ""}
    <div class="slinks">{link_html}</div>
    {gist_html}
  </div>
  {card}
</div>
{day_panels}"""

    if not st:
        return head + '<p class="known" style="margin-top:30px">Their first numbers arrive after the next collection run.</p>'

    if reports:
        stem, md = reports[0]
        kind = "Weekly report" if stem.endswith("weekly") else "Midweek update"
        week_html = f"""<div class="sect">This week</div>
<div class="report-meta label"><span>{kind} · {day_short(stem[:10])}</span><span class="report-actions"><a class="btn sm" href="{u("/r/" + esc(stem) + "/")}">Whole issue</a><a class="btn sm" href="{u("/p/" + esc(pid) + "/" + esc(stem) + "/")}">Permalink</a></span></div>
<div class="prose">{render_md(md, demote=1)}</div>"""
    elif st["recent"]:
        week_html = f'<div class="sect">This week, from the record</div>{recent_list(st["recent"])}'
    else:
        week_html = '<div class="sect">This week, from the record</div><p class="muted">Nothing public landed in the last seven days.</p>'

    repos_list = visible_repos(st)
    peak_repo = max((r["merged_prs"] + r["commits"] for r in repos_list), default=1) or 1
    repos = "".join(
        f'<div class="repo"><span>{ext("https://github.com/" + r["repo"], esc(r["repo"]))}'
        + (f'<span class="chip{" priv" if r.get("private") else ""}">{"Private" if r.get("private") else "Public"}</span>' if SHOW_PRIVATE else "")
        + f'</span><span class="cnt">{plural(r["merged_prs"], "PR")} · {plural(r["commits"], "commit")}</span>'
        f'<span class="bar" style="width:{max(1, round(100 * (r["merged_prs"] + r["commits"]) / peak_repo))}%"></span></div>'
        for r in repos_list
    )
    repos_html = f'<div class="sect">Where the time went</div><div class="repos">{repos}</div>' if repos else ""

    profile_html = f'<div class="sect">The file on {esc(name)}</div><div class="prose">{render_md(profile, demote=1)}</div>' if profile else ""
    issues = "".join(
        f'<li><a href="{u("/p/" + esc(pid) + "/" + esc(stem) + "/")}">{esc(report_title(md, stem))}</a><span class="label">{day_short(stem[:10])}</span></li>'
        for stem, md in reports
    )
    issues_html = f'<div class="sect">Back issues</div><ul class="issues">{issues}</ul>' if issues else ""

    order = ordered(people)
    i = next(k for k, q in enumerate(order) if q["id"] == pid)
    pager = ""
    if len(order) > 1:
        prv, nxt = order[i - 1], order[(i + 1) % len(order)]
        pager = (f'<nav class="pager"><a href="{u("/p/" + esc(prv["id"]) + "/")}"><span class="label">[ Previous</span>{esc(prv["name"])}</a>'
                 f'<a href="{u("/p/" + esc(nxt["id"]) + "/")}" style="text-align:right"><span class="label">Next ]</span>{esc(nxt["name"])}</a></nav>')

    return f'{head}<div class="reading">{week_html}{videos_block(st)}{repos_html}{profile_html}{issues_html}{pager}</div>'


def report_page(p, stem, md):
    kind = "Weekly report" if stem.endswith("weekly") else "Midweek update"
    return f"""<div class="runhead"><span>{esc(PAPER)} · <a href="{u("/p/" + esc(p["id"]) + "/")}">{esc(p["name"])}</a></span><span>{kind} · {day_short(stem[:10])}</span></div>
<div class="reading"><h1 class="page-hed">{inline(report_title(md, stem))}</h1><div class="prose">{render_md(md, demote=1)}</div></div>"""


# ---------- issues: one page per writer run, linked from the email ----------

def issues_of(people, reports):
    """{"<date>-<kind>": [(person, md), ...]} in display order, newest issue first."""
    out = {}
    for p in people:
        for stem, md in reports[p["id"]]:
            out.setdefault(stem, []).append((p, md))
    return dict(sorted(out.items(), reverse=True))


def folded_report(md):
    """A report with each section folded: the TL;DR (or a report with no sections) stays open."""
    parts = []
    for heading, content in digest.sections(md):
        body = f'<div class="prose">{render_blocks(content, demote=1)}</div>'
        if heading is None:
            parts.append(body)
        else:
            open_ = " open" if digest.is_tldr(heading) else ""
            parts.append(f'<details class="rsect"{open_}><summary>{inline(heading)}</summary>{body}</details>')
    return "".join(parts)


def issue_page(stem, items, edition):
    kind = digest.KIND_NAMES[digest.kind_of(stem)]
    span = digest.span_of(digest.title_of(items[0][1], "")) or day_long(stem[:10])
    bullets = digest.tldr(edition, items)
    tldr_html = f'<div class="tldr"><div class="sect">TL;DR</div><ul>{"".join(f"<li>{esc(b)}</li>" for b in bullets)}</ul></div>' if bullets else ""
    toc = "".join(
        f'<li><a href="#{esc(p["id"])}">{esc(p["name"])}</a>'
        f'<span class="toc-line">{"Quiet this time" if digest.is_quiet(md) else esc(digest.one_liner(md, 120))}</span></li>'
        for p, md in items
    )
    people = "".join(
        f"""<section class="issue-person" id="{esc(p["id"])}">
  <div class="ip-head"><h2><a href="{u("/p/" + esc(p["id"]) + "/")}">{esc(p["name"])}</a></h2>
  <span class="label"><a href="{u("/p/" + esc(p["id"]) + "/" + esc(stem) + "/")}">Permalink</a> · <a href="#top">Contents</a></span></div>
  {folded_report(md)}
</section>"""
        for p, md in items
    )
    return f"""<div class="runhead" id="top"><span>{esc(PAPER)} · {kind}</span><span>{day_short(stem[:10])}</span></div>
<h1 class="page-hed">{esc(span)}</h1>
<p class="page-sub">{kind} on {plural(len(items), "person", "people")}. Sections fold: open the ones you want.</p>
{tldr_html}
<nav class="toc" aria-label="In this issue"><div class="sect">In this issue</div><ol>{toc}</ol></nav>
<div class="issue-people">{people}</div>"""


# ---------- archive + settings ----------

def archive_page(editions, people, reports):
    items = "".join(
        f'<li><a href="{u("/e/" + esc(e["stem"]) + "/")}">{esc(e["lead"]["headline"])}</a><span class="label">No. {e["number"]} · {day_long(e["date"])}</span></li>'
        for e in reversed(editions)
    ) or '<li class="muted">No written editions yet. The front page is a facts edition, rebuilt from public activity on every run.</li>'
    report_items = "".join(
        f'<li><a href="{u("/r/" + esc(stem) + "/")}">{digest.KIND_NAMES[digest.kind_of(stem)]}, {day_long(stem[:10])}</a>'
        f'<span class="label">{plural(len(items), "person", "people")}: ' + ", ".join(esc(p["name"]) for p, _ in items) + "</span></li>"
        for stem, items in issues_of(people, reports).items()
    )
    reports_html = f'<div class="sect">Reports by date</div><ul class="arch">{report_items}</ul>' if report_items else ""
    return f"""<h1 class="page-hed">Back issues</h1><p class="page-sub">Every written front page, newest first, and every report behind them.</p>
<ul class="arch">{items}</ul>
{reports_html}"""


def settings_page(people):
    data = json.dumps([{k: v for k, v in p.items() if v not in (None, "", [], False) or k in ("github", "name")} for p in people],
                      ensure_ascii=False).replace("</", "<\\/")
    return f"""<h1 class="page-hed">People and settings</h1>
<p class="page-sub">Star the people you care about most so they lead the sidebar and the front page. Drag or use the arrows to reorder. Removing someone stops new reports; their back issues stay.</p>
<ul class="set-list" id="set-list"></ul>
<div class="sect">Follow someone new</div>
<form class="add-form" id="add-form" autocomplete="off">
  <label>GitHub username<input name="github" id="add-github" required placeholder="e.g. octocat"></label>
  <label>What you call them<input name="name" id="add-name" placeholder="e.g. Simon Willison"></label>
  <label class="wide">Orgs where their work lands (optional, comma separated)<input name="orgs" id="add-orgs" placeholder="e.g. datasette"></label>
  <label class="wide">Known for (optional)<input name="known_for" id="add-known" placeholder="One line on who they are"></label>
  <div class="wide"><button class="btn" type="submit">Add to the list</button></div>
</form>
<div class="save-bar"><span class="status" id="status" role="status">No changes.</span><button class="btn solid" id="save" type="button" disabled>Save changes</button></div>
<script type="application/json" id="watchlist-data">{data}</script>"""


def people_page():
    """The People page of a paper kept in Cloudflare: a shell that app.js fills from the Worker's /api/people."""
    return """<h1 class="page-hed">People</h1>
<p class="page-sub">Who the paper follows. Changes save as you make them, and the next collection uses them; back issues always stay.</p>
<div id="people-app" data-api="/api/people" aria-live="polite"><p class="muted">Loading the watch list…</p></div>
<div class="sect">Follow someone new</div>
<form class="add-form" id="people-add" autocomplete="off">
  <label class="wide">GitHub username or profile link<input name="github" id="people-add-github" required autocomplete="off" placeholder="e.g. octocat"></label>
  <div class="wide"><button class="btn solid" type="submit">Research and draft them</button>
  <span class="status" id="people-add-status" role="status"></span></div>
</form>
<p class="muted people-help">A job looks them up on GitHub and drafts their entry and profile with a list of things to check. Approve the draft to start following them.</p>"""


# ---------- who to follow ----------

PART_LABELS = {"stack": "Stack overlap", "activity": "Activity", "quality": "Quality", "relevance": "Your interests", "network": "Your network"}


def add_button(login, cmd):
    """Add to PR Post: a draft on the People page when the Worker is there, else a copied command."""
    if people_api():
        return f'<button class="btn solid" type="button" data-draft="{esc(login)}" data-api="{DRAFTS_API}">Add to PR Post</button>'
    return f'<button class="btn solid" type="button" data-copy="{esc(cmd)}">Add to PR Post</button>'


def follow_card(c, report, repo_url):
    login = c["login"]
    rec = c["recommendation"].lower()
    avatar = c.get("avatar") or report.get("avatar")
    avatar_html = f'<img class="fc-avatar" src="{esc(safe_url(avatar))}" alt="" width="56" height="56" loading="lazy">' if avatar and config.is_http_url(avatar) else ""
    parts = "".join(
        f'<div class="fc-part"><span class="fc-pl">{esc(PART_LABELS.get(k, k))}</span>'
        f'<span class="fc-bar" aria-hidden="true"><span style="width:{round(100 * p["points"] / p["max"]) if p["max"] else 0}%"></span></span>'
        f'<span class="fc-pts num">{round(p["points"])}/{p["max"]:g}</span><span class="fc-pw">{esc(p["why"])}</span></div>'
        for k, p in c["parts"].items()
    )
    found = "".join(f"<li>{ext(f['url'], esc(f['text']))}</li>" for f in c.get("found", [])[:6])
    cmd = follow_lib.add_command(repo_url, login)
    add_href = follow_lib.add_url(repo_url)
    add_link = f' or {ext(add_href, "run it from GitHub")} with their username' if add_href else ""
    return f"""<article class="fcard" id="{esc(login.lower())}">
  <div class="fc-head">{avatar_html}<div class="fc-id"><h2 class="fc-name">{esc(c["name"])}</h2><div class="label">@{esc(login)} · researched {day_short(c["researched"])}</div></div>
  <div class="fc-score fc-{esc(rec)}" title="Fit score out of 100"><b class="num">{c["score"]}</b><span>{esc(c["recommendation"])}</span></div></div>
  <p class="fc-why">{esc(c["why_follow"])}</p>
  <div class="fc-parts">{parts}</div>
  <div class="fc-actions">{add_button(login, cmd)}{ext(c["profile"], "Open GitHub profile", "btn")}<span class="fc-status" role="status"></span></div>
  <details class="fc-report"><summary>Mini report</summary>
    <div class="prose">{render_md(follow_lib.render_md(report), demote=1)}</div>
    {f'<div class="sect">How they turned up</div><ul class="fc-found">{found}</ul>' if found else ""}
    {"" if people_api() else f'<p class="fc-cmd">Add to PR Post drafts their watch-list entry and profile on a branch for you to review: <code>{esc(cmd)}</code>{add_link}.</p>'}
  </details>
</article>"""


def deck_order(latest):
    """Who the picks deck deals: this week's picks first, then the other Follow and Maybe candidates."""
    by_login = {c["login"]: c for c in latest["candidates"]}
    picks = [by_login[x] for x in latest.get("picks", []) if x in by_login]
    rest = [c for c in latest["candidates"] if c["login"] not in latest.get("picks", []) and c["recommendation"] != "Skip"]
    return picks, rest


def follow_page(latest, reports, repo_url):
    picks, rest = deck_order(latest)
    skipped = [c for c in latest["candidates"] if c["login"] not in latest.get("picks", []) and c["recommendation"] == "Skip"]
    card = lambda c: follow_card(c, reports[c["login"].lower()], repo_url)
    picks_html = "".join(card(c) for c in picks) or '<p class="muted">No new picks this week: everyone worth suggesting was suggested in the last few weeks.</p>'
    rest_html = f'<div class="sect">Also worth a look</div><div class="fgrid">{"".join(card(c) for c in rest)}</div>' if rest else ""
    skip_html = (f'<details class="fc-skipped"><summary class="sect">Researched, but not a fit ({len(skipped)})</summary><div class="fgrid">{"".join(card(c) for c in skipped)}</div></details>'
                 if skipped else "")
    stack = ", ".join(latest.get("stack", [])[:8])
    toc_items = "".join(
        f'<li><a href="#{esc(c["login"].lower())}">{esc(c["name"])}</a><span class="toc-line">{c["score"]} · {esc(c["recommendation"])}</span></li>'
        for c in picks + rest + skipped
    )
    toc_html = f'<nav class="toc" aria-label="Everyone on this page"><div class="sect">On this page</div><ol>{toc_items}</ol></nav>' if toc_items else ""
    cta = f'<p class="picks-cta"><a class="btn solid" href="{u("/follow/picks/")}">Swipe through this week\'s picks</a></p>' if picks or rest else ""
    return f"""<div class="runhead"><span>{esc(PAPER)} · Who to follow</span><span>Updated {day_short(latest["date"])}</span></div>
<div class="follow-intro">
<h1 class="page-hed">Who to follow</h1>
<p class="page-sub">Builders you do not follow yet, found through the people you read, the repos you build on and projects like yours. Each has a one-time mini report from public sources; every fact links to where it came from.</p>
{cta}
</div>
{toc_html}
<div class="sect">This week's picks</div>
<div class="fgrid">{picks_html}</div>
{rest_html}
{skip_html}
<p class="facts-note">The fit score adds up five parts: how much of your stack they use ({esc(stack)}), how active they have been over 12 weeks, the quality and reach of their work, how many of your interests their work touches, and whether the builders you already read follow or work with them. Weights and thresholds live under [follow] in watchlist.toml. Nothing here follows anyone for you: Open GitHub profile only opens their page on GitHub.</p>"""


def follow_picks_page(latest, reports, repo_url):
    """This week's picks, then the Maybes, one card at a time; follow.js deals them from the JSON below.

    On a paper kept in Cloudflare, Add posts to the People page's drafts endpoint (the same path as
    "Research and draft them"), so the person waits there as a draft. Elsewhere it copies the command.
    """
    picks, rest = deck_order(latest)
    api = people_api()
    cards = []
    for c in picks + rest:
        r = reports[c["login"].lower()]
        top = max(r.get("builds") or [{}], key=lambda b: b.get("stars", 0))
        avatar = c.get("avatar") or r.get("avatar")
        card = {
            "login": c["login"], "name": c["name"], "profile": safe_url(c["profile"]),
            "avatar": safe_url(avatar) if avatar and config.is_http_url(avatar) else "",
            "score": c["score"], "rec": c["recommendation"], "why": c["why_follow"],
            "stars": r["standing"]["stars"], "followers": r["standing"]["followers"],
            "active": r["activity"]["last_12w"],
            "top": {"repo": top["repo"], "stars": top.get("stars", 0)} if top.get("repo") else None,
        }
        if not api:
            card["cmd"] = follow_lib.add_command(repo_url, c["login"])
        cards.append(card)
    data = json.dumps({"week": latest["date"], "picks": len(picks), "cards": cards}, ensure_ascii=False).replace("</", "<\\/")
    attrs = f'data-api="{DRAFTS_API}" data-people="{u("/people/")}"' if api else ""
    keys = '<span class="picks-keys">Keys: right arrow adds, left arrow skips, O opens their GitHub profile, Z goes back.</span>'
    how = "add them to the paper as a draft, or skip them for this week" if api else "copy the command that adds them, or skip them for this week"
    return f"""<div class="runhead"><span>{esc(PAPER)} · <a href="{u("/follow/")}">Who to follow</a></span><span>Week of {day_short(latest["date"])}</span></div>
<h1 class="page-hed picks-hed">This week's picks</h1>
<p class="page-sub picks-sub">One at a time, then the maybes: {how}. {keys}</p>
<div id="picks-app" {attrs} data-all="{u("/follow/")}"><noscript><p class="muted">This page needs JavaScript; <a href="{u("/follow/")}">the full list</a> works without it.</p></noscript></div>
<script type="application/json" id="picks-data">{data}</script>"""


# ---------- build ----------

def build(paper, out):
    """Build the whole site for `paper` into `out`. Returns the feed dict."""
    cfg = config.load(paper)
    configure(cfg["site"])
    people = cfg["people"]
    snaps, editions, reports, profiles = load(paper, cfg)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "site" / "assets", out / "assets")

    def write(path, text):
        f = out / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)

    # "Who to follow" names people the owner does not follow yet, so it stays off public sites unless asked for.
    global HAS_FOLLOW
    follow_latest, follow_reports = follow_lib.load_latest(paper, cfg)
    HAS_FOLLOW = bool(follow_latest and follow_latest["candidates"]) and (SITE["mode"] == "private" or cfg["follow"]["public_page"])

    order = ordered(people)
    stats = {p["id"]: latest_stats(snaps, p["id"]) for p in people}
    facts = facts_edition(order, stats)
    facts_by_id = {s["id"]: s for s in (facts or {}).get("stories", [])}
    if facts:
        lead_p = next(p for p in people if p["id"] == facts["lead"]["id"])
        facts_by_id[lead_p["id"]] = facts_story(lead_p, stats[lead_p["id"]])
    window_start = min((st["window"]["start"] for st in stats.values() if st), default="9999")
    summaries = feedlib.load_summaries(paper, window_start)

    # A written edition leads only while it is current; otherwise the facts edition does.
    latest = editions[-1] if editions else None
    if facts and (not latest or latest["date"] < facts["date"]):
        latest = facts
    sheet = lambda body: f'<div class="sheet">{body}</div>'
    write("index.html", layout(PAPER, sheet(front_page(latest, order, snaps, len(editions))), order, snaps, page="front"))
    for e in editions:
        write(f"e/{e['stem']}/index.html", layout(f"No. {e['number']} · {PAPER}", sheet(front_page(e, order, snaps, len(editions))), order, snaps, page="archive"))

    for p in order:
        body = person_page(p, stats[p["id"]], reports[p["id"]], profiles.get(p["id"]), people, editions, summaries.get(p["id"]), facts_by_id.get(p["id"]))
        desc = f"What {p['name']} shipped this week, from public activity." + (f" {p['known_for']}" if p["known_for"] else "")
        write(f"p/{p['id']}/index.html", layout(f"{p['name']} · {PAPER}", sheet(body), order, snaps, current=p["id"], description=desc))
        for stem, md in reports[p["id"]]:
            write(f"p/{p['id']}/{stem}/index.html", layout(f"{report_title(md, stem)} · {PAPER}", sheet(report_page(p, stem, md)), order, snaps, current=p["id"]))

    for stem, items in issues_of(order, reports).items():
        title = f"{digest.KIND_NAMES[digest.kind_of(stem)]}, {day_short(stem[:10])} · {PAPER}"
        write(f"r/{stem}/index.html", layout(title, sheet(issue_page(stem, items, digest.load_edition(paper, stem))), order, snaps, page="archive",
                                            description=f"Every report from the {day_long(stem[:10])} issue of {PAPER}."))
    write("archive/index.html", layout(f"Back issues · {PAPER}", sheet(archive_page(editions, order, reports)), order, snaps, page="archive"))
    if HAS_FOLLOW:
        repo_url = cfg["links"].get("repo")
        write("follow/index.html", layout(f"Who to follow · {PAPER}", sheet(follow_page(follow_latest, follow_reports, repo_url)),
                                          order, snaps, page="follow", description=f"Who to follow: builders suggested for {PAPER}.", scripts=("follow.js",)))
        write("follow/picks/index.html", layout(f"This week's picks · {PAPER}", sheet(follow_picks_page(follow_latest, follow_reports, repo_url)),
                                                order, snaps, page="follow", description=f"This week's picks for {PAPER}, one at a time.", scripts=("follow.js",)))
    if people_api():
        write("people/index.html", layout(f"People · {PAPER}", sheet(people_page()), order, snaps, page="people"))
    elif SITE["mode"] == "private":
        write("settings/index.html", layout(f"People and settings · {PAPER}", sheet(settings_page(people)), order, snaps, page="settings"))
    write("404.html", layout(f"Not found · {PAPER}", sheet(f'<h1 class="page-hed">Page not found</h1><p class="page-sub">That page is not in this paper.</p><p><a class="btn solid" href="{u("/")}">Back to the front page</a></p>'), order, snaps))

    feed = None
    if snaps:
        feed = feedlib.build(cfg, snaps[-1], summaries)
        write("feed.json", json.dumps(feed, indent=1, ensure_ascii=False) + "\n")
    if SITE["mode"] == "public":
        write(".nojekyll", "")
    count = sum(1 for _ in out.rglob("*.html"))
    print(f"Built {count} pages{' and feed.json' if feed else ''} into {out}")
    return feed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper", help="folder holding watchlist.toml (default: $PR_POST_PAPER or the repo root)")
    ap.add_argument("--out", default=str(ROOT / "dist"), help="output folder (default: dist/)")
    args = ap.parse_args()
    try:
        build(config.paper_dir(args.paper), Path(args.out).resolve())
    except config.ConfigError as e:
        sys.exit(f"Config error: {e}")


if __name__ == "__main__":
    main()
