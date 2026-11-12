// node --test tests/ : starting the paper's jobs (worker/jobs.js), one-click links with D1, drafts and owner files.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import worker from "../worker/index.js";
import { signAction } from "../worker/lib.js";
import { SCHEDULE, issueFor } from "../worker/jobs.js";
import { fakeD1, fakeR2 } from "./fakes.mjs";

const JOBS = "j".repeat(40);
const WRITER = "w".repeat(40);
const PASSWORD = "p".repeat(16);
const KEY = "k".repeat(32);
const SITE = "https://paper.example";

const makeEnv = (extra = {}) => ({ DB: fakeD1(), STORE: fakeR2(), JOBS_TOKEN: JOBS, WRITER_TOKEN: WRITER, SITE_PASSWORD: PASSWORD,
  ACTION_SIGNING_KEY: KEY, ACTIONS_TOKEN: "dispatch-token", GITHUB_REPO: "me/paper", ...extra });
const bearer = (env, path, token, init = {}) => worker.fetch(new Request(SITE + path, { ...init, headers: { ...(init.headers || {}), Authorization: `Bearer ${token}` } }), env);

function withGitHub(run, status = 204) {
  const calls = [];
  const real = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), body: init.body ? JSON.parse(init.body) : null, auth: init.headers && init.headers.Authorization });
    return new Response(null, { status });
  };
  return run(calls).finally(() => { globalThis.fetch = real; });
}

test("the writer and jobs can ask for a publish; nobody else", async () => {
  const env = makeEnv();
  await withGitHub(async (calls) => {
    const r = await bearer(env, "/api/jobs/publish", WRITER, { method: "POST" });
    assert.equal(r.status, 202);
    assert.equal(calls[0].url, "https://api.github.com/repos/me/paper/actions/workflows/paper-publish.yml/dispatches");
    assert.deepEqual(calls[0].body, { ref: "main", inputs: {} });
    assert.equal(calls[0].auth, "Bearer dispatch-token");
    assert.equal((await worker.fetch(new Request(`${SITE}/api/jobs/publish`, { method: "POST" }), env)).status, 401);
  });
  const none = await bearer(makeEnv({ ACTIONS_TOKEN: "" }), "/api/jobs/publish", WRITER, { method: "POST" });
  assert.equal(none.status, 503);
  assert.match((await none.json()).fallback, /17:30/);
});

const started = (calls) => calls.map((c) => [c.url.split("/workflows/")[1].split("/")[0], c.body.inputs]);
const SAT = Date.parse("2026-11-14T16:45:00Z");
const WED = Date.parse("2026-11-18T17:30:00Z");

test("the cron starts the scheduled workflows with their inputs", async () => {
  const env = makeEnv();
  await withGitHub(async (calls) => {
    for (const cron of ["0 11 * * 3,6", "30 17,20 * * 3,6", "0 10 * * 6"]) await worker.scheduled({ cron, scheduledTime: WED }, env);
    await worker.scheduled({ cron: "* * * * *" }, env);
    assert.deepEqual(started(calls), [["paper-collect.yml", {}], ["paper-publish.yml", {}],
      ["paper-gemini.yml", { date: "2026-11-18", kind: "update" }], ["paper-follow.yml", { email: "true" }]]);
  });
});

test("an issue is the UTC date, weekly on Saturdays and an update otherwise", () => {
  assert.deepEqual(issueFor(new Date(SAT)), { date: "2026-11-14", kind: "weekly" });
  assert.deepEqual(issueFor(new Date(WED)), { date: "2026-11-18", kind: "update" });
  assert.deepEqual(issueFor(new Date("2026-11-14T23:59:59Z")), { date: "2026-11-14", kind: "weekly" });
});

