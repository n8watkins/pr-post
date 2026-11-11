// Starting the paper's GitHub Actions jobs from the Worker (docs/private-mode.md).
//
// Actions on the public repository do the work; the Worker is the clock and
// the doorbell. Its cron triggers start the scheduled jobs on time (GitHub's
// own schedules start late and stop after 60 days without commits), the
// writer asks for a publish when its reports are in, and the one-click links
// and the People page hand work over. Every start is a workflow_dispatch with
// ACTIONS_TOKEN (a fine-grained token with Actions: read and write on the one
// repository). Inputs are public there, so a job gets only an opaque request
// id and fetches the payload from /api/jobs/requests/<id> with JOBS_TOKEN.

const REQUEST_RE = /^[0-9a-f]{32}$/;
const REQUEST_TTL_MS = 24 * 3600 * 1000;

// The Gemini writer (paper-gemini.yml) runs once per issue, after the Claude
// routine's 16:00 run: at 16:45 UTC if Claude's edition for the day is in, else
// at the 17:30 publish backstop (or 20:30, if 17:30 could not start it). One
// run per issue keeps it within the Gemini API's free tier.
const geminiAfterClaude = (env, now) => startGemini(env, now, { needClaude: true });
const geminiBackstop = (env, now) => startGemini(env, now);

// Cron expression (as in paper/wrangler.jsonc) -> what it starts, in order:
// [workflow, inputs] pairs, or a function for a job that decides for itself.
export const SCHEDULE = {
  "0 11 * * 3,6": [["paper-collect.yml", {}]],
  "45 16 * * 3,6": [geminiAfterClaude],
  "30 17,20 * * 3,6": [["paper-publish.yml", {}], geminiBackstop],
  "0 10 * * 6": [["paper-follow.yml", { email: "true" }]],
};

function reply(status, body) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** Start `workflow` on main. Returns the GitHub status (204 on success) or 0 when no token is set. */
export async function dispatch(env, workflow, inputs = {}) {
  if (!env.ACTIONS_TOKEN || !env.GITHUB_REPO) return 0;
  const r = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${workflow}/dispatches`, {
    method: "POST",
    headers: { Authorization: `Bearer ${env.ACTIONS_TOKEN}`, Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "pull-request-post", "Content-Type": "application/json" },
    body: JSON.stringify({ ref: "main", inputs }),
  });
  if (r.status !== 204) console.error(`dispatch ${workflow}: ${r.status}`);
  return r.status;
}

/** Store work for a job and return its id. */
export async function createRequest(env, kind, payload) {
  const id = [...crypto.getRandomValues(new Uint8Array(16))].map((b) => b.toString(16).padStart(2, "0")).join("");
  await env.DB.prepare("INSERT INTO requests (id, kind, payload) VALUES (?1, ?2, ?3)").bind(id, kind, payload).run();
  return id;
}

/** The issue a run on `now` belongs to: Saturday (UTC) is the weekly, any other day a midweek update. */
export function issueFor(now) {
  return { date: now.toISOString().slice(0, 10), kind: now.getUTCDay() === 6 ? "weekly" : "update" };
}

/**
 * Start the Gemini writer for today's issue unless it already started (a
 * "gemini" row in requests, keyed by the issue) or, with needClaude, unless the
 * Claude routine's edition is not in yet. Returns what happened, for the log.
 */
export async function startGemini(env, now = new Date(), { needClaude = false } = {}) {
  const issue = issueFor(now);
  const key = JSON.stringify(issue);
  if (await env.DB.prepare("SELECT id FROM requests WHERE kind = 'gemini' AND payload = ?1").bind(key).first()) return "already started for this issue";
  if (needClaude && !(await env.STORE.head(`paper/editions/${issue.date}-${issue.kind}.json`))) return "waiting for Claude's edition";
  const status = await dispatch(env, "paper-gemini.yml", issue);
  if (status !== 204) return `not started (${status || "no ACTIONS_TOKEN"})`;
  await createRequest(env, "gemini", key);
  return "started";
}

/**
 * The cron handler: start what this trigger lists, in order. Each job is on its
 * own, so a failure in one (say the Gemini writer's check) never stops the next
 * or the publish before it.
 */
export async function scheduled(event, env) {
  const jobs = SCHEDULE[event.cron];
  if (!jobs) return console.warn(`no workflow for cron ${event.cron}`);
  const now = new Date(event.scheduledTime || Date.now());
  for (const job of jobs) {
    try {
      if (typeof job === "function") {
        console.log(`cron ${event.cron}: ${job.name}: ${await job(env, now)}`);
      } else {
        const [workflow, inputs] = job;
        const status = await dispatch(env, workflow, inputs);
        console.log(`cron ${event.cron}: ${workflow} ${status || "not started (no ACTIONS_TOKEN)"}`);
      }
    } catch (e) {
      console.error(`cron ${event.cron}: ${typeof job === "function" ? job.name : job[0]} failed: ${e.message}`);
    }
  }
}

/** /api/jobs/* for a bearer role. Returns a Response, or null when the path is not one of these. */
export async function handleJobs(request, env, url, role) {
  const p = url.pathname;
  if (!p.startsWith("/api/jobs/")) return null;
  if (p === "/api/jobs/publish" && request.method === "POST") {
    if (role !== "writer" && role !== "jobs") return reply(403, { error: "Not allowed." });
    const status = await dispatch(env, "paper-publish.yml");
    if (status === 204) return reply(202, { ok: true, started: "paper-publish.yml" });
    return reply(503, { error: status ? `GitHub refused to start the publish job (${status}).` : "No ACTIONS_TOKEN on the Worker yet.",
      fallback: "The scheduled publish at 17:30 and 20:30 UTC on Wednesdays and Saturdays picks the reports up." });
  }
  const m = p.match(/^\/api\/jobs\/requests\/([0-9a-f]{32})$/);
  if (m && request.method === "GET") {
    if (role !== "jobs") return reply(403, { error: "Not allowed." });
    if (!REQUEST_RE.test(m[1])) return reply(400, { error: "Not a request id." });
    const row = await env.DB.prepare("SELECT kind, payload, created_at FROM requests WHERE id = ?1").bind(m[1]).first();
    if (!row) return reply(404, { error: "No such request." });
    if (Date.now() - Date.parse(row.created_at) > REQUEST_TTL_MS) return reply(410, { error: "That request has expired." });
    await env.DB.prepare("UPDATE requests SET fetched_at = ?2 WHERE id = ?1").bind(m[1], new Date().toISOString()).run();
    return reply(200, { kind: row.kind, payload: row.payload });
  }
  return reply(404, { error: "Not found." });
}
