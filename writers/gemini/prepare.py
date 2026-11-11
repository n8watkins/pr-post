"""Stage 0, in code: the window, each brief trimmed to it, quiet people, and the items a quote may come from.

Nothing here calls a model. Everything the later stages send is built from what
this module returns, so the dry run and the real run send the same text.
"""

import datetime as dt
import json
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
                continue
            keep_child = False
            if not line.strip():
                continue
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
            out += ["", re.sub(r"\s*\(\d+\)\s*$", "", heading).replace(", newest first", " in the window, newest first")] + kept
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
                  "archived": gone, "numbers": numbers_line(text), "titles": load_exact_titles(paper, pid)}
        if not items and not gone:
            people.append({**person, "status": "quiet"})
            continue
        person["profile"] = prof.read_text() if prof.is_file() else ""
        people.append({**person, "status": "active"})
    return {"date": today.isoformat(), "kind": kind, "reader": cfg["writer"]["reader"], "people": people}