test("the Gemini writer starts at 16:45 only once Claude's edition is in, and once per issue", async () => {
  const env = makeEnv();
  await withGitHub(async (calls) => {
    await worker.scheduled({ cron: "45 16 * * 3,6", scheduledTime: SAT }, env);
    assert.deepEqual(calls, []);
    await env.STORE.put("paper/editions/2026-11-14-weekly.json", "{}");
    await worker.scheduled({ cron: "45 16 * * 3,6", scheduledTime: SAT }, env);
    assert.deepEqual(started(calls), [["paper-gemini.yml", { date: "2026-11-14", kind: "weekly" }]]);
    // The backstops publish again but do not start a second Gemini run for the same issue.
    await worker.scheduled({ cron: "30 17,20 * * 3,6", scheduledTime: SAT + 45 * 60e3 }, env);
    await worker.scheduled({ cron: "30 17,20 * * 3,6", scheduledTime: SAT + 225 * 60e3 }, env);
    assert.deepEqual(started(calls).map((c) => c[0]), ["paper-gemini.yml", "paper-publish.yml", "paper-publish.yml"]);
    const rows = env.DB.raw.prepare("SELECT kind, payload FROM requests").all();
    assert.deepEqual(rows.map((r) => ({ ...r })), [{ kind: "gemini", payload: '{"date":"2026-11-14","kind":"weekly"}' }]);
    // The next issue is a new run.
    await worker.scheduled({ cron: "30 17,20 * * 3,6", scheduledTime: WED }, env);
    assert.deepEqual(started(calls).slice(-2), [["paper-publish.yml", {}], ["paper-gemini.yml", { date: "2026-11-18", kind: "update" }]]);
  });
});

test("the 17:30 backstop starts Gemini without Claude's edition, and retries one GitHub refused", async () => {
  const env = makeEnv();
  await withGitHub(async (calls) => {
    await worker.scheduled({ cron: "30 17,20 * * 3,6", scheduledTime: WED }, env);
    assert.equal(calls.length, 2);
    assert.equal(env.DB.raw.prepare("SELECT COUNT(*) AS n FROM requests").get().n, 0); // refused: nothing recorded
  }, 500);
  await withGitHub(async (calls) => {
    await worker.scheduled({ cron: "30 17,20 * * 3,6", scheduledTime: WED + 180 * 60e3 }, env);
    assert.deepEqual(started(calls), [["paper-publish.yml", {}], ["paper-gemini.yml", { date: "2026-11-18", kind: "update" }]]);
  });
});

test("a Gemini failure never stops the publish", async () => {
  const env = makeEnv();
  env.DB = { prepare() { throw new Error("D1 is down"); } };
  const errors = [];
  const realError = console.error;
  console.error = (m) => errors.push(m);
  try {
    await withGitHub(async (calls) => {
      await worker.scheduled({ cron: "30 17,20 * * 3,6", scheduledTime: WED }, env);
      assert.deepEqual(started(calls), [["paper-publish.yml", {}]]);
    });
  } finally {
    console.error = realError;
  }
  assert.match(errors.join("\n"), /geminiBackstop failed: D1 is down/);
});

test("the wrangler config's crons all have a workflow", async () => {
  const { readFileSync } = await import("node:fs");
  const text = readFileSync(new URL("../paper/wrangler.jsonc", import.meta.url), "utf8");
  const crons = JSON.parse(text.match(/"triggers": (\{ "crons": \[[^\]]*\] \})/)[1]).crons;
  assert.ok(crons.length >= 2);
  for (const c of crons) assert.ok(SCHEDULE[c], c);
});

