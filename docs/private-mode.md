# Private mode

The demo is a public site on GitHub Pages.
If you would rather keep your paper to yourself and a few friends, serve it from a Cloudflare Worker that requires sign-in on every request.
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
     In the Cloudflare dashboard open the Worker, then **Settings**, **Domains & Routes**, and turn on Cloudflare Access for its address.
     Allow the email addresses you want.
     Then redeploy with `--var ACCESS_TEAM_DOMAIN:<team>.cloudflareaccess.com --var ACCESS_AUD:<application audience tag>`.
     The Worker verifies Access's signed token on every request and also checks `ALLOWED_EMAILS`.
   - **A shared password until Access is set up.**
     `npx wrangler secret put SITE_PASSWORD`.
     Readers sign in with any username and that password.

   With neither configured the Worker refuses to serve anything, so the paper is never public by accident.
4. Optional: let the settings page save.
   Create a fine-grained GitHub token with **Contents: Read and write** on your repository only, then `npx wrangler secret put GITHUB_TOKEN`.
   The settings page then commits your watch list after validating every field.

## A paper in its own private repository

A private paper usually holds things that should not sit in a public fork: the people you follow, written reports, your email address.
Keep those in a private repository of their own and run this engine from it, pinned to a release, so there is one copy of the code and upgrades are a one-line change.

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
To test the email without sending it, run `send_email.py --dry-run --preview digest.html`: it runs every check a real send would and writes the HTML, but never contacts Resend.

## Secrets

| Secret | Stored in | Used for |
| --- | --- | --- |
| `SITE_PASSWORD` | Worker secret | Sign-in until Access is on |
| `GITHUB_TOKEN` | Worker secret | Saving from the settings page |
| `CLOUDFLARE_API_TOKEN` | Your machine or a repository secret | Deploying |

Never commit keys; `.env` files are ignored.
