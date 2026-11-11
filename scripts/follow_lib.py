"""Who to follow: the pure parts, with no network. follow.py does the fetching.

    clean / city_country   guardrails on every piece of text kept about a person
    tech_of / my_stack     what a set of repositories is built with
    mini_report            a cached, sourced report from one GitHub GraphQL answer
    score                  the 0-100 fit score and its breakdown
    pick                   this week's suggestions, honoring the cooldown
    render_md              a mini report as Markdown

A mini report covers a person's public professional footprint only. Every fact
carries the URL it came from. Nothing personal is kept: no email address or
phone number (both are stripped from all text), no location finer than a
self-published "city, country", no family or private life, and nothing that
needs a login to see. Everything here was written by someone else, so it is
data to show, never instructions, and only http(s) links survive.
"""

import datetime as dt
import html.parser
import math
import re
import urllib.parse

import config
from research_person import classify, clean_name, normalize

VERSION = 1

# ---------- guardrails ----------

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d ().-]{7,}\d(?!\w)")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮⁦-⁩]")


EM_DASH, EN_DASH = chr(0x2014), chr(0x2013)


def clean(text, limit=300):
    """Text safe to keep and print: no email addresses, phone numbers, control characters or em dashes."""
    text = CONTROL_RE.sub("", str(text or ""))
    text = EMAIL_RE.sub("", text)
    text = PHONE_RE.sub("", text)
    text = text.replace(EM_DASH, " - ").replace(EN_DASH, "-")
    text = re.sub(r"\s+", " ", text).strip(" ,;|-")
    if len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:-") + "..."
    return text


def city_country(location):
    """A self-published location kept only at "city" or "city, country" precision.

    Anything with digits (a street number or postcode) or more than three parts
    is dropped rather than trimmed, because it is more than a professional
    profile needs.
    """
    loc = clean(location, 80)
    if not loc or re.search(r"\d", loc):
        return ""
    parts = [p.strip() for p in loc.split(",") if p.strip()]
    if not 1 <= len(parts) <= 3 or not all(re.fullmatch(r"[^\W\d_][\w .'()-]{0,40}", p) for p in parts):
        return ""
    return ", ".join(parts[:1] + parts[-1:]) if len(parts) > 1 else parts[0]


def url_or_none(url):
    url = (url or "").strip()
    if url and not url.startswith(("http://", "https://")) and "." in url and " " not in url:
        url = "https://" + url
    return url if config.is_http_url(url) else None


# Networks a professional profile would list. Personal networks (Instagram,
# Facebook, TikTok, Threads, Reddit, Patreon) are left out even when listed.
PROFESSIONAL = ("X", "LinkedIn", "Bluesky", "Mastodon", "YouTube", "Substack", "Medium", "Newsletter", "Twitch", "egghead")

# ---------- tech ----------

# label -> keywords, matched in repository names, descriptions and topics.
TECH = {
    "Next.js": ["next.js", "nextjs", "next-js"],
    "React": ["react", "reactjs", "react-native", "react native"],
    "Svelte": ["svelte", "sveltekit"],
    "Vue": ["vue", "vuejs", "nuxt"],
    "Astro": ["astro"],
    "Tailwind": ["tailwind", "tailwindcss"],
    "Cloudflare Workers": ["cloudflare", "cloudflare-workers", "wrangler", "durable objects", "durable-objects", "workers-ai", "miniflare"],
    "Vercel": ["vercel"],
    "Chrome extensions": ["chrome extension", "chrome-extension", "browser extension", "browser-extension", "webextension", "manifest v3", "mv3"],
    "Tauri": ["tauri"],
    "Electron": ["electron"],
    "Local AI": ["llama.cpp", "llama-cpp", "ggml", "gguf", "ollama", "whisper", "whisper.cpp", "local llm", "local-llm", "on-device", "mlx", "onnx"],
    "Speech": ["speech-to-text", "text-to-speech", "tts", "stt", "dictation", "speech", "asr"],
    "AI coding agents": ["claude code", "claude-code", "codex", "coding agent", "coding agents", "coding-agent", "mcp", "model context protocol", "agentic", "cursor", "aider"],
    "LLM apps": ["llm", "llms", "openai", "anthropic", "gpt", "gemini", "rag", "embeddings"],
    "Playwright": ["playwright"],
    "Remotion": ["remotion"],
    "Video": ["ffmpeg", "obs", "video"],
    "PWA": ["pwa", "service worker", "service-worker"],
    "Bun": ["bun"],
    "Deno": ["deno"],
    "SQLite": ["sqlite", "libsql", "turso"],
    "Postgres": ["postgres", "postgresql", "supabase", "neon"],
    "GitHub Actions": ["github actions", "github-actions"],
    "CLI tools": ["cli", "command-line", "command line", "terminal", "tui"],
    "WebAssembly": ["wasm", "webassembly"],
    "Stripe": ["stripe"],
}


