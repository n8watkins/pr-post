// node --test tests/ : the People page's API (worker/people.js), owner only.
import { test } from "node:test";
import assert from "node:assert/strict";
import worker from "../worker/index.js";
import { fakeD1, fakeR2 } from "./fakes.mjs";

const PASSWORD = "p".repeat(16);
const SITE = "https://paper.example";
const AUTH = { Authorization: `Basic ${btoa(`me:${PASSWORD}`)}` };

function makeEnv(extra = {}) {
  const env = { DB: fakeD1(), STORE: fakeR2(), SITE_PASSWORD: PASSWORD, ACTIONS_TOKEN: "t", GITHUB_REPO: "me/paper", ...extra };
  env.DB.raw.exec(`INSERT INTO people (id, status, position, entry, profile, note) VALUES
    ('ada', 'active', 0, '{"id":"ada","github":"ada","name":"Ada"}', '# Ada', ''),
    ('bob', 'active', 1, '{"id":"bob","github":"bob","name":"Bob"}', '', ''),
    ('cy', 'draft', 2, '{"id":"cy","github":"cy","name":"Cy"}', '# Cy\n## Still to check\n- socials', 'Drafted by a job')`);
  return env;
}

const call = (env, path, method = "GET", body, headers = {}) => worker.fetch(new Request(SITE + path, {
  method, body: body === undefined ? undefined : JSON.stringify(body),
  headers: { ...AUTH, ...(body === undefined ? {} : { "Content-Type": "application/json" }), ...(method === "GET" ? {} : { Origin: SITE }), ...headers },
}), env);
const rows = (env) => env.DB.raw.prepare("SELECT id, status, position, json_extract(entry, '$.name') AS name FROM people ORDER BY id").all().map((r) => [r.id, r.status, r.position, r.name]);

test("the owner sees everyone, with profiles and recent changes; nobody else does", async () => {
  const env = makeEnv();
  assert.equal((await worker.fetch(new Request(`${SITE}/api/people`), env)).status, 401);
  const r = await call(env, "/api/people");
  const body = await r.json();
  assert.deepEqual(body.people.map((p) => [p.id, p.status]), [["ada", "active"], ["bob", "active"], ["cy", "draft"]]);
  assert.match(body.people[2].profile, /Still to check/);
  assert.deepEqual(body.suppressed, []);
  const bearer = await worker.fetch(new Request(`${SITE}/api/people`, { headers: { Authorization: `Bearer ${"j".repeat(40)}` } }), { ...env, JOBS_TOKEN: "j".repeat(40) });
  assert.equal(bearer.status, 404); // tokens never reach the owner's API
});

test("approve, pause, resume, remove; all logged; cross-site changes refused", async () => {
  const env = makeEnv();
  assert.equal((await call(env, "/api/people/cy/status", "POST", { status: "active" }, { Origin: "https://evil.example" })).status, 403);
  assert.equal((await call(env, "/api/people/cy/status", "POST", { status: "active" })).status, 200);
  assert.equal((await call(env, "/api/people/ada/status", "POST", { status: "paused" })).status, 200);
  assert.equal((await call(env, "/api/people/ada/status", "POST", { status: "draft" })).status, 400);
  assert.equal((await call(env, "/api/people/bob", "DELETE")).status, 200);
  assert.equal((await call(env, "/api/people/nobody", "DELETE")).status, 404);
  assert.deepEqual(rows(env), [["ada", "paused", 0, "Ada"], ["cy", "active", 2, "Cy"]]);
  assert.equal(env.DB.raw.prepare("SELECT note FROM people WHERE id = 'cy'").get().note, "");
  const what = env.DB.raw.prepare("SELECT group_concat(what, '|') AS w FROM changes").get().w;
  assert.equal(what, "cy approved|ada paused|bob removed");
});

test("edits are validated and the GitHub account stays the same", async () => {
  const env = makeEnv();
  const ok = await call(env, "/api/people/ada", "PUT", { entry: { github: "ada", name: "Ada L", ticker: "ada", links: [{ label: "X", url: "https://x.com/ada" }] } });
  assert.equal(ok.status, 200);
  assert.equal((await ok.json()).entry.ticker, "ADA");
  assert.equal((await call(env, "/api/people/ada", "PUT", { entry: { github: "ada", name: "x", links: [{ label: "x", url: "javascript:alert(1)" }] } })).status, 400);
  assert.equal((await call(env, "/api/people/ada", "PUT", { entry: { github: "mallory", name: "Ada" } })).status, 400);
  assert.equal((await call(env, "/api/people/ada", "PUT", { entry: { github: "ada", name: "a\nb" } })).status, 400);
  assert.deepEqual(rows(env)[0], ["ada", "active", 0, "Ada L"]);
});

test("order is saved for the people listed", async () => {
  const env = makeEnv();
  assert.equal((await call(env, "/api/people/order", "PUT", { ids: ["bob", "ada"] })).status, 200);
  assert.deepEqual(rows(env).map((r) => [r[0], r[2]]), [["ada", 1], ["bob", 0], ["cy", 2]]);
  assert.equal((await call(env, "/api/people/order", "PUT", { ids: ["Bad Id"] })).status, 400);
});

test("adding someone starts the draft job with an opaque id, and refuses duplicates", async () => {
  const env = makeEnv();
  const calls = [];
  const real = globalThis.fetch;
  globalThis.fetch = async (url, init) => { calls.push({ url: String(url), body: JSON.parse(init.body) }); return new Response(null, { status: 204 }); };
  try {
    const r = await call(env, "/api/people/drafts", "POST", { github: "https://github.com/Dee" });
    assert.equal(r.status, 202);
    assert.match(calls[0].url, /paper-people\.yml\/dispatches$/);
    assert.ok(!JSON.stringify(calls[0].body).includes("Dee"));
    const req = env.DB.raw.prepare("SELECT kind, payload FROM requests").get();
    assert.deepEqual([req.kind, req.payload], ["draft", "Dee"]);
    assert.equal((await call(env, "/api/people/drafts", "POST", { github: "CY" })).status, 409);
    assert.equal((await call(env, "/api/people/drafts", "POST", { github: "not valid!" })).status, 400);
  } finally { globalThis.fetch = real; }
  assert.equal((await call(makeEnv({ ACTIONS_TOKEN: "" }), "/api/people/drafts", "POST", { github: "dee" })).status, 503);
});

test("a suppressed address can be cleared", async () => {
  const env = makeEnv();
  env.DB.raw.exec(`INSERT INTO email_events (at, type, address, detail, event_id) VALUES ('2026-11-09', 'Complaint', 'me@example.com', 'abuse', 's1')`);
  assert.equal((await (await call(env, "/api/people")).json()).suppressed.length, 1);
  assert.equal((await call(env, "/api/email/clear", "POST", { address: "ME@example.com" })).status, 200);
  assert.equal((await (await call(env, "/api/people")).json()).suppressed.length, 0);
  assert.equal((await call(env, "/api/email/clear", "POST", { address: "me@example.com" })).status, 404);
});

test("the old settings page sends the owner to the People page", async () => {
  const r = await call(makeEnv(), "/settings/");
  assert.equal(r.status, 308);
  assert.equal(r.headers.get("Location"), "/people/");
});
