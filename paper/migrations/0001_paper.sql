-- The private paper's structured data (D1 database "pr-post").
-- Files (collected data, reports, the built site) live in R2; see worker/store.js.

-- The watch list: one row per person. entry holds the [[people]] fields as
-- JSON (github, id, name, ticker, starred, self, orgs, known_for,
-- youtube_channel_id, avatar, links). Only active people reach the jobs'
-- watchlist.toml; drafts wait on the People page, paused people are kept but
-- not collected.
CREATE TABLE people (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'draft')),
  position INTEGER NOT NULL DEFAULT 0,
  entry TEXT NOT NULL,
  profile TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Every other watchlist.toml table ([site], [writer], [delivery], [email],
-- [links], [follow]) as JSON, one row per table.
CREATE TABLE settings (
  section TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Who to follow state (follow/state.json): one row per GitHub login, plus the
-- file's other top-level keys in follow_meta.
CREATE TABLE follow_candidates (
  login TEXT PRIMARY KEY,
  data TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE TABLE follow_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- One-click actions from the Who to follow email (follow/actions.jsonl).
-- The nonce is the primary key, so a link can be logged only once.
CREATE TABLE actions (
  nonce TEXT PRIMARY KEY,
  at TEXT NOT NULL,
  action TEXT NOT NULL,
  login TEXT NOT NULL,
  result TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX actions_at ON actions (at);

-- Who changed the watch list and settings, and when.
CREATE TABLE changes (
  at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  who TEXT NOT NULL,
  what TEXT NOT NULL
);
