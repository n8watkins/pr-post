# Running PR Post on Cloudflare

This is the full setup, from zero, for a paper that only you can read: a Cloudflare Worker with D1 and R2 behind Cloudflare Access.
It was written from a real setup on 2026-11-11, in the Cloudflare dashboard as it looked that day.
Older Cloudflare docs, and older notes in this repository, describe menus that have since moved.

Placeholders used below:

| Placeholder | What it is |
| --- | --- |
| `<you>/<repo>` | Your fork of this repository on GitHub |
| `<worker>` | Your Worker's name, `name` in `paper/wrangler.jsonc` |
| `<account>.workers.dev` | Your account's workers.dev subdomain, so the paper lives at `https://<worker>.<account>.workers.dev` |
| `<team>` | Your Zero Trust team name, so the team domain is `<team>.cloudflareaccess.com` |

## 1. Choose: public or private

**Public**: anyone can read it.
Nothing is private: the watch list, every report and `feed.json` are visible to anyone, and the repository holds them.

**Private**: only you, signed in.
The watch list, the collected data and the reports never become public: they live in your Cloudflare account and the Worker serves them only after sign-in.

| | Public | Private |
| --- | --- | --- |
| Who can read it | Anyone | Only the email addresses you allow |
| Where it runs | GitHub Pages, built by GitHub Actions | A Cloudflare Worker, with D1 and R2, behind Cloudflare Access |
| The watch list | `watchlist.toml` in your repository, public | In D1, edited on the site's People page |
| Written reports | Committed to the repository, public | In R2 |
| `feed.json` | Public | Behind the same sign-in |
| GitHub's private contribution counts | Never shown | Optional (`show_private_counts`) |
| Email digests and one-click Who to follow | No | Yes |
| Search engines | Can index it | Told not to (`noindex`) |
| Setup | Four steps ([section 5](#5-public-setup)) | The checklist below |

## 2. Private setup checklist

Do these in order.
Every secret goes in through a hidden prompt or a pipe; none is ever written into a file in the repository or pasted into a chat.

### Before you start

- [ ] **Fork** this repository to `<you>/<repo>` and clone it.
- [ ] **Install** Node 20 or newer and Python 3.11 or newer.
  Wrangler runs through `npx`, pinned to the version the workflows use: `npx wrangler@4.151.3`.
- [ ] **Point the paper jobs at your fork.**
  Each `.github/workflows/paper-*.yml` job starts with `if: github.repository == 'n8watkins/pr-post'`, so it never runs anywhere else by accident.
  Change that to `'<you>/<repo>'` in every `paper-*.yml` file.

### Cloudflare account, D1 and R2

- [ ] **Create a Cloudflare account** (the Free plan is enough) and sign in once with `npx wrangler@4.151.3 login`.
  Note your account id, shown by `npx wrangler@4.151.3 whoami`.
- [ ] **Create the D1 database**: `npx wrangler@4.151.3 d1 create pr-post`.
  Keep the name `pr-post`: `paper-deploy.yml` applies migrations to the database by that name.
  Copy the `database_id` it prints.
- [ ] **Create the R2 bucket**: `npx wrangler@4.151.3 r2 bucket create <bucket>`, for example `pr-post-paper`.
  If R2 is not enabled on the account yet, the dashboard asks you to enable it first (R2 in the sidebar).
- [ ] **Check that the bucket is not public**: `npx wrangler@4.151.3 r2 bucket dev-url get <bucket>` must say public access is disabled, and the bucket must have no custom domain.
  New buckets start that way.
  Everything in the bucket is meant to reach a browser only through the Worker, after sign-in.

### The Worker's config

`paper/wrangler.jsonc` is the owner's config.
In your fork, edit it in place, because the paper workflows deploy with `-c paper/wrangler.jsonc`:

- [ ] `name`: `<worker>`.
- [ ] `account_id`: your account id.
- [ ] `vars.GITHUB_REPO`: `<you>/<repo>`.
- [ ] `vars.SITE_TITLE` and `vars.EMAIL_FROM_NAME`: your paper's name.
- [ ] `vars.ACCESS_TEAM_DOMAIN` and `vars.ACCESS_AUD`: set both to `""` for now; [section 3](#3-cloudflare-access) fills them in.
- [ ] `d1_databases`: your `database_id`.
- [ ] `r2_buckets`: your `bucket_name`.
- [ ] `services`: remove the `MAILROOM` binding.
  It points at the owner's own mail Worker, and a deploy fails when the Worker it names does not exist on your account.
  To send email, use an `EMAIL` binding instead ([email.md](email.md)).
- [ ] `triggers.crons`: keep them; they are the clock that starts the jobs (collect on Wednesdays and Saturdays at 11:00 UTC, publish as a backstop at 17:30 and 20:30 UTC).

### Create the tables and settings

- [ ] **Apply the migrations**: `npx wrangler@4.151.3 d1 migrations apply pr-post -c paper/wrangler.jsonc --remote`.
- [ ] **Store the paper's settings.**
  Settings live in D1's `settings` table, one row per `watchlist.toml` table, and no token can change them: you write them with Wrangler.
  At least `[site]` is needed, with `mode = "private"` and `storage = "cloudflare"`:

  ```sh
  npx wrangler@4.151.3 d1 execute pr-post -c paper/wrangler.jsonc --remote --command \
    "INSERT INTO settings (section, value) VALUES ('site', '{\"title\": \"My Paper\", \"mode\": \"private\", \"storage\": \"cloudflare\"}')"
  ```

  Add `[writer]`, `[email]`, `[delivery]` or `[follow]` the same way when you want them, as JSON with the same keys as in `watchlist.toml` (the README's Configuration section lists them).

### Deploy

- [ ] **Deploy the Worker**: `npx wrangler@4.151.3 deploy -c paper/wrangler.jsonc`.
  It prints the address, `https://<worker>.<account>.workers.dev`.
  Until a sign-in is set up, the Worker refuses to serve the site to anyone (503, "This paper is locked"), so it is never public by accident.

### Secrets

Generate each token as a long random string, for example `openssl rand -hex 32`, and keep it in your password manager.
`wrangler secret put` and `gh secret set` both ask for the value with a hidden prompt, or read it from a pipe.
Each token must be at least 32 characters.

| Secret | Worker | GitHub Actions | What it does |
| --- | --- | --- | --- |
| `JOBS_TOKEN` | Yes | Yes, same value | The jobs read and write the paper's storage |
| `WRITER_TOKEN` | Yes | No | The Claude routine reads briefs and writes reports; it also goes in the routine's environment ([section 4](#4-the-claude-routine)) |
| `ACTION_SIGNING_KEY` | Yes | Yes, same value | Signs the one-click links in the Who to follow email |
| `ACTIONS_TOKEN` | Yes | No | The Worker starts the jobs on GitHub (next step) |
| `ALLOWED_EMAILS` | Yes | No | Comma-separated addresses the Worker accepts after Access sign-in |
| `PR_POST_API` | No | Yes | `https://<worker>.<account>.workers.dev`, a secret so run logs mask it |
| `CLOUDFLARE_API_TOKEN` | No | Yes | Lets `paper-deploy.yml` deploy the Worker and apply migrations when they change on `main` |
| `SITE_PASSWORD` | Yes, temporary | No | A shared password until Access is on; deleted at the end of section 3 |

- [ ] **Worker secrets**, one at a time: `npx wrangler@4.151.3 secret put JOBS_TOKEN -c paper/wrangler.jsonc`, then the same for `WRITER_TOKEN`, `ACTION_SIGNING_KEY`, `ALLOWED_EMAILS` and, if you want to look at the site before Access is on, `SITE_PASSWORD`.
- [ ] **Actions secrets**, one at a time: `gh secret set JOBS_TOKEN -R <you>/<repo>`, then `ACTION_SIGNING_KEY` and `PR_POST_API`.
- [ ] **Cloudflare deploy token**: dash.cloudflare.com/profile/api-tokens, **Create Token**, **Create Custom Token**, name it `pr-post deploy`, permissions **Account, Workers Scripts, Edit** and **Account, D1, Edit**, your account only.
  Then `gh secret set CLOUDFLARE_API_TOKEN -R <you>/<repo>`, and add the repository variable `PAPER_DEPLOY_LIVE` set to `true` (`gh variable set PAPER_DEPLOY_LIVE -b true -R <you>/<repo>`), which tells `paper-deploy.yml` to deploy.
- [ ] **Optional, Who to follow**: a `FOLLOW_GITHUB_TOKEN` Actions secret, a fine-grained token with **Public repositories** access and only **Followers: Read and write** under account permissions ([who-to-follow.md](who-to-follow.md)).

### GitHub dispatch token

The Worker starts every job (collect, publish, drafting a person, one-click actions) by dispatching a workflow, so it needs a GitHub token of its own.

- [ ] github.com/settings/personal-access-tokens/new: name `pr-post dispatch`, an expiry you will remember (a year at most), **Repository access: Only select repositories**, `<you>/<repo>` only, **Repository permissions: Actions: Read and write**, nothing else.
- [ ] `npx wrangler@4.151.3 secret put ACTIONS_TOKEN -c paper/wrangler.jsonc` and paste it.

Without it the site and the storage work, but nothing starts on its own: no scheduled collect or publish, and the routine's publish request answers "No ACTIONS_TOKEN on the Worker yet".

### Your first people and the first run

- [ ] **Add people.**
  Once Access is on, the People page does this from a GitHub username.
  Before that, or for many at once, pull the empty paper, add `[[people]]` blocks and push them; they arrive as drafts:

  ```sh
  export PR_POST_API=https://<worker>.<account>.workers.dev
  read -rs PR_POST_TOKEN && export PR_POST_TOKEN   # paste JOBS_TOKEN
  python3 scripts/store.py pull work
  # add [[people]] blocks to work/watchlist.toml (README, Configuration)
  python3 scripts/store.py push work
  ```

  Approve each draft on the People page after you sign in; nothing joins the watch list before that.
- [ ] **Run the first collect**: **Actions**, **Paper: collect**, **Run workflow**.
  It collects, writes the briefs and publishes the site; after that the Worker's cron starts it on schedule.

### Then

- [ ] Set up the Claude routine, if you want written reports ([section 4](#4-the-claude-routine)).
- [ ] Turn on Cloudflare Access ([section 3](#3-cloudflare-access)).

## 3. Cloudflare Access

Access puts a sign-in page in front of the Worker.
The Worker also checks Access's signed token on every request, so a request that somehow skips Access is still refused.

Build it in this order: first the bypass for the machine paths, then the protection for the Worker.
Done the other way round, the jobs and the routine hit the login page as soon as Access is on.

### Open Zero Trust

- [ ] In the Cloudflare dashboard, open **Zero Trust** from the sidebar.
- [ ] The first visit asks for a team name (Cloudflare suggests one, such as `white-poetry-96ec`) and a plan.
  Choose the **Free** plan.
  Your team domain is `<team>.cloudflareaccess.com`.
  If it asks for a payment method, stop and check that you chose Free; the Free plan covers up to 50 users and a paper needs one.

### First: the bypass for machine paths

The jobs, the routine and the one-click email links never sign in.
They use a bearer token (`JOBS_TOKEN`, `WRITER_TOKEN`) or a signed, expiring link, and the Worker checks those itself on each of these paths:

| Path | Who uses it | What the Worker checks |
| --- | --- | --- |
| `act` | One-click links in the Who to follow email | The link's signature, expiry and single use |
| `api/paper` | Jobs and the routine | `JOBS_TOKEN` or `WRITER_TOKEN`, each limited to its own files |
| `api/site` | The publish job | `JOBS_TOKEN` |
| `api/email` | The jobs' emails | `JOBS_TOKEN` |
| `api/jobs` | The routine's publish request, job payloads | `JOBS_TOKEN` or `WRITER_TOKEN` |

A request on these paths with no token gets the Worker's own 403, and one with a wrong token gets a 401, so letting them past Access opens nothing.

- [ ] **Zero Trust**, **Access controls**, **Applications**, **Add an application**.
- [ ] Choose the **Self-hosted and private** tab, then the **Public DNS** sub-tab, then **Continue**.
  Not **Workers**: a Workers destination has no path field, and it leaves an empty Workers row that fails to save with "worker_id is required for worker destinations".
- [ ] Add one public hostname per path, five in all.
  For each: **Subdomain** `<worker>`, **Domain** `<account>.workers.dev` (it is in the Domain dropdown), **Path** one of `act`, `api/paper`, `api/site`, `api/email`, `api/jobs`.
- [ ] **Policy**: create one named `Machines`, **Action: Bypass**, **Include: Everyone**.
- [ ] **Name** the application, for example `PR Post machine paths`.
  The session duration does not matter for a bypass.
- [ ] Save.

### Then: protect the Worker

- [ ] **Workers & Pages**, your Worker, the **Access** tab.
  (Older docs say **Settings**, **Domains & Routes**; that is not where it is anymore.)
  The tab lists the hostname policies it found, including the bypass you just made, and the order Cloudflare matches in: hostname policies, then Worker policies, then account policies.
- [ ] Under **Worker policies**, **Enable access**:
  - **Scope**: **All traffic**, not "Previews only".
  - **Authentication policy**: the pre-configured **Cloudflare account** policy (only members of your Cloudflare account), or a new **Allow** policy with **Include: Emails** and your address.
    Never **Email domain**: that lets in anyone at, say, gmail.com.
    Never the `Machines` bypass policy.
  - **Session duration**: how often you type the email code again.
    The default is 24 hours; a week is reasonable for a paper with one reader.
- [ ] Save.

### Tell the Worker about Access

- [ ] The same **Access** tab shows **Application values**: the **AUD tag** and the **JWKS URL** (`https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`).
- [ ] In `paper/wrangler.jsonc`, set `vars.ACCESS_TEAM_DOMAIN` to `<team>.cloudflareaccess.com` (no `https://`) and `vars.ACCESS_AUD` to the AUD tag.
  Neither is secret: the AUD tag is in every login redirect.
  But it looks like one to gitleaks, whose generic-api-key rule flags it, so end that line with a `// gitleaks:allow` comment.
  Add the comment in the same commit as the value: gitleaks scans every commit in a pull request, not just the last one.
- [ ] Make sure `ALLOWED_EMAILS` is set as a Worker secret (previous section).
  The Worker accepts a signed-in visitor only if their email is on that list.
- [ ] Deploy: merge the change so `paper-deploy.yml` deploys it, or run `npx wrangler@4.151.3 deploy -c paper/wrangler.jsonc`.

### Check it

- [ ] The site sends you to sign in:

  ```sh
  curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" https://<worker>.<account>.workers.dev/
  # 302 https://<team>.cloudflareaccess.com/cdn-cgi/access/login/...
  ```

- [ ] The machine paths reach the Worker instead of the login page:

  ```sh
  curl -s -w " %{http_code}\n" https://<worker>.<account>.workers.dev/api/paper/watchlist
  # Sign in through Cloudflare Access to read this paper. 403
  ```

  A 403 or 401 from the Worker proves the bypass works.
  A 302 to `cloudflareaccess.com` means the bypass application is missing or does not cover that path.
- [ ] Open the site in a browser and sign in.
  A fresh browser gets a one-time code by email; if you are already signed in to the Cloudflare dashboard in that browser, the Cloudflare account policy may let you straight in.
- [ ] Delete the temporary password: `npx wrangler@4.151.3 secret delete SITE_PASSWORD -c paper/wrangler.jsonc`.
  It asks you to confirm.

### On your phone

The paper installs as an app from the browser, behind the same sign-in.
Sign in once in the browser first, then:

- [ ] **iPhone or iPad:** in Safari, tap **Share**, **Add to Home Screen**, **Add**.
  The installed app keeps its own sign-in, apart from Safari's, so it asks for the email code once the first time you open it.
- [ ] **Android:** in Chrome, tap **Install** on the card at the bottom of the page, or **⋮**, **Add to Home screen** (**Install app**).
- [ ] Open the app, then turn on airplane mode and open it again: the front page and everyone's pages still open, and a page that was never saved shows "You are offline".

How it works with Access:

- The manifest is fetched with your sign-in cookie (`crossorigin="use-credentials"`), so Access lets it through without a bypass.
- The service worker answers only pages, `assets/` and the manifest.
  It never answers `/api/`, `/act/`, `/files/`, `/cdn-cgi/` or anything that is not a GET, and it never caches a redirect, an opaque answer or anything from `cloudflareaccess.com`.
- When your session runs out, a page load is redirected to Access as usual, even for a page the app has saved, so you sign in again and land back where you were.
  Only when the network is down does the app show its saved copy.
- **Session duration** decides how often the app asks for a code; for an installed app on a phone, a month is comfortable.
- Each publish has a new build id, so the app drops the old copies and keeps the new issue the next time it opens online.
- The saved pages stay on the device until then, readable offline by whoever holds the unlocked phone; signing out of Access does not remove them.
  To clear them, remove the app (or, in a browser, clear the site's data).

### Other Workers on the same storage

Any other Worker bound to the same D1 database and R2 bucket, such as a test copy, can read the whole paper.
Give it the same Access protection and its own `ACCESS_*` values, or delete it.
With no sign-in configured it serves nothing to a browser, but it still accepts the jobs' tokens if they are set on it.

## 4. The Claude routine

The routine is the writer: a scheduled Claude Code session that pulls the briefs, writes the reports and an edition, pushes them to the paper and asks for a publish.
It follows [paper/INSTRUCTIONS.md](../paper/INSTRUCTIONS.md).

- [ ] Create a routine on claude.ai (Claude Code, Routines; or `/schedule` in Claude Code) on your repository `<you>/<repo>`.
  The owner's runs Wednesdays and Saturdays at 16:00 UTC, five hours after collect.
- [ ] Its prompt:

  > Write this run's PR Post reports.
  > Follow paper/INSTRUCTIONS.md in this repository exactly: it says how to pull the paper with scripts/store.py, what to write, and how to send it back with store.py push and store.py publish.
  > Never commit or push to git: this repository is public.

- [ ] **Give it its own cloud environment.**
  Variables live in the routine's cloud environment, not in the routine dialog.
  In the routine's **Advanced settings**, **General**, **Cloud environment**, add a new environment; do not put tokens in **Default**, which other sessions share.
  - **Network access**: **Limited**, with **Allowed domains** set to the Worker's host, `<worker>.<account>.workers.dev`.
    Keep **Allow package managers** on.
  - **Environment variables**:
    `PR_POST_API=https://<worker>.<account>.workers.dev` and `PR_POST_WRITER_TOKEN=<your WRITER_TOKEN>`.
    Paste the token yourself; if you keep it in `pass` through `pass-env`, `pass-env copy <entry>` puts it on the clipboard for 45 seconds.
- [ ] Run it once by hand and read its last lines.
  "Not started: No ACTIONS_TOKEN on the Worker yet" means the reports were stored but nothing will publish them until the Worker has `ACTIONS_TOKEN`.

The routine checks out the public repository and must never push to git.
Its reports go to R2 through `store.py`, with `WRITER_TOKEN`, which can write only reports, editions and the end of each profile.

## 5. Public setup

Everything in a public paper is visible to anyone: the watch list in your repository, the site, `feed.json`, and any report a writer commits.
Do not put anything in it you would not publish.

Today the code supports a public paper on **GitHub Pages** only.
The Worker in `worker/` always requires sign-in and never serves a public site, and there is no workflow that publishes the static site to Cloudflare.
The README's quickstart is the supported path:

- [ ] Fork this repository.
- [ ] Edit `demo/watchlist.toml`, or put your paper in its own folder and set the repository variable `PR_POST_PAPER` to it.
  Keep `[site] mode = "public"`, the default.
- [ ] **Settings**, **Pages**, **Build and deployment**, **Source: GitHub Actions**.
- [ ] **Actions**, **Demo site**, **Run workflow**.
  It then runs every Wednesday and Saturday, and the paper is at `https://<you>.github.io/<repo>/`.

A public paper has no D1, R2, Worker, Access, email or People page; the watch list is edited in git.

## 6. Troubleshooting

| What you see | What it means |
| --- | --- |
| "worker_id is required for worker destinations" when saving an Access application | You chose the **Workers** sub-tab, which has no path field and left an empty Workers row. Start again on **Self-hosted and private**, **Public DNS**. |
| Jobs or the routine get HTML from `cloudflareaccess.com`, or a 302 to it | The bypass application is missing a path, or was made after the Worker policy with a different hostname. Check all five paths and the hostname. |
| "Sign in through Cloudflare Access to read this paper." (403) in a browser | Access let you in but the Worker did not accept the token: check `ACCESS_TEAM_DOMAIN` (no `https://`), `ACCESS_AUD`, and that your email is in `ALLOWED_EMAILS`. From `curl` with no token on an `/api/` path, the same 403 is the expected answer. |
| "That token is not valid." (401) | A bearer token was sent but matches neither `JOBS_TOKEN` nor `WRITER_TOKEN` on this Worker, or is shorter than 32 characters. |
| "This paper is locked: no sign-in is configured yet." (503) | Neither Access values nor `SITE_PASSWORD` are set on this Worker. Expected before section 3. |
| "Not started: No ACTIONS_TOKEN on the Worker yet." | The routine's push worked, but the Worker cannot start the publish job. Add `ACTIONS_TOKEN`. The scheduled publish also needs it. |
| "GitHub refused to start the publish job (403)" or (404) | `ACTIONS_TOKEN` lacks **Actions: Read and write** on this repository, has expired, or `GITHUB_REPO` names another repository. |
| A paper job is skipped on your fork | Its `if: github.repository == ...` still names `n8watkins/pr-post`. |
| A deploy fails on a service binding | `paper/wrangler.jsonc` still has the owner's `MAILROOM` binding. Remove it. |
| "Email is not set up on this Worker" | No `MAILROOM` or `EMAIL` binding. See [email.md](email.md). |
| gitleaks flags `ACCESS_AUD` in a pull request | Add `// gitleaks:allow` at the end of that line, in the commit that adds the value; a fix in a later commit does not clear the earlier one. |
| `wrangler secret delete --force` fails | There is no `--force` flag; the command asks for confirmation. Run it in a terminal and answer the prompt. |
| Zero Trust asks for a payment method | Stop, go back and choose the Free plan. |
| The phone app shows a blank or generic icon, or Chrome offers no Install | The manifest or icons came back as the Access login page. Open `/manifest.webmanifest` in the signed-in browser: it must be JSON. If the phone still fails, add `/manifest.webmanifest` and `/assets/icons/` to the bypass application; they hold only the paper's name and its icon. |
| The installed app keeps asking for the email code | Each installed app has its own sign-in, apart from the browser. Lengthen the **Session duration** on the Worker's Access policy. |
| The app shows an old issue | It was offline, so it showed its saved copy. Close the app and open it again once online; a new publish replaces the saved copies. |

Rollback: the Worker keeps its earlier versions, `npx wrangler@4.151.3 rollback <version id> -c paper/wrangler.jsonc`.
A full local copy of the paper is one `python3 scripts/store.py pull <folder>` away, with `JOBS_TOKEN`.
