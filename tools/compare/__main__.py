"""Compare two writers on one snapshot, blind.

    python3 -m tools.compare packet --paper work/ --date 2026-11-14 [--kind weekly] [--a ""] [--b gemini]
    # a Claude agent reviews <paper>/.compare/<date>/packet.md as tools/compare/REVIEWER.md says, writing review.md there
    python3 -m tools.compare unblind --paper work/ --date 2026-11-14 [--push]

The packet and its key live in <paper>/.compare/, which store.py never pushes.
unblind writes <paper>/reviews/<date>.md and, with --push, stores it in the paper.
Output is counts and local paths only.
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from . import blind

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import config  # noqa: E402
import store  # noqa: E402
from writers.gemini import prepare  # noqa: E402


def default_kind(paper, date):
    for kind in ("weekly", "update"):
        if (Path(paper) / "editions" / f"{date}-{kind}.json").is_file():
            return kind
    return prepare.kind_for(dt.date.fromisoformat(date))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m tools.compare", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    pk = sub.add_parser("packet", help="build the blind packet and its key")
    pk.add_argument("--paper", help="the paper folder (default: $PR_POST_PAPER or the repo root)")
    pk.add_argument("--date", required=True, help="the run's date, YYYY-MM-DD")
    pk.add_argument("--kind", choices=("weekly", "update"), help="default: the kind of that date's edition")
    pk.add_argument("--a", default="", help='first writer\'s root in the paper (default "", the Claude routine)')
    pk.add_argument("--b", default="gemini", help="second writer's root (default gemini; gemini/single for variant B)")
    pk.add_argument("--out", help="packet folder (default <paper>/.compare/<date>)")
    pk.add_argument("--key", help="key file (default <paper>/.compare/<date>.key.json, outside the packet folder)")
    pk.add_argument("--seed", type=int, help="fix the A/B shuffle (tests only)")
    ub = sub.add_parser("unblind", help="name the writers in a finished review and save it as reviews/<date>.md")
    ub.add_argument("--paper")
    ub.add_argument("--date", required=True)
    ub.add_argument("--key", help="default <paper>/.compare/<date>.key.json")
    ub.add_argument("--review", help="the blind review (default <paper>/.compare/<date>/review.md)")
    ub.add_argument("--price-in", type=float, help="Gemini price in USD per million input tokens, for the cost table")
    ub.add_argument("--price-out", type=float, help="Gemini price in USD per million output tokens")
    ub.add_argument("--claude-usage", help='JSON file with the Claude run\'s {"requests", "input_tokens", "output_tokens", "usd"}, if known')
    ub.add_argument("--push", action="store_true", help="store reviews/ in the paper with scripts/store.py")
    args = ap.parse_args(argv)

    paper = config.paper_dir(args.paper)
    work = paper / ".compare"
    key = Path(args.key) if args.key else work / f"{args.date}.key.json"
    if args.cmd == "packet":
        import random
        kind = args.kind or default_kind(paper, args.date)
        out = Path(args.out) if args.out else work / args.date
        rng = random.Random(args.seed) if args.seed is not None else None
        blind.build_packet(paper, args.date, kind, args.a, args.b, out, key, rng)
        print(f"Wrote {out / 'packet.md'} and its key {key}. Give the reviewer only the packet and tools/compare/REVIEWER.md.")
        return 0
    review = Path(args.review) if args.review else work / args.date / "review.md"
    claude = json.loads(Path(args.claude_usage).read_text()) if args.claude_usage else None
    try:
        path, _ = blind.unblind(paper, key, review.read_text(), args.price_in, args.price_out, claude)
    except (OSError, ValueError) as e:
        sys.exit(f"compare: {e}")
    print(f"Wrote {path.relative_to(paper)}.")
    if args.push:
        if not (paper / store.SNAPSHOT).is_file():
            sys.exit("compare: this folder was not pulled with scripts/store.py, so there is nothing to push to.")
        try:
            store.push(store.Api(), paper, dirs=("reviews",))
        except store.StoreError as e:
            sys.exit(f"compare: push failed: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