def _kw_re(words):
    alts = sorted({w.lower() for w in words}, key=len, reverse=True)
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(w) for w in alts) + r")(?![a-z0-9])")


def vocabulary(extra=None):
    """The built-in tech vocabulary plus a paper's [follow.tech], as compiled patterns."""
    vocab = {k: list(v) for k, v in TECH.items()}
    for label, words in (extra or {}).items():
        vocab.setdefault(label, []).extend(words)
    return {label: _kw_re(words) for label, words in vocab.items()}


def repo_text(r):
    topics = " ".join(r.get("topics") or [])
    return f"{r.get('name') or ''} {(r.get('name') or '').replace('-', ' ')} {r.get('description') or ''} {topics}".lower()


# Languages that say little about someone's stack.
NOISE_LANGUAGES = {"HTML", "CSS", "SCSS", "Shell", "PowerShell", "Batchfile", "Makefile", "Dockerfile", "Nix", "Procfile", "Jupyter Notebook", "Roff", "TeX", "Rich Text Format", "Markdown", "Text", "Smarty", "EJS", "Handlebars", "Less", "Vim Script"}


def tech_of(repos, vocab, recent_since=None):
    """{label: weight} for languages and frameworks across repos. Repos pushed since recent_since count double."""
    out = {}
    for r in repos:
        w = 2 if recent_since and (r.get("pushed") or "") >= recent_since else 1
        langs = [x for x in (r.get("languages") or ([r["language"]] if r.get("language") else [])) if x not in NOISE_LANGUAGES]
        for i, lang in enumerate(langs[:3]):
            out[lang] = out.get(lang, 0) + (w if i == 0 else w / 2)
        text = repo_text(r)
        for label, pat in vocab.items():
            if pat.search(text):
                out[label] = out.get(label, 0) + w
    return out


def my_stack(repos, vocab, today, top=14):
    """The owner's stack as {label: weight}, from their own public repos, most used first."""
    since = (today - dt.timedelta(days=180)).isoformat()
    weights = tech_of([r for r in repos if not r.get("archived")], vocab, since)
    return dict(sorted(weights.items(), key=lambda kv: -kv[1])[:top])


# ---------- the mini report ----------

def _repo(node):
    if not node:
        return None
    rel = node.get("latestRelease") or {}
    return {
        "repo": node.get("nameWithOwner") or "",
        "name": node.get("name") or (node.get("nameWithOwner") or "").split("/")[-1],
        "url": url_or_none(node.get("url")) or f"https://github.com/{node.get('nameWithOwner')}",
        "description": clean(node.get("description"), 200),
        "stars": node.get("stargazerCount") or 0,
        "pushed": (node.get("pushedAt") or "")[:10],
        "archived": bool(node.get("isArchived")),
        "language": (node.get("primaryLanguage") or {}).get("name"),
        "languages": [n["name"] for n in ((node.get("languages") or {}).get("nodes") or []) if n],
        "topics": [n["topic"]["name"] for n in ((node.get("repositoryTopics") or {}).get("nodes") or []) if n and n.get("topic")],
        "release": {"name": clean(rel.get("name") or rel.get("tagName"), 80), "date": (rel.get("publishedAt") or "")[:10],
                    "url": url_or_none(rel.get("url"))} if rel.get("url") else None,
    }


