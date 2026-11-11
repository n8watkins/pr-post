# Private mode

The demo is a public site on GitHub Pages.
If you would rather keep your paper to yourself and a few friends, serve it from a Cloudflare Worker that requires sign-in on every request.
[cloudflare-setup.md](cloudflare-setup.md) is the step-by-step setup; this page explains the design.
Private mode also unlocks two things a public site never does: GitHub's private contribution counts (when you opt in) and a settings page that edits your watch list.

## Settings

In your `watchlist.toml`:

```toml
[site]
mode = "private"
show_private_counts = true   # optional: print GitHub's private contribution counts
```

`show_private_counts` is ignored on a public site, and `feed.json` never includes private counts in either mode.
A private site also tells search engines not to index it.

## Deploy

1. Build: `python3 scripts/build_site.py --paper <your paper>`.
2. Deploy with your Cloudflare account and settings passed in, so nothing personal is committed:

   ```sh
   export CLOUDFLARE_ACCOUNT_ID=<your account id>
   npx wrangler@4.151.3 deploy \
     --var GITHUB_REPO:<you>/<your repo> \
     --var WATCHLIST_PATH:<your paper>/watchlist.toml \
     --var ALLOWED_EMAILS:you@example.com,friend@example.com \
     --var "SITE_TITLE:The Pull Request Post"
   ```

   `SITE_TITLE` is only the name the browser's password prompt shows; it defaults to `pr-post`.

