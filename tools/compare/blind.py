"""Build a blind A/B packet comparing two writers on one snapshot, and unblind the review into reviews/<date>.md.

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


def unblind(paper, key_path, review_text, price_in=None, price_out=None, claude_usage=None):
    """The review with Writer A and Writer B named, a header with the mapping and a cost table. Returns (path, text)."""
    key = json.loads(Path(key_path).read_text())
    for label in ("A", "B"):
        if f"Writer {label}" not in review_text:
            raise ValueError(f"The review never mentions Writer {label}; is it the review of this packet?")
    names = {label: key[label]["writer"] for label in ("A", "B")}
    body = re.sub(r"\bWriter ([AB])\b", lambda m: f"{names[m.group(1)]} (Writer {m.group(1)})", review_text.strip())
    body = re.sub(r"^# .*\n+", "", body, count=1)
    rows = []
    for label in ("A", "B"):
        root = key[label]["root"]
        override = claude_usage if not root else None
        rows.append((names[label], usage_row(paper, root, key["date"], price_in if root else None, price_out if root else None, override)))
    header = [f"# Writer comparison: {key['kind']} of {key['date']}", "",
              f"Reviewed blind, then unblinded: Writer A was {names['A']} and Writer B was {names['B']}.", "",
              "## Cost", "", cost_table(rows), ""]
    text = "\n".join(header) + "\n" + body + "\n"
    path = Path(paper) / "reviews" / f"{key['date']}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path, text
