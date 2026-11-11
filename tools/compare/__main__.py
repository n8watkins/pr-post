"""Compare two writers on one snapshot, blind.

    python3 -m tools.compare packet --paper work/ --date 2026-11-14 [--kind weekly] [--a ""] [--b gemini]
    # a Claude agent reviews <paper>/.compare/<date>/packet.md as tools/compare/REVIEWER.md says, writing review-claude.md there
    pass-env run -- python3 -m tools.compare review --reviewer gemini --paper work/ --date 2026-11-14   # -> review-gemini.md
    python3 -m tools.compare score --paper work/ --date 2026-11-14
    python3 -m tools.compare unblind --paper work/ --date 2026-11-14 [--label v2] [--push]

The packet and its key live in <paper>/.compare/, which store.py never pushes.
unblind writes <paper>/reviews/<date>-<label>.md and, with --push, stores it in the paper.
Output is counts and local paths only.
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from . import blind, gemini_review, score

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import config  # noqa: E402
import store  # noqa: E402
from writers.gemini import api, prepare  # noqa: E402


def default_kind(paper, date):
    for kind in ("weekly", "update"):
        if (Path(paper) / "editions" / f"{date}-{kind}.json").is_file():
            return kind
    return prepare.kind_for(dt.date.fromisoformat(date))


def reviewer_name(path):
    """review-gemini.md -> gemini; the older review.md is the Claude reviewer's."""
    stem = Path(path).stem
    return stem[len("review-"):] if stem.startswith("review-") and len(stem) > len("review-") else "claude"


def find_reviews(folder, given=None):
    """{reviewer: path}: the --review files given, else review-*.md in the packet folder (or review.md)."""
    paths = [Path(p) for p in given] if given else sorted(Path(folder).glob("review-*.md")) or [Path(folder) / "review.md"]
    out = {}
    for p in paths:
        name = reviewer_name(p)
        if name in out:
            raise ValueError(f"two reviews from the {name} reviewer: {out[name]} and {p}")
        out[name] = p
    return out


def read_reviews(found):
    try:
        return {name: p.read_text() for name, p in found.items()}
    except OSError as e:
        raise ValueError(f"cannot read the review: {e}") from None


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
    rv = sub.add_parser("review", help="have Gemini review the packet (one request, two if its answer is unusable)")
    rv.add_argument("--reviewer", choices=("gemini",), default="gemini", help="the reviewer to call (the Claude review is an agent step)")
    rv.add_argument("--paper")
    rv.add_argument("--date", help="the run's date; required unless --packet is given")
    rv.add_argument("--packet", help="the packet (default <paper>/.compare/<date>/packet.md)")
    rv.add_argument("--out", help="the review file (default review-gemini.md beside the packet)")
    rv.add_argument("--model", default=gemini_review.DEFAULT_MODEL, help=f"Gemini model (default {gemini_review.DEFAULT_MODEL})")
    sc = sub.add_parser("score", help="points per writer from the reviews' scores blocks, combining every reviewer")
    sc.add_argument("--paper")
    sc.add_argument("--date", help="the run's date; required unless --review is given")
    sc.add_argument("--review", action="append", help="a review file (repeatable; default every review-*.md in the packet folder)")
    sc.add_argument("--key", help="name the writers from this key file (leave it out to stay blind)")
    ub = sub.add_parser("unblind", help="name the writers in the finished reviews and save them as reviews/<date>-<label>.md")
    ub.add_argument("--paper")
    ub.add_argument("--date", required=True)
    ub.add_argument("--key", help="default <paper>/.compare/<date>.key.json")
    ub.add_argument("--review", action="append", help="a blind review (repeatable; default every review-*.md in <paper>/.compare/<date>/)")
    ub.add_argument("--label", help="file name part after the date (default from the writers compared, such as claude-vs-gemini)")
    ub.add_argument("--price-in", type=float, help="Gemini price in USD per million input tokens, for the cost table")
    ub.add_argument("--price-out", type=float, help="Gemini price in USD per million output tokens")
    ub.add_argument("--claude-usage", help='JSON file with the Claude run\'s {"requests", "input_tokens", "output_tokens", "usd"}, if known')
    ub.add_argument("--push", action="store_true", help="store reviews/ in the paper with scripts/store.py")
    args = ap.parse_args(argv)

    paper = config.paper_dir(args.paper)
    work = paper / ".compare"
    if args.cmd == "packet":
        import random
        key = Path(args.key) if args.key else work / f"{args.date}.key.json"
        kind = args.kind or default_kind(paper, args.date)
        out = Path(args.out) if args.out else work / args.date
        rng = random.Random(args.seed) if args.seed is not None else None
        blind.build_packet(paper, args.date, kind, args.a, args.b, out, key, rng)
        print(f"Wrote {out / 'packet.md'} and its key {key}. Give the reviewer only the packet and tools/compare/REVIEWER.md.")
        return 0

    if args.cmd == "review":
        if not args.packet and not args.date:
            sys.exit("compare: give --date or --packet.")
        packet = Path(args.packet) if args.packet else work / args.date / "packet.md"
        out = Path(args.out) if args.out else packet.parent / f"review-{args.reviewer}.md"
        try:
            text = packet.read_text()
            client = api.Client(args.model, timeout=600)
            md, stats = gemini_review.review(client, text)
        except OSError as e:
            sys.exit(f"compare: cannot read the packet: {e}")
        except (api.GeminiError, ValueError) as e:
            sys.exit(f"compare: {e}")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md)
        print(f"Wrote {out}. Requests {stats['requests']}, {stats['input_tokens']} input and {stats['output_tokens']} output tokens "
              f"({stats['thinking_tokens']} thinking), {stats['latency_s']}s in the model.")
        return 0

    if args.cmd == "score":
        if not args.review and not args.date:
            sys.exit("compare: give --date or --review.")
        try:
            found = find_reviews(work / (args.date or ""), args.review)
            parsed = {name: score.parse_review(text) for name, text in read_reviews(found).items()}
            names = None
            if args.key:
                k = json.loads(Path(args.key).read_text())
                names = {lb: k[lb]["writer"] for lb in score.LABELS}
        except (OSError, ValueError) as e:
            sys.exit(f"compare: {e}")
        print(f"Points: start at {score.BASE}, weights in tools/compare/score.py.\n")
        print(score.render(parsed, names))
        return 0

    key = Path(args.key) if args.key else work / f"{args.date}.key.json"
    claude = json.loads(Path(args.claude_usage).read_text()) if args.claude_usage else None
    try:
        reviews = read_reviews(find_reviews(work / args.date, args.review))
        path, _ = blind.unblind(paper, key, reviews, args.price_in, args.price_out, claude, args.label)
    except (OSError, ValueError) as e:
        sys.exit(f"compare: {e}")
    print(f"Wrote {path.relative_to(paper) if path.is_relative_to(paper) else path}.")
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
