#!/usr/bin/env python3
"""Email one issue of reports as a short digest, sent by the paper's Worker.

Usage:
    python3 scripts/send_email.py [--paper DIR] <paper>/reports/<id>/<date>-<kind>.md [more.md ...]
    python3 scripts/send_email.py --paper DIR --unsent [--dry-run]     # every new issue not in emailed.json
    python3 scripts/send_email.py [--paper DIR] <report.md> ... --preview out.html   # render only
    python3 scripts/send_email.py [--paper DIR] <report.md> ... --dry-run            # every check, no send

The email is deliberately short: a TL;DR of three to five bullets, one line
per person with a link, and a "Read the full edition" button. The full
reports live on the web edition's issue page, /r/<date>-<kind>/ (build_site.py),
where sections fold and a table of contents jumps between people. Without
[site] url the links point at the reports on GitHub instead.

The TL;DR comes from the issue's edition (editions/<date>-<kind>.json): its
"tldr" list, else its headlines, else the busiest people's first bullets.
"Who to follow" has its own email (send_follow.py).

Hands the email to the paper's Worker (PR_POST_API and PR_POST_TOKEN, docs/email.md),
which sends it through mailroom. The recipient comes from [email] to, and
sending must be turned on with email = true
under [delivery]. Exits 0 with a message when email is turned off or no
credentials are configured, so a run without email still succeeds. A recipient
that bounced or complained (suppressed.json, written by store.py) stops the
send. --dry-run runs every check a real send would, but never sends.
"""

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import digest  # noqa: E402
import email_lib as E  # noqa: E402

KINDS = {"weekly": "Weekly report", "update": "Midweek update"}


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


def bullet_rows(bullets):
    return "".join(
        f'<tr><td valign="top" class="e-accent" style="width:18px;padding:0 0 9px;font-family:{E.SANS};font-size:16px;line-height:24px;color:{E.ACCENT};font-weight:700">&#8226;</td>'
        f'<td class="e-ink" style="padding:0 0 9px;font-family:{E.SANS};font-size:16px;line-height:24px;color:{E.INK}">{E.esc(b)}</td></tr>'
        for b in bullets
    )


def person_rows(items):
    """One line per person who shipped something, then one line naming the quiet ones."""
    rows, quiet = [], []
    for person, md, url in items:
        if digest.is_quiet(md):
            quiet.append((person, url))
            continue
        read = f' {E.link(url, "Read&nbsp;&rarr;")}' if url else ""
        rows.append(
            f'<tr><td class="e-rule" style="padding:10px 0;border-top:1px solid {E.RULE};font-family:{E.SANS};font-size:15px;line-height:22px">'
            f'<strong class="e-ink" style="color:{E.INK}">{E.esc(person["name"])}</strong>'
            f'<span class="e-soft" style="color:{E.SOFT}"> &middot; {E.esc(digest.one_liner(md, 120))}</span>{read}</td></tr>'
        )
    if quiet:
        names = ", ".join(E.link(u, E.esc(p["name"]), "e-soft", E.SOFT, 400) if u else E.esc(p["name"]) for p, u in quiet)
        rows.append(
            f'<tr><td class="e-rule e-faint" style="padding:10px 0;border-top:1px solid {E.RULE};font-family:{E.SANS};font-size:14px;line-height:21px;color:{E.FAINT}">'
            f'Quiet this time: {names}.</td></tr>'
        )
    return "".join(rows)


