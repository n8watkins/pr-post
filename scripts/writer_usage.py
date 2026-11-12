#!/usr/bin/env python3
"""Write the Claude routine's usage record: <paper>/logs/writer-usage/<date>-claude.json.

Usage (paper/INSTRUCTIONS.md, last step):
    python3 scripts/writer_usage.py paper --started 2026-11-11T12:00:05Z --turns 31 \\
        --read briefs/ada.md briefs/bram.md profiles/ada.md [--model NAME] [--input-tokens N --output-tokens N]

The routine cannot see a bill, so this records what it can: when it ran, how
many turns it took, which files it read and their size (bytes read are what
input tokens grow with), and what it wrote. Files written are found by
comparing reports/, editions/ and profiles/ with the snapshot store.py pull
left in the folder, so the routine does not have to list them. Token counts
stay null unless the model was told them. The keys "requests",
"input_tokens", "output_tokens" and "usd" match what
`python3 -m tools.compare unblind --claude-usage` reads.

No AI and no network. No dependencies.
"""

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

WRITTEN_DIRS = ("reports", "editions", "profiles")
SNAPSHOT = ".store.json"  # scripts/store.py


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def written(paper):
    """[{"path", "bytes"}] of files under WRITTEN_DIRS that are new or changed since the pull."""
    try:
        snap = json.loads((paper / SNAPSHOT).read_text())
    except (OSError, ValueError):
        snap = {}
    before = dict(snap.get("files") or {})
    before.update({f"profiles/{pid}.md": digest for pid, digest in (snap.get("profiles") or {}).items()})
    out = []
    for d in WRITTEN_DIRS:
        for f in sorted((paper / d).rglob("*")) if (paper / d).is_dir() else []:
            rel = f.relative_to(paper).as_posix()
            if f.is_file() and not any(part.startswith(".") for part in rel.split("/")):
                data = f.read_bytes()
                if before.get(rel) != sha256(data):
                    out.append({"path": rel, "bytes": len(data)})
    return out


def record(paper, started, turns, read, model=None, input_tokens=None, output_tokens=None, now=None):
    paper = Path(paper)
    now = now or dt.datetime.now(dt.timezone.utc)
    start = dt.datetime.fromisoformat(started.replace("Z", "+00:00"))
    if start.tzinfo is None:
        start = start.replace(tzinfo=dt.timezone.utc)
    files_read = []
    for rel in dict.fromkeys(read):  # each file once, in the order given
        f = paper / rel
        files_read.append({"path": rel, "bytes": f.stat().st_size if f.is_file() else None})
    out = written(paper)
    editions = [w["path"] for w in out if w["path"].startswith("editions/")]
    kind = next((k for k in ("weekly", "update") if any(p.endswith(f"-{k}.json") for p in editions)), None)
    bytes_read = sum(f["bytes"] or 0 for f in files_read)
    return {
        "date": now.date().isoformat(), "kind": kind, "writer": "claude", "model": model,
        "started_at": start.isoformat(timespec="seconds"), "finished_at": now.isoformat(timespec="seconds"),
        "wall_s": round((now - start).total_seconds()),
        "requests": turns, "turns": turns,
        "files_read": files_read, "bytes_read": bytes_read,
        "reports_written": sum(1 for w in out if w["path"].startswith("reports/") and w["path"].endswith(".md") and w["path"].count("/") == 2),
        "files_written": out, "bytes_written": sum(w["bytes"] for w in out),
        "input_tokens": input_tokens, "output_tokens": output_tokens, "usd": None,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paper", help="the folder store.py pulled the paper into")
    ap.add_argument("--started", required=True, help="when the run started, as `date -u +%%Y-%%m-%%dT%%H:%%M:%%SZ` printed it")
    ap.add_argument("--turns", required=True, type=int, help="your turns (model responses) in this run, counted as well as you can")
    ap.add_argument("--read", nargs="*", default=[], help="every paper file you read, relative to the paper folder")
    ap.add_argument("--model", help="the model you are, if you know it")
    ap.add_argument("--input-tokens", type=int, help="only if you were told it")
    ap.add_argument("--output-tokens", type=int, help="only if you were told it")
    args = ap.parse_args(argv)
    paper = Path(args.paper)
    try:
        rec = record(paper, args.started, args.turns, args.read, args.model, args.input_tokens, args.output_tokens)
    except ValueError as e:
        sys.exit(f"writer_usage.py: {e}")
    path = paper / "logs" / "writer-usage" / f"{rec['date']}-claude.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, indent=1) + "\n")
    print(f"Wrote {path.relative_to(paper).as_posix()}: {rec['turns']} turns, {len(rec['files_read'])} files read ({rec['bytes_read']} bytes), "
          f"{rec['reports_written']} reports written.")


if __name__ == "__main__":
    main()
