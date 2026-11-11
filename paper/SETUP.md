# Setup

The owner's paper: its values, where each one lives, and the owner's own commands.
The steps themselves are in **[docs/cloudflare-setup.md](../docs/cloudflare-setup.md)**, the one setup guide; this page only adds what is specific to this paper.
Values never go in this repository or in a chat: secrets go through `pass-env` (hidden prompt or pipe) into GitHub or the Worker.

## Secrets and variables

| Name | Kind | Where | In pass |
| --- | --- | --- | --- |
| `JOBS_TOKEN` | Secret | Worker and Actions | `pr-post/JOBS_TOKEN` (`pass-env generate`) |
| `WRITER_TOKEN` | Secret | Worker, and the routine's environment as `PR_POST_WRITER_TOKEN` | `pr-post/WRITER_TOKEN` |
| `ACTION_SIGNING_KEY` | Secret | Worker and Actions | `pr-post/ACTION_SIGNING_KEY` |
| `ACTIONS_TOKEN` | Secret | Worker | `pr-post/ACTIONS_TOKEN` |
| `ALLOWED_EMAILS` | Secret | Worker | |
| `FOLLOW_GITHUB_TOKEN` | Secret | Actions | `pr-post/FOLLOW_GITHUB_TOKEN` |
| `CLOUDFLARE_API_TOKEN` | Secret | Actions | `pr-post/CLOUDFLARE_API_TOKEN` |
| `PR_POST_API` | Secret (so run logs mask it), never a variable | Actions, and the routine's environment | The live Worker's address |
| `PAPER_DEPLOY_LIVE` | Variable | Actions | `true` (the live Worker runs this code since 2026-11-11) |

Cloudflare Access is on (team `white-poetry-96ec`, the values in `wrangler.jsonc`), and `SITE_PASSWORD` was deleted on 2026-11-11.

Email needs no secret: the jobs hand each email to the Worker with `JOBS_TOKEN`, and the Worker sends it through its `MAILROOM` service binding to [mailroom](https://github.com/n8watkins/mailroom), from `prpost@n8builds.dev` ([docs/email.md](../docs/email.md)).
Bounces and complaints come back to the live Worker's `EmailEvents` entrypoint and show on the People page.

## The owner's commands

Create each token as the guide says, then store and send it with `pass-env`:

```sh
pass-env set pr-post/ACTIONS_TOKEN
pass-env pipe pr-post/ACTIONS_TOKEN -- npx wrangler@4.151.3 secret put ACTIONS_TOKEN -c paper/wrangler.jsonc

pass-env set pr-post/FOLLOW_GITHUB_TOKEN
pass-env pipe pr-post/FOLLOW_GITHUB_TOKEN -- gh secret set FOLLOW_GITHUB_TOKEN -R n8watkins/pr-post

pass-env set pr-post/CLOUDFLARE_API_TOKEN
pass-env pipe pr-post/CLOUDFLARE_API_TOKEN -- gh secret set CLOUDFLARE_API_TOKEN -R n8watkins/pr-post
```

The routine's writer token: `pass-env copy pr-post/WRITER_TOKEN`, then paste it into the routine's cloud environment within 45 seconds ([the guide, section 4](../docs/cloudflare-setup.md#4-the-claude-routine)).

## Running things by hand

Every `paper-*` workflow has **Run workflow** on the Actions tab.
`Paper: publish` and both email workflows have **Dry run**, which renders the email into the paper's `previews/` and sends nothing.

## Rollback

The Worker keeps its previous versions: `npx wrangler@4.151.3 rollback <version id> -c paper/wrangler.jsonc`.
`python3 scripts/store.py pull <folder>` with `JOBS_TOKEN` makes a full local copy of the paper at any time.
