"""Load and validate a paper: its watchlist.toml and the folders next to it.

A paper is a folder holding watchlist.toml. Everything the tools write or read
for that paper lives beside it:

    <paper>/watchlist.toml   people to follow and site settings (you edit this)
    <paper>/data/            raw public activity per person (collect.py)
    <paper>/stats/           every number the site prints (stats.py)
    <paper>/editions/        optional written front pages (a writer)
    <paper>/reports/         optional written reports per person (a writer)
    <paper>/profiles/        optional long-term notes per person (a writer)
    <paper>/summaries/       optional one-paragraph summaries per person (a writer)

Choose the paper with --paper DIR or the PR_POST_PAPER environment variable;
the default is the current repository root. No dependencies.
"""

import os
import re
import tomllib
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent

ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38})$")
GITHUB_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
CHANNEL_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
TICKER_RE = re.compile(r"^[A-Z0-9]{1,6}$")

SITE_DEFAULTS = {
    "title": "The Pull Request Post",
    "motto": "All the commits fit to print",
    "url": "",            # public address, e.g. https://you.github.io/pr-post ; used for absolute links in feed.json
    "base_path": "",      # path prefix when served from a sub-folder, e.g. /pr-post (the Pages workflow sets it)
    "mode": "public",     # "public": anyone can read it (GitHub Pages); "private": served behind sign-in (Cloudflare Worker)
    "show_private_counts": False,  # private mode only: print GitHub's private contribution counts
    "desk": "the facts desk",      # byline on pages built from facts alone
}
WRITER_DEFAULTS = {
    "kind": "none",       # "none", "claude-routine" or "gemini"; only documents who writes, nothing calls an AI here
    "reader": "a friend of theirs, technical but not inside their projects",
}
EMAIL_DEFAULTS = {"to": "", "from": "PR Post <onboarding@resend.dev>"}
DELIVERY_DEFAULTS = {"email": False}


class ConfigError(ValueError):
    pass


def paper_dir(arg=None):
    """The paper folder: --paper, then PR_POST_PAPER, then the repo root."""
    p = Path(arg or os.environ.get("PR_POST_PAPER") or ROOT)
    if not p.is_absolute():
        p = Path.cwd() / p
    return p.resolve()


def is_http_url(url):
    try:
        u = urlparse((url or "").strip())
    except ValueError:
        return False
    return u.scheme in ("http", "https") and bool(u.netloc)


def _text(v, field, max_len):
    if v is None:
        return ""
    if not isinstance(v, str):
        raise ConfigError(f"{field} must be text")
    v = v.strip()
    if len(v) > max_len:
        raise ConfigError(f"{field} is longer than {max_len} characters")
    if re.search(r"[\x00-\x1f\x7f]", v):
        raise ConfigError(f"{field} contains control characters")
    return v


