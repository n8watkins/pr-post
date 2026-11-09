#!/usr/bin/env python3
"""Email this week's top Who to follow picks, on their own (not inside the digest).

Usage:
    python3 scripts/send_follow.py [--paper DIR] [--preview out.html] [--dry-run] [--force]

One short email: for each of the top picks (at most five) an avatar, the name,
the fit score with Follow or Maybe, one line on why, a link to their GitHub
profile and a "Full report" link to their card on the web edition's Who to
follow page (/follow/#<login>).

With ACTION_SIGNING_KEY set and [site] url known, "Follow on GitHub" and "Add
to PR Post" are one-click: signed, single-use links that expire after seven
days (act_lib.py). Without them, Follow opens the profile and Add opens the
follow-add workflow.

Sends only when [follow] email = true and email is on under [delivery], and
only picks from a follow.py run in the last eight days. --force sends once even
though [follow] email is off, for a test the owner asked for. --dry-run runs
every check and never contacts Resend.
"""

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import act_lib  # noqa: E402
import config  # noqa: E402
import digest  # noqa: E402
import email_lib as E  # noqa: E402
import follow_lib  # noqa: E402

MAX_PICKS = 5


def day(iso):
    d = dt.date.fromisoformat(iso)
    return f"{d:%b} {d.day}, {d.year}"


def small_button(url, text, solid):
    bg, fg, cls_bg, cls_fg = (E.INK, "#ffffff", "e-btn", "e-btn-text") if solid else (E.SHEET, E.INK, "e-sheet", "e-ink e-pill")
    return (f'<td class="{cls_bg}" bgcolor="{bg}" style="background:{bg};border:1.5px solid {E.INK};border-radius:5px">'
            f'<a href="{E.href(url)}" class="{cls_fg}" style="display:inline-block;padding:8px 13px;font-family:{E.SANS};font-size:14px;font-weight:700;'
            f'line-height:16px;color:{fg};text-decoration:none;white-space:nowrap">{text}</a></td>')


def pick_row(c, report, site_url, repo_url, key, avatar_src=None):
    login = c["login"]
    avatar = avatar_src or c.get("avatar") or report.get("avatar")
    img = (f'<img src="{E.esc(avatar) if avatar_src else E.href(avatar)}" width="48" height="48" alt="" '
           f'style="display:block;width:48px;height:48px;border-radius:50%;border:1px solid {E.RULE}">'
           if avatar and (avatar_src or config.is_http_url(avatar)) else "")
    rec = c["recommendation"]
    solid = rec == "Follow"
    pill = (f'<span class="{"e-pill-solid" if solid else "e-pill"}" style="display:inline-block;font-family:{E.SANS};font-size:11px;font-weight:800;'
            f'letter-spacing:.1em;text-transform:uppercase;padding:3px 7px;border:1.5px solid {E.INK};'
            f'background:{E.INK if solid else "transparent"};color:{"#ffffff" if solid else E.INK}">{E.esc(rec)}</span>')

    one_click = bool(key and site_url)
    if one_click:
        follow_href = act_lib.url(site_url, act_lib.sign(key, "follow", login))
        add_href = act_lib.url(site_url, act_lib.sign(key, "add", login))
    else:
        follow_href, add_href = c["profile"], follow_lib.add_url(repo_url)
    buttons = small_button(follow_href, "Follow on GitHub", True)
    if add_href:
        buttons += '<td style="width:8px;font-size:0">&nbsp;</td>' + small_button(add_href, "Add to PR Post", False)
    more = [E.link(c["profile"], "GitHub profile", "e-soft", E.SOFT, 600)]
    if site_url:
        more.append(E.link(f"{site_url}/follow/#{login.lower()}", "Full report", "e-soft", E.SOFT, 600))

    return f"""<tr><td class="e-pad e-rule" style="padding:18px 32px;border-top:1px solid {E.RULE}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
  <td valign="top" style="width:60px;padding:2px 12px 0 0">{img}</td>
  <td valign="top">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
      <td valign="top" style="font-family:{E.SERIF};font-size:20px;line-height:24px;font-weight:700"><span class="e-ink" style="color:{E.INK}">{E.esc(c["name"])}</span>
        <div class="e-faint" style="font-family:{E.SANS};font-size:13px;line-height:18px;font-weight:400;color:{E.FAINT}">@{E.esc(login)}</div></td>
      <td valign="top" align="right" style="white-space:nowrap;padding-left:10px"><span class="e-ink" style="font-family:{E.SERIF};font-size:26px;line-height:26px;font-weight:700;color:{E.INK}">{int(c["score"])}</span><br>{pill}</td>
    </tr></table>
    <div class="e-ink" style="font-family:{E.SANS};font-size:15px;line-height:22px;color:{E.INK};margin:8px 0 12px">{E.esc(digest.first_sentence(c["why_follow"], 150))}</div>
    <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>{buttons}</tr></table>
    <div style="font-family:{E.SANS};font-size:13px;line-height:20px;margin:10px 0 0">{" &nbsp;&middot;&nbsp; ".join(more)}</div>
  </td>
</tr></table></td></tr>"""


