#!/usr/bin/env python3
"""Email a Markdown report through Resend, rendered as a styled HTML email.

Usage:
    python3 scripts/send_email.py [--paper DIR] <paper>/reports/<id>/<date>-<kind>.md [more.md ...]
    python3 scripts/send_email.py [--paper DIR] <report.md> ... --preview out.html   # render only
    python3 scripts/send_email.py [--paper DIR] <report.md> ... --dry-run            # every check, no send

Several reports become one digest email with an "In this issue" summary.

Needs RESEND_API_KEY to send. Recipient and sender come from [email] in
watchlist.toml, and sending must be turned on with email = true under
[delivery]. Exits 0 with a message when email is turned off or no key is
configured, so a run without email still succeeds. --dry-run runs every check
a real send would (email on, a recipient, a key) and prints the subject, but
never contacts Resend; add --preview to keep the rendered HTML too.

Email clients ignore most CSS, so the layout is tables with inline styles, and
the Markdown is converted here (the subset reports use) to avoid dependencies.
"""

import argparse
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
from md import blocks, safe_url  # noqa: E402

FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"
INK, MUTED, LINE, ACCENT, PAPER, CANVAS = "#16161d", "#5f6170", "#e6e4ee", "#5b3df5", "#ffffff", "#f1eff8"

KINDS = {
    "weekly": ("WEEKLY REPORT", "#5b3df5", "#c026d3"),
    "update": ("MIDWEEK UPDATE", "#0e7490", "#2563eb"),
}


# ---------- Markdown subset -> inline-styled HTML ----------

def inline(text):
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", lambda m: f'<code style="font-family:{MONO};font-size:13px;background:#f3f1fa;border-radius:4px;padding:1px 5px">{m[1]}</code>', text)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", lambda m: f'<a href="{html.escape(safe_url(html.unescape(m[2])))}" style="color:{ACCENT};text-decoration:none;font-weight:600">{m[1]}</a>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", rf'<strong style="color:{INK}">\1</strong>', text)
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?!\w)", r"<em>\1</em>", text)
    return text


def render_list(ordered, items, tight=False):
    tag = "ol" if ordered else "ul"
    rows = []
    for nested, text in items:
        pad = "padding-left:18px;" if nested else ""
        size = "14px" if nested else "15px"
        rows.append(f'<li style="margin:0 0 {6 if tight else 9}px;{pad}font-size:{size};line-height:1.55">{inline(text)}</li>')
    return f'<{tag} style="margin:0 0 14px;padding-left:20px;color:{INK}">{"".join(rows)}</{tag}>'


def render_table(rows):
    head, *body = rows
    th = "".join(f'<th align="left" style="padding:9px 12px;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:{MUTED};border-bottom:2px solid {LINE};white-space:nowrap">{inline(c)}</th>' for c in head)
    trs = []
    for n, r in enumerate(body):
        bg = "#faf9fd" if n % 2 else PAPER
        tds = "".join(f'<td style="padding:10px 12px;font-size:14px;line-height:1.45;color:{INK};border-bottom:1px solid {LINE};{"font-weight:600;" if j == 0 else ""}">{inline(c)}</td>' for j, c in enumerate(r))
        trs.append(f'<tr style="background:{bg}">{tds}</tr>')
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin:4px 0 14px"><tr>{th}</tr>{"".join(trs)}</table>'


def render_block(kind, data):
    if kind == "p":
        # "**What it is:** ..." style lead-ins become small labels.
        if m := re.match(r"^\*\*([^*]+?):?\*\*:?\s*(.*)", data):
            return (f'<p style="margin:0 0 12px;font-size:15px;line-height:1.6;color:{INK}">'
                    f'<span style="display:inline-block;font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:{ACCENT};margin-right:6px">{html.escape(m[1])}</span>{inline(m[2])}</p>')
        return f'<p style="margin:0 0 12px;font-size:15px;line-height:1.6;color:{INK}">{inline(data)}</p>'
    if kind == "h3":
        return f'<h3 style="margin:18px 0 8px;font-size:15px;color:{INK}">{inline(data)}</h3>'
    if kind == "list":
        return render_list(*data)
    if kind == "table":
        return render_table(data)
    if kind == "hr":
        return f'<div style="height:1px;background:{LINE};margin:18px 0"></div>'
    return ""


