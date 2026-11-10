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
| `PR_POST_API` | Variable | Actions, and the routine's environment | The Worker's address |
| `AWS_SES_ROLE_ARN` | Variable | Actions | Agent, from the SES stack's outputs |
| `SES_TOPIC_ARN` | Variable | Worker (`wrangler.jsonc` or `--var`) | Agent, from the SES stack's outputs |
| `PAPER_DEPLOY_LIVE` | Variable | Actions | `true` once the live Worker runs this code |

The GitHub environment `email` (limited to `main`) holds the jobs that send; the SES role trusts nothing else.

## Owner steps

### A. Amazon SES

1. Sign in at console.aws.amazon.com and pick the region **US East (N. Virginia) us-east-1**.
2. IAM, Identity providers: note whether `token.actions.githubusercontent.com` is listed.
3. CloudFormation, Create stack, With new resources, **Upload a template file**: [`aws/ses.yml`](aws/ses.yml).
   Stack name `pr-post-ses`.
   Parameters: `OwnerEmail` your address; `EventsEndpoint` the Worker's address followed by `/api/ses/events`; `CreateGitHubOidcProvider` `false` if step 2 found the provider, otherwise `true`; keep the other defaults.
   Tick the IAM acknowledgement and submit; wait for `CREATE_COMPLETE`.
4. Copy the stack's Outputs into the chat (none are secret); an agent adds the DNS records and sets the variables.
5. Click the verification link SES emails you.
6. Optional: SES, Account dashboard, **Request production access** (Transactional; the use case text is in the plan).
   The sandbox already delivers to your own verified address.

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
3. A second Self-hosted application on the same hostname with paths `act`, `api/jobs`, `api/paper`, `api/site` and `api/ses`, policy **Bypass**, Everyone; the Worker checks its own tokens, signatures and signed links there.
4. Copy the `PR Post` application's **AUD tag** and your team domain into the chat; an agent sets `ACCESS_TEAM_DOMAIN`, `ACCESS_AUD` and `ALLOWED_EMAILS`, checks the login, then deletes `SITE_PASSWORD`.

## Running things by hand

Every `paper-*` workflow has **Run workflow** on the Actions tab.
`Paper: publish` and both email workflows have **Dry run**, which renders the email into the paper's `previews/` and sends nothing.

## Rollback

The Worker keeps its previous versions: `npx wrangler@4.151.3 rollback <version id> -c paper/wrangler.jsonc`.
`python3 scripts/store.py pull <folder>` with `JOBS_TOKEN` makes a full local copy of the paper at any time.
