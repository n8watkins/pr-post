"""Shared parts of the emails: a short, robust HTML shell and the Resend call.

Email clients are the hard part. The rules this module follows:

- Layout is tables with inline styles; a <style> block only adds dark mode
  and phone tweaks, so a client that drops it (Gmail in some apps) still gets
  a readable light email.
- Gmail and Outlook ignore <details>, scripts and most anchors, so the emails
  stay short and link to the web edition for depth.
- Outlook for Windows renders with Word: it ignores max-width, so a fixed
  600px table wraps the body in an MSO conditional, and buttons are table
  cells with a VML fallback.
- Dark mode: Apple Mail, iOS Mail and Outlook for Mac follow the
  prefers-color-scheme rules below; Outlook.com uses the [data-ogsc] and
  [data-ogsb] ones; Gmail recolors on its own, which this palette survives
  because no text sits on an image or a gradient.

The look follows the web edition: ink on newsprint, a red accent, serif heads.
"""

import html
import json
import re
import urllib.error
import urllib.request

from md import safe_url

SERIF = "Georgia,'Times New Roman',serif"
SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"

# Light palette, matching the site's tokens (site/assets/style.css).
CANVAS, SHEET, INK, SOFT, FAINT, RULE, ACCENT = "#e9e8e3", "#f8f7f3", "#17171a", "#46464c", "#6b6b72", "#cfcdc5", "#c4161c"
# Dark palette, applied by the <style> block where the client supports it.
D_CANVAS, D_SHEET, D_INK, D_SOFT, D_FAINT, D_RULE, D_ACCENT = "#0f0f11", "#18181c", "#ecebe6", "#c4c3c9", "#9a99a0", "#34343a", "#ff5459"

STYLE = f"""
:root {{ color-scheme: light dark; supported-color-schemes: light dark; }}
a {{ color: inherit; }}
@media (prefers-color-scheme: dark) {{
  .e-canvas {{ background: {D_CANVAS} !important; }}
  .e-sheet {{ background: {D_SHEET} !important; border-color: {D_RULE} !important; }}
  .e-ink {{ color: {D_INK} !important; }}
  .e-soft {{ color: {D_SOFT} !important; }}
  .e-faint {{ color: {D_FAINT} !important; }}
  .e-accent {{ color: {D_ACCENT} !important; }}
  .e-rule {{ border-color: {D_RULE} !important; }}
  .e-btn {{ background: {D_INK} !important; }}
  .e-btn a, .e-btn-text {{ color: {D_SHEET} !important; }}
  .e-pill {{ border-color: {D_INK} !important; color: {D_INK} !important; }}
  .e-pill-solid {{ background: {D_INK} !important; color: {D_SHEET} !important; }}
}}
[data-ogsc] .e-ink {{ color: {D_INK} !important; }}
[data-ogsc] .e-soft {{ color: {D_SOFT} !important; }}
[data-ogsc] .e-faint {{ color: {D_FAINT} !important; }}
[data-ogsc] .e-accent {{ color: {D_ACCENT} !important; }}
[data-ogsb] .e-canvas {{ background: {D_CANVAS} !important; }}
[data-ogsb] .e-sheet {{ background: {D_SHEET} !important; }}
@media (max-width: 620px) {{
  .e-pad {{ padding-left: 18px !important; padding-right: 18px !important; }}
  .e-outer {{ padding: 0 !important; }}
  .e-sheet {{ border-left: 0 !important; border-right: 0 !important; }}
  .e-title {{ font-size: 26px !important; }}
  .e-hide-sm {{ display: none !important; }}
}}
"""


def esc(s):
    return html.escape(str(s), quote=True)


def href(url):
    return esc(safe_url(url))


def link(url, label, cls="e-accent", color=ACCENT, weight=700):
    return (f'<a href="{href(url)}" class="{cls}" style="color:{color};font-weight:{weight};'
            f'text-decoration:underline;text-underline-offset:2px">{label}</a>')


