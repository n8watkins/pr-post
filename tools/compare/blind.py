"""Build a blind A/B packet comparing two writers on one snapshot, and unblind the reviews into reviews/<date>-<label>.md.

A writer is a root inside the paper: "" for the Claude routine (reports/,
editions/, profiles/) and "gemini" or "gemini/single" for the Gemini writer.
The packet shows the same trimmed briefs the Gemini writer read, then each
writer's output under the labels Writer A and Writer B, with every field that
names a model removed. The mapping goes in a separate key file that the
reviewer never opens.
"""

import datetime as dt
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from writers.gemini import prepare  # noqa: E402

# Fields that would tell the reviewer which writer is which.
REVEALING = ("source", "prompt_version", "prompt_sha", "mode", "checks", "model")
FENCE = "~~~~"


def writer_name(paper, root, date):
    """A readable name for the writer at `root`, from its edition's source or its usage log."""
    base = Path(paper) / root if root else Path(paper)
    for f in sorted((base / "editions").glob(f"{date}-*.json")):
        try:
            src = json.loads(f.read_text()).get("source")
        except (OSError, ValueError):
            src = None
        if src:
            mode = "single" if root.endswith("single") else ("split" if root else "")
            return f"{src} ({mode})" if mode else src
    return "claude" if not root else root


def strip_revealing(value):
    if isinstance(value, dict):
        return {k: strip_revealing(v) for k, v in value.items() if k not in REVEALING}
    if isinstance(value, list):
        return [strip_revealing(v) for v in value]
    return value


def read(path):
    try:
        return Path(path).read_text()
    except OSError:
        return None


def edition(paper, root, date, kind):
    base = Path(paper) / root if root else Path(paper)
    text = read(base / "editions" / f"{date}-{kind}.json")
    if text is None:
        return None
    try:
        return strip_revealing(json.loads(text))
    except ValueError:
        return {"unreadable": True}


def profile_lines(paper, root, pid, date):
    """Profile lines a writer dated on this run (both writers add "- <date>: ..." lines)."""
    base = Path(paper) / root if root else Path(paper)
    text = read(base / "profiles" / f"{pid}.md") or ""
    return [line for line in text.splitlines() if date in line]


def fenced(text, lang=""):
    return f"{FENCE}{lang}\n{(text or '').rstrip()}\n{FENCE}"


def build_packet(paper, date, kind, a_root, b_root, out, key, rng=None):
    """Write <out>/packet.md and the key file. Returns the key dict."""
    paper = Path(paper)
    today = dt.date.fromisoformat(date)
    now = dt.datetime.combine(today + dt.timedelta(days=1), dt.time(), dt.timezone.utc)
    prep = prepare.prepare(paper, kind, today, now)
    rng = rng or random.SystemRandom()
    roots = [a_root, b_root]
    if rng.random() < 0.5:
        roots.reverse()
    labels = {"A": roots[0], "B": roots[1]}

    lines = [f"# Blind comparison packet: {kind} of {date}", "",
             "Two writers, Writer A and Writer B, wrote this run from the same briefs below.",
             "Review it as tools/compare/REVIEWER.md says; do not open anything else in the paper.", ""]
    lines += ["## Front pages", ""]
    for label in ("A", "B"):
        e = edition(paper, labels[label], date, kind)
        lines += [f"### Writer {label}: edition", ""]
        lines += [fenced(json.dumps(e, indent=1, ensure_ascii=False), "json") if e is not None else "(no edition)", ""]
    lines += ["## People", ""]
    for p in prep["people"]:
        window = f"{p['start']} to {p['end']}" if p.get("start") else "no window"
        status = p["status"] + (f", {p['why']}" if p.get("why") else "")
        lines += [f"### {p['name']} ({p['id']})", "", f"Window {window}; {status}.", ""]
        if p.get("brief"):
            lines += ["#### Brief, trimmed to the window", "", fenced(p["brief"], "markdown"), ""]
        for label in ("A", "B"):
            base = paper / labels[label] if labels[label] else paper
            report = read(base / "reports" / p["id"] / f"{date}-{kind}.md")
            lines += [f"#### Writer {label}: report", "", fenced(report, "markdown") if report is not None else "(no report)", ""]
            added = profile_lines(paper, labels[label], p["id"], date)
            if added:
                lines += [f"Writer {label} added to the profile:", ""] + [f"    {line}" for line in added] + [""]
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "packet.md").write_text("\n".join(lines).rstrip() + "\n")
    data = {"date": date, "kind": kind, "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "packet": str(out / "packet.md"),
            "A": {"root": labels["A"], "writer": writer_name(paper, labels["A"], date)},
            "B": {"root": labels["B"], "writer": writer_name(paper, labels["B"], date)}}
    key = Path(key)
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text(json.dumps(data, indent=1) + "\n")
    return data


