// node --test tests/ : the Who to follow page's script (site/assets/follow.js) without a
// browser: the picks deck's decisions, what it keeps in the browser, and Add to PR Post
// against a mocked fetch and clipboard.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

// follow.js is a classic browser script; without a document it hands its logic to `module`.
const ctx = { module: {} };
vm.runInNewContext(readFileSync(new URL("../site/assets/follow.js", import.meta.url), "utf8"), ctx);
const f = ctx.module.exports;
const plain = (x) => JSON.parse(JSON.stringify(x)); // values from the vm compare by shape

const CARDS = [
  { login: "ada", name: "Ada", cmd: "add ada" },
  { login: "bo", name: "Bo", cmd: "add bo" },
  { login: "cy", name: "Cy", cmd: "add cy" },
];

test("the deck deals the cards in order and counts where it is", () => {
  let d = [];
  assert.deepEqual(plain(f.position(CARDS, d)), { at: 1, of: 3, left: 3 });
  d = f.decide(d, "ada", "added");
  d = f.decide(d, "bo", "skipped");
  assert.deepEqual(plain(f.remaining(CARDS, d)).map((c) => c.login), ["cy"]);
  assert.deepEqual(plain(f.position(CARDS, d)), { at: 3, of: 3, left: 1 });
  d = f.decide(d, "cy", "added");
  assert.deepEqual(plain(f.position(CARDS, d)), { at: 3, of: 3, left: 0 });
  assert.deepEqual(plain(f.counts(d)), { added: 2, skipped: 1 });
  assert.deepEqual(plain(f.addedCommands(CARDS, d)), ["add ada", "add cy"]);
});

test("Back undoes the last decision, and deciding again replaces the old one", () => {
  let d = f.decide(f.decide([], "ada", "skipped"), "bo", "added");
  d = f.undo(d);
  assert.deepEqual(plain(d), [{ login: "ada", as: "skipped" }]);
  assert.deepEqual(plain(f.remaining(CARDS, d)).map((c) => c.login), ["bo", "cy"]);
  d = f.decide(d, "ada", "added");
  assert.deepEqual(plain(d), [{ login: "ada", as: "added" }]);
  assert.deepEqual(plain(f.undo([])), []);
});

test("showing the skipped again keeps who was added", () => {
  const d = [{ login: "ada", as: "skipped" }, { login: "bo", as: "added" }, { login: "cy", as: "skipped" }];
  assert.deepEqual(plain(f.unskip(d)), [{ login: "bo", as: "added" }]);
  assert.deepEqual(plain(f.remaining(CARDS, f.unskip(d))).map((c) => c.login), ["ada", "cy"]);
});

test("addedCommands has nothing when Add drafts through the People page", () => {
  const cards = CARDS.map(({ cmd, ...c }) => c);
  assert.deepEqual(plain(f.addedCommands(cards, [{ login: "ada", as: "added" }])), []);
});

function memoryStorage(initial = {}) {
  const data = { ...initial };
  return {
    data,
    get length() { return Object.keys(data).length; },
    key: (i) => Object.keys(data)[i] ?? null,
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => { data[k] = String(v); },
    removeItem: (k) => { delete data[k]; },
  };
}

test("decisions are kept per week and survive a reload", () => {
  const s = memoryStorage();
  f.saveDecisions(s, "2026-11-07", [{ login: "ada", as: "skipped" }]);
  assert.equal(f.storageKey("2026-11-07"), "pr-post-picks-2026-11-07");
  assert.deepEqual(plain(f.loadDecisions(s, "2026-11-07", CARDS)), [{ login: "ada", as: "skipped" }]);
  // a new week starts fresh: a skip means "not this week"
  assert.deepEqual(plain(f.loadDecisions(s, "2026-11-14", CARDS)), []);
});

test("saving a week's decisions drops older weeks and leaves other keys alone", () => {
  const s = memoryStorage({ "pr-post-picks-2026-10-31": "[]", "pr-post-picks-2026-11-07": "[]", "pr-post-theme": "dark" });
  f.saveDecisions(s, "2026-11-14", [{ login: "bo", as: "added" }]);
  assert.deepEqual(Object.keys(s.data).sort(), ["pr-post-picks-2026-11-14", "pr-post-theme"]);
});