def button(url, label, width=280):
    """A bulletproof button: a table cell everywhere, a VML roundrect in Outlook for Windows."""
    u = href(url)
    return f"""<table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" style="margin:0 auto"><tr><td align="center">
<!--[if mso]><v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" xmlns:w="urn:schemas-microsoft-com:office:word" href="{u}" style="height:48px;v-text-anchor:middle;width:{width}px" arcsize="12%" stroke="f" fillcolor="{INK}"><w:anchorlock/><center style="color:#ffffff;font-family:Arial,sans-serif;font-size:16px;font-weight:bold">{label}</center></v:roundrect><![endif]-->
<!--[if !mso]><!-- --><table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr><td class="e-btn" bgcolor="{INK}" style="background:{INK};border-radius:6px">
<a href="{u}" class="e-btn-text" style="display:inline-block;padding:15px 28px;font-family:{SANS};font-size:16px;font-weight:700;line-height:18px;color:#ffffff;text-decoration:none;border-radius:6px">{label}</a>
</td></tr></table><!--<![endif]-->
</td></tr></table>"""


def label(text):
    """A small uppercase section label in the accent color."""
    return (f'<div class="e-accent" style="font-family:{SANS};font-size:12px;font-weight:800;letter-spacing:.14em;'
            f'text-transform:uppercase;color:{ACCENT};margin:0 0 10px">{esc(text)}</div>')


def section(inner, top_rule=True):
    rule = f"border-top:1px solid {RULE};" if top_rule else ""
    return (f'<tr><td class="e-pad e-rule" style="padding:22px 32px;{rule}">{inner}</td></tr>')


def shell(title, preheader, kicker, heading, subline, rows, footer):
    """The whole email. `rows` are <tr> rows for the sheet; `footer` is HTML below it."""
    pre = esc(preheader[:140]) + "&#8199;&#847;" * 40
    return f"""<!doctype html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<meta name="format-detection" content="telephone=no,date=no,address=no,email=no,url=no">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{esc(title)}</title>
<!--[if mso]><noscript><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml></noscript><style>td,a,div,span,p{{font-family:Arial,sans-serif !important}}</style><![endif]-->
<style>{STYLE}</style>
</head>
<body class="e-canvas" style="margin:0;padding:0;background:{CANVAS};-webkit-text-size-adjust:100%;text-size-adjust:100%">
<div style="display:none;max-height:0;max-width:0;overflow:hidden;opacity:0;mso-hide:all">{pre}</div>
<table role="presentation" class="e-canvas" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{CANVAS}" style="background:{CANVAS}">
<tr><td align="center" class="e-outer" style="padding:24px 12px 32px">
<!--[if mso]><table role="presentation" width="600" align="center" cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->
<table role="presentation" class="e-sheet" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{SHEET}" style="max-width:600px;background:{SHEET};border:1px solid {RULE}">
<tr><td class="e-pad" style="padding:26px 32px 18px">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
    <td class="e-ink" style="font-family:{SERIF};font-size:18px;font-weight:700;color:{INK}">{esc(kicker[0])}</td>
    <td align="right" class="e-faint" style="font-family:{SANS};font-size:11px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:{FAINT}">{esc(kicker[1])}</td>
  </tr></table>
  <div class="e-rule" style="border-top:3px double {INK};margin:12px 0 18px;font-size:0;line-height:0">&nbsp;</div>
  <h1 class="e-ink e-title" style="margin:0;font-family:{SERIF};font-size:30px;line-height:1.15;font-weight:700;color:{INK}">{heading}</h1>
  <div class="e-soft" style="font-family:{SANS};font-size:14px;line-height:1.5;color:{SOFT};margin:6px 0 0">{subline}</div>
</td></tr>
{rows}
</table>
<!--[if mso]></td></tr></table><![endif]-->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:600px"><tr>
<td align="center" class="e-pad e-faint" style="padding:16px 32px 0;font-family:{SANS};font-size:12px;line-height:1.7;color:{FAINT}">{footer}</td>
</tr></table>
</td></tr></table>
</body></html>"""


def send(key, sender, to, subject, page, text):
    """Send one email through Resend. Returns the Resend id; raises SystemExit with Resend's reason on refusal."""
    payload = {"from": sender, "to": [to], "subject": subject, "html": page, "text": text}
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "pr-post"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r).get("id")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Resend refused the email ({e.code}): {e.read().decode()[:500]}") from None


def check_delivery(watch, key, dry_run):
    """The checks every send shares. Returns None to go ahead, else the message to print and stop with exit 0."""
    if not watch["delivery"].get("email"):
        return "Email is turned off in watchlist.toml; skipping."
    if not watch["email"].get("to"):
        raise SystemExit("Set [email] to in watchlist.toml before turning email on.")
    if not key and not dry_run:
        return "RESEND_API_KEY not set; skipping email."
    return None
