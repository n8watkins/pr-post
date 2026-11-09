-- Email for the private paper (docs/email.md).

-- What Amazon SES reported about each send, posted by SNS to /api/ses/events.
-- A permanent bounce or a complaint suppresses the address until the owner
-- clears it (cleared_at).
CREATE TABLE email_events (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  type TEXT NOT NULL,
  address TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  message_id TEXT NOT NULL DEFAULT '',
  sns_id TEXT NOT NULL,
  cleared_at TEXT
);
CREATE UNIQUE INDEX email_events_once ON email_events (sns_id, address);
CREATE INDEX email_events_address ON email_events (address);

-- Reports already emailed, so each goes out once (emailed.json for the jobs).
CREATE TABLE emails_sent (
  item TEXT PRIMARY KEY,
  sent_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
