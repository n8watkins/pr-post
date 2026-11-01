"""Parse the Markdown subset reports use into blocks, shared by email and site.

Blocks are (kind, data) pairs: ("h1".."h3", text), ("p", text), ("hr", None),
("table", rows), ("list", (ordered, [[nested, text], ...])).
"""

import re
from urllib.parse import urlparse


def safe_url(url):
    """Only http(s) links survive; anything else (javascript:, data:, ...) becomes '#'.

    Reports are written from other people's PR text, so a link is untrusted input.
    """
    url = (url or "").strip()
    return url if urlparse(url).scheme in ("http", "https") else "#"


def blocks(md):
    """Parse Markdown into (kind, data) blocks: h1-h3, p, list, table, hr."""
    out, para, lines, i = [], [], md.splitlines(), 0

    def flush():
        if para:
            out.append(("p", " ".join(para)))
            para.clear()

    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            flush()
        elif m := re.match(r"^(#{1,3})\s+(.*)", s):
            flush()
            out.append((f"h{len(m[1])}", m[2]))
        elif re.match(r"^(-{3,}|\*{3,})$", s):
            flush()
            out.append(("hr", None))
        elif s.startswith("|") and i + 1 < len(lines) and re.match(r"^\|?\s*:?-{2,}", lines[i + 1].strip()):
            flush()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                if not re.match(r"^\|?[\s:|-]+$", lines[i].strip()):
                    rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            out.append(("table", rows))
            continue
        elif re.match(r"^\s*([-*]|\d+\.)\s+", line):
            flush()
            items, ordered = [], bool(re.match(r"^\s*\d+\.", line))
            while i < len(lines) and lines[i].strip():
                m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)", lines[i])
                if m:
                    items.append([len(m[1]) >= 2, m[3]])
                elif items:  # continuation line of the previous item
                    items[-1][1] += " " + lines[i].strip()
                i += 1
            out.append(("list", (ordered, items)))
            continue
        else:
            para.append(s)
        i += 1
    flush()
    return out
