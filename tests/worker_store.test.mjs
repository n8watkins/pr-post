// node --test tests/ : the paper's storage API (worker/store.js) through the real Worker.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import worker from "../worker/index.js";
import { paperAllowed } from "../worker/store.js";
import { signAction, verifyAction } from "../worker/lib.js";
import { fakeD1, fakeR2 } from "./fakes.mjs";

const JOBS = "j".repeat(40);
const WRITER = "w".repeat(40);
const PASSWORD = "p".repeat(16);
const SITE = "https://paper.example";
const sha = (s) => createHash("sha256").update(s).digest("hex");

function makeEnv(extra = {}) {
  return { DB: fakeD1(), STORE: fakeR2(), JOBS_TOKEN: JOBS, WRITER_TOKEN: WRITER, SITE_PASSWORD: PASSWORD, ...extra };
}

function call(env, path, { token, method = "GET", body, headers = {}, basic = false } = {}) {
  const h = { ...headers };
  if (token) h.Authorization = `Bearer ${token}`;
  if (basic) h.Authorization = `Basic ${btoa(`me:${PASSWORD}`)}`;
  return worker.fetch(new Request(SITE + path, { method, headers: h, body }), env);
}

const put = (env, path, text, token = JOBS) => call(env, `/api/paper/files/${path}`, { token, method: "PUT", body: text, headers: { "X-Content-Sha256": sha(text) } });

function seedPeople(env) {
  env.DB.raw.exec(`INSERT INTO people (id, status, position, entry, profile) VALUES
    ('ada', 'active', 0, '{"github":"ada","name":"Ada"}', '# Ada\n'),
    ('bob', 'paused', 1, '{"github":"bob","name":"Bob"}', ''),
    ('cy', 'draft', 2, '{"github":"cy","name":"Cy"}', '');
    INSERT INTO settings (section, value) VALUES ('site', '{"title":"T","mode":"private"}');`);
}

test("lanes: who may read and write which paper paths", () => {
  assert.ok(paperAllowed("jobs", "data/ada.json", true));
  assert.ok(!paperAllowed("jobs", "follow/state.json", true));
  assert.ok(!paperAllowed("jobs", "../x", true));
  assert.ok(!paperAllowed("jobs", "data/../x", true));
  assert.ok(!paperAllowed("jobs", "secrets/x", true));
  assert.ok(!paperAllowed("jobs", "data", true));
  assert.ok(paperAllowed("owner", "reports/ada/2026-11-01-weekly.md", false));
  assert.ok(!paperAllowed("owner", "reports/ada/x.md", true));
  assert.ok(paperAllowed("writer", "briefs/ada.md", false));
  assert.ok(paperAllowed("writer", "logs/writer-usage/2026-11-14-claude.json", true));
  assert.ok(!paperAllowed("writer", "logs/writer-usage/nested/x.json", true));
  assert.ok(!paperAllowed("writer", "logs/run.log", true));
  assert.ok(!paperAllowed("writer", "logs/writer-usage/2026-11-14-claude.json", false));
  assert.ok(paperAllowed("writer", "reports/ada/x.md", true));
  assert.ok(!paperAllowed("writer", "briefs/ada.md", true));
  assert.ok(!paperAllowed("writer", "follow/latest.json", false));
  assert.ok(!paperAllowed("writer", "logs/run.log", false));
  assert.ok(paperAllowed("jobs", "gemini/reports/ada/2026-11-14-weekly.md", true));
  assert.ok(paperAllowed("jobs", "reviews/2026-11-14.md", true));
  assert.ok(paperAllowed("owner", "gemini/usage/2026-11-14.json", false));
  assert.ok(!paperAllowed("writer", "gemini/reports/ada/2026-11-14-weekly.md", false)); // the Claude routine never sees Gemini's work
  assert.ok(!paperAllowed("writer", "reviews/2026-11-14.md", false));
});

test("a wrong or empty bearer token gets nothing", async () => {
  const env = makeEnv();
  assert.equal((await call(env, "/api/paper/manifest", { token: "nope" })).status, 401);
  assert.equal((await call(env, "/api/paper/manifest", { token: "" })).status, 401); // no token: the sign-in prompt
  const empty = makeEnv({ JOBS_TOKEN: "", WRITER_TOKEN: "" });
  assert.equal((await call(empty, "/api/paper/manifest", { token: "x".repeat(40) })).status, 401);
  assert.equal((await call(makeEnv({ JOBS_TOKEN: "short" }), "/api/paper/manifest", { token: "short" })).status, 401);
});