def weekly_totals(calendar):
    """Contribution totals per week, oldest first, from a GraphQL contributionCalendar."""
    return [sum(d.get("contributionCount") or 0 for d in w.get("contributionDays") or []) for w in (calendar or {}).get("weeks") or []]


def activity(weeks):
    """Last 12 weeks against the 12 before: level, trend and the numbers behind them."""
    last = sum(weeks[-12:])
    prev = sum(weeks[-24:-12])
    if last < 10:
        trend = "quiet"
    else:
        ratio = (last + 5) / (prev + 5)
        trend = "rising" if ratio > 1.3 else "cooling" if ratio < 0.7 else "steady"
    return {"last_12w": last, "prev_12w": prev, "trend": trend, "weeks": weeks[-26:]}


class PageInfo(html.parser.HTMLParser):
    """Title, meta description and anchors (href, text) from someone's own site."""

    def __init__(self, base):
        super().__init__()
        self.base, self.title, self.description, self.anchors = base, "", "", []
        self._in_title, self._a = False, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "meta" and (a.get("name") or a.get("property") or "").lower() in ("description", "og:description") and not self.description:
            self.description = a.get("content") or ""
        elif tag == "a" and a.get("href"):
            self._a = [urllib.parse.urljoin(self.base, a["href"].strip()), ""]

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "a" and self._a:
            self.anchors.append((self._a[0], clean(self._a[1], 120)))
            self._a = None

    def handle_data(self, data):
        if self._in_title and len(self.title) < 200:
            self.title += data
        if self._a is not None and len(self._a[1]) < 200:
            self._a[1] += data


TALK_RE = re.compile(r"(?<![a-z])(talks?|speaking|speaker|conference|keynote|podcasts?|interviews?|presentations?|slides)(?![a-z])", re.I)


def page_info(page_html, base):
    p = PageInfo(base)
    try:
        p.feed(page_html or "")
        p.close()
    except Exception:  # noqa: BLE001 - a broken page just yields less
        pass
    talks = []
    for href, text in p.anchors:
        if not config.is_http_url(href) or len(talks) >= 3:
            continue
        path = urllib.parse.urlparse(href).path
        if (TALK_RE.search(path) or TALK_RE.search(text)) and not any(t["url"] == href for t in talks):
            talks.append({"title": text or path.strip("/") or href, "url": href})
    return {"title": clean(p.title, 120), "description": clean(p.description, 240), "talks": talks}


# Post titles are kept only when they read as work: a tech or interest keyword,
# or one of these. A personal post ("our wedding") never matches.
WORK_WORDS = _kw_re(["code", "coding", "programming", "software", "api", "apis", "release", "released", "launch", "launching",
                     "building", "build", "shipping", "ship", "engineering", "engineer", "developer", "developers", "dev",
                     "open source", "open-source", "ai", "llm", "llms", "model", "models", "agent", "agents", "rust",
                     "typescript", "javascript", "python", "go", "web", "app", "apps", "database", "performance", "compiler",
                     "startup", "product", "tool", "tools", "tooling", "cli", "talk", "conference", "podcast", "framework",
                     "library", "browser", "server", "cloud", "data", "security", "testing", "debugging", "design system"])


def work_posts(items, vocab, limit=3):
    out = []
    for it in items:
        title = clean(it.get("title"), 140)
        url = url_or_none(it.get("url"))
        if not title or not url:
            continue
        low = title.lower()
        if WORK_WORDS.search(low) or any(p.search(low) for p in vocab.values()):
            out.append({"title": title, "url": url, "date": (it.get("date") or "")[:10]})
        if len(out) >= limit:
            break
    return out


