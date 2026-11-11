"""Stage 0, in code: the window, each brief trimmed to it, quiet people, and the items a quote may come from.

Nothing here calls a model. Everything the later stages send is built from what
this module returns, so the dry run and the real run send the same text.
"""

import datetime as dt
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import config  # noqa: E402

WEEKLY_FALLBACK_DAYS = 7
UPDATE_FALLBACK_DAYS = 4
STALE_HOURS = 24

DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
REPORT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(weekly|update)$")
ITEM_RE = re.compile(r"^- (\d{4}-\d{2}-\d{2}\S*) (.*) <(\S+)>$")
COLLECTED_RE = re.compile(r"^Collected (\S+?);")
NEWEST_RE = re.compile(r"Newest report: (\d{4}-\d{2}-\d{2})-(weekly|update)(?:; newest weekly: (\d{4}-\d{2}-\d{2})-weekly)?")

# Brief sections whose items are dated and so belong to a window, by kind of item.
DATED = {"Merged PRs": "pr", "Open PRs": "open_pr", "Commits to repos with no merged PR": "commit",
         "Releases": "release", "New repos": "birth", "Videos": "video"}
QUOTABLE = {"pr", "open_pr", "commit", "video"}
ID_TAG = re.compile(r"^- \[i\d+\] ")


def kind_for(day):
    """Saturday is the weekly report; any other day a midweek update (paper/INSTRUCTIONS.md)."""
    return "weekly" if day.weekday() == 5 else "update"


def report_dates(paper, pid, before):
    """(date, kind) of every report of this person dated before `before`, newest first."""
    out = []
    for f in (Path(paper) / "reports" / pid).glob("*.md"):
        m = REPORT_RE.match(f.stem)
        if m and m.group(1) < before.isoformat():
            out.append((m.group(1), m.group(2)))
    return sorted(out, reverse=True)


def window(kind, today, reports, brief_text=""):
    """The report window [start, end] as dates, following paper/INSTRUCTIONS.md step 1.

    Weekly: from the newest weekly report, or 7 days ago. Midweek: from the newest
    report of either kind, or 4 days ago. `reports` is report_dates(); the brief's
    "Newest report" line fills in when the reports folder was not pulled.
    """
    newest_any = reports[0][0] if reports else None
    newest_weekly = next((d for d, k in reports if k == "weekly"), None)
    m = NEWEST_RE.search(brief_text or "")
    if m:
        if m.group(1) < today.isoformat():
            newest_any = max(filter(None, [newest_any, m.group(1)]))
        weekly = m.group(1) if m.group(2) == "weekly" else m.group(3)
        if weekly and weekly < today.isoformat():
            newest_weekly = max(filter(None, [newest_weekly, weekly]))
    if kind == "weekly":
        start = dt.date.fromisoformat(newest_weekly) if newest_weekly else today - dt.timedelta(days=WEEKLY_FALLBACK_DAYS)
    else:
        start = dt.date.fromisoformat(newest_any) if newest_any else today - dt.timedelta(days=UPDATE_FALLBACK_DAYS)
    return start, today


def collected_at(brief_text):
    for line in brief_text.splitlines()[:6]:
        m = COLLECTED_RE.match(line)
        if m:
            try:
                t = dt.datetime.fromisoformat(m.group(1))
            except ValueError:
                return None
            return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    return None


def section_kind(heading):
    for prefix, kind in DATED.items():
        if heading.startswith(prefix):
            return kind
    return None


def parse_item(kind, rest):
    """Split the text of a brief item line (after its date) into repo and title."""
    if kind in ("pr", "open_pr"):
        m = re.match(r"^(\S+?)#(\d+) (.*)$", rest)
        return (m.group(1), m.group(3)) if m else ("", rest)
    if kind in ("commit", "birth"):
        m = re.match(r"^(\S+): ?(.*)$", rest)
        return (m.group(1), m.group(2)) if m else ("", rest)
    if kind == "release":
        repo, _, name = rest.partition(" ")
        return repo, name
    return "", rest


