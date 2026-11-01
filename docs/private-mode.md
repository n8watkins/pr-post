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
     --var ALLOWED_EMAILS:you@example.com,friend@example.com
   ```

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

## Secrets

| Secret | Stored in | Used for |
| --- | --- | --- |
| `SITE_PASSWORD` | Worker secret | Sign-in until Access is on |
| `GITHUB_TOKEN` | Worker secret | Saving from the settings page |
| `CLOUDFLARE_API_TOKEN` | Your machine or a repository secret | Deploying |

Never commit keys; `.env` files are ignored.