def mini_report(user, today, site=None, socials_from_site=(), posts=(), vocab=None):
    """A mini report from one GraphQL `user` node (see follow.py USER_QUERY) and an optional site visit."""
    vocab = vocab or vocabulary()
    login = user["login"]
    profile = f"https://github.com/{login}"
    name = clean(clean_name(user.get("name") or ""), 60) or login
    bio = clean(user.get("bio"), 240)
    orgs = [o["login"] for o in ((user.get("organizations") or {}).get("nodes") or []) if o]
    orgs_lower = {o.lower() for o in orgs}

    work = []
    company = clean(user.get("company"), 100)
    if company:
        mentions = re.findall(r"@([A-Za-z0-9-]{1,39})", company)
        for m in mentions:
            verified = m.lower() in orgs_lower
            work.append({"text": f"Works at @{m}" + (" (public member of that GitHub org)" if verified else " (their GitHub company field)"),
                         "source": f"https://github.com/{m}" if verified else profile})
        rest = clean(re.sub(r"@[A-Za-z0-9-]+", "", company), 100)
        if rest and not mentions:
            work.append({"text": f"Company: {rest} (their GitHub company field)", "source": profile})
    if bio:
        work.append({"text": f"In their own words: \"{bio}\"", "source": profile})
    if site and site.get("description"):
        work.append({"text": f"Their site says: \"{clean(site['description'], 240)}\"", "source": site["url"]})

    pinned = [_repo(n) for n in ((user.get("pinnedItems") or {}).get("nodes") or []) if n and n.get("nameWithOwner")]
    top = [_repo(n) for n in ((user.get("top") or {}).get("nodes") or []) if n]
    recent = [_repo(n) for n in ((user.get("recent") or {}).get("nodes") or []) if n]
    contributed = [_repo(n) for n in ((user.get("contributed") or {}).get("nodes") or []) if n]
    own = {}
    for r in pinned + top + recent:
        if r and r["repo"] not in own:
            own[r["repo"]] = r
    builds, seen = [], set()
    for r in pinned + sorted(top, key=lambda r: -r["stars"]) + recent:
        if r and r["repo"] not in seen and not r["archived"] and len(builds) < 5:
            seen.add(r["repo"])
            builds.append({k: r[k] for k in ("repo", "url", "description", "stars", "pushed", "language")})
    since = (today - dt.timedelta(days=180)).isoformat()
    releases = sorted(({"repo": r["repo"], **r["release"]} for r in own.values() if r["release"] and r["release"]["date"] >= since),
                      key=lambda x: x["date"], reverse=True)[:3]
    tech = tech_of(list(own.values()) + contributed, vocab, since)
    langs = [k for k, _ in sorted(((k, v) for k, v in tech.items() if k not in vocab), key=lambda kv: -kv[1])][:5]
    frameworks = [k for k, _ in sorted(((k, v) for k, v in tech.items() if k in vocab), key=lambda kv: -kv[1])][:6]

    act = activity(weekly_totals(((user.get("contributionsCollection") or {}).get("contributionCalendar"))))
    last_push = max((r["pushed"] for r in own.values()), default="")

    stars = sum(r["stars"] for r in own.values() if r["repo"].split("/")[0].lower() == login.lower())
    maintains = [r["repo"] for r in contributed if r["repo"].split("/")[0].lower() in orgs_lower][:4]
    contributes = [r["repo"] for r in contributed if r["repo"] not in maintains][:5]

    socials = []

    def add_social(label, url, how):
        url = url_or_none(normalize(url or ""))
        if url and label in PROFESSIONAL and not any(s["url"].rstrip("/").lower() == url.rstrip("/").lower() for s in socials):
            socials.append({"label": label, "url": url, "verified_by": how})

    if user.get("twitterUsername"):
        add_social("X", f"https://x.com/{user['twitterUsername']}", "listed on their GitHub profile")
    for acct in ((user.get("socialAccounts") or {}).get("nodes") or []):
        add_social(classify(acct.get("url") or "") or "", acct.get("url"), "listed on their GitHub profile")
    for s in socials_from_site:
        add_social(s["label"], s["url"], clean(s["verified_by"], 80))

    loc = city_country(user.get("location"))
    who = f"{name} (@{login})"
    if bio:
        who += f": {bio.rstrip('.')}."
    else:
        who += " has no bio on GitHub."
    created = (user.get("createdAt") or "")[:4]
    followers = (user.get("followers") or {}).get("totalCount") or 0

    return {
        "version": VERSION,
        "login": login,
        "name": name,
        "researched": today.isoformat(),
        "profile": profile,
        "avatar": url_or_none(user.get("avatarUrl")),
        "who": {"text": who + (f" On GitHub since {created}." if created else ""), "source": profile},
        "location": {"text": loc, "source": profile} if loc else None,
        "work": work,
        "site": {"url": site["url"], "title": site.get("title") or ""} if site and site.get("url") else None,
        "builds": builds,
        "releases": releases,
        "tech": {"languages": langs, "frameworks": frameworks, "weights": tech,
                 "source": f"{profile}?tab=repositories"},
        "activity": {**act, "last_push": last_push, "source": profile},
        "standing": {"followers": followers, "stars": stars, "repos": (user.get("top") or {}).get("totalCount") or len(own),
                     "maintains": maintains, "contributes": contributes, "orgs": orgs[:8], "source": f"{profile}?tab=repositories"},
        "socials": socials,
        "posts": list(posts)[:3],
        "talks": (site or {}).get("talks", [])[:3],
        "corpus": clean(" ".join([bio, company, (site or {}).get("title", ""), (site or {}).get("description", "")]
                                 + [f"{r['name']} {r['description']} {' '.join(r['topics'])}" for r in own.values()]
                                 + [p["title"] for p in posts]), 4000).lower(),
    }