def trim(brief_text, start, end):
    """The brief with dated items outside [start, end] removed, each kept item tagged with an id.

    Returns (text, items). Section headings lose their counts, which describe the
    whole brief rather than the window; "plus N more" lines stay only where no
    item was cut, since they then may still be inside the window.
    """
    lo, hi = start.isoformat(), end.isoformat()
    out, items = [], []
    sections = []  # [heading, kind, lines]
    head = []
    current = None
    for line in brief_text.rstrip("\n").split("\n"):
        if line.startswith("## "):
            current = [line, section_kind(line[3:]), []]
            sections.append(current)
        elif current is None:
            head.append(line)
        else:
            current[2].append(line)
    while head and not head[-1].strip():
        head.pop()
    out += head
    n = 0
    for heading, kind, lines in sections:
        if kind is None:
            out += ["", heading] + [line for line in lines if line.strip()]
            continue
        kept, dropped, keep_child = [], 0, False
        for line in lines:
            if line.startswith("  ") and line.strip():
                if keep_child:
                    kept.append(line)
                    if items and items[-1]["id"] == f"i{n}" and not items[-1].get("desc"):
                        items[-1]["desc"] = line.strip()
                continue
            keep_child = False
            if not line.strip():
                continue
            line = ID_TAG.sub("- ", line, count=1)  # a brief trimmed before keeps its items; they get fresh ids
            m = ITEM_RE.match(line)
            day = DATE_RE.match(line[2:]) if line.startswith("- ") else None
            if day:
                if not (lo <= day.group(1) <= hi):
                    dropped += 1
                    continue
                n += 1
                iid = f"i{n}"
                if m:
                    repo, title = parse_item(kind, m.group(2))
                    items.append({"id": iid, "kind": kind, "date": m.group(1)[:10], "repo": repo, "title": title, "url": m.group(3)})
                kept.append(f"- [{iid}] {line[2:]}")
                keep_child = True
            elif line.startswith("- plus "):
                if not dropped:
                    kept.append(line)
            elif line.startswith("- ") and kind == "birth":
                # A new repo with no date: keep it, stats/ only lists births inside its window.
                m2 = re.match(r"^- (\S+?): ?(.*) <(\S+)>$", line)
                n += 1
                iid = f"i{n}"
                if m2:
                    items.append({"id": iid, "kind": kind, "date": "", "repo": m2.group(1), "title": m2.group(2), "url": m2.group(3)})
                kept.append(f"- [{iid}] {line[2:]}")
            else:
                kept.append(line)
        if any(l.startswith("- [") for l in kept):
            heading = re.sub(r"\s*\(\d+\)\s*$", "", heading)
            if " in the window" not in heading:
                heading = heading.replace(", newest first", " in the window, newest first")
            out += ["", heading] + kept
    return "\n".join(out).rstrip() + "\n", items


def archived(brief_text):
    """Archived or gone repos listed in the brief: [{"repo", "url"}]."""
    out, inside = [], False
    for line in brief_text.splitlines():
        if line.startswith("## "):
            inside = line[3:].startswith("Archived or gone")
            continue
        m = re.match(r"^- (\S+) <(\S*)>$", line) if inside else None
        if m:
            out.append({"repo": m.group(1), "url": m.group(2)})
    return out


def busiest(brief_text):
    """Repos in the brief's "Busiest public repos" section, busiest first."""
    out, inside = [], False
    for line in brief_text.splitlines():
        if line.startswith("## "):
            inside = line[3:].startswith("Busiest")
            continue
        m = re.match(r"^- (\S+): ", line) if inside else None
        if m:
            out.append(m.group(1))
    return out


# News value, used to order what each report should lead with. A rough, code-only
# reading of titles: it never decides what is written, only what comes first.
STRONG = re.compile(r"(\b\d+(\.\d+)?x (faster|smaller|less)\b|\bhalve[sd]?\b|(?<!\bat )(?<!\bon )\blaunch(es|ed|ing)?\b(?! contract)|"
                    r"\bfirst release\b|\binitial (public )?release\b|\bprepare for v?\d+\.0|\brelease prep\b.*\d+\.\d+|"
                    r"(?<![\d.])v?[1-9]\d*\.0(\.0)?\b(?![.-])|\bintroduc\w+)", re.I)
MEDIUM = re.compile(r"\b(faster|speed ?up|speeds? up|quicker|release[sd]?|publish\w*|new|support\w*|add\w*|ship\w*|redesign\w*)\b", re.I)
FEATURE = re.compile(r"^feat\b", re.I)
SMALL = re.compile(r"^(chore|ci|test|tests|docs?|style|build|deps|lint|bump|typo|format|refactor)\b|\b(bump|chore|typo|lint|"
                   r"formatting|dependabot|update dependenc\w*|readme)\b|\b(update|bump|upgrade) [\w@/.-]+( cask)? to v?\d", re.I)
