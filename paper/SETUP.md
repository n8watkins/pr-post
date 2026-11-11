# Setup

What the owner's paper needs, where each piece lives, and the steps only the owner can do.
Values never go in this repository or in a chat: secrets go through `pass-env` (hidden prompt or pipe) into GitHub or the Worker.

## Secrets and variables

| Name | Kind | Where | Set by |
| --- | --- | --- | --- |
| `SITE_PASSWORD` | Secret | Worker | Already set; removed once Cloudflare Access is on |
| `JOBS_TOKEN` | Secret | Worker and Actions | Agent (`pass-env generate pr-post/JOBS_TOKEN`) |
| `WRITER_TOKEN` | Secret | Worker, and the routine's environment as `PR_POST_WRITER_TOKEN` | Agent generates; the routine's copy is pasted by the owner |
| `ACTION_SIGNING_KEY` | Secret | Worker and Actions | Agent |
| `ACTIONS_TOKEN` | Secret | Worker | Owner (step C2) |
| `FOLLOW_GITHUB_TOKEN` | Secret | Actions | Owner (step C1) |
| `CLOUDFLARE_API_TOKEN` | Secret | Actions | Owner, optional (step C3) |
| `PR_POST_API` | Secret (so run logs mask it) | Actions, and the routine's environment | The Worker's address |
| `PAPER_DEPLOY_LIVE` | Variable | Actions | `true` once the live Worker runs this code |

Email needs no secret: the jobs hand each email to the Worker with `JOBS_TOKEN`, and the Worker sends it through its `MAILROOM` service binding to [mailroom](https://github.com/n8watkins/mailroom), from `prpost@n8builds.dev` ([docs/email.md](../docs/email.md)).
Bounces and complaints come back to the live Worker's `EmailEvents` entrypoint and show on the People page.

## Owner steps

### C. GitHub tokens

1. **Follow token**: github.com/settings/personal-access-tokens/new, name `pr-post follow`, one-year expiry, resource owner you, Repository access **Public repositories**, Account permissions **Followers: Read and write**, nothing else. Then:
   `pass-env set pr-post/FOLLOW_GITHUB_TOKEN` and `pass-env pipe pr-post/FOLLOW_GITHUB_TOKEN -- gh secret set FOLLOW_GITHUB_TOKEN -R n8watkins/pr-post`.
2. **Dispatch token**: same page, name `pr-post dispatch`, one-year expiry, Repository access **Only select repositories**, `n8watkins/pr-post`, Repository permissions **Actions: Read and write**, nothing else. Then:
   `pass-env set pr-post/ACTIONS_TOKEN` and `pass-env pipe pr-post/ACTIONS_TOKEN -- npx wrangler@4.151.3 secret put ACTIONS_TOKEN -c paper/wrangler.jsonc`.
3. Optional, **Cloudflare deploy token** so merged Worker changes deploy from CI: dash.cloudflare.com/profile/api-tokens, Create Custom Token, `pr-post deploy`, permissions Account Workers Scripts Edit and Account D1 Edit, your account only. Then:
   `pass-env set pr-post/CLOUDFLARE_API_TOKEN` and `pass-env pipe pr-post/CLOUDFLARE_API_TOKEN -- gh secret set CLOUDFLARE_API_TOKEN -R n8watkins/pr-post`.

### D. The Claude routine

The routine checks out this repository and follows [INSTRUCTIONS.md](INSTRUCTIONS.md).
Its environment needs `PR_POST_API` and `PR_POST_WRITER_TOKEN` (`pass-env copy pr-post/WRITER_TOKEN`, then paste within 45 seconds), and network access to the Worker's host.
Its prompt:

> Write this run's PR Post reports.
> Follow paper/INSTRUCTIONS.md in this repository exactly: it says how to pull the paper with scripts/store.py, what to write, and how to send it back with store.py push and store.py publish.
> Never commit or push to git: this repository is public.

### F. Cloudflare Access

1. Cloudflare dashboard, Zero Trust (Free plan), Settings, Authentication: **One-time PIN** on.
2. Access, Applications, Add, **Self-hosted**: name `PR Post`, the Worker's hostname, policy **Allow** for your email address.
3. A second Self-hosted application on the same hostname with paths `act`, `api/jobs`, `api/paper`, `api/site` and `api/email`, policy **Bypass**, Everyone; the Worker checks its own tokens, signatures and signed links there.
4. Copy the `PR Post` application's **AUD tag** and your team domain into the chat; an agent sets `ACCESS_TEAM_DOMAIN`, `ACCESS_AUD` and `ALLOWED_EMAILS`, checks the login, then deletes `SITE_PASSWORD`.

## Running things by hand

Every `paper-*` workflow has **Run workflow** on the Actions tab.
`Paper: publish` and both email workflows have **Dry run**, which renders the email into the paper's `previews/` and sends nothing.

## Rollback

The Worker keeps its previous versions: `npx wrangler@4.151.3 rollback <version id> -c paper/wrangler.jsonc`.
`python3 scripts/store.py pull <folder>` with `JOBS_TOKEN` makes a full local copy of the paper at any time.