# ---------- the score ----------

TREND_VALUE = {"rising": 1.0, "steady": 0.7, "cooling": 0.35, "quiet": 0.0}


def _cap(x):
    return max(0.0, min(1.0, x))


def score(report, signals, mine, follow_cfg, names=None, pulse=None):
    """The fit score: {score, recommendation, parts: {name: {points, max, why}}, now: {last_12w, followers}, why_follow}.

    report:  a mini report; pulse (optional) refreshes its followers and weekly activity.
    signals: how discovery found them (followed_by, collab, stack, similar).
    mine:    the owner's stack from my_stack().
    names:   builder id -> display name, for "followed by ..." lines.
    """
    names = names or {}
    weights = follow_cfg["weights"]
    interests = {label: _kw_re(words) for label, words in follow_cfg["interests"].items()}
    act = activity(pulse["weeks"]) if pulse and pulse.get("weeks") else report["activity"]
    followers = pulse["followers"] if pulse and pulse.get("followers") is not None else report["standing"]["followers"]
    parts = {}

    # Stack: how much of the owner's stack they use, plus committing to a repo in it.
    # Frameworks and platforms say more than languages, which nearly everyone shares.
    theirs = report["tech"]["weights"]
    shared = [k for k in mine if k in theirs]
    vocab_labels = set(TECH) | set(follow_cfg["tech"])
    mine_fw = {k: v for k, v in mine.items() if k in vocab_labels}
    mine_lang = {k: v for k, v in mine.items() if k not in vocab_labels}
    fw = sum(mine_fw[k] for k in shared if k in mine_fw) / (sum(sorted(mine_fw.values(), reverse=True)[:5]) or 1)
    lang = sum(mine_lang[k] for k in shared if k in mine_lang) / (sum(sorted(mine_lang.values(), reverse=True)[:3]) or 1)
    stack_commits = signals.get("stack") or {}
    stack = _cap(0.35 * _cap(lang) + 0.65 * _cap(fw) + (0.2 if stack_commits else 0))
    shared = [k for k in shared if k in mine_fw] + [k for k in shared if k in mine_lang]
    why = ", ".join(shared[:4]) or "little overlap"
    if stack_commits:
        why += "; commits to " + ", ".join(sorted(stack_commits, key=lambda r: -stack_commits[r])[:2])
    parts["stack"] = (stack, why)

    # Activity: contributions over the last 12 weeks (log scale) and their trend.
    level = _cap(math.log1p(act["last_12w"]) / math.log1p(400))
    parts["activity"] = (0.75 * level + 0.25 * TREND_VALUE[act["trend"]], f"{act['last_12w']:,} contributions in 12 weeks, {act['trend']}")

    # Quality: stars on their own work, followers, and repos that explain themselves and ship releases.
    st = report["standing"]
    described = [b for b in report["builds"] if b["description"]]
    hygiene = len(described) / len(report["builds"]) if report["builds"] else 0
    quality = (0.4 * _cap(math.log10(1 + st["stars"]) / 4.3) + 0.35 * _cap(math.log10(1 + followers) / 3.7)
               + 0.15 * hygiene + 0.10 * (1 if report["releases"] else 0))
    parts["quality"] = (quality, f"{st['stars']:,} stars, {followers:,} followers" + (", recent releases" if report["releases"] else ""))

    # Relevance: the owner's interests found in their bio, site, repos and posts.
    matched = [label for label, pat in interests.items() if pat.search(report["corpus"])]
    parts["relevance"] = (_cap(len(matched) / 3) if interests else 0.0, ", ".join(matched) or "none of your interests")

    # Network: the builders you already read follow them or work with them.
    fb = signals.get("followed_by") or []
    collab = signals.get("collab") or []
    collab_with = sorted({c["with"] for c in collab})
    net = _cap(0.35 * len(fb) + 0.4 * len(collab_with))
    bits = []
    if fb:
        bits.append("followed by " + ", ".join(names.get(b, b) for b in fb[:3]) + (f" and {len(fb) - 3} more" if len(fb) > 3 else ""))
    if collab_with:
        bits.append("works with " + ", ".join(names.get(b, b) for b in collab_with[:3]))
    parts["network"] = (net, "; ".join(bits) or "no link to your builders yet")

    total_w = sum(weights.values())
    out_parts = {k: {"points": round(weights[k] * v, 1), "max": weights[k], "why": w} for k, (v, w) in parts.items() if weights.get(k)}
    total = round(sum(weights[k] * v for k, (v, _) in parts.items()) * 100 / total_w)
    th = follow_cfg["thresholds"]
    rec = "Follow" if total >= th["follow"] else "Maybe" if total >= th["maybe"] else "Skip"
    return {"score": total, "recommendation": rec, "parts": out_parts,
            "now": {"last_12w": act["last_12w"], "followers": followers},  # the numbers the score used, fresher than the report
            "why_follow": why_follow(report, shared, matched, fb, collab_with, stack_commits, names)}