def usage_row(paper, root, date, price_in=None, price_out=None, override=None):
    """Requests, tokens and an estimated cost for one writer, from its usage log (the Gemini writer writes one)."""
    if override is not None:
        return override
    base = Path(paper) / root if root else Path(paper)
    text = read(base / "usage" / f"{date}.json")
    if text is None:
        return None
    u = json.loads(text)
    row = {k: u.get(k) for k in ("requests", "retries", "input_tokens", "output_tokens", "thinking_tokens", "wall_s")}
    if price_in is not None and price_out is not None:
        row["usd"] = round(u["input_tokens"] / 1e6 * price_in + u["output_tokens"] / 1e6 * price_out, 4)
    return row


def cost_table(rows):
    out = ["| Writer | Requests | Retries | Input tokens | Output tokens | Estimated cost |", "| --- | --- | --- | --- | --- | --- |"]
    for name, row in rows:
        if row is None:
            out.append(f"| {name} | not logged | | | | |")
            continue
        usd = f"${row['usd']}" if row.get("usd") is not None else "no price given"
        out.append(f"| {name} | {row.get('requests', '')} | {row.get('retries', '')} | {row.get('input_tokens', '')} | {row.get('output_tokens', '')} | {usd} |")
    return "\n".join(out)


def root_slug(root):
    return root.replace("/", "-") if root else "claude"


def default_label(key):
    """A file name part naming the two writers compared, such as "claude-vs-gemini-single"."""
    return "-vs-".join(sorted(root_slug(key[lb]["root"]) for lb in ("A", "B")))


def packet_marker(key):
    return f"Packet built {key.get('created_at', 'at an unknown time')}."


def review_path(paper, key, label=None):
    """reviews/<date>-<label>.md. A file there from another packet is kept: the next free -2, -3 is used instead."""
    label = re.sub(r"[^a-z0-9.]+", "-", (label or default_label(key)).lower()).strip("-.") or "review"
    marker = packet_marker(key)
    base = Path(paper) / "reviews"
    n = 1
    while True:
        path = base / (f"{key['date']}-{label}.md" if n == 1 else f"{key['date']}-{label}-{n}.md")
        existing = read(path)
        if existing is None or marker in existing:
            return path
        n += 1


def demote(md):
    """Every Markdown heading one level down, outside fenced blocks."""
    out, fence = [], None
    for line in md.splitlines():
        m = re.match(r"^(`{3,}|~{3,})", line)
        if m:
            if fence is None:
                fence = m.group(1)
            elif line.strip() == fence:
                fence = None
        out.append("#" + line if fence is None and re.match(r"^#{1,5} ", line) else line)
    return "\n".join(out)


def unblind(paper, key_path, reviews, price_in=None, price_out=None, claude_usage=None, label=None):
    """Name the writers in one or more blind reviews and save them as one file, with cost and points tables.

    `reviews` is {reviewer: review text}; a single text counts as the Claude reviewer's.
    Writes reviews/<date>-<label>.md and returns (path, text).
    """
    from . import score

    key = json.loads(Path(key_path).read_text())
    if isinstance(reviews, str):
        reviews = {"claude": reviews}
    if not reviews:
        raise ValueError("No review to unblind.")
    for name, review_text in reviews.items():
        for lb in ("A", "B"):
            if f"Writer {lb}" not in review_text:
                raise ValueError(f"The {name} review never mentions Writer {lb}; is it the review of this packet?")
    names = {lb: key[lb]["writer"] for lb in ("A", "B")}

    def named(text):
        return re.sub(r"\bWriter ([AB])\b", lambda m: f"{names[m.group(1)]} (Writer {m.group(1)})", text)

    parsed, unscored = {}, []
    for name, review_text in reviews.items():
        try:
            parsed[name] = score.parse_review(review_text)
        except ValueError:
            unscored.append(name)
    rows = []
    for lb in ("A", "B"):
        root = key[lb]["root"]
        override = claude_usage if not root else None
        rows.append((names[lb], usage_row(paper, root, key["date"], price_in if root else None, price_out if root else None, override)))
    count = f"{len(reviews)} reviewer{'s' if len(reviews) > 1 else ''} ({', '.join(reviews)})"
    header = [f"# Writer comparison: {key['kind']} of {key['date']}", "",
              f"Reviewed blind by {count}, then unblinded: Writer A was {names['A']} and Writer B was {names['B']}.",
              packet_marker(key), "", "## Cost", "", cost_table(rows), ""]
    if parsed:
        header += ["## Points", "", f"Each writer starts at {score.BASE}; the weights are in tools/compare/score.py.", "",
                   score.render(parsed, names), ""]
    if unscored:
        header += [f"No scores block in the review by {', '.join(unscored)}, so it is left out of the points.", ""]
    parts = []
    for name, review_text in reviews.items():
        body = re.sub(r"^# .*\n+", "", review_text.strip(), count=1)
        if len(reviews) > 1:
            body = f"## Review by the {name} reviewer\n\n" + demote(body)
        parts.append(named(body))
    text = "\n".join(header) + "\n" + "\n\n".join(parts) + "\n"
    path = review_path(paper, key, label)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path, text