3. Choose how readers sign in.
   - **Cloudflare Access (recommended).**
     Follow [cloudflare-setup.md, section 3](cloudflare-setup.md#3-cloudflare-access): a bypass for the machine paths first, then Access on the Worker, then `ACCESS_TEAM_DOMAIN` and `ACCESS_AUD` as variables.
     The Worker verifies Access's signed token on every request and also checks `ALLOWED_EMAILS`.
   - **A shared password until Access is set up.**
     `npx wrangler secret put SITE_PASSWORD`.
     Readers sign in with any username and that password.

   With neither configured the Worker refuses to serve anything, so the paper is never public by accident.
4. Optional: let the settings page save.
   Create a fine-grained GitHub token with **Contents: Read and write** on your repository only, then `npx wrangler secret put GITHUB_TOKEN`.
   The settings page then commits your watch list after validating every field.

## Keeping the paper out of git: D1 and R2

A private paper holds things that should not sit in any repository: the people you follow, their profiles, written reports, your email address.
The Worker can keep all of it in Cloudflare instead, behind the same sign-in, so the code stays in one public repository and nothing private is ever committed.
This is how the owner's paper runs (`paper/` in this repository).

| What | Where | Read and written by |
| --- | --- | --- |
| The watch list (people, their status, sources and socials) and the other `watchlist.toml` tables | D1 `people` and `settings` | You on the site; jobs read it |
| Profiles | D1 `people.profile` | Jobs; the writer may only add to the end |
| Who to follow state, the one-click action log | D1 `follow_candidates`, `follow_meta`, `actions` | Jobs |
| `data/`, `stats/`, `briefs/`, `reports/`, `editions/`, `summaries/`, `follow/` mini reports, `notes/`, `logs/`, `previews/`, `backups/`, `gemini/` (the Gemini writer's parallel output), `reviews/` (blind writer comparisons) | R2, under `paper/` | Jobs; the writer reads briefs, data, reports, editions and summaries and writes reports and editions (never `gemini/` or `reviews/`) |
| The built site | R2, under `SITE_PREFIX` (`site/`) | The publish job; the Worker serves it after sign-in |

Bind a D1 database as `DB` (apply `paper/migrations/`) and an R2 bucket as `STORE`; `paper/wrangler.jsonc` is a complete example, and [cloudflare-setup.md](cloudflare-setup.md) sets it all up from zero, step by step.
With `STORE` bound the Worker serves the site from R2 and needs no static assets, so new content never needs a deploy.

Jobs and the writer reach the storage with a bearer token instead of the sign-in: `JOBS_TOKEN` for GitHub Actions and `WRITER_TOKEN` for an AI writer such as a Claude routine.
Both are Worker secrets of at least 32 characters; `worker/store.js` decides what each may read and write, and checks every upload against its SHA-256.

The engine's scripts still work on a plain folder.
`scripts/store.py` moves the paper between Cloudflare and a working folder, with `PR_POST_API` set to the Worker's address and `PR_POST_TOKEN` to a token:

```sh
python3 scripts/store.py pull work                 # watchlist.toml (active people only), profiles, files, follow state, action log
python3 scripts/collect.py --paper work            # any engine script
python3 scripts/stats.py --paper work
python3 scripts/store.py push work                 # uploads only what changed
python3 scripts/build_site.py --paper work --out dist
python3 scripts/store.py site dist                 # publishes the site, then removes pages the build no longer has
```

`store.py` prints counts only, never file names, so it is safe in the public logs of a public repository's Actions; `--verbose` lists paths for local use.
Keep every other step quiet too: Actions logs, artifacts and workflow inputs of a public repository are readable by anyone.

### Jobs on a public repository

The owner's paper runs its jobs on this public repository's Actions (`.github/workflows/paper-*.yml`), which are free, and keeps them quiet because public logs, artifacts and workflow inputs are readable by anyone:

- Engine output goes through `paper/bin/quiet` into a log file that is stored in the paper (`logs/`), readable on the site at `/files/logs/...`; the public log shows only which script ran.
- `store.py pull` masks every watched person and the email address for the rest of the job.
- No artifacts: email previews go to the paper's `previews/`.
- The Worker starts jobs with an opaque request id (`worker/jobs.js`); the job fetches the payload, such as a one-click link, with `JOBS_TOKEN`.

The Worker is also the clock: its cron triggers start collect and publish on time, which GitHub's own schedules do not guarantee, and dispatched runs keep going when the repository sees no commits for 60 days.
One-click Add from the Who to follow email (`act.py run`) drafts the person, and `store.py push` sends new `[[people]]` blocks as drafts that wait for the owner on the site; nothing joins the watch list without that.

## A paper in its own private repository

If you would rather keep your paper in git, put it in a private repository of its own and run this engine from it, pinned to a release, so there is one copy of the code and upgrades are a one-line change.

The private repository holds only the paper and its deployment settings:

```text
watchlist.toml          [site] mode = "private", the people you follow
data/ stats/            committed by your collect workflow, so a writer can read them
reports/ profiles/ editions/ summaries/   written material, if you have a writer
wrangler.jsonc          your Worker; "main" points into the engine checkout
.github/actions/engine/action.yml        checks out pr-post at one pinned ref
.github/workflows/      collect, check, deploy and email, all running the engine
```

Pin the engine in one place, a small composite action that every workflow uses:

```yaml
# .github/actions/engine/action.yml
name: pr-post engine
description: Check out pr-post at the pinned ref into engine/ and set up Python.
runs:
  using: composite
  steps:
    - uses: actions/checkout@v4
      with:
        repository: n8watkins/pr-post
        ref: <a commit SHA>   # the release you want; bump it to upgrade
        path: engine
        persist-credentials: false
    - uses: actions/setup-python@v5
      with:
        python-version: "3.12"
```

Then run the engine against the repository root, which is the paper:

```yaml
steps:
  - uses: actions/checkout@v4
  - uses: ./.github/actions/engine
  - run: python3 engine/scripts/collect.py --paper .
    env:
      GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
  - run: python3 engine/scripts/stats.py --paper .
  - run: python3 engine/scripts/build_site.py --paper . --out dist
  - run: python3 engine/scripts/feed.py --check dist/feed.json
```

In `wrangler.jsonc` set `"main": "engine/worker/index.js"` and keep `"assets": { "directory": "./dist" }`, add `/engine/` to `.gitignore` (anchored, so it does not also hide `.github/actions/engine/`), and pass the repository at deploy time with `--var GITHUB_REPO:${{ github.repository }}` so nothing depends on the repository's name.
`send_email.py` links each report to the repository that holds it, so set `[links] repo` to your private repository's address.
To test the email without sending it, run `send_email.py --dry-run --preview digest.html`: it runs every check a real send would and writes the HTML, but never contacts the Worker.

## Secrets

| Secret | Stored in | Used for |
| --- | --- | --- |
| `SITE_PASSWORD` | Worker secret | Sign-in until Access is on |
| `GITHUB_TOKEN` | Worker secret | Saving from the settings page |
| `CLOUDFLARE_API_TOKEN` | Your machine or a repository secret | Deploying |
| `ACTION_SIGNING_KEY` | Worker secret and repository secret (same value) | Optional one-click links in the Who to follow email ([who-to-follow.md](who-to-follow.md)) |
| `ACTIONS_TOKEN` | Worker secret | Starting the paper's `act.yml` workflow from those links |
| `JOBS_TOKEN` | Worker secret and repository secret (same value) | Jobs reading and writing the paper's D1 and R2 storage |
| `WRITER_TOKEN` | Worker secret and the writer's environment | An AI writer reading briefs and writing reports |
| `FOLLOW_GITHUB_TOKEN` | Repository secret | Following and unfollowing from those links (Followers: read and write only) |

Never commit keys; `.env` files are ignored.