test("what is kept in the browser is checked before it is used", () => {
  const week = "2026-11-07";
  const key = f.storageKey(week);
  const load = (raw) => plain(f.loadDecisions(memoryStorage({ [key]: raw }), week, CARDS));
  assert.deepEqual(load("not json"), []);
  assert.deepEqual(load('{"ada":"added"}'), []);
  // people no longer in this week's deck, and unknown decisions, are dropped
  assert.deepEqual(load(JSON.stringify([{ login: "zed", as: "added" }, { login: "bo", as: "maybe" }, null, { login: "cy", as: "added" }])),
    [{ login: "cy", as: "added" }]);
  const broken = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("full"); } };
  assert.deepEqual(plain(f.loadDecisions(broken, week, CARDS)), []);
  assert.doesNotThrow(() => f.saveDecisions(broken, week, []));
});

function mockFetch(status, body) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, ...init, body: JSON.parse(init.body) });
    return { ok: status >= 200 && status < 300, status, json: async () => (body instanceof Error ? Promise.reject(body) : body) };
  };
  fn.calls = calls;
  return fn;
}

test("Add posts {github} to the People page's drafts endpoint and says where the draft waits", async () => {
  const fetchFn = mockFetch(202, { ok: true, message: "Researching @ada." });
  const r = plain(await f.requestDraft(fetchFn, "/api/people/drafts", "ada"));
  assert.equal(fetchFn.calls.length, 1);
  const call = fetchFn.calls[0];
  assert.equal(call.url, "/api/people/drafts");
  assert.equal(call.method, "POST");
  assert.equal(call.headers["Content-Type"], "application/json");
  assert.equal(call.credentials, "same-origin");
  assert.deepEqual(call.body, { github: "ada" });
  assert.deepEqual(r, { ok: true, kind: "ok", text: "Added @ada as a draft; approve it on People once the research finishes." });
});

test("Add counts someone already on the People page as done", async () => {
  const r = plain(await f.requestDraft(mockFetch(409, { error: "@ada is already here (draft)." }), "/api/people/drafts", "ada"));
  assert.deepEqual(r, { ok: true, kind: "ok", text: "@ada is already on the People page." });
});

test("a refused Add says why and that nothing was added", async () => {
  const r = plain(await f.requestDraft(mockFetch(503, { error: "Drafting needs the ACTIONS_TOKEN Worker secret." }), "/api/people/drafts", "ada"));
  assert.deepEqual(r, { ok: false, kind: "err", text: "Drafting needs the ACTIONS_TOKEN Worker secret. @ada was not added." });
  const bare = plain(await f.requestDraft(mockFetch(500, new Error("not json")), "/api/people/drafts", "ada"));
  assert.equal(bare.text, "The paper refused it (500). @ada was not added.");
  // a 200 that is not the Worker's answer (a sign-in page, say) is not a success
  const odd = plain(await f.requestDraft(mockFetch(200, new Error("html")), "/api/people/drafts", "ada"));
  assert.deepEqual(odd, { ok: false, kind: "err", text: "The paper sent an answer this page did not expect. @ada was not added." });
});

test("an Add that never reaches the paper says so", async () => {
  const r = plain(await f.requestDraft(async () => { throw new TypeError("Failed to fetch"); }, "/api/people/drafts", "ada"));
  assert.equal(r.ok, false);
  assert.equal(r.kind, "err");
  assert.match(r.text, /^Could not reach the paper, so @ada was not added\./);
});

test("without the People page, Add copies the command, or shows it when copying fails", async () => {
  const copied = [];
  const ok = plain(await f.copyCommand({ writeText: async (t) => { copied.push(t); } }, "add ada", "ada"));
  assert.deepEqual(copied, ["add ada"]);
  assert.deepEqual(ok, { ok: true, kind: "ok", text: "Copied the command that adds @ada; run it to draft a pull request." });
  const failed = plain(await f.copyCommand({ writeText: async () => { throw new Error("denied"); } }, "add ada", "ada"));
  assert.deepEqual(failed, { ok: false, kind: "err", text: "Could not copy. To add @ada, run:", cmd: "add ada" });
});
