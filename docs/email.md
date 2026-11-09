# Email

pr-post can send two short emails: a digest of written reports (`scripts/send_email.py`) and the Who to follow picks (`scripts/send_follow.py`).
Both go through one of two services, chosen with `[email] via`:

```toml
[email]
to = "you@example.com"
from = "PR Post <paper@post.example.com>"
via = "ses"                    # "ses" (Amazon SES) or "resend"
region = "us-east-1"           # SES only
configuration_set = "pr-post"  # SES only: reports bounces and complaints

[delivery]
email = true
```

`--dry-run` runs every check a real send would and sends nothing.
It never prints the recipient, the subject or a key, because dry runs often run in public Actions logs.

## Amazon SES

`scripts/ses.py` calls the SES v2 `SendEmail` API with Signature Version 4, written with the standard library, so the engine still has no dependencies.
It reads the usual AWS environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`).

The recommended setup keeps no long-lived AWS keys anywhere:

1. Deploy `paper/aws/ses.yml` with CloudFormation in your sending region.
   It creates the sender domain identity (Easy DKIM, custom MAIL FROM), your own address as a verified identity, a configuration set, an SNS topic for bounce and complaint events, and the role `pr-post-ses-sender`.
   The role trusts only GitHub Actions jobs of one repository running in one GitHub environment (`email`), and may only send as one From address.
2. Add the DNS records from the stack's Outputs: three DKIM CNAMEs, the MAIL FROM MX and TXT records, and a DMARC record for the sender domain.
   Use a subdomain of its own (such as `post.example.com`) so the records never touch your main domain's mail.
3. In GitHub, create the environment `email` limited to the `main` branch, and set the repository variable `AWS_SES_ROLE_ARN` to the role's ARN.
   Jobs that send declare `environment: email` and `permissions: id-token: write`, then run `aws-actions/configure-aws-credentials` with `role-to-assume: ${{ vars.AWS_SES_ROLE_ARN }}` for one-hour credentials.
4. New SES accounts start in the sandbox, which only delivers to verified addresses.
   That covers sending to yourself; request production access in the SES console to send to anyone else.

### Bounces and complaints

The configuration set sends Bounce, Complaint, Reject, Delivery and DeliveryDelay events to the SNS topic, which posts them to the Worker's `/api/ses/events`.
The Worker accepts a message only with a valid SNS signature (SignatureVersion 2), a signing certificate from an `sns.<region>.amazonaws.com` address, and the topic in its `SES_TOPIC_ARN` variable, and it confirms the subscription only through an SNS address (`worker/sns.js`).
Events are stored in D1 (`email_events`).
A permanent bounce or a complaint suppresses that address: `scripts/store.py pull` writes `suppressed.json`, and the senders refuse to email a suppressed address.
SES's own account-level suppression list stays on as well.

After setting `SES_TOPIC_ARN` on a Worker that already runs this code, confirm the subscription once: SNS console, the topic, Subscriptions, select the pending one, **Request confirmation**.

### Cost

SES costs about $0.10 per 1,000 emails, and SNS HTTPS notifications are free for the first 100,000 a month, so a paper that sends a few emails a week costs well under a cent a month.

## Resend

With `via = "resend"` (the default) the emails go through Resend with `RESEND_API_KEY`.
