# Writers

pr-post works in two layers.

1. **Facts** are always there.
   `collect.py` gathers public GitHub activity and YouTube RSS feeds, `stats.py` turns them into numbers and items, and `build_site.py` prints a facts edition and `feed.json` from them.
   No AI is involved and no key is needed beyond the `GITHUB_TOKEN` that Actions provides.
2. **Writing** is optional.
   A writer reads the collected data and adds reports, profiles, written front pages and one-paragraph summaries.
   The site shows written material when it exists and falls back to facts when it does not.
   Anything an AI writes is labelled with its source on the site and in `feed.json`.

Set `[writer] kind` in `watchlist.toml` to record which writer a paper uses.
It is documentation for people reading your config: collecting and building never call an AI, and the Gemini writer below runs only when you start it (or, for the owner's paper, its schedule does).

## What a writer produces

All paths are inside the paper folder (`demo/` for the demo).

| Path | What | Shown as |
| --- | --- | --- |
| `reports/<id>/<YYYY-MM-DD>-weekly.md` or `-update.md` | A report on one person | "This week" on their page, plus a permalink and back issues |
| `profiles/<id>.md` | Long-term notes on a person and their projects | "The file on ..." on their page |
| `editions/<YYYY-MM-DD>-<weekly or update>.json` | A written front page | The front page while it is the newest; older ones under Back issues |
| `summaries/<YYYY-MM-DD>.json` (optional) | One paragraph per person | The gist on their page and `summary` in `feed.json`, labelled "AI summary" unless `source` is `"human"`; without it the gist comes from the person's edition story |

Every report with the same date and kind forms an issue, published at `/r/<date>-<kind>/` with a TL;DR, a table of contents and folding sections.
That page is what the email digest links to; the digest itself carries only the TL;DR (the edition's `tldr` list) and each report's first bullet.

## Briefs: what a writer reads

`scripts/brief.py`, run after `stats.py`, writes `briefs/<id>.md` for each person: a few kilobytes with the numbers from `stats/`, the busiest public repos, merged pull requests with the start of each description, releases, new and archived repos, videos, and READMEs the profile does not mention yet.
`data/<id>.json` can run to hundreds of kilobytes for a busy person, and an AI writer pays for every byte it reads on every later step, so reading briefs instead is the biggest single saving.
Commit `briefs/` with `data/` and `stats/`.

Each brief is cut to that person's report window and opens with it, as in `Window: 2026-11-07 to 2026-11-11 (since the 2026-11-07 weekly), for a midweek update.`
The window ends on the collection date; a weekly report (Saturday) starts at the newest weekly report or 7 days back, and a midweek update at the newest report of either kind or 4 days back, both ends included.
Every writer therefore reads the same items, and none repeats what an earlier report covered; the Gemini writer applies the same rule, and `tests/test_brief.py` checks that the two agree.
`python3 scripts/brief.py --kind weekly` cuts the briefs for the other kind of run.

The Claude routine ends each run with `scripts/writer_usage.py`, which stores `logs/writer-usage/<date>-claude.json`: start and finish time, turns, the files it read and their bytes, what it wrote, and tokens when the model was told them (otherwise `null`).
Pass that file to `python3 -m tools.compare unblind --claude-usage` for the cost table.

[INSTRUCTIONS.md](../INSTRUCTIONS.md) describes each file's format in detail.
The summaries file looks like this:

```json
{
  "source": "claude",
  "written_at": "2026-11-01T16:00:00Z",
  "people": {
    "kunchenguid": "Kun Chen spent the week hardening runner-up, his tool for running GitHub Actions jobs on your own machines, and cut five releases of it."
  }
}
```

`source` must be a short lowercase name such as `claude`, `gemini-3.5-flash-lite` or `human`.
Summaries older than the current week are ignored.

## (a) A Claude Code routine

This is how the original private paper runs.

1. Collected data must be in the repository, because a routine's sandbox usually cannot reach GitHub's API.
   Add a workflow that collects and commits the paper's `data/` and `stats/` a few hours before the routine runs:

   ```yaml
   name: Collect and commit
   on:
     schedule:
       - cron: "0 11 * * 3,6"
     workflow_dispatch:
   permissions:
     contents: write
   env:
     PR_POST_PAPER: paper
   jobs:
     collect:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v4
         - uses: actions/setup-python@v5
           with:
             python-version: "3.12"
         - env:
             GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
           run: |
             python3 scripts/collect.py
             python3 scripts/stats.py
             python3 scripts/brief.py
         - run: |
             git config user.name "pr-post collector"
             git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
             git add "$PR_POST_PAPER/data" "$PR_POST_PAPER/stats" "$PR_POST_PAPER/briefs"
             git diff --cached --quiet && exit 0
             git commit -m "data: collect activity $(date -u +%F)"
             git push
   ```

   Remove `data/` and `stats/` for that paper from `.gitignore`.
2. Create a routine in Claude Code (`/schedule`) on your repository that runs a few hours later with the prompt "Follow INSTRUCTIONS.md".
3. Have your deploy workflow build after the routine pushes (for example on `push` to `main` with `paths: ["paper/**"]`), skipping the collect step because the data is already committed.

Report commits start with `report:`.
If you want a guard against a prompt-injected writer touching other files, fail CI when a `report:` commit changes anything outside `reports/`, `profiles/`, `editions/` and `summaries/`.

### What the instructions ask of the writer

[paper/INSTRUCTIONS.md](../paper/INSTRUCTIONS.md) briefs the routine the way you would brief a capable writer; it is not a pipeline.
It is read on every run and paid for in tokens, so keep it short.
Blind reviews of the November 2026 runs showed what matters most, and the instructions now say each of these plainly:

- The brief is the only source: the writer never opens `data/` or `stats/`, and every link is copied from that person's brief.
  Step 8's check prints any link that is not.
- A quiet person (a brief that ends `Nothing dated inside the window`) gets one line, whatever the numbers or READMEs show.
- `births` and `obituaries` list every new and archived repo in every brief, not a sample.
- Each project is named with what it is the first time it appears, from the brief or the profile, and bullets stay within the cap.
- The lead is the biggest news, and the reader is "you" everywhere, in the edition too.

Before changing the instructions, run a test round: copy the paper, rebuild its briefs with `scripts/brief.py --paper <copy>`, and have a Sonnet agent follow the instructions with store.py, connectors and git turned off.

## (b) The Gemini writer

`writers/gemini/` is a second, fully independent writer: plain Python (standard library only) that calls the Gemini API's `generateContent` with a JSON response schema.
For now it runs beside the Claude routine on the same input, so the two can be compared; it never overwrites Claude's files and nothing it writes is published or emailed.
No Claude is involved anywhere in it.

```sh
python3 -m writers.gemini run --paper work/ --dry-run           # every request built, counted and estimated; nothing sent
python3 -m writers.gemini run --paper work/ --mock --no-push    # the whole pipeline with a built-in fake model, no key
pass-env run -- python3 -m writers.gemini run --paper work/     # the real run: GEMINI_API_KEY from pass-env
python3 -m writers.gemini run --paper work/ --single            # variant B: one request for the whole run
```

`--kind weekly|update` overrides the day (weekly on Saturdays), `--date` runs it for an older snapshot, `--model` picks the model (default `gemini-3.5-flash-lite`), `--rpm` paces requests for the free tier (default 10 a minute) and `--verbose` lists people and paths for local use.
Without `--verbose` it prints counts only, like `scripts/store.py`, because Actions logs on a public repository are public.

### How a run works

0. **Prepare, in code.** For each person, the window follows [paper/INSTRUCTIONS.md](../paper/INSTRUCTIONS.md) step 1 (weekly: since the newest weekly report, or 7 days; midweek: since the newest report of either kind, or 4 days), counting only reports dated before the run.
   The brief is trimmed to that window, and each kept item gets an id such as `i4`.
   A missing brief, or one collected more than 24 hours before the run, is skipped, as the instructions say.
   Trimming a brief that was already trimmed to the window changes nothing, so briefs built per window upstream work the same.
   A person with nothing in the window is quiet: they get a one-line report from a template and no request.
   Active people get their profile attached, plus one line on what each project of their profile's "Active projects" is, for the projects the window touches, so a bullet can say what a project is.
   Code also ranks each person's items by news value (new repos, releases, videos, headline speedups, release prep) and picks the front-page lead: the biggest ranked news (the top item counted twice, plus the next two) and a capped bonus for the brief's Numbers (merged PRs, releases) and launches.
   The reader leads only when their value beats everyone else's by a clear margin, since they already know what they shipped.
1. **One request per active person**, answering `{report_md, story {beat, headline, summary}, profile_lines[], quote_candidate_ids[]}` (plus `profile_md` when they have no profile yet).
   Each item in the brief ends with the exact Markdown link to use, short label included (`[PR 15162](...)`, `[CodexBar v0.84.0](...)`), and the request ends with a short checklist (links, word caps, voice, the JSON shape), so first answers pass more often.
2. **One edition request** that sees only stage 1's answers, not the briefs: `tldr`, `lead` (headline, deck, body), `weather` and `corrections` (checked against the previous Gemini edition's "probably" guesses).
   It is told who leads and which items won it; the model only writes the lead.
3. **Assemble, in code.** The lead is the one code picked (the next best when their report failed); births and obituaries come from the briefs; the quote of the week is copied by code from `data/<id>.json` by item id, so it is never model-written; quiet people get their template line and a quiet story.
   A midweek edition keeps the lead and three stories.
4. **Check, in code.** Word caps (weekly report 250, update 120, front page 400, TL;DR bullets 15, story summaries 30), the JSON shape, every number printed must appear in the request's input, and every link must be an address from the input.
   Before checking, code fixes what needs no model: a pasted title as a link label becomes the label its address calls for, a midweek update keeps its first four bullets, and Markdown links on the front page become plain text.
   On a failure the request is sent once more with the problems listed; whatever still fails is kept and recorded, so the comparison shows it.
   Em dashes are replaced with hyphens.
5. **Save** under `gemini/` in the paper, and push that folder with `scripts/store.py push` when the folder was pulled with it (`--no-push` keeps it local).
   It never runs `store.py publish` and never sends email.

| Path in the paper | What |
| --- | --- |
| `gemini/reports/<id>/<date>-<weekly or update>.md` | Each person's report, same format as Claude's |
| `gemini/editions/<date>-<kind>.json` | The edition, with `source` set to the model name, `prompt_version`, `prompt_sha` and `mode` |
| `gemini/profiles/<id>.md` | Only when the run would change a profile: the profile with its new dated lines, or a first draft |
| `gemini/usage/<date>.json` | Requests, retries, input, output and thinking tokens (from `usageMetadata`), latency, checks still failing |
| `gemini/single/...` | The same for variant B (`--single`), so both variants can run on one snapshot |

The prompts are files in `writers/gemini/prompts/v3/` (the default; `v1/` and `v2/` are kept for comparison), built from the writing rules and formats in `paper/INSTRUCTIONS.md` (not its tool or file steps).
v3 lists each person's one or two must-cover items apart from the rest of their top news, asks for full sentences that explain a change rather than reword its title, and keeps each new repo and each lead project in its own sentence.
Code checks back those rules up, so an answer that breaks one is retried once with the problem named: a bullet written as "project: change", a bullet of bare counts, a bullet that only rewords PR or commit titles, a new repo folded into another project's bullet, a must-cover item left out, a purpose or quality claim ("designed to", "cleaner") the brief never makes, and a lead that leaves a winning project unnamed or joins projects with "alongside".
Change them by adding a new version folder and passing `--prompt-version`, so every output records which prompts wrote it.

The paper's storage accepts `gemini/` and `reviews/` from the jobs token, and the owner can read them at `/files/gemini/...` after sign-in.
The Claude routine's writer token cannot read either, so its runs stay independent of Gemini's.

### Privacy on the free tier

On the Gemini API's free tier, Google may use what you send, and what the model answers, to improve its products, and people may review it.
A run sends each active person's brief (public GitHub and YouTube activity), their profile from the paper, their name, `known_for` line and the `[writer] reader` line.
The briefs are public data, but the profiles and the reader line are the paper's own private notes.
Use a paid tier (a Cloud Billing account on the API key's project), where inputs are not used that way, before sending anything you would not publish.

### Comparing writers blind

`tools/compare/` compares two writers on one run without the reviewer knowing which is which.

```sh
python3 -m tools.compare packet --paper work/ --date 2026-11-14                 # Claude ("") against gemini; --b gemini/single for variant B
# reviewer 1: a Claude agent reviews work/.compare/2026-11-14/packet.md following tools/compare/REVIEWER.md, writing review-claude.md beside it
pass-env run -- python3 -m tools.compare review --reviewer gemini --paper work/ --date 2026-11-14   # reviewer 2 -> review-gemini.md
python3 -m tools.compare score --paper work/ --date 2026-11-14                  # points per writer, both reviewers, still blind
python3 -m tools.compare unblind --paper work/ --date 2026-11-14 --push        # -> reviews/2026-11-14-claude-vs-gemini.md in the paper
```

The packet holds the trimmed briefs, then each writer's reports and edition labelled Writer A and Writer B in a random order, with every field that names a model removed.
The mapping goes to `.compare/<date>.key.json`, outside the folder the reviewer reads; `.compare/` is never pushed.
[tools/compare/REVIEWER.md](../tools/compare/REVIEWER.md) is the reviewer's prompt: missed news, untraceable facts, numbers that do not match the brief, word caps, format, tone and readability, person by person, with concrete suggestions for each writer's instructions.
After unblinding, suggestions for the Gemini writer go to its prompts and suggestions for the Claude routine to `paper/INSTRUCTIONS.md`, as proposals for the owner.

### Running Gemini on a schedule

On the owner's paper the Gemini writer runs by itself on every issue, right after the Claude routine, so the comparison builds up without anyone starting it.
The Worker's cron (`worker/jobs.js`, triggers in `paper/wrangler.jsonc`) starts `.github/workflows/paper-gemini.yml` once per issue:

| When (UTC) | What |
| --- | --- |
| Wed and Sat 16:45 | Starts it if the Claude routine's edition for the day (`editions/<date>-<kind>.json`) is in; otherwise waits |
| Wed and Sat 17:30, then 20:30 | The publish backstop, and after it the Gemini writer if it has not started for this issue yet, edition or not |

Each start is recorded as a `gemini` row in the Worker's `requests` table, keyed by the issue (`{"date", "kind"}`), so an issue gets one run, which keeps it on the free tier; a start GitHub refused is not recorded and the next trigger tries again.
Wednesday is a midweek update and Saturday the weekly, as for the Claude routine.

The job pulls the paper with `JOBS_TOKEN`, runs `python3 -m writers.gemini run --date <date> --kind <kind>` at its default `--rpm 10` (which pushes `gemini/`), and, when Claude's edition for the issue is in, builds the blind packet with `python3 -m tools.compare packet`.
The packet and its key are stored only in the paper, at `reviews/packets/<date>/packet.md` and `reviews/packets/<date>.key.json` (the same layout as the local `.compare/`), never as a workflow artifact or in the log.
The public log shows counts only (people, requests, retries, tokens, checks still failing), and the full log goes to the paper's `logs/<run id>-gemini.log`.
It needs the `GEMINI_API_KEY` repository secret ([paper/SETUP.md](../paper/SETUP.md)).

It fails soft: the publish job and the email are separate workflows, the Worker starts the publish before the Gemini writer in the same trigger and a failure in one never stops the other, and nothing the Gemini job does is published or emailed.
A red Gemini run means only that this issue has no Gemini side to compare.
If Claude's edition arrives after the Gemini run started, there is no packet for that issue; build it by hand with the commands above.

**Run workflow** on `Paper: Gemini writer` takes a date (for an older snapshot), a kind and **Dry run**, which counts the requests and estimates the tokens without sending anything.
The reviewing step (`tools.compare review` and `score`) will hook in after the packet step, where the workflow has a TODO.

#### Comparing writers: two reviewers and points

One gut score per writer is too noisy to compare runs, so every review ends with a `## Scores` section: a fenced JSON block listing, for each writer, every problem found under six metrics, plus a readability grade from 1 to 5 and the gut score out of 10.
`score` counts the lists and applies fixed weights (the `WEIGHTS` table in `tools/compare/score.py`):

| Metric | Points |
| --- | --- |
| Start | 100 |
| `missed_news` | -6 each |
| `untraceable_facts` | -8 each |
| `wrong_numbers` | -10 each |
| `cap_overruns` | -2 each |
| `format_errors` | -3 each |
| `tone_issues` | -2 each |
| `readability` | +4 per grade (1 to 5) |

Points have no floor, so a very poor run can go below zero.
Writers are ranked by their mean points over the reviewers; the mean gut score only breaks a tie.
`score` also prints every metric per reviewer with the spread between them, so you see where the two reviewers disagree.

There are two independent reviewers.
The Claude review is an agent or routine step that writes `review-claude.md`.
`review --reviewer gemini` sends only REVIEWER.md (as the system instruction) and the packet to the Gemini API and writes `review-gemini.md` in the same shape.
It is one request, and a second only when the answer is not usable JSON or its scores do not parse.
The answer is streamed (`streamGenerateContent`), so a long review is not cut off by one overall read timeout.
A 429 or 503 (the server did no work) waits and tries again, at most twice; a timeout or a dropped connection is never resent, since that request may already have been billed.
`--model` picks the model (default `gemini-3.5-flash`; `gemini-3.8-flash` is stronger on the free tier but is often overloaded); the key is `GEMINI_API_KEY` from pass-env, and the privacy note above applies to the packet too.
`score` and `unblind` read every `review-*.md` in the packet folder (or the files given with `--review`, repeatable); an older `review.md` counts as the Claude reviewer's.

`unblind` names the writers, adds a cost table from `gemini/usage/<date>.json` (`--price-in` and `--price-out` turn tokens into dollars; `--claude-usage` adds the Claude run's numbers if you have them), the points table, and every review one after the other, in one file: `reviews/<date>-<label>.md`.
The label defaults to the writers compared (`claude-vs-gemini`, `claude-vs-gemini-single`); `--label` sets it, for example `--label v2-split` for a new prompt version.
Unblinding the same packet again replaces its own file, while a file there from a different packet is never overwritten: the next free `-2`, `-3` name is used.

### Later: choosing the writer

The plan is to make `[writer] kind` choose who writes the paper that gets published and emailed: `"claude-routine"`, `"gemini"` (its output written to `reports/` and `editions/` instead of `gemini/`) or `"both"` (today's side-by-side setup).
That switch is not built yet; today `kind` is still documentation and the Gemini writer only ever writes `gemini/`, even on its schedule.

## (c) None

Leave `[writer] kind = "none"`.
The front page is a facts edition, each person's page lists their recent public items, and `feed.json` carries no summaries.
This is how the demo runs.

## Email digests

`scripts/send_email.py` turns one or more reports into a single HTML email, which the paper's Worker sends ([email.md](email.md)).
Set `[email] to`, set `email = true` under `[delivery]`, and run it with `PR_POST_API` and `PR_POST_TOKEN` (the Worker's `JOBS_TOKEN`) in the environment:

```sh
python3 scripts/send_email.py --paper paper paper/reports/<id>/<date>-weekly.md
python3 scripts/send_email.py --paper paper <report.md> --preview out.html   # render only
python3 scripts/send_email.py --paper paper <report.md> --dry-run            # every check, nothing sent
```

`--dry-run` checks that email is on, that `[email] to` is set and whether the Worker settings are present, and stops before contacting the Worker; combine it with `--preview` to keep the HTML.
It never prints the recipient, the subject or the token.