def render(latest, reports, site_url, repo_url, key, brand, avatars=None):
    """Returns (html, text, subject), or None when there are no picks."""
    by_login = {c["login"]: c for c in latest["candidates"]}
    picks = [by_login[x] for x in latest.get("picks", []) if x in by_login][:MAX_PICKS]
    if not picks:
        return None
    avatars = avatars or {}
    rows = "".join(pick_row(c, reports.get(c["login"].lower(), {}), site_url, repo_url, key, avatars.get(c["login"])) for c in picks)
    one_click = bool(key and site_url)
    note = ("Follow on GitHub and Add to PR Post work from here: each opens a page that does it, with an Undo. "
            "Links work once and expire in 7 days." if one_click else
            "Nothing is followed for you: Follow on GitHub opens their profile.")
    intro = E.section(f'<div class="e-soft" style="font-family:{E.SANS};font-size:15px;line-height:22px;color:{E.SOFT}">'
                      f'Builders you do not follow yet, scored 0 to 100 for fit with your stack and interests. {E.esc(note)}</div>', top_rule=False)
    all_link = ""
    if site_url:
        all_link = E.section(E.button(f"{site_url}/follow/", "See every suggestion", 260))
    names = [c["name"] for c in picks]
    subject = f"Who to follow: {', '.join(names[:2])}" + (f" and {len(names) - 2} more" if len(names) > 2 else "")
    footer = (f'Sent after the Who to follow run. To stop it, set <span style="font-family:{E.MONO}">email = false</span> under [follow] in watchlist.toml.')
    page = E.shell(subject, f"{len(picks)} builders worth a look: " + ", ".join(names), (brand, "Who to follow"),
                   "Who to follow", f"Top {len(picks)} picks &middot; {E.esc(day(latest['date']))}", intro + rows + all_link, footer)
    text = [f"{brand}: Who to follow, {day(latest['date'])}", ""]
    for c in picks:
        text.append(f"- {c['name']} (@{c['login']}), {c['score']}/100 {c['recommendation']}: {digest.first_sentence(c['why_follow'], 150)}")
        text.append(f"  GitHub: {c['profile']}" + (f"  Full report: {site_url}/follow/#{c['login'].lower()}" if site_url else ""))
    return page, "\n".join(text) + "\n", subject


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper", help="folder holding watchlist.toml (default: $PR_POST_PAPER or the repo root)")
    ap.add_argument("--preview", metavar="FILE", help="write the HTML here instead of sending")
    ap.add_argument("--dry-run", action="store_true", help="run every check a real send would, but never send")
    ap.add_argument("--force", action="store_true", help="send even though [follow] email is off (a one-off test)")
    args = ap.parse_args()

    paper = config.paper_dir(args.paper)
    try:
        watch = config.load(paper)
    except config.ConfigError as e:
        sys.exit(f"Config error: {e}")
    if not watch["follow"]["enabled"]:
        print("Who to follow is turned off in watchlist.toml; skipping.")
        return
    latest, reports = follow_lib.load_latest(paper, watch, max_age_days=8)
    if not latest:
        print("No Who to follow run in the last eight days; nothing to send.")
        return
    key = os.environ.get("ACTION_SIGNING_KEY", "")
    out = render(latest, reports, watch["site"]["url"], watch["links"].get("repo"), key, watch["site"]["title"])
    if not out:
        print("No picks this week; nothing to send.")
        return
    page, text, subject = out

    if args.preview:
        Path(args.preview).write_text(page)
        print("Wrote", args.preview)
        if not args.dry_run:
            return
    if not watch["follow"]["email"] and not args.force:
        print("The Who to follow email is off ([follow] email = false); skipping.")
        return
    resend = os.environ.get("RESEND_API_KEY")
    if stop := E.check_delivery(watch, resend, args.dry_run):
        print(stop)
        return
    one_click = "one-click buttons" if key and watch["site"]["url"] else "plain links (no ACTION_SIGNING_KEY or [site] url)"
    if args.dry_run:
        print(f"Dry run: would send {subject!r} with {one_click}; RESEND_API_KEY is {'set' if resend else 'not set'}. Nothing was sent.")
        return
    print(f"Sending with {one_click}.")
    print("Sent:", E.send(resend, watch["email"]["from"], watch["email"]["to"], subject, page, text))


if __name__ == "__main__":
    main()