def card(title, body, highlight=False):
    if highlight:
        return (f'<tr><td style="padding:0 0 16px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                f'style="background:#f6f3ff;border:1px solid #ddd5ff;border-radius:14px"><tr><td style="padding:22px 24px 10px">'
                f'<div style="font-size:12px;font-weight:800;letter-spacing:.12em;color:{ACCENT};margin:0 0 12px">&#9889; {html.escape(title.upper())}</div>'
                f'{body}</td></tr></table></td></tr>')
    heading = (f'<h2 style="margin:0 0 14px;font-size:19px;line-height:1.3;color:{INK};letter-spacing:-.01em">'
               f'<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:{ACCENT};margin:0 10px 2px 0"></span>{inline(title)}</h2>') if title else ""
    return (f'<tr><td style="padding:0 0 16px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="background:{PAPER};border:1px solid {LINE};border-radius:14px"><tr><td style="padding:22px 24px 10px">'
            f'{heading}{body}</td></tr></table></td></tr>')


def split_title(title):
    # Report titles look like "<name>: <start> to <end>".
    name, _, span = title.partition(":")
    return name.strip(), span.strip()


def plain(text):
    """Markdown to preheader text: keep link labels, drop link targets of any scheme."""
    text = re.sub(r"\[([^\]]+)\]\([^)\s]*\)*", r"\1", text)
    return re.sub(r"[*`\[\]]", "", text)


def person_cards(md, fallback):
    """One report -> (title, card rows, first TL;DR line)."""
    parsed = blocks(md)
    title = next((d for k, d in parsed if k == "h1"), fallback)
    parsed = [b for b in parsed if b[0] != "h1"]

    # Split on h2 into sections; anything before the first h2 is an untitled intro.
    sections, current = [], [None, []]
    for k, d in parsed:
        if k == "h2":
            sections.append(current)
            current = [d, []]
        else:
            current[1].append((k, d))
    sections.append(current)
    sections = [s for s in sections if s[0] or s[1]]

    lead, cards = "", []
    for heading, content in sections:
        is_tldr = bool(heading and re.match(r"(?i)tl;?dr", heading))
        if is_tldr or not lead:
            first = next((d for k, d in content if k in ("p", "list")), None)
            if first:
                lead = plain(first if isinstance(first, str) else first[1][0][1])
        body = "".join(
            render_list(*d, tight=True) if is_tldr and k == "list" else render_block(k, d)
            for k, d in content
        )
        cards.append(card(heading or "", body, highlight=is_tldr))
    return title, "".join(cards), lead


def person_divider(n, total, name, span):
    return (f'<tr><td style="padding:22px 6px 12px">'
            f'<div style="font-size:11px;font-weight:800;letter-spacing:.14em;color:{ACCENT}">{n} OF {total}</div>'
            f'<div style="font-size:26px;line-height:1.2;font-weight:800;color:{INK};letter-spacing:-.02em;margin:4px 0 2px">{inline(name)}</div>'
            f'<div style="font-size:14px;color:{MUTED}">{inline(span)}</div></td></tr>')


