# Email

pr-post can send two short emails: a digest of written reports (`scripts/send_email.py`) and the Who to follow picks (`scripts/send_follow.py`).
Both need a private paper on Cloudflare (docs/private-mode.md): the jobs render each email and hand it to the paper's Worker, which sends it.
No email service key is needed anywhere.

```toml
[email]
to = "you@example.com"

[delivery]
email = true
```

`--dry-run` runs every check a real send would and sends nothing.
It never prints the recipient, the subject or a token, because dry runs often run in public Actions logs.

## How a send works

1. The job renders the email and POSTs it to the Worker's `/api/email/send` with `JOBS_TOKEN` (`PR_POST_API` and `PR_POST_TOKEN`, which the paper workflows already have).
2. The Worker (`worker/email.js`) checks the email, refuses an address that is suppressed, and sends it through one of:
   - a `MAILROOM` service binding to [mailroom](https://github.com/n8watkins/mailroom), the owner's Worker that sends every app's email through Cloudflare Email Service, with caps per app and bounce handling (the owner's paper, `paper/wrangler.jsonc`);
   - a `send_email` binding named `EMAIL` and an `EMAIL_FROM` variable on a domain onboarded for Cloudflare Email Sending, for a paper on another Cloudflare account.
3. Each email carries a key made from its recipient, subject and text, so a retried job never sends it twice.

The sender's display name is the Worker's `EMAIL_FROM_NAME` variable (or `SITE_TITLE`).
With mailroom the address is `prpost@n8builds.dev`; the `[email] from` of earlier versions is ignored, as are `via`, `region` and `configuration_set`.

## Bounces and complaints

mailroom passes the paper's bounces, complaints, rejections and failures to the live Worker's `EmailEvents` entrypoint (`worker/main.js`), which stores them in D1 (`email_events`).
A hard bounce or a complaint suppresses that address: `scripts/store.py pull` writes `suppressed.json`, the senders refuse to email it, and the Worker refuses it too.
The People page lists suppressed addresses and clears one once the address is fixed.
Cloudflare keeps its own account-wide suppression list as well, and mailroom refuses any address on its own list.

## Cost

Cloudflare Email Service on Workers Paid includes 3,000 emails a month for the whole account, then $0.35 per 1,000; mail to your own verified Email Routing address is free.
A paper that sends a few emails a week is well inside that.