test("a one-click link starts paper-act.yml with only an opaque id, which jobs can fetch once a day", async () => {
  const env = makeEnv();
  const token = await signAction(KEY, "add", "jane");
  await withGitHub(async (calls) => {
    const r = await worker.fetch(new Request(`${SITE}/act/${token}`, { method: "POST", headers: { Origin: SITE } }), env);
    assert.equal(r.status, 200);
    assert.match(await r.text(), /paper-act\.yml/);
    assert.match(calls[0].url, /\/workflows\/paper-act\.yml\/dispatches$/);
    const inputs = calls[0].body.inputs;
    assert.deepEqual(Object.keys(inputs), ["request"]);
    assert.match(inputs.request, /^[0-9a-f]{32}$/);
    assert.ok(!JSON.stringify(calls[0].body).includes("jane"));
    assert.equal((await bearer(env, `/api/jobs/requests/${inputs.request}`, WRITER)).status, 403);
    const got = await (await bearer(env, `/api/jobs/requests/${inputs.request}`, JOBS)).json();
    assert.deepEqual(got, { kind: "act", payload: token });
    assert.equal((await bearer(env, `/api/jobs/requests/${"0".repeat(32)}`, JOBS)).status, 404);
    env.DB.raw.exec(`UPDATE requests SET created_at = '2020-01-01T00:00:00Z'`);
    assert.equal((await bearer(env, `/api/jobs/requests/${inputs.request}`, JOBS)).status, 410);
  });
});

test("jobs add drafts and discard only drafts", async () => {
  const env = makeEnv();
  env.DB.raw.exec(`INSERT INTO people (id, status, position, entry) VALUES ('ada', 'active', 0, '{"github":"ada","name":"Ada"}')`);
  const draft = (entry, token = JOBS) => bearer(env, "/api/paper/people/drafts", token, { method: "POST", body: JSON.stringify({ entry, profile: "# Jane\n## Still to check\n- x\n", note: "n" }) });
  assert.equal((await draft({ github: "jane", name: "Jane" }, WRITER)).status, 403);
  assert.equal((await draft({ github: "not valid!" })).status, 400);
  assert.equal((await draft({ github: "jane", name: "Jane" })).status, 200);
  assert.equal((await draft({ github: "jane", name: "Jane" })).status, 409);
  assert.equal((await draft({ github: "ada", name: "Ada" })).status, 409);
  const row = env.DB.raw.prepare("SELECT status, position, profile FROM people WHERE id = 'jane'").get();
  assert.deepEqual([row.status, row.position], ["draft", 1]);
  assert.match(row.profile, /Still to check/);
  const wl = await (await bearer(env, "/api/paper/watchlist", WRITER)).json();
  assert.deepEqual(wl.people.map((p) => p.id), ["ada"]);
  assert.equal((await bearer(env, "/api/paper/people/drafts/ada", JOBS, { method: "DELETE" })).status, 404);
  assert.equal((await bearer(env, "/api/paper/people/drafts/jane", JOBS, { method: "DELETE" })).status, 200);
  assert.equal(env.DB.raw.prepare("SELECT COUNT(*) AS n FROM people").get().n, 1);
  assert.match(env.DB.raw.prepare("SELECT group_concat(what, '|') AS w FROM changes").get().w, /draft jane added\|draft jane discarded/);
});

test("the owner reads logs and previews at /files/, and nothing else there", async () => {
  const env = makeEnv();
  const put = (path, text) => bearer(env, `/api/paper/files/${path}`, JOBS, { method: "PUT", body: text, headers: { "X-Content-Sha256": createHash("sha256").update(text).digest("hex") } });
  await put("logs/1-collect.log", "ok: collect.py");
  await put("previews/1-digest.html", "<p>hi</p>");
  const basic = { Authorization: `Basic ${btoa(`me:${PASSWORD}`)}` };
  assert.equal((await worker.fetch(new Request(`${SITE}/files/logs/1-collect.log`), env)).status, 401);
  const log = await worker.fetch(new Request(`${SITE}/files/logs/1-collect.log`, { headers: basic }), env);
  assert.equal(await log.text(), "ok: collect.py");
  assert.equal(log.headers.get("Content-Type"), "text/plain; charset=utf-8");
  const prev = await worker.fetch(new Request(`${SITE}/files/previews/1-digest.html`, { headers: basic }), env);
  assert.equal(prev.headers.get("Content-Type"), "text/html; charset=utf-8");
  assert.equal((await worker.fetch(new Request(`${SITE}/files/follow/state.json`, { headers: basic }), env)).status, 404);
  assert.equal((await worker.fetch(new Request(`${SITE}/files/../x`, { headers: basic }), env)).status, 404);
});
