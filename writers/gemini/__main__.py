"""The Gemini writer: a parallel, independent writer for a paper, saved under gemini/.

    python3 -m writers.gemini run --paper work/ [--kind weekly|update] [--single] [--dry-run] [--mock]

It reads the same snapshot the Claude routine reads (briefs/, profiles/,
reports/ for the window, data/ for exact quotes), writes the same kinds of
files under gemini/ (gemini/single/ for --single) and never touches Claude's
reports/, editions/ or profiles. When the folder was pulled with
scripts/store.py, it pushes gemini/ back (never publishes, never emails).

Output is counts only, never people or paths, because Actions logs on a public
repository are public; --verbose lists them for local use.
"""

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

from . import api, fake, pipeline, prepare

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import config  # noqa: E402
import store  # noqa: E402


def out_root(paper, single):
    return Path(paper) / "gemini" / ("single" if single else "")


def previous_edition(root, today):
    found = sorted(f for f in (root / "editions").glob("*.json") if f.name[:10] < today)
    if not found:
        return None
    try:
        return json.loads(found[-1].read_text())
    except (OSError, ValueError):
        return None


def usage_summary(prep, mode, model, prompts, calls, failed, wall):
    total = lambda k: sum(c[k] for c in calls)
    counts = {s: sum(1 for p in prep["people"] if p["status"] == s) for s in ("active", "quiet", "skipped")}
    return {
        "date": prep["date"], "kind": prep["kind"], "mode": mode, "model": model,
        "prompt_version": prompts["version"], "prompt_sha": prompts["sha"],
        "people": counts,
        "skipped": [{"id": p["id"], "why": p["why"]} for p in prep["people"] if p["status"] == "skipped"],
        "requests": sum(c["attempts"] for c in calls),
        "retries": sum(c["attempts"] - 1 for c in calls),
        "transport_retries": total("transport_retries"),
        "input_tokens": total("input_tokens"), "output_tokens": total("output_tokens"), "thinking_tokens": total("thinking_tokens"),
        "latency_s": round(sum(c["latency_s"] for c in calls), 3), "wall_s": round(wall, 3),
        "checks_failing": [c["id"] or c["stage"] for c in calls if c["problems_final"]],
        "failed": failed,
        "calls": calls,
    }