test("jobs store files with a checked SHA-256 and see them in the manifest", async () => {
  const env = makeEnv();
  const bad = await call(env, "/api/paper/files/data/ada.json", { token: JOBS, method: "PUT", body: "{}", headers: { "X-Content-Sha256": sha("other") } });
  assert.equal(bad.status, 400);
  assert.equal((await call(env, "/api/paper/files/data/ada.json", { token: JOBS, method: "PUT", body: "{}" })).status, 400);
  for (const p of ["data/ada.json", "briefs/ada.md", "reports/ada/2026-11-01-weekly.md", "logs/run.log"]) assert.equal((await put(env, p, `content of ${p}`)).status, 200);
  const man = await (await call(env, "/api/paper/manifest", { token: JOBS })).json();
  assert.equal(man.files.length, 4); // across several list pages
  assert.equal(man.files.find((f) => f.path === "data/ada.json").sha256, sha("content of data/ada.json"));
  const got = await call(env, "/api/paper/files/data/ada.json", { token: JOBS });
  assert.equal(await got.text(), "content of data/ada.json");
  assert.equal(got.headers.get("Cache-Control"), "private, no-store");
  assert.equal((await call(env, "/api/paper/files/data/ada.json", { token: JOBS, method: "DELETE" })).status, 200);
  assert.equal((await call(env, "/api/paper/files/data/ada.json", { token: JOBS })).status, 404);
});

test("the writer reads briefs and writes reports, and nothing else", async () => {
  const env = makeEnv();
  seedPeople(env);
  await put(env, "briefs/ada.md", "brief");
  await put(env, "logs/run.log", "log");
  await put(env, "follow/latest.json", "{}");
  const man = await (await call(env, "/api/paper/manifest", { token: WRITER })).json();
  assert.deepEqual(man.files.map((f) => f.path), ["briefs/ada.md"]);
  assert.equal((await put(env, "reports/ada/2026-11-01-weekly.md", "r", WRITER)).status, 200);
  assert.equal((await put(env, "editions/2026-11-01-weekly.json", "{}", WRITER)).status, 200);
  assert.equal((await put(env, "briefs/ada.md", "changed", WRITER)).status, 403);
  assert.equal((await put(env, "data/ada.json", "x", WRITER)).status, 403);
  assert.equal((await call(env, "/api/paper/files/logs/run.log", { token: WRITER })).status, 403);
  assert.equal((await call(env, "/api/paper/files/briefs/ada.md", { token: WRITER, method: "DELETE" })).status, 403);
  assert.equal((await call(env, "/api/paper/follow", { token: WRITER })).status, 403);
  assert.equal((await call(env, "/api/paper/actions", { token: WRITER })).status, 403);
  assert.equal((await call(env, "/api/site/manifest", { token: WRITER })).status, 403);
  const wl = await (await call(env, "/api/paper/watchlist", { token: WRITER })).json();
  assert.deepEqual(wl.people.map((p) => p.id), ["ada"]); // drafts and paused people stay out of the writer's view
});

test("the writer may only add to the end of a profile", async () => {
  const env = makeEnv();
  seedPeople(env);
  const putProfile = (text, token = WRITER) => call(env, "/api/paper/profiles/ada", { token, method: "PUT", body: text });
  assert.equal((await putProfile("# Ada\n- 2026-11-09: shipped v2\n")).status, 200);
  assert.equal((await putProfile("rewritten")).status, 403);
  assert.equal((await putProfile("# Ada\n- 2026-11-09: shipped v2\n" + "x".repeat(5000))).status, 413);
  assert.equal((await putProfile("anything", JOBS)).status, 200);
  assert.equal((await call(env, "/api/paper/profiles/nobody", { token: JOBS, method: "PUT", body: "x" })).status, 404);
  const all = await (await call(env, "/api/paper/profiles", { token: JOBS })).json();
  assert.deepEqual(all.profiles, { ada: "anything" });
  assert.match(env.DB.raw.prepare("SELECT group_concat(who) AS w FROM changes").get().w, /writer.*jobs/);
});

test("follow state round-trips through rows", async () => {
  const env = makeEnv();
  assert.equal((await call(env, "/api/paper/follow", { token: JOBS })).status, 404);
  const post = (body) => call(env, "/api/paper/follow", { token: JOBS, method: "POST", body: JSON.stringify(body) });
  assert.equal((await post({ meta: { version: "1" }, upsert: { zed: { status: "candidate", n: [1] }, amy: { status: "followed" } } })).status, 200);
  assert.deepEqual(await (await call(env, "/api/paper/follow", { token: JOBS })).json(),
    { version: 1, people: { zed: { status: "candidate", n: [1] }, amy: { status: "followed" } } });
  assert.equal((await post({ upsert: { zed: { status: "suggested" } }, delete: ["amy"] })).status, 200);
  assert.deepEqual((await (await call(env, "/api/paper/follow", { token: JOBS })).json()).people, { zed: { status: "suggested" } });
  assert.equal((await post({ upsert: { "bad login!": {} } })).status, 400);
  assert.equal((await post({ meta: { people: "{}" } })).status, 400);
  assert.equal((await post({ meta: { version: "not json" } })).status, 400);
});

