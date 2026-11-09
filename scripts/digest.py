"""What the short digest says, shared by the email and the web edition.

One run of the writer produces an issue: every report with the same date and
kind (reports/<id>/<date>-<kind>.md), plus usually an edition JSON for the
front page. The email shows the issue's TL;DR and one line per person, and
links to the issue's web page, /r/<date>-<kind>/, for everything else.
"""

import json
import re
from pathlib import Path

from md import blocks

KIND_NAMES = {"weekly": "Weekly report", "update": "Midweek update"}


def plain(md_text):
    """Markdown to plain text: link labels kept, link targets, emphasis and code marks dropped."""
    text = re.sub(r"\[([^\]]+)\]\((?:[^()\s]|\([^()\s]*\))*\)", r"\1", md_text or "")
    text = re.sub(r"[*`]", "", text)
    text = re.sub(r"\s*\(\s*(,\s*)*\)", "", text)  # "(PR #1)" whose label was the whole link stays; "()" goes
    return re.sub(r"\s+", " ", text).strip()


def first_sentence(text, limit=130, asides=False):
    """The first sentence of plain text, cut at a word boundary to at most `limit` characters.

    asides=False drops long parenthetical asides ("Hex (a voice dictation app: ...) got") so a teaser line keeps its verb.
    """
    text = plain(text)
    if not asides:
        text = re.sub(r"\s*\([^()]{18,}\)", "", text)
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    if m and len(m[1]) >= 25:
        text = m[1]
    if len(text) <= limit:
        return text
    return text[: limit - 3].rsplit(" ", 1)[0].rstrip(",;:(") + "..."


def kind_of(stem):
    return "update" if stem.endswith("-update") else "weekly"


def sections(md):
    """[(h2 heading or None, [(kind, data), ...])] with the h1 dropped."""
    out, current = [], [None, []]
    for k, d in blocks(md):
        if k == "h1":
            continue
        if k == "h2":
            out.append(current)
            current = [d, []]
        else:
            current[1].append((k, d))
    out.append(current)
    return [s for s in out if s[0] or s[1]]


def is_tldr(heading):
    return bool(heading and re.match(r"(?i)tl;?dr", heading.strip()))


def title_of(md, fallback):
    return next((l[2:].strip() for l in md.splitlines() if l.startswith("# ")), fallback)


def span_of(title):
    """Report titles look like "<name>: <start> to <end>"; the part after the colon."""
    return title.partition(":")[2].strip()


def one_liner(md, limit=130):
    """What they shipped, in one line: the TL;DR's first bullet, else the first bullet, else the first paragraph."""
    secs = sections(md)
    ordered = [s for s in secs if is_tldr(s[0])] + [s for s in secs if not is_tldr(s[0])]
    for _, content in ordered:
        for k, d in content:
            if k == "list" and d[1]:
                return first_sentence(d[1][0][1], limit)
    for _, content in secs:
        for k, d in content:
            if k == "p":
                return first_sentence(d, limit)
    return ""


def link_count(md):
    return len(re.findall(r"\]\(https?://", md))


def is_quiet(md):
    """A quiet-week note ("Nothing notable since Tuesday."): no bullets and only a line or two of text."""
    body = [(k, d) for _, content in sections(md) for k, d in content]
    return not any(k in ("list", "table") for k, _ in body) and len(plain(" ".join(d for k, d in body if k == "p"))) < 280


def load_edition(paper, stem):
    """editions/<stem>.json when the writer made one for this issue, else None."""
    try:
        return json.loads((Path(paper) / "editions" / f"{stem}.json").read_text())
    except (OSError, ValueError):
        return None


def tldr(edition, items, limit=5):
    """3 to 5 short bullets for the top of the email and the web page.

    items: [(person, md)] in display order. Best source first: the edition's
    own "tldr" list, then its headlines, then the busiest people's one-liners.
    """
    if edition and isinstance(edition.get("tldr"), list):
        bullets = [plain(b) for b in edition["tldr"] if isinstance(b, str) and b.strip()]
        if bullets:
            return bullets[:limit]
    if edition and edition.get("lead", {}).get("headline"):
        heads = [edition["lead"]["headline"]] + [s.get("headline", "") for s in edition.get("stories", [])]
        return [plain(h) for h in heads if h][: min(limit, 4)]
    busy = sorted((x for x in items if not is_quiet(x[1])), key=lambda x: -link_count(x[1]))
    return [f"{p['name']}: {one_liner(md, 110)}" for p, md in busy[: min(limit, 4)]]