FILLER_REPO = re.compile(r"(^|/)\.github$|\.github\.io$|release-key|signing key|dotfiles", re.I)
KIND_SCORE = {"birth": 9, "release": 8, "video": 8, "pr": 4, "commit": 2, "open_pr": 1}
KIND_LABEL = {"birth": "new repo", "release": "release", "video": "video", "pr": "merged PR", "commit": "commit", "open_pr": "open PR"}


def major_release(title):
    m = re.search(r"v?(\d+)\.(\d+)(?:\.(\d+))?", title or "")
    return bool(m) and m.group(1) != "0" and m.group(2) == "0" and m.group(3) in (None, "0")


def filler_birth(item):
    owner, _, name = item["repo"].partition("/")
    return bool(FILLER_REPO.search(item["repo"]) or FILLER_REPO.search(item["title"])) or (name and name.lower() == owner.lower())


def news_score(item, busy):
    score = KIND_SCORE.get(item["kind"], 0)
    if item["kind"] in ("pr", "commit", "open_pr"):
        title = item["title"]
        if SMALL.search(title):
            score -= 3
        elif STRONG.search(title):
            score += 4
        elif MEDIUM.search(title) or FEATURE.search(title) or STRONG.search(item.get("desc", "")[:200]):
            score += 2
    if item["kind"] == "release" and major_release(item["title"]):
        score += 2
    if item["kind"] == "birth" and filler_birth(item):
        score = 2
    if item["repo"] and busy and item["repo"] == busy[0]:
        score += 1
    return score


def highlights(items, gone, busy, limit=8):
    """The window's most newsworthy items, best first: [{"id", "kind", "label", "repo", "title", "url", "score"}].

    Several releases of one repo count once (its newest), and each repo gets at
    most two entries (three for the busiest), so one busy repo cannot crowd out a launch elsewhere.
    Archived repos come last but are always listed.
    """
    scored, seen_release, per_repo = [], set(), {}
    for item in items:
        if item["kind"] == "release":
            if item["repo"] in seen_release:
                continue
            seen_release.add(item["repo"])
        scored.append((news_score(item, busy), item))
    scored.sort(key=lambda t: (-t[0], t[1]["date"] and -int(t[1]["date"].replace("-", ""))))
    out = []
    for score, item in scored:
        if score <= 0 or per_repo.get(item["repo"], 0) >= (3 if busy and item["repo"] == busy[0] else 2):
            continue
        per_repo[item["repo"]] = per_repo.get(item["repo"], 0) + 1
        out.append({"id": item["id"], "kind": item["kind"], "label": KIND_LABEL[item["kind"]], "repo": item["repo"],
                    "title": item["title"], "url": item["url"], "score": score})
        if len(out) >= limit:
            break
    for g in gone:
        out.append({"id": "", "kind": "archived", "label": "archived repo", "repo": g["repo"], "title": "archived or gone",
                    "url": g["url"], "score": 7})
    return out


def numbers_line(brief_text):
    """The brief's "Numbers (copied from stats/)" lines, the only source for figures."""
    lines, inside = [], False
    for line in brief_text.splitlines():
        if line.startswith("## "):
            inside = line[3:].startswith("Numbers")
            continue
        if inside and line.startswith("- "):
            lines.append(line[2:])
    return lines


COUNT_RE = {"merged": re.compile(r"(\d+)\+? merged PRs?\b"), "releases": re.compile(r"(\d+)\+? releases?\b"),
            "videos": re.compile(r"(\d+)\+? videos?\b")}


def number_counts(numbers):
    """{"merged", "releases", "videos"} read from the brief's Numbers lines (0 when a figure is missing)."""
    text = " ".join(numbers or [])
    out = {}
    for key, rx in COUNT_RE.items():
        m = rx.search(text)
        out[key] = int(m.group(1)) if m else 0
    return out


PROJECT_SECTION = re.compile(r"^(active projects|projects)\b", re.I)
REPO_LINK = re.compile(r"\[([\w.-]+/[\w.-]+)\]\(https?://github\.com/[^)\s]+\)|https?://github\.com/([\w.-]+/[\w.-]+)")
WHAT_WORDS = 20