def render(items, kind, stem, edition, edition_url, links, brand="PR Post"):
    """items: [(person dict, report markdown, link or None)] in display order. Returns (html, text, subject)."""
    titles = [digest.title_of(md, p["name"]) for p, md, _ in items]
    span = digest.span_of(titles[0]) or stem[:10]
    label = KINDS.get(kind, KINDS["weekly"])
    count = f"{len(items)} {'person' if len(items) == 1 else 'people'}"
    bullets = digest.tldr(edition, [(p, md) for p, md, _ in items])

    rows = E.section(E.label("TL;DR") + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{bullet_rows(bullets)}</table>', top_rule=False)
    rows += E.section(E.label("Who shipped what")
                      + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{person_rows(items)}</table>')
    if edition_url:
        rows += E.section(
            E.button(edition_url, "Read the full edition")
            + f'<div class="e-faint" style="font-family:{E.SANS};font-size:13px;line-height:1.5;color:{E.FAINT};text-align:center;margin:12px 0 0">'
              f'Full reports, numbers and every link, on {E.esc(brand)}.</div>')

    footer_links = " &nbsp;&middot;&nbsp; ".join(E.link(u, E.esc(t), "e-faint", E.FAINT, 400) for t, u in links if u)
    footer = (f"{footer_links}<br>" if footer_links else "") + \
        f'To stop these emails, set <span style="font-family:{E.MONO}">email = false</span> under [delivery] in watchlist.toml.'
    heading = E.esc(span)
    page = E.shell(f"{label}: {span}", " / ".join(bullets)[:140], (brand, label), heading, f"{label} &middot; {count}", rows, footer)

    text = [f"{brand}: {label}, {span} ({count})", "", "TL;DR"] + [f"- {b}" for b in bullets] + ["", "Who shipped what"]
    for p, md, url in items:
        line = "quiet this time" if digest.is_quiet(md) else digest.one_liner(md, 120)
        text.append(f"- {p['name']}: {line}" + (f" {url}" if url else ""))
    if edition_url:
        text += ["", f"Read the full edition: {edition_url}"]
    subject = f"{label}: {titles[0]}" if len(items) == 1 else f"{label}: {count}, {span}"
    return page, "\n".join(text) + "\n", subject


REPORT_RE = re.compile(r"^reports/[a-z0-9-]+/(\d{4}-\d{2}-\d{2})-(weekly|update)\.md$")
UNSENT_MAX_AGE_DAYS = 14


def unsent_issues(paper, today=None):
    """Issues (stem -> report paths) with reports not yet in <paper>/emailed.json, oldest first.

    Reports older than two weeks are left alone, so a new emailed.json never
    floods the inbox with old issues.
    """
    paper = Path(paper)
    try:
        sent = set(json.loads((paper / "emailed.json").read_text()))
    except (OSError, ValueError):
        sys.exit("No emailed.json in the paper: --unsent needs the list of reports already emailed (store.py pull writes it).")
    today = today or dt.date.today()
    issues = {}
    for f in sorted((paper / "reports").glob("*/*.md")):
        rel = f.relative_to(paper).as_posix()
        m = REPORT_RE.match(rel)
        if not m or rel in sent or (today - dt.date.fromisoformat(m.group(1))).days > UNSENT_MAX_AGE_DAYS:
            continue
        issues.setdefault(f.stem, []).append(f)
    return dict(sorted(issues.items()))


def send_issue(paper, watch, reports, subject=None, preview=None, dry_run=False):
    """Render and send one issue. Returns True when an email went out."""
    by_id = {p["id"]: p for p in watch["people"]}
    order = [p["id"] for p in watch["people"]]
    paths = sorted(
        (Path(r) for r in reports),
        key=lambda p: order.index(p.parent.name.lower()) if p.parent.name.lower() in order else len(order),
    )
    stems = {p.stem for p in paths}
    if len(stems) > 1:
        sys.exit(f"One email covers one issue (one date and kind); got {', '.join(sorted(stems))}.")
    stem = paths[0].stem
    kind = digest.kind_of(stem)

    site = watch["site"]["url"]
    repo = watch["links"].get("repo")
    edition_url = f"{site}/r/{stem}/" if site else None
    items = []
    for p in paths:
        md = p.read_text()
        pid = p.parent.name.lower()
        person = by_id.get(pid) or {"id": pid, "name": digest.title_of(md, pid).partition(":")[0].strip()}
        rel = repo_path(p)
        url = f"{edition_url}#{pid}" if edition_url else (f"{repo}/blob/main/{rel}" if repo and rel else None)
        items.append((person, md, url))

    links = [("Read the paper", site or None), ("Reports on GitHub", repo)] + [
        (label.replace("_", " ").title(), url) for label, url in watch["links"].items() if label != "repo" and isinstance(url, str)]
    page, text, auto_subject = render(items, kind, stem, digest.load_edition(paper, stem), edition_url, links, watch["site"]["title"])
    subject = subject or auto_subject

    if preview:
        Path(preview).write_text(page)
        print("Wrote", preview)
        if not dry_run:
            return False
    if stop := E.check_delivery(watch, dry_run, paper):
        print(stop)
        return False
    if dry_run:
        # Never print the recipient, the subject or any key: dry runs show up in Actions logs.
        print(f"Dry run: would send {len(items)} report(s) as one email through {E.describe(watch)}. Nothing was sent.")
        return False
    print("Sent:", E.deliver(watch, subject, page, text))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper", help="folder holding watchlist.toml (default: $PR_POST_PAPER or the repo root)")
    ap.add_argument("reports", nargs="*", help="the reports of one issue (same date and kind); one email for all")
    ap.add_argument("--unsent", action="store_true",
                    help="send every recent issue whose reports are not in <paper>/emailed.json, one email each, and record them there")
    ap.add_argument("--subject", help="default: built from the report titles")
    ap.add_argument("--preview", metavar="FILE", help="write the HTML here instead of sending (with --unsent, the newest issue)")
    ap.add_argument("--dry-run", action="store_true", help="run every check a real send would, but never send")
    args = ap.parse_args()
    if bool(args.reports) == args.unsent:
        ap.error("give the reports of one issue, or --unsent")

    paper = config.paper_dir(args.paper)
    try:
        watch = config.load(paper)
    except config.ConfigError as e:
        sys.exit(f"Config error: {e}")
    if not args.unsent:
        send_issue(paper, watch, args.reports, args.subject, args.preview, args.dry_run)
        return

    issues = unsent_issues(paper)
    if not issues:
        print("No new reports to email.")
        return
    print(f"{len(issues)} issue(s) with new reports.")
    emailed = paper / "emailed.json"
    for i, (stem, paths) in enumerate(issues.items()):
        last = i == len(issues) - 1
        if send_issue(paper, watch, paths, None, args.preview if last else None, args.dry_run):
            # Recorded after each send, so a failure later never resends this one.
            done = json.loads(emailed.read_text()) + [p.relative_to(paper).as_posix() for p in paths]
            emailed.write_text(json.dumps(sorted(set(done)), indent=1) + "\n")


if __name__ == "__main__":
    main()
