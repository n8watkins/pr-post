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
It is documentation for people reading your config; nothing in this repository calls an AI.

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

## (b) A Gemini API key in Actions (later)

The plan is an optional step in the Pages workflow that runs only when a `GEMINI_API_KEY` secret exists, writes `summaries/<date>.json` with `source` set to the model name, and leaves the facts untouched.
It is not built yet: the demo is facts-only while models are compared.
Anything that writes the summaries file in the format above works with the site and feed today.

## (c) None

Leave `[writer] kind = "none"`.
The front page is a facts edition, each person's page lists their recent public items, and `feed.json` carries no summaries.
This is how the demo runs.

## Email digests

`scripts/send_email.py` turns one or more reports into a single HTML email through [Resend](https://resend.com).
Set `[email] to` and `from`, set `email = true` under `[delivery]`, and run it with `RESEND_API_KEY` in the environment:

```sh
python3 scripts/send_email.py --paper paper paper/reports/<id>/<date>-weekly.md
python3 scripts/send_email.py --paper paper <report.md> --preview out.html   # render only
python3 scripts/send_email.py --paper paper <report.md> --dry-run            # every check, nothing sent
```

`--dry-run` checks that email is on, that `[email] to` is set and whether `RESEND_API_KEY` is present, prints the subject, and stops before contacting Resend; combine it with `--preview` to keep the HTML.
It never prints the recipient or the key.