def why_follow(report, shared, matched, followed_by, collab_with, stack_commits, names):
    clauses = []
    if stack_commits:
        repo = max(stack_commits, key=stack_commits.get)
        clauses.append(f"commits to {repo}, which you build on")
    elif report["builds"]:
        b = max(report["builds"], key=lambda b: b["stars"])
        what = f"builds {b['repo'].split('/')[-1]}" + (f" ({b['stars']:,} stars)" if b["stars"] >= 50 else "")
        clauses.append(what)
    if matched:
        clauses.append("works on " + " and ".join(matched[:2]))
    elif shared:
        clauses.append("uses " + ", ".join(shared[:3]) + " like you")
    if followed_by:
        clauses.append("followed by " + " and ".join(names.get(b, b) for b in followed_by[:2]))
    elif collab_with:
        clauses.append("works with " + names.get(collab_with[0], collab_with[0]))
    text = "; ".join(clauses[:3]) or "active public builder"
    return clean(text[0].upper() + text[1:] + ".", 200)


# ---------- this week's picks ----------

def pick(scored, state, follow_cfg, today):
    """Choose this week's suggestions and record them in state.

    scored: [{login, score, recommendation, ...}], any order.
    Someone suggested in the last cooldown_weeks comes back early only when
    their score rose by at least rejump points since then.
    """
    cutoff = (today - dt.timedelta(weeks=follow_cfg["cooldown_weeks"])).isoformat()
    picks = []
    for c in sorted(scored, key=lambda c: (-c["score"], c["login"].lower())):
        if len(picks) >= follow_cfg["picks"]:
            break
        if c["recommendation"] == "Skip":
            continue
        s = state.setdefault(c["login"].lower(), {})
        last = (s.get("suggested") or [""])[-1]
        if last and last > cutoff and c["score"] < (s.get("suggested_score") or 0) + follow_cfg["rejump"]:
            continue
        picks.append(c["login"])
    for login in picks:
        s = state[login.lower()]
        s["suggested"] = (s.get("suggested") or []) + [today.isoformat()]
        s["suggested_score"] = next(c["score"] for c in scored if c["login"] == login)
    return picks


