# The owner's paper

The Pull Request Post that the owner reads is a private paper built by this repository's engine.
Its code lives here; its content does not.

| What | Where |
| --- | --- |
| The Worker that serves it behind sign-in, its D1 migrations | `wrangler.jsonc`, `migrations/` (code in `../worker/`) |
| The writer's instructions (the Claude routine) | `INSTRUCTIONS.md` |
| Amazon SES for its emails | `aws/ses.yml` ([docs/email.md](../docs/email.md)) |
| Its jobs | `../.github/workflows/paper-*.yml` |
| The watch list, profiles, settings, follow state, action log | D1 database `pr-post` |
| Collected data, briefs, reports, editions, notes, the built site, logs | R2 bucket `pr-post-paper` |

Nothing private is ever committed: [docs/private-mode.md](../docs/private-mode.md) explains the storage and the three credentials (the owner's sign-in, `JOBS_TOKEN` for Actions, `WRITER_TOKEN` for the routine).

## How a week runs

1. **Collect** (`paper-collect.yml`, Wednesdays and Saturdays at 11:00 UTC): public GitHub and YouTube activity, stats and the writer's briefs, then the site is rebuilt.
2. **Write** (the Claude routine, 16:00 UTC): pulls the briefs, writes reports and an edition, pushes them and asks for a publish.
3. **Publish** (`paper-publish.yml`): builds the site into R2 and emails each new issue as one short digest through SES. A backstop runs at 17:30 and 20:30 UTC.
4. **Who to follow** (`paper-follow.yml`, Saturdays once turned on): suggestions, mini reports, and the separate Who to follow email with one-click Follow and Add buttons (`paper-act.yml`).

The Worker's cron starts these on time ([worker/jobs.js](../worker/jobs.js)); every job can also be run by hand from the Actions tab.
Their public logs show only counts: each job stores its full log in the paper, readable on the site at `/files/logs/<run id>-<job>.log` (email previews at `/files/previews/...`).

[SETUP.md](SETUP.md) lists the secrets, variables and owner steps.
