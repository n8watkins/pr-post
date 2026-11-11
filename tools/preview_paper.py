"""Build a synthetic private paper with a Who to follow run, to look at the site's UI.

    python3 tools/preview_paper.py /tmp/prpost-preview/paper /tmp/prpost-preview/dist
    python3 -m http.server -d /tmp/prpost-preview/dist 8737
    # open http://localhost:8737/ and http://localhost:8737/follow/picks/

Every person here is made up; avatars are local SVG initials served from the
same folder (so the server must run on PORT, default 8737). The paper is kept
"in Cloudflare", so Add to PR Post and the People page call /api/people: a plain
file server answers 501 there and the page says nothing was added.
"""

import datetime as dt
import json
import os
import sys
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(repo / "tests"), str(repo / "scripts")]

import build_site  # noqa: E402
import config  # noqa: E402
import follow_lib as fl  # noqa: E402
from fixture_paper import write_paper  # noqa: E402
from test_follow import FOLLOW_TOML, SITE, repo as mkrepo, user  # noqa: E402

paper, out = Path(sys.argv[1]), Path(sys.argv[2])
TODAY = dt.date(2026, 11, 7)
AV = "http://localhost:" + os.environ.get("PORT", "8737") + "/fake-avatars/{}.svg"
COLORS = ["#2f5d8a", "#8a2f5d", "#2f8a5d", "#8a5d2f", "#5d2f8a", "#2f7a8a", "#7a8a2f", "#8a3b2f", "#3b2f8a", "#4a4a52", "#2f8a3b", "#8a2f2f"]

PEOPLE = [
    ("ada-example", "Ada Example", "Edge runtime tinkerer. Cloudflare Workers and tiny CLIs.", "edge-kit", 4200, "TypeScript", ["cli", "workers"]),
    ("bo-sample", "Bo Sample", "Local AI on laptops: whisper and llama.cpp front ends.", "whisper-tray", 1800, "Rust", ["whisper"]),
    ("cy-fixture", "Cy Fixture", "Builds dev tools for code review.", "review-lens", 950, "Go", ["cli"]),
    ("dee-demo", "Dee Demo", "Workers, Durable Objects and realtime sync.", "sync-room", 610, "TypeScript", ["workers"]),
    ("eli-mock", "Eli Mock", "Chrome extensions and small SDKs.", "tab-sdk", 300, "TypeScript", ["sdk"]),
    ("fay-stub", "Fay Stub", "On-device speech and llama.cpp bindings.", "llama-swift", 2600, "Swift", ["llama.cpp"]),
    ("gus-test", "Gus Test", "Infra notes, Terraform and a CLI or two.", "tf-lint", 120, "Go", ["cli"]),
    ("hal-proto", "Hal Proto", "Static site generators and feeds.", "feedsmith", 75, "Python", []),
]

paper.mkdir(parents=True, exist_ok=True)
write_paper(paper, 'mode = "private"\nstorage = "cloudflare"\nurl = "https://paper.example"', end=dt.date(2026, 11, 6))
for f in ("octo.json",):
    d = json.loads((paper / "data" / f).read_text())
    d["github"]["profile"]["avatar"] = AV.format("octo")
    (paper / "data" / f).write_text(json.dumps(d))
wl = paper / "watchlist.toml"
wl.write_text(wl.read_text().replace('The Test Gazette', 'The Pull Request Post').replace('Testing, testing', 'All the commits fit to print') + FOLLOW_TOML.replace("picks = 2", "picks = 3") + '\n[links]\nrepo = "https://github.com/example/my-paper"\n[follow.thresholds]\nfollow = 60\nmaybe = 30\n')