def _person(raw, i):
    where = f"people[{i}]"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} is not a table")
    github = _text(raw.get("github"), f"{where}.github", 39)
    if github and not GITHUB_RE.match(github):
        raise ConfigError(f'{where}: "{github}" is not a GitHub username')
    pid = _text(raw.get("id"), f"{where}.id", 39) or github.lower()
    if not pid:
        raise ConfigError(f"{where} needs a github username or an id")
    if not ID_RE.match(pid):
        raise ConfigError(f'{where}: id "{pid}" must be lowercase letters, digits and dashes')
    channel = _text(raw.get("youtube_channel_id"), f"{where}.youtube_channel_id", 40)
    if channel and not CHANNEL_RE.match(channel):
        raise ConfigError(f'{where}: "{channel}" is not a YouTube channel id (UC followed by 22 characters)')
    if not github and not channel:
        raise ConfigError(f"{where} ({pid}) needs a github username or a youtube_channel_id to follow")
    orgs = raw.get("orgs") or []
    if not isinstance(orgs, list) or not all(isinstance(o, str) and GITHUB_RE.match(o) for o in orgs):
        raise ConfigError(f"{where}.orgs must be a list of GitHub organization names")
    ticker = _text(raw.get("ticker"), f"{where}.ticker", 6).upper()
    if ticker and not TICKER_RE.match(ticker):
        raise ConfigError(f"{where}.ticker must be up to 6 letters or digits")
    links = []
    for j, link in enumerate(raw.get("links") or []):
        if not isinstance(link, dict):
            raise ConfigError(f"{where}.links[{j}] must be {{ label = ..., url = ... }}")
        url = _text(link.get("url"), f"{where}.links[{j}].url", 300)
        if not is_http_url(url):
            raise ConfigError(f"{where}.links[{j}].url must start with http:// or https://")
        links.append({"label": _text(link.get("label"), f"{where}.links[{j}].label", 40) or "Link", "url": url})
    avatar = _text(raw.get("avatar"), f"{where}.avatar", 300)
    if avatar and not is_http_url(avatar):
        raise ConfigError(f"{where}.avatar must start with http:// or https://")
    return {
        "id": pid,
        "github": github or None,
        "name": _text(raw.get("name"), f"{where}.name", 60) or github or pid,
        "ticker": ticker,
        "starred": raw.get("starred") is True,
        "self": raw.get("self") is True,
        "orgs": orgs,
        "known_for": _text(raw.get("known_for"), f"{where}.known_for", 240),
        "youtube_channel_id": channel or None,
        "avatar": avatar or None,
        "links": links,
    }


def parse(text):
    """Parse watchlist.toml text into a validated config dict."""
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"watchlist.toml is not valid TOML: {e}") from e
    site = {**SITE_DEFAULTS, **(raw.get("site") or {})}
    if site["mode"] not in ("public", "private"):
        raise ConfigError('[site] mode must be "public" or "private"')
    if site["url"] and not is_http_url(site["url"]):
        raise ConfigError("[site] url must start with http:// or https://")
    site["url"] = site["url"].rstrip("/")
    base = (site["base_path"] or "").strip().rstrip("/")
    if base and not re.match(r"^/[A-Za-z0-9._/-]*$", base):
        raise ConfigError('[site] base_path must look like "/pr-post"')
    site["base_path"] = base
    for key in ("title", "motto", "desk"):
        site[key] = _text(site[key], f"[site] {key}", 120)
    writer = {**WRITER_DEFAULTS, **(raw.get("writer") or {})}
    if writer["kind"] not in ("none", "claude-routine", "gemini"):
        raise ConfigError('[writer] kind must be "none", "claude-routine" or "gemini"')
    people = [_person(p, i) for i, p in enumerate(raw.get("people") or [])]
    seen = set()
    for p in people:
        if p["id"] in seen:
            raise ConfigError(f'"{p["id"]}" is listed twice')
        seen.add(p["id"])
    return {
        "site": site,
        "writer": writer,
        "email": {**EMAIL_DEFAULTS, **(raw.get("email") or {})},
        "delivery": {**DELIVERY_DEFAULTS, **(raw.get("delivery") or {})},
        "links": raw.get("links") or {},
        "people": people,
    }


def load(paper):
    """Load <paper>/watchlist.toml, with PR_POST_SITE_URL and PR_POST_BASE_PATH overriding [site]."""
    path = Path(paper) / "watchlist.toml"
    if not path.exists():
        raise ConfigError(f"No watchlist.toml in {paper}")
    cfg = parse(path.read_text())
    # The Pages workflow learns the real address from actions/configure-pages.
    if url := os.environ.get("PR_POST_SITE_URL"):
        if not is_http_url(url):
            raise ConfigError("PR_POST_SITE_URL must start with http:// or https://")
        cfg["site"]["url"] = url.rstrip("/")
    if (base := os.environ.get("PR_POST_BASE_PATH")) is not None and base.strip():
        cfg["site"]["base_path"] = "" if base.strip() == "/" else base.strip().rstrip("/")
    return cfg


def private_counts(site):
    """Private contribution counts are printed only on a private site that opts in."""
    return site["mode"] == "private" and site["show_private_counts"] is True