def render(reports, kind, links, brand="PR Post"):
    """reports: [(markdown, fallback title)] in display order."""
    people = [person_cards(md, fb) for md, fb in reports]
    label, c1, c2 = KINDS.get(kind, KINDS["weekly"])

    if len(people) == 1:
        title, rows, preheader = people[0]
        head_name, head_span = split_title(title)
    else:
        spans = [split_title(t)[1] for t, _, _ in people]
        head_name, head_span = f"{len(people)} people", spans[0]
        title = f"{head_name}: {head_span}"
        preheader = " / ".join(split_title(t)[0] for t, _, _ in people)
        toc = "".join(
            f'<li style="margin:0 0 9px;font-size:15px;line-height:1.5;color:{INK}"><strong style="color:{INK}">{html.escape(split_title(t)[0])}</strong>'
            f'<span style="color:{MUTED}">: </span>{html.escape(lead[:160])}</li>'
            for t, _, lead in people
        )
        rows = card("In this issue", f'<ul style="margin:0 0 14px;padding-left:20px">{toc}</ul>', highlight=True)
        for n, (t, cards, _) in enumerate(people, 1):
            rows += person_divider(n, len(people), *split_title(t)) + cards

    footer_links = " &nbsp;&middot;&nbsp; ".join(
        f'<a href="{html.escape(safe_url(u))}" style="color:{MUTED};text-decoration:underline">{t}</a>' for t, u in links if u
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only"><title>{html.escape(title)}</title></head>
<body style="margin:0;padding:0;background:{CANVAS};font-family:{FONT};-webkit-font-smoothing:antialiased">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{html.escape(preheader[:140])}&#8199;&#847;&#8199;&#847;&#8199;&#847;</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{CANVAS}"><tr><td align="center" style="padding:24px 12px 40px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px">
<tr><td style="padding:0 0 16px">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{c1};background-image:linear-gradient(135deg,{c1} 0%,{c2} 100%);border-radius:18px">
  <tr><td style="padding:26px 28px 28px">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td style="font-family:{MONO};font-size:14px;font-weight:700;color:#ffffff;letter-spacing:.02em">{html.escape(brand)}</td>
      <td align="right"><span style="display:inline-block;font-size:10px;font-weight:800;letter-spacing:.14em;color:{c1};background:#ffffff;border-radius:999px;padding:5px 10px">{label}</span></td>
    </tr></table>
    <div style="font-size:32px;line-height:1.15;font-weight:800;color:#ffffff;letter-spacing:-.02em;margin:26px 0 6px">{inline(head_name)}</div>
    <div style="font-size:15px;color:#ffffff;opacity:.85">{inline(head_span)}</div>
  </td></tr></table>
</td></tr>
{rows}
<tr><td align="center" style="padding:10px 12px 0;font-size:12px;line-height:1.7;color:{MUTED}">
  {footer_links}<br>
  To stop these emails, set <code style="font-family:{MONO}">email = false</code> in watchlist.toml.
</td></tr>
</table></td></tr></table></body></html>"""


# ---------- sending ----------

def repo_path(path):
    """A file's path inside the git repository that holds it, for a link to it on GitHub, or None.

    The engine and the paper may live in different repositories, so this
    looks for the repository around the report, not around this script.
    """
    path = Path(path).resolve()
    for parent in path.parents:
        if (parent / ".git").exists():
            return path.relative_to(parent).as_posix()
    return None


def subject_for(reports, kind):
    titles = [next((l[2:].strip() for l in md.splitlines() if l.startswith("# ")), fb) for md, fb in reports]
    label = KINDS[kind][0].title()
    if len(titles) == 1:
        return f"{label}: {titles[0]}"
    return f"{label}: {len(titles)} people, {split_title(titles[0])[1]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper", help="folder holding watchlist.toml (default: $PR_POST_PAPER or the repo root)")
    ap.add_argument("reports", nargs="+", help="one email for all of them, in watchlist order")
    ap.add_argument("--subject", help="default: built from the report titles")
    ap.add_argument("--preview", metavar="FILE", help="write the HTML here instead of sending")
    ap.add_argument("--dry-run", action="store_true", help="run every check a real send would, print the subject, but never send")
    args = ap.parse_args()

    paper = config.paper_dir(args.paper)
    try:
        watch = config.load(paper)
    except config.ConfigError as e:
        sys.exit(f"Config error: {e}")
    cfg, link_cfg = watch["email"], watch["links"]
    order = [p["id"] for p in watch["people"]]
    paths = sorted(
        (Path(r) for r in args.reports),
        key=lambda p: order.index(p.parent.name.lower()) if p.parent.name.lower() in order else len(order),
    )
    kind = "update" if all(p.stem.endswith("-update") for p in paths) else "weekly"
    reports = [(p.read_text(), p.stem) for p in paths]

    repo = link_cfg.get("repo")
    rel = repo_path(paths[0]) if len(paths) == 1 else None
    github = f"{repo}/blob/main/{rel}" if repo and rel else repo
    links = [
        ("Read on GitHub", github),
        ("Read the paper", watch["site"]["url"] or None),
    ] + [(label.replace("_", " ").title(), url) for label, url in link_cfg.items() if label != "repo" and isinstance(url, str)]
    page = render(reports, kind, links, watch["site"]["title"])

    if args.preview:
        Path(args.preview).write_text(page)
        print("Wrote", args.preview)
        if not args.dry_run:
            return

    if not watch["delivery"].get("email"):
        print("Email is turned off in watchlist.toml; skipping.")
        return
    if not cfg.get("to"):
        sys.exit("Set [email] to in watchlist.toml before turning email on.")
    key = os.environ.get("RESEND_API_KEY")
    if not key and not args.dry_run:
        print("RESEND_API_KEY not set; skipping email.")
        return

    subject = args.subject or subject_for(reports, kind)
    if args.dry_run:
        # Never print the recipient or the key: dry runs show up in public Actions logs.
        print(f"Dry run: would send {len(reports)} report(s) as one email, subject {subject!r},"
              f" from {cfg['from']!r}; RESEND_API_KEY is {'set' if key else 'not set'}. Nothing was sent.")
        return
    payload = {
        "from": cfg["from"],
        "to": [cfg["to"]],
        "subject": subject,
        "html": page,
        "text": "\n\n---\n\n".join(md for md, _ in reports),
    }
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "pr-post"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print("Sent:", json.load(r).get("id"))
    except urllib.error.HTTPError as e:
        sys.exit(f"Resend refused the email ({e.code}): {e.read().decode()[:500]}")


if __name__ == "__main__":
    main()
