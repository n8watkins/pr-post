"""A 19-person synthetic paper for the Gemini writer and the compare tools. Every person is invented.

    python3 tests/gemini_fixture.py <folder> [--date YYYY-MM-DD] [--kind weekly|update]

Briefs are written by scripts/brief.py from synthetic data and stats, so the
writer reads exactly the format the collector produces. The people:

- 0 to 11 are busy all window long (5 has no profile yet, 3 archived a repo, 4 started one);
- 12 and 13 were busy only 5 and 6 days ago, so they are quiet in a midweek update;
- 14 to 16 have nothing in either window;
- 17 posts YouTube videos only;
- 18 is the reader (self = true).

A weekly run (prior weekly report 7 days back) has 16 active and 3 quiet people;
a midweek update (prior weekly report 4 days back) has 14 active and 5 quiet.
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import brief  # noqa: E402

FIRST = ["Ada", "Bram", "Cleo", "Dov", "Esme", "Finn", "Gus", "Hana", "Ivo", "Juno", "Kit", "Lio", "Mae", "Nils", "Oda", "Pim", "Quin", "Rue", "Sol"]
SHARED_PROFILE = "# {name}\n\n{name} builds small developer tools in public.\n\n## Projects\n- {repo}: a tool for tidy builds.\n"


def person_id(i):
    return f"{FIRST[i].lower()}-fixture"


def watchlist():
    out = ['[site]\ntitle = "The Fixture Gazette"\nmode = "private"\n\n[writer]\nkind = "claude-routine"\nreader = "a friend of theirs"\n']
    for i, first in enumerate(FIRST):
        lines = [f'[[people]]', f'id = "{person_id(i)}"', f'name = "{first} Fixture"', f'known_for = "invented builder number {i + 1}"']
        if i == 17:
            lines.append('youtube_channel_id = "UC' + "a" * 22 + '"')
        else:
            lines.insert(1, f'github = "{first.lower()}-fixture"')
        if i == 18:
            lines.append("self = true")
        if i in (0, 1):
            lines.append("starred = true")
        out.append("\n".join(lines) + "\n")
    return "\n".join(out)


def active_days(i):
    """Days before the run date on which person i did something."""
    if i <= 11 or i == 18:
        return [0, 1, 2, 3, 5]
    if i in (12, 13):
        return [5, 6]
    if i in (14, 15, 16):
        return [12, 20]
    return [1]  # 17: one video


def person_data(i, today, collected):
    pid = person_id(i)
    login = pid
    repo = f"{login}/tool-{i + 1}"
    days = [(today - dt.timedelta(days=d)).isoformat() for d in active_days(i)]
    stamp = lambda d: f"{d}T10:00:00Z"
    if i == 17:
        videos = [{"date": days[0], "title": "Building a robot that waters plants, part 2", "url": "https://www.youtube.com/watch?v=fixture017"}]
        data = {"collected_at": collected, "since": (today - dt.timedelta(days=8)).isoformat(), "github": {},
                "youtube": {"videos": [{"published": stamp(days[0]), **videos[0]}]}}
        st = {"window": {"start": (today - dt.timedelta(days=7)).isoformat(), "end": today.isoformat()}, "public_merged_prs": 0, "opened_prs": 0,
              "commits": 0, "repos_touched": 0, "releases": [], "videos": videos, "contributions": None, "repos": [], "births": [], "deaths": []}
        return data, st
    prs = []
    for n, d in enumerate(days, start=1):
        number = 100 + i * 10 + n
        prs.append({"repo": repo, "number": number, "title": f"Add retry budget to the sync step ({n})", "state": "merged",
                    "created": stamp(d), "merged": stamp(d), "url": f"https://github.com/{repo}/pull/{number}",
                    "body": "Retries a failed sync up to 3 times before giving up."})
    commits = [{"repo": f"{login}/notes", "date": stamp(days[0]), "message": "Write down the release checklist",
                "url": f"https://github.com/{login}/notes/commit/{i:02d}abc"}]
    releases = [{"date": days[0], "repo": repo, "name": f"v1.{i}.0", "tag": f"v1.{i}.0", "url": f"https://github.com/{repo}/releases/tag/v1.{i}.0"}]
    births = [{"date": days[1], "repo": f"{login}/fresh-idea", "description": "A brand new experiment", "url": f"https://github.com/{login}/fresh-idea"}] if i == 4 else []
    deaths = [{"repo": f"{login}/old-thing", "url": f"https://github.com/{login}/old-thing"}] if i == 3 else []
    data = {"collected_at": collected, "since": (today - dt.timedelta(days=21)).isoformat(),
            "github": {"pull_requests": prs, "commits": commits, "readmes": {}}, "youtube": {}}
    st = {"window": {"start": (today - dt.timedelta(days=7)).isoformat(), "end": today.isoformat()},
          "public_merged_prs": len(prs), "opened_prs": len(prs), "commits": 7 + i, "repos_touched": 2,
          "releases": releases if i <= 11 or i == 18 else [], "videos": [], "contributions": {"public": 20 + i, "private": 0},
          "repos": [{"repo": repo, "merged_prs": len(prs), "commits": 6 + i, "private": False},
                    {"repo": f"{login}/notes", "merged_prs": 0, "commits": 1, "private": False}],
          "births": births, "deaths": deaths}
    return data, st


def write_paper(root, today, kind="weekly"):
    """Write the paper into `root` for a run on `today` (a date). Returns root."""
    root = Path(root)
    for d in ("briefs", "data", "profiles", "reports"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "watchlist.toml").write_text(watchlist())
    collected = f"{today.isoformat()}T06:00:00+00:00"
    last = today - dt.timedelta(days=7 if kind == "weekly" else 4)
    for i, first in enumerate(FIRST):
        pid = person_id(i)
        data, st = person_data(i, today, collected)
        (root / "data" / f"{pid}.json").write_text(json.dumps(data))
        (root / "reports" / pid).mkdir(exist_ok=True)
        (root / "reports" / pid / f"{last.isoformat()}-weekly.md").write_text(f"# {first} Fixture: earlier\n\nQuiet.\n")
        profile = "" if i == 5 else SHARED_PROFILE.format(name=f"{first} Fixture", repo=f"tool-{i + 1}")
        if profile:
            (root / "profiles" / f"{pid}.md").write_text(profile)
        person = {"id": pid, "name": f"{first} Fixture", "youtube_channel_id": "UC" + "a" * 22 if i == 17 else None}
        reports = sorted((f.stem for f in (root / "reports" / pid).glob("*.md")), reverse=True)
        (root / "briefs" / f"{pid}.md").write_text(brief.brief(person, data, st, profile, reports))
    return root


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--date", default="2026-11-14", help="the run date (default a Saturday, 2026-11-14)")
    ap.add_argument("--kind", choices=("weekly", "update"), default="weekly")
    args = ap.parse_args()
    write_paper(args.folder, dt.date.fromisoformat(args.date), args.kind)
    print(f"Wrote a 19-person fixture paper for a {args.kind} run on {args.date} into {args.folder}")


if __name__ == "__main__":
    main()