def first_sentence(text, limit=WHAT_WORDS):
    """The first sentence of `text`, cut to `limit` words (with "..." when cut)."""
    text = re.sub(r"\s+", " ", text or "").strip()
    for m in re.finditer(r"[.!?](?=\s|$)", text):
        if text[:m.start()].count('"') % 2 == 0 and text[:m.start()].count("(") <= text[:m.start()].count(")"):
            text = text[:m.end()]
            break
    words = text.split()
    return " ".join(words[:limit]).rstrip(".,;:") + "..." if len(words) > limit else text


# A profile line that only classifies a project ("Product, open source (about 11.5k stars).", "Internal tooling for
# the agency") or names its audience says little about what it is; such lines are used only when nothing else does.
CLASSIFY = re.compile(r"^(product|open[- ]source|internal tool\w*|personal tool\w*|someone else'?s|what it is)\b[^:]*:\s*", re.I)
CLASSIFY_ONLY = re.compile(r"^(product|open[- ]source|internal tool|personal tool|someone else)", re.I)
SKIP_LINE = re.compile(r"^(audience|status|history|links?)\s*:|^\(", re.I)
DESCRIBE_WORDS = 6


def describe(lines):
    """The line that best says what a project is: the first one of some length that is not only a classification."""
    cleaned = []
    for line in lines:
        line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line).strip(" .,;")
        if not line or SKIP_LINE.match(line):
            continue
        cleaned.append(CLASSIFY.sub("", line).strip(" .,;") or line)
    bare = lambda l: re.sub(r"\([^)]*\)", "", l)
    good = [l for l in cleaned if len(bare(l).split()) >= DESCRIBE_WORDS and not (CLASSIFY_ONLY.match(l) and len(bare(l).split()) < 8)]
    pick = good[0] if good else (cleaned[0] if cleaned else "")
    what = first_sentence(pick) if pick else ""
    return what[:-1] if what.endswith(".") and not what.endswith("...") else what


def project_lines(profile):
    """One line per project in the profile's "Active projects" (or "Projects") section: [{"name", "repo", "what"}].

    Reads both shapes profiles use: a "### Name" subsection whose first lines are a repo link and a
    description (before any "History:"), or a "- name: description" bullet. "what" is the first
    sentence that says what the project is, so a bullet can say it in a few words.
    """
    out, inside, current = [], False, None
    for line in (profile or "").splitlines():
        stripped = line.strip()
        if line.startswith("## "):
            inside, current = bool(PROJECT_SECTION.match(line[3:].strip())), None
            continue
        if not inside:
            continue
        if line.startswith("### "):
            current = {"name": line[4:].strip(), "repo": "", "lines": [], "closed": False}
            out.append(current)
            continue
        if current is None:
            m = re.match(r"^- (?:\[([^\]]+)\]\([^)]*\)|([^:]+)):\s*(.+)$", line)
            if m:
                name = (m.group(1) or m.group(2)).strip()
                link = REPO_LINK.search(line)
                repo = (link.group(1) or link.group(2)) if link else (name if "/" in name else "")
                out.append({"name": name.split("/")[-1], "repo": repo, "lines": [m.group(3)], "closed": True})
            continue
        if current["closed"] or not stripped:
            continue
        if re.match(r"^(history|links?)\s*:", stripped, re.I) or stripped.startswith("- "):
            current["closed"] = True
            continue
        link = REPO_LINK.search(stripped)
        if link and not current["repo"]:
            current["repo"] = link.group(1) or link.group(2)
        current["lines"].append(REPO_LINK.sub("", stripped))
    found = []
    for p in out:
        what = describe(p["lines"])
        if what:
            found.append({"name": p["name"], "repo": p["repo"], "what": what})
    return found


def projects_in_window(projects, items, gone=()):
    """The profile projects this window's items touch, in profile order."""
    repos = {i["repo"].lower() for i in items if i.get("repo")} | {g["repo"].lower() for g in gone}
    names = {r.split("/")[-1] for r in repos}
    return [p for p in projects if (p["repo"] and p["repo"].lower() in repos)
            or re.split(r"[\s(]", p["name"].lower())[0] in names]


# The front-page lead, picked in code: the biggest news (the ranked highlights, the top one counted twice),
# plus a capped bonus for volume (merged PRs, releases and launches). The reader knows what they shipped,
# so they lead only when their value beats everyone else's by READER_MARGIN.
LEAD_ITEMS = 3
VOLUME_CAP = 8
READER_MARGIN = 3
LAUNCH = re.compile(r"(?<!\bat )(?<!\bon )\blaunch(es|ed|ing)?\b(?! contract)", re.I)


