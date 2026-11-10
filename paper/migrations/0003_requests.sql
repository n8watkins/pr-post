-- Work the Worker hands to a GitHub Actions job (worker/jobs.js). Public
-- repositories show workflow inputs to anyone, so a job is started with only
-- this row's random id and fetches the payload (a signed one-click link, a
-- GitHub username to draft) with JOBS_TOKEN.
CREATE TABLE requests (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  fetched_at TEXT
);
