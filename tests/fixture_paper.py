"""A synthetic paper for offline tests: one GitHub builder with private work, one YouTube-only person.

The numbers 98765 (private contributions) and the repo name secret-sauce
(private) must never appear on a public site or in feed.json.
"""

import datetime as dt
import json
from pathlib import Path

PRIVATE_COUNT = 98765
PRIVATE_REPO = "octo/secret-sauce"

WATCHLIST = """
[site]
title = "The Test Gazette"
motto = "Testing, testing"
{site_extra}

[[people]]
github = "octo"
name = "Octo Cat"
ticker = "OCTO"
starred = true
orgs = ["octo-org"]
known_for = "ships <b>things</b>"
links = [{{ label = "Site", url = "https://octo.example" }}]

[[people]]
id = "tuber"
name = "Tube Person"
youtube_channel_id = "UCaaaaaaaaaaaaaaaaaaaaaa"
known_for = "makes videos"
"""


def day(offset, end):
    return (end - dt.timedelta(days=offset)).isoformat()


def github_block(end):
    days = [day(6 - i, end) for i in range(7)]
    pub_repo = {"description": "A public thing", "language": "Python", "stars": 5, "created": day(30, end), "pushed": day(0, end),
                "archived": False, "fork": False, "private": False, "url": "https://github.com/octo/public-thing"}
    priv_repo = {**pub_repo, "private": True, "url": "https://github.com/octo/secret-sauce", "description": "hidden"}
    new_repo = {**pub_repo, "created": day(2, end), "url": "https://github.com/octo/fresh", "description": "Brand new"}
    return {
        "user": "octo", "orgs": ["octo-org"], "since": day(8, end),
        "pull_requests": [
            {"repo": "octo/public-thing", "number": 1, "title": "Add <script>alert(1)</script> parser", "state": "merged",
             "created": day(3, end), "merged": day(2, end), "url": "https://github.com/octo/public-thing/pull/1", "body": ""},
            {"repo": "octo/public-thing", "number": 2, "title": "Evil link", "state": "merged",
             "created": day(2, end), "merged": day(1, end), "url": "javascript:alert(1)", "body": ""},
            {"repo": PRIVATE_REPO, "number": 3, "title": "Private launch plan", "state": "merged",
             "created": day(2, end), "merged": day(1, end), "url": "https://github.com/octo/secret-sauce/pull/3", "body": ""},
        ],
        "issues_opened": [],
        "commits": [{"repo": "octo/public-thing", "date": day(1, end), "message": "fix", "url": "https://github.com/octo/public-thing/commit/abc"}],
        "events_by_repo": {},
        "active_repos": {"octo/public-thing": pub_repo, PRIVATE_REPO: priv_repo},
        "new_repos": {"octo/fresh": new_repo},
        "readmes": {},
        "releases": [{"repo": "octo/public-thing", "tag": "v1.2.0", "name": "v1.2.0", "published": day(1, end), "prerelease": False,
                      "url": "https://github.com/octo/public-thing/releases/tag/v1.2.0"},
                     {"repo": PRIVATE_REPO, "tag": "v9", "name": "Secret v9", "published": day(1, end), "prerelease": False,
                      "url": "https://github.com/octo/secret-sauce/releases/tag/v9"}],
        "archived_repos": {},
        "repo_states": {"octo/public-thing": False, "octo/fresh": False},
        "totals": {"pull_requests": 3, "commits": 1, "commits_capped": False},
        "contributions": {
            "public": 12, "private": PRIVATE_COUNT, "private_visible": True,
            "commits": 8, "pull_requests": 3, "issues": 0, "reviews": 1,
            "days": {d: (3 if i % 2 else 0) + 100 for i, d in enumerate(days)},
            "by_day": [{"date": d, "public": {"commits": i % 2, "pull_requests": 1 if i == 4 else 0, "issues": 0, "reviews": 0},
                        "private": 14000 + i, "pr_repos": {}} for i, d in enumerate(days)],
        },
        "profile": {"bio": "hi", "avatar": "https://avatars.githubusercontent.com/u/1?v=4",
                    "links": [{"label": "X", "url": "https://x.com/octo"}, {"label": "Bad", "url": "javascript:alert(1)"}]},
    }


def youtube_block(end):
    return {
        "channel_id": "UCaaaaaaaaaaaaaaaaaaaaaa",
        "channel_url": "https://www.youtube.com/channel/UCaaaaaaaaaaaaaaaaaaaaaa",
        "channel_title": "Tube Channel",
        "videos": [
            {"id": "AAAAAAAAAA1", "title": "How I ship <img src=x onerror=alert(1)>", "url": "https://www.youtube.com/watch?v=AAAAAAAAAA1",
             "thumbnail": "https://i.ytimg.com/vi/AAAAAAAAAA1/mqdefault.jpg", "published": day(1, end), "published_at": day(1, end) + "T10:00:00+00:00",
             "short": False, "views": 1234},
            {"id": "BBBBBBBBBB2", "title": "Old video", "url": "https://www.youtube.com/watch?v=BBBBBBBBBB2",
             "thumbnail": "https://i.ytimg.com/vi/BBBBBBBBBB2/mqdefault.jpg", "published": day(7, end), "published_at": day(7, end) + "T10:00:00+00:00",
             "short": False, "views": 5},
        ],
        "latest": [],
    }


def write_paper(root, site_extra="", end=None):
    """Write watchlist.toml and data/ for the synthetic paper into `root`; returns root."""
    end = end or dt.date(2026, 10, 31)
    root = Path(root)
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "watchlist.toml").write_text(WATCHLIST.format(site_extra=site_extra))
    collected = f"{end.isoformat()}T12:00:00+00:00"
    (root / "data" / "octo.json").write_text(json.dumps(
        {"id": "octo", "since": day(8, end), "collected_at": collected, "github": github_block(end), "youtube": None, "errors": []}))
    (root / "data" / "tuber.json").write_text(json.dumps(
        {"id": "tuber", "since": day(8, end), "collected_at": collected, "github": None, "youtube": youtube_block(end), "errors": []}))
    return root


if __name__ == "__main__":
    import sys
    print("Wrote", write_paper(sys.argv[1] if len(sys.argv) > 1 else "fixture-paper"))
