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
import feed as feedlib  # noqa: E402
from md import blocks, safe_url  # noqa: E402

ROOT = config.ROOT
FONTS = ("https://fonts.googleapis.com/css2?family=Bodoni+Moda:opsz,wght@6..96,800;6..96,900"
         "&family=Newsreader:opsz,wght@6..72,400;6..72,600&family=JetBrains+Mono:wght@500;700&display=swap")
ARROW_MIN_DAYS = 4  # compare against a snapshot at least this old
FACTS_SIDE_MAX = 6  # births and obituaries the facts edition prints before "plus N more"
REPO_URL = "https://github.com/n8watkins/pr-post"
FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%2317171a'/%3E"
           "%3Ctext x='16' y='23' font-family='Georgia,serif' font-weight='900' font-size='20' text-anchor='middle' fill='%23f8f7f3'%3EP%3C/text%3E%3C/svg%3E")

# Set by configure() from [site] in watchlist.toml.
SITE = dict(config.SITE_DEFAULTS)
PAPER = SITE["title"]
BASE = ""
SHOW_PRIVATE = False


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
    out = []
    for kind, data in blocks(md):
        if kind == "h1" and skip_h1:
            continue
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
    if SITE["mode"] == "private":
        pages.append(("settings", "/settings/", "People and settings"))
    nav = "".join(f'<a href="{u(href)}"{" aria-current=\"page\"" if page == key else ""}>{label}</a>' for key, href, label in pages)
    legend = ('<span class="legend"><span class="sw pub"></span>public</span> &nbsp; <span class="legend"><span class="sw priv"></span>private</span><br>'
              "Numbers are this week's contributions." if SHOW_PRIVATE else "Numbers are this week's public contributions.")
    return f"""<aside class="rail" id="rail" aria-label="People">
  <a class="rail-brand" href="{u("/")}">{esc(PAPER)}</a>
  <nav class="rail-nav">{nav}</nav>
  <input class="rail-search" id="rail-search" type="search" placeholder="Find a person  ( / )" aria-label="Find a person" autocomplete="off">
  {groups}
  <div class="rail-foot">
    {legend} Press [ and ] to move between people.
  </div>
</aside>"""


def layout(title, body, people, snaps, current=None, page=None, description=None):
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
  <header class="topbar"><button id="rail-toggle" type="button" aria-controls="rail" aria-expanded="false">People</button><a href="{u("/")}">{esc(PAPER)}</a></header>
  <main class="main">
{body}
<footer class="colophon">{esc(PAPER)} · built from public GitHub and YouTube activity{private_note} · <a href="{u("/feed.json")}">feed.json</a> · made with <a href="{REPO_URL}">pr-post</a></footer>
  </main>
</div>
</div>
<script src="{u("/assets/app.js")}" defer></script>
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
    # Two copies so the crawl loops without a gap and the strip is full at rest.
    track = "".join(items)
    return (f'<div class="ticker" aria-label="Commit Index: contributions this week"><div class="ticker-track">'
            f'{track}<span aria-hidden="true" style="display:contents">{track}</span></div></div>')


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
<div class="report-meta label"><span>{kind} · {day_short(stem[:10])}</span><a href="{u("/p/" + esc(pid) + "/" + esc(stem) + "/")}">Permalink</a></div>
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


# ---------- archive + settings ----------

def archive_page(editions, people, reports):
    items = "".join(
        f'<li><a href="{u("/e/" + esc(e["stem"]) + "/")}">{esc(e["lead"]["headline"])}</a><span class="label">No. {e["number"]} · {day_long(e["date"])}</span></li>'
        for e in reversed(editions)
    ) or '<li class="muted">No written editions yet. The front page is a facts edition, rebuilt from public activity on every run.</li>'
    by_date = {}
    for p in people:
        for stem, md in reports[p["id"]]:
            by_date.setdefault(stem[:10], []).append((p, stem, md))
    report_items = "".join(
        f'<li><span class="label">{day_long(d)}</span>' + " · ".join(
            f'<a href="{u("/p/" + esc(p["id"]) + "/" + esc(stem) + "/")}" style="font-family:var(--body);font-size:18px;font-weight:600">{esc(p["name"])}</a>' for p, stem, _ in items
        ) + "</li>"
        for d, items in sorted(by_date.items(), reverse=True)
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
  <label>GitHub username<input name="github" id="add-github" required placeholder="e.g. simonw"></label>
  <label>What you call them<input name="name" id="add-name" placeholder="e.g. Simon Willison"></label>
  <label class="wide">Orgs where their work lands (optional, comma separated)<input name="orgs" id="add-orgs" placeholder="e.g. datasette"></label>
  <label class="wide">Known for (optional)<input name="known_for" id="add-known" placeholder="One line on who they are"></label>
  <div class="wide"><button class="btn" type="submit">Add to the list</button></div>
</form>
<div class="save-bar"><span class="status" id="status" role="status">No changes.</span><button class="btn solid" id="save" type="button" disabled>Save changes</button></div>
<script type="application/json" id="watchlist-data">{data}</script>"""


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

    write("archive/index.html", layout(f"Back issues · {PAPER}", sheet(archive_page(editions, order, reports)), order, snaps, page="archive"))
    if SITE["mode"] == "private":
        write("settings/index.html", layout(f"People and settings · {PAPER}", sheet(settings_page(people)), order, snaps, page="settings"))
    write("404.html", layout(f"Not found · {PAPER}", sheet(f'<h1 class="page-hed">Page not found</h1><p class="page-sub"><a href="{u("/")}">Back to the front page</a></p>'), order, snaps))

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
