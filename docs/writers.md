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
It is documentation for people reading your config: collecting and building never call an AI, and the Gemini writer below runs only when you start it.

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
   A person with nothing in the window is quiet: they get a one-line report from a template and no request.
   Active people get their profile attached.
1. **One request per active person**, answering `{report_md, story {beat, headline, summary}, profile_lines[], lead_score, quote_candidate_ids[]}` (plus `profile_md` when they have no profile yet).
2. **One edition request** that sees only stage 1's answers, not the briefs: `tldr`, `lead` (headline, deck, body), `weather` and `corrections` (checked against the previous Gemini edition's "probably" guesses).
3. **Assemble, in code.** The lead is the highest `lead_score`; births and obituaries come from the briefs; the quote of the week is copied by code from `data/<id>.json` by item id, so it is never model-written; quiet people get their template line and a quiet story.
   A midweek edition keeps the lead and three stories.
4. **Check, in code.** Word caps (weekly report 250, update 120, front page 400, TL;DR bullets 15, story summaries 30), the JSON shape, every number printed must appear in the request's input, and every link must be an address from the input.
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

The prompts are files in `writers/gemini/prompts/v1/`, built from the writing rules and formats in `paper/INSTRUCTIONS.md` (not its tool or file steps).
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
# a Claude agent reviews work/.compare/2026-11-14/packet.md following tools/compare/REVIEWER.md, writing review.md beside it
python3 -m tools.compare unblind --paper work/ --date 2026-11-14 --push        # -> reviews/2026-11-14.md in the paper
```

The packet holds the trimmed briefs, then each writer's reports and edition labelled Writer A and Writer B in a random order, with every field that names a model removed.
The mapping goes to `.compare/<date>.key.json`, outside the folder the reviewer reads; `.compare/` is never pushed.
[tools/compare/REVIEWER.md](../tools/compare/REVIEWER.md) is the reviewer's prompt: missed news, untraceable facts, numbers that do not match the brief, word caps, format, tone and readability, person by person, with concrete suggestions for each writer's instructions.
`unblind` names the writers, adds a cost table from `gemini/usage/<date>.json` (`--price-in` and `--price-out` turn tokens into dollars; `--claude-usage` adds the Claude run's numbers if you have them) and writes `reviews/<date>.md`.
After unblinding, suggestions for the Gemini writer go to its prompts and suggestions for the Claude routine to `paper/INSTRUCTIONS.md`, as proposals for the owner.

### Later: choosing the writer

The plan is to make `[writer] kind` choose who writes the paper that gets published and emailed: `"claude-routine"`, `"gemini"` (its output written to `reports/` and `editions/` instead of `gemini/`) or `"both"` (today's side-by-side setup).
That switch, and running the Gemini writer from a scheduled workflow, are not built yet; today `kind` is still documentation and the Gemini writer only ever writes `gemini/`.

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