def write_files(root, files):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def run(args, say=print):
    paper = config.paper_dir(args.paper)
    today = dt.date.fromisoformat(args.date) if args.date else None
    # A run for a past date (comparing writers on an older snapshot) judges the briefs as of the end of that day.
    now = dt.datetime.combine(today + dt.timedelta(days=1), dt.time(), dt.timezone.utc) if today else None
    try:
        prep = prepare.prepare(paper, args.kind, today, now)
    except config.ConfigError as e:
        sys.exit(f"Config error: {e}")
    prompts = pipeline.load_prompts(args.prompt_version)
    mode = "single" if args.single else "split"
    root = out_root(paper, args.single)
    previous = previous_edition(root, prep["date"])
    counts = {s: sum(1 for p in prep["people"] if p["status"] == s) for s in ("active", "quiet", "skipped")}
    say(f"{prep['kind'].capitalize()} run for {prep['date']} ({mode}): {counts['active']} active, {counts['quiet']} quiet, "
        f"{counts['skipped']} skipped (no or stale brief).")
    if args.verbose:
        for p in prep["people"]:
            say(f"  {p['id']}: {p['status']}" + (f" ({p['why']})" if p.get("why") else f", {p.get('start')} to {p.get('end')}"))

    if args.dry_run:
        planned = pipeline.plan(prompts, prep, mode, previous)
        est_in = sum(r["est_input_tokens"] for r in planned)
        est_out = sum(r["est_output_tokens"] for r in planned)
        stages = {s: sum(1 for r in planned if r["stage"] == s) for s in ("person", "edition", "single")}
        say(f"Dry run: {len(planned)} request{'' if len(planned) == 1 else 's'} planned ({stages['person']} person, {stages['edition']} edition, {stages['single']} single), "
            f"about {est_in} input and {est_out} output tokens; up to {len(planned)} more if every answer needs its retry. Nothing sent.")
        if args.verbose:
            for r in planned:
                say(f"  {r['stage']} {r['id'] or ''}: ~{r['est_input_tokens']} in, ~{r['est_output_tokens']} out")
        return 0

    if args.mock:
        client, model = fake.FakeClient(), "mock"
    else:
        try:
            client = api.Client(args.model, temperature=args.temperature, thinking_level=args.thinking_level)
        except api.GeminiError as e:
            sys.exit(f"gemini: {e}")
        model = args.model
    runner = pipeline.Runner(client, prompts, min_interval=0 if args.mock else 60.0 / args.rpm if args.rpm else 0)
    started = time.monotonic()
    runner_fn = pipeline.run_single if args.single else pipeline.run_split
    answers, front, lead_id, failed = runner_fn(runner, prep, previous)
    files, edition = pipeline.assemble(prep, answers, front, lead_id, model, prompts, mode)
    usage = usage_summary(prep, mode, model, prompts, runner.calls, failed, time.monotonic() - started)
    files[f"usage/{prep['date']}.json"] = json.dumps(usage, indent=1, ensure_ascii=False) + "\n"
    write_files(root, files)

    say(f"Requests {usage['requests']} ({usage['retries']} retries, {usage['transport_retries']} transport retries), "
        f"{usage['input_tokens']} input and {usage['output_tokens']} output tokens ({usage['thinking_tokens']} thinking), "
        f"{usage['latency_s']}s in the model.")
    say(f"Wrote {sum(1 for k in files if k.startswith('reports/'))} reports, {sum(1 for k in files if k.startswith('profiles/'))} profiles, "
        f"{1 if edition else 0} edition under gemini/{'single/' if args.single else ''}.")
    if usage["checks_failing"]:
        say(f"Warning: {len(usage['checks_failing'])} answers still fail a check after their retry (see the usage file).")
    if failed:
        say(f"Warning: {len(failed)} answers could not be used (see the usage file).")
    if args.verbose:
        for c in runner.calls:
            if c["problems_final"] or c["error"]:
                say(f"  {c['stage']} {c['id'] or ''}: {c['error'] or '; '.join(c['problems_final'])}")
        for rel in files:
            say(f"  wrote {root.relative_to(paper) / rel}")

    if args.no_push:
        say("Not pushed (--no-push).")
    elif not (paper / store.SNAPSHOT).is_file():
        say("Not pushed: this folder was not pulled with scripts/store.py.")
    else:
        try:
            store.push(store.Api(verbose=args.verbose), paper, dirs=("gemini",), verbose=args.verbose, say=say)
        except store.StoreError as e:
            say(f"Push failed: {e}")
            return 1
    return 1 if "edition" in failed or "single" in failed else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m writers.gemini", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="write this run's reports and edition under gemini/")
    r.add_argument("--paper", help="the paper folder (default: $PR_POST_PAPER or the repo root)")
    r.add_argument("--kind", choices=("weekly", "update"), help="default: weekly on Saturdays, else update")
    r.add_argument("--date", help="the run's date, YYYY-MM-DD (default: today, UTC)")
    r.add_argument("--single", action="store_true", help="variant B: one request for everything, saved under gemini/single/")
    r.add_argument("--dry-run", action="store_true", help="build every request, print the count and estimated tokens, send nothing")
    r.add_argument("--mock", action="store_true", help="answer with the built-in fake model instead of Gemini (no key, no network)")
    r.add_argument("--no-push", action="store_true", help="write gemini/ locally only")
    r.add_argument("--model", default=api.DEFAULT_MODEL, help=f"Gemini model (default {api.DEFAULT_MODEL})")
    r.add_argument("--prompt-version", default=pipeline.PROMPT_VERSION, help="folder under writers/gemini/prompts/")
    r.add_argument("--rpm", type=float, default=10, help="at most this many requests a minute (default 10, under the free tier's limit; 0 for no pacing)")
    r.add_argument("--temperature", type=float, default=None, help="sampling temperature (default: the model's)")
    r.add_argument("--thinking-level", default=None, help="Gemini thinkingLevel, for models that think (default: the model's)")
    r.add_argument("--verbose", action="store_true", help="list people, requests and paths (not for public logs)")
    args = ap.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