test("the action log takes each nonce once, and the act page sees it", async () => {
  const KEY = "k".repeat(32);
  const env = makeEnv({ ACTION_SIGNING_KEY: KEY, ACTIONS_TOKEN: "t", GITHUB_REPO: "me/paper" });
  const t = await signAction(KEY, "add", "jane");
  const { n } = await verifyAction(KEY, t);
  const entry = { at: "2026-11-09T10:00:00+00:00", action: "add", login: "jane", nonce: n, result: "ok", detail: "" };
  const log = (e) => call(env, "/api/paper/actions", { token: JOBS, method: "POST", body: JSON.stringify(e) });
  assert.equal((await log(entry)).status, 200);
  assert.equal((await log(entry)).status, 409);
  assert.equal((await log({ ...entry, nonce: "nope" })).status, 400);
  const text = await (await call(env, "/api/paper/actions", { token: JOBS })).text();
  assert.equal(text, JSON.stringify(entry) + "\n");
  const page = await worker.fetch(new Request(`${SITE}/act/${t}`), env);
  assert.equal(page.status, 409);
  assert.match(await page.text(), /already used/);
});

test("the settings page saves active people to D1 and leaves paused people and drafts alone", async () => {
  const env = makeEnv();
  seedPeople(env);
  const save = (people, origin = SITE) => worker.fetch(new Request(`${SITE}/api/watchlist`, {
    method: "PUT", body: JSON.stringify({ people }),
    headers: { Authorization: `Basic ${btoa(`me:${PASSWORD}`)}`, "Content-Type": "application/json", Origin: origin },
  }), env);
  assert.equal((await save([{ github: "dee", name: "Dee" }], "https://evil.example")).status, 403);
  assert.equal((await save([{ github: "dee", name: "Dee" }, { github: "ada", name: "Ada L" }])).status, 200);
  const rows = env.DB.raw.prepare("SELECT id, status, position, json_extract(entry, '$.name') AS name, profile FROM people ORDER BY id").all();
  assert.deepEqual(rows.map((r) => [r.id, r.status, r.position, r.name]), [["ada", "active", 1, "Ada L"], ["bob", "paused", 1, "Bob"], ["cy", "draft", 2, "Cy"], ["dee", "active", 0, "Dee"]]);
  assert.equal(rows[0].profile, "# Ada\n"); // saving the list keeps profiles
});

test("the signed-in owner reads the store but changes nothing through it", async () => {
  const env = makeEnv();
  await put(env, "reports/ada/x.md", "r");
  assert.equal((await call(env, "/api/paper/manifest")).status, 401);
  assert.equal((await call(env, "/api/paper/manifest", { basic: true })).status, 200);
  assert.equal(await (await call(env, "/api/paper/files/reports/ada/x.md", { basic: true })).text(), "r");
  const cross = await call(env, "/api/paper/files/reports/ada/x.md", { basic: true, method: "PUT", body: "x", headers: { "X-Content-Sha256": sha("x") } });
  assert.equal(cross.status, 403);
  const same = await call(env, "/api/paper/files/reports/ada/x.md", { basic: true, method: "PUT", body: "x", headers: { "X-Content-Sha256": sha("x"), Origin: SITE } });
  assert.equal(same.status, 403);
  assert.equal((await call(env, "/api/site/manifest", { basic: true })).status, 403);
});

test("the site is served from R2 after sign-in, like static assets", async () => {
  const env = makeEnv({ SITE_PREFIX: "site-next/" });
  const site = (p, text) => call(env, `/api/site/files/${p}`, { token: JOBS, method: "PUT", body: text, headers: { "X-Content-Sha256": sha(text) } });
  await site("index.html", "<h1>front</h1>");
  await site("p/ada/index.html", "ada page");
  await site("assets/site.css", "body{}");
  await site("404.html", "lost");
  assert.ok(env.STORE.objects.has("site-next/index.html"));
  assert.equal((await call(env, "/")).status, 401);
  const front = await call(env, "/", { basic: true });
  assert.equal(await front.text(), "<h1>front</h1>");
  assert.equal(front.headers.get("Content-Type"), "text/html; charset=utf-8");
  assert.ok(front.headers.get("Content-Security-Policy"));
  assert.equal((await call(env, "/assets/site.css", { basic: true })).headers.get("Content-Type"), "text/css; charset=utf-8");
  const redirect = await call(env, "/p/ada", { basic: true });
  assert.equal(redirect.status, 307);
  assert.equal(redirect.headers.get("Location"), "/p/ada/");
  assert.equal(await (await call(env, "/p/ada/", { basic: true })).text(), "ada page");
  const missing = await call(env, "/p/nobody/", { basic: true });
  assert.equal(missing.status, 404);
  assert.equal(await missing.text(), "lost");
  const etag = front.headers.get("ETag");
  assert.equal((await call(env, "/", { basic: true, headers: { "If-None-Match": etag } })).status, 304);
  assert.equal((await call(env, "/api/site/files/index.html", { token: JOBS, method: "DELETE" })).status, 200);
  assert.equal((await call(env, "/", { basic: true })).status, 404);
});