# ---------- Markdown ----------

def _link(text, url):
    url = url_or_none(url)
    text = text.replace("[", "(").replace("]", ")")
    return f"[{text}]({url})" if url else text


def render_md(report, scored=None):
    r = report
    out = [f"# {r['name']} (@{r['login']})", ""]
    if scored:
        out += [f"**Fit:** {scored['score']}/100, {scored['recommendation']}. {scored['why_follow']}", ""]
        out += ["| Part | Points | Why |", "| --- | --- | --- |"]
        out += [f"| {k} | {p['points']:g} of {p['max']:g} | {p['why']} |" for k, p in scored["parts"].items()]
        out.append("")
    out.append(f"**Who they are:** {r['who']['text']} ({_link('GitHub', r['who']['source'])})")
    if r.get("location"):
        out += ["", f"**Based in:** {r['location']['text']} (self-published, {_link('GitHub', r['location']['source'])})"]
    out.append("")
    if r["work"]:
        out += ["## Work and role", ""] + [f"- {w['text']} ({_link('source', w['source'])})" for w in r["work"]] + [""]
    if r["builds"]:
        out += ["## What they build", ""]
        out += [f"- {_link(b['repo'], b['url'])}" + (f": {b['description']}" if b["description"] else "")
                + f" ({b['stars']:,} stars" + (f", {b['language']}" if b["language"] else "") + (f", pushed {b['pushed']}" if b["pushed"] else "") + ")"
                for b in r["builds"]]
        out.append("")
    if r["releases"]:
        out += ["Recent releases: " + ", ".join(f"{_link(x['repo'].split('/')[-1] + ' ' + x['name'], x['url'])} ({x['date']})" for x in r["releases"]) + ".", ""]
    t = r["tech"]
    if t["languages"] or t["frameworks"]:
        out += [f"**Tech:** {', '.join(t['languages']) or 'no languages detected'}"
                + (f"; {', '.join(t['frameworks'])}" if t["frameworks"] else "") + f" ({_link('their repos', t['source'])})", ""]
    a = r["activity"]
    out += [f"**Activity:** {a['last_12w']:,} contributions in the last 12 weeks against {a['prev_12w']:,} in the 12 before ({a['trend']})"
               + (f", last push {a['last_push']}" if a.get("last_push") else "") + f" ({_link('GitHub', a['source'])})", ""]
    s = r["standing"]
    standing = f"{s['followers']:,} followers, {s['stars']:,} stars across their own repos"
    if s["maintains"]:
        standing += "; member and contributor of " + ", ".join(s["maintains"])
    if s["contributes"]:
        standing += "; also contributes to " + ", ".join(s["contributes"][:4])
    out += [f"**Standing:** {standing} ({_link('repos', s['source'])})", ""]
    if r["socials"] or r.get("site"):
        out += ["## Where to find them", ""]
        if r.get("site"):
            out.append(f"- Website: {_link(r['site']['title'] or r['site']['url'], r['site']['url'])} (their GitHub website field)")
        out += [f"- {x['label']}: {_link(x['url'], x['url'])} ({x['verified_by']})" for x in r["socials"]]
        out.append("")
    if r["posts"] or r["talks"]:
        out += ["## Talks and posts", ""]
        out += [f"- {_link(p['title'], p['url'])}" + (f" ({p['date']})" if p.get("date") else "") for p in r["posts"]]
        out += [f"- {_link(t['title'], t['url'])} (linked from their site)" for t in r["talks"]]
        out.append("")
    out.append(f"*Researched {r['researched']} from public, logged-out sources only.*")
    return "\n".join(out) + "\n"