cfg = config.load(paper)
(paper / "follow" / "people").mkdir(parents=True, exist_ok=True)
cands = []
for i, (login, name, bio, top, stars, lang, topics) in enumerate(PEOPLE):
    u = user(login=login, name=name, bio=bio, company=None, websiteUrl=None, twitterUsername=None, location="Example City, Nowhere",
             avatarUrl=AV.format(login), followers={"totalCount": stars // 2},
             socialAccounts={"nodes": []}, organizations={"nodes": []},
             pinnedItems={"nodes": [mkrepo(top, stars, bio, lang, topics, owner=login)]},
             top={"totalCount": 2, "nodes": [mkrepo(top + "-extras", stars // 10, "Extras for " + top, lang, topics, owner=login)]},
             contributed={"nodes": []})
    weeks = [{"contributionDays": [{"contributionCount": max(0, 6 - i // 2)}] * 7} for _ in range(26)]
    u["contributionsCollection"] = {"contributionCalendar": {"weeks": weeks}}
    rep = fl.mini_report(u, TODAY, None)
    rep["avatar"] = AV.format(login)
    (paper / "follow" / "people" / f"{login}.json").write_text(json.dumps(rep))
    signals = {"followed_by": ["octo"] if i % 3 == 0 else []}
    s = fl.score(rep, signals, {"TypeScript": 3, "Cloudflare Workers": 2}, cfg["follow"], names={"octo": "Octo Cat"})
    cands.append({"login": login, "name": name, "avatar": AV.format(login), "profile": rep["profile"], "researched": rep["researched"], **s,
                  "found": [{"text": "Followed by Octo Cat", "url": "https://github.com/octo?tab=following"}] if i % 3 == 0 else []})
cands.sort(key=lambda c: -c["score"])
latest = {"date": TODAY.isoformat(), "picks": [c["login"] for c in cands if c["recommendation"] != "Skip"][:3],
          "stack": ["TypeScript", "Cloudflare Workers"], "candidates": cands}
(paper / "follow" / "latest.json").write_text(json.dumps(latest))
import stats  # noqa: E402
(paper / "reports" / "octo").mkdir(parents=True, exist_ok=True)
(paper / "reports" / "octo" / "2026-11-06-weekly.md").write_text(
    "# Octo Cat: Oct 31 to Nov 6\n\n## TL;DR\n- Shipped [a faster parser](https://github.com/octo/public-thing/pull/1) and tagged v1.2.0.\n"
    "- Started a fresh repo for experiments.\n\n## The parser\n**What it is:** a streaming parser for config files. "
    "The new version reads files in one pass and reports errors with line numbers, which makes the CLI feel instant on large inputs.\n\n"
    "**Why it matters:** every tool built on public-thing gets the speedup for free.\n")
(paper / "editions").mkdir(exist_ok=True)
(paper / "editions" / "2026-11-06-weekly.json").write_text(json.dumps({
    "date": "2026-11-06", "kind": "weekly", "number": 12, "source": "claude",
    "tldr": ["Octo Cat ships a faster parser.", "Tube Person posts a how-to video."],
    "lead": {"id": "octo", "headline": "Octo Cat ships a parser twice as fast", "deck": "A one-pass rewrite lands in v1.2.0, with line numbers on every error.",
             "body": ["Octo Cat merged a rewrite of the public-thing parser this week. It now reads a file in a single pass instead of three, "
                      "and every error points at the line that caused it.",
                      "The release, v1.2.0, followed a day later. Octo also opened a new repository, fresh, for experiments that are not ready yet.",
                      "Nothing else in the project changed shape: the API is the same, so tools built on it pick up the speed without edits."],
             "sources": [{"label": "PR #1", "url": "https://github.com/octo/public-thing/pull/1"}]},
    "stories": [{"id": "tuber", "headline": "Tube Person explains how they ship", "body": ["A new video walks through a release from branch to tag in twelve minutes."]}],
}))
snap = stats.build(paper, cfg)
(paper / "stats").mkdir(exist_ok=True)
(paper / "stats" / f"{snap['date']}.json").write_text(json.dumps(snap))
build_site.build(paper, out)
av_dir = out / "fake-avatars"
av_dir.mkdir(parents=True, exist_ok=True)
for i, login in enumerate(["octo"] + [p[0] for p in PEOPLE]):
    initials = "".join(w[0].upper() for w in login.split("-"))[:2]
    (av_dir / f"{login}.svg").write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96"><rect width="96" height="96" fill="{COLORS[i % len(COLORS)]}"/>'
        f'<text x="48" y="60" font-family="Arial,sans-serif" font-size="38" font-weight="700" fill="#fff" text-anchor="middle">{initials}</text></svg>')

print("picks:", len(latest["picks"]), [(c["login"], c["score"], c["recommendation"]) for c in cands])
