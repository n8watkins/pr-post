-- Email events now come from mailroom (worker/email.js), keyed by its event id;
-- the column was named for the Amazon SNS message id it held before.
ALTER TABLE email_events RENAME COLUMN sns_id TO event_id;