# ---------- feeds ----------

def parse_feed(text, limit=10):
    """[{title, url, date}] from an RSS or Atom document, newest first as published. Never raises."""
    import xml.etree.ElementTree as ET  # noqa: PLC0415

    try:
        root = ET.fromstring((text or "")[:1_000_000])
    except ET.ParseError:
        return []
    out = []
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        title = url = date = ""
        for child in el:
            ctag = child.tag.split("}")[-1]
            if ctag == "title":
                title = "".join(child.itertext())
            elif ctag == "link":
                href = child.get("href")
                if href and child.get("rel", "alternate") == "alternate":
                    url = href
                elif not href and child.text:
                    url = child.text.strip()
            elif ctag in ("pubDate", "published", "updated", "date") and not date:
                date = (child.text or "").strip()
        iso = ""
        if re.match(r"\d{4}-\d{2}-\d{2}", date):
            iso = date[:10]
        else:
            try:
                import email.utils  # noqa: PLC0415
                iso = email.utils.parsedate_to_datetime(date).date().isoformat()
            except (TypeError, ValueError, IndexError):
                iso = ""
        out.append({"title": title, "url": url, "date": iso})
        if len(out) >= limit:
            break
    return out


# ---------- shared by the site and the email ----------

def load_latest(paper, cfg, max_age_days=None, today=None):
    """(latest, {login_lower: report}) from <paper>/follow, or (None, {}) when there is nothing current to show."""
    import json  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    if not cfg["follow"]["enabled"]:
        return None, {}
    folder = Path(paper) / "follow"
    try:
        latest = json.loads((folder / "latest.json").read_text())
    except (OSError, ValueError):
        return None, {}
    if max_age_days is not None:
        today = today or dt.date.today()
        if (today - dt.date.fromisoformat(latest["date"])).days > max_age_days:
            return None, {}
    reports = {}
    for c in latest.get("candidates", []):
        try:
            reports[c["login"].lower()] = json.loads((folder / "people" / f"{c['login'].lower()}.json").read_text())
        except (OSError, ValueError):
            pass
    latest["candidates"] = [c for c in latest.get("candidates", []) if c["login"].lower() in reports]
    return latest, reports


def repo_slug(repo_url):
    m = re.fullmatch(r"https://github\.com/([A-Za-z0-9-]+/[A-Za-z0-9._-]+?)/?", repo_url or "")
    return m.group(1) if m else None


def add_command(repo_url, login):
    """The one-liner behind "Add to PR Post": a workflow that drafts the watch-list entry as a branch for review."""
    slug = repo_slug(repo_url)
    if slug:
        return f"gh workflow run follow-add.yml --repo {slug} -f github={login}"
    return f"python3 engine/scripts/follow.py --paper . draft {login}"


def add_url(repo_url):
    slug = repo_slug(repo_url)
    return f"https://github.com/{slug}/actions/workflows/follow-add.yml" if slug else None


def headline_fact(report):
    """One short line on what they do, for the email: their company, else their best-known repo."""
    for w in report.get("work") or []:
        if w["text"].startswith(("Works at", "Company:")):
            return w["text"].split(" (")[0]
    if report.get("builds"):
        b = max(report["builds"], key=lambda b: b["stars"])
        return clean(f"{b['repo']}: {b['description']}", 100) if b["description"] else b["repo"]
    return ""