def launches(person):
    """New repos worth a story, and items whose title says they launched, in the window."""
    return sum(1 for h in person.get("highlights") or [] if (h["kind"] == "birth" and h["score"] >= KIND_SCORE["birth"])
               or (h["kind"] in ("pr", "commit", "release") and LAUNCH.search(h["title"])))


def lead_value(person):
    """How much this person's news deserves the front page lead, from code alone."""
    scores = sorted((h["score"] for h in person.get("highlights") or [] if h["kind"] != "archived"), reverse=True)[:LEAD_ITEMS]
    news = (scores[0] if scores else 0) + sum(scores)
    n = number_counts(person.get("numbers"))
    volume = min(VOLUME_CAP, math.log2(1 + n["merged"] + 2 * n["releases"] + 3 * launches(person)))
    return round(news + volume, 2)


def pick_lead(people):
    """The id of the person whose news leads, among `people` (active people with highlights), or None."""
    if not people:
        return None
    key = lambda p: (lead_value(p), max((h["score"] for h in p.get("highlights") or []), default=0), bool(p.get("starred")),
                     len(p.get("items") or []))
    ranked = sorted(people, key=key, reverse=True)
    others = [p for p in ranked if not p.get("self")]
    reader = next((p for p in ranked if p.get("self")), None)
    if reader is None or not others:
        return ranked[0]["id"]
    return reader["id"] if lead_value(reader) >= lead_value(others[0]) + READER_MARGIN else others[0]["id"]


def load_exact_titles(paper, pid):
    """url -> exact title from data/<id>.json (PR titles, commit messages, video titles), when the data was pulled."""
    try:
        data = json.loads((Path(paper) / "data" / f"{pid}.json").read_text())
    except (OSError, ValueError):
        return {}
    out = {}
    gh = data.get("github") or {}
    for p in gh.get("pull_requests", []):
        if p.get("url") and p.get("title"):
            out[p["url"]] = p["title"]
    for c in gh.get("commits", []):
        if c.get("url") and c.get("message"):
            out[c["url"]] = c["message"].splitlines()[0]
    for v in (data.get("youtube") or {}).get("videos", []):
        if v.get("url") and v.get("title"):
            out[v["url"]] = v["title"]
    return out


def prepare(paper, kind=None, today=None, now=None):
    """Everything the run needs, per person, without calling a model.

    Returns {"date", "kind", "reader", "people": [...]} where each person has
    "status": "active" (gets a request), "quiet" (a template line) or "skipped"
    (no or stale brief, as paper/INSTRUCTIONS.md step 2 says: no report).
    """
    paper = Path(paper)
    cfg = config.load(paper)
    today = today or dt.datetime.now(dt.timezone.utc).date()
    now = now or dt.datetime.now(dt.timezone.utc)
    kind = kind or kind_for(today)
    people = []
    for p in cfg["people"]:
        pid = p["id"]
        entry = {"id": pid, "name": p["name"], "self": p["self"], "starred": p["starred"], "known_for": p["known_for"]}
        brief_path = paper / "briefs" / f"{pid}.md"
        if not brief_path.is_file():
            people.append({**entry, "status": "skipped", "why": "no brief"})
            continue
        text = brief_path.read_text()
        when = collected_at(text)
        if when is None or now - when > dt.timedelta(hours=STALE_HOURS):
            people.append({**entry, "status": "skipped", "why": "brief older than 24 hours" if when else "brief has no collection time"})
            continue
        start, end = window(kind, today, report_dates(paper, pid, today), text)
        trimmed, items = trim(text, start, end)
        gone = archived(text)
        prof = paper / "profiles" / f"{pid}.md"
        person = {**entry, "start": start.isoformat(), "end": end.isoformat(), "brief": trimmed, "items": items,
                  "archived": gone, "numbers": numbers_line(text), "titles": load_exact_titles(paper, pid),
                  "highlights": highlights(items, gone, busiest(text))}
        if not items and not gone:
            people.append({**person, "status": "quiet"})
            continue
        person["profile"] = prof.read_text() if prof.is_file() else ""
        person["projects"] = projects_in_window(project_lines(person["profile"]), items, gone)
        people.append({**person, "status": "active"})
    lead = pick_lead([p for p in people if p["status"] == "active"])
    return {"date": today.isoformat(), "kind": kind, "reader": cfg["writer"]["reader"], "people": people, "lead": lead}
