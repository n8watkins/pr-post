// node --test tests/
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { validatePeople, replacePeople, checkClaims, decodeJwt, realm, PEOPLE_MARKER } from "../worker/lib.js";

const person = { github: "simonw", name: "Simon", ticker: "simw", orgs: ["datasette"], links: [{ label: "X", url: "https://x.com/simonw" }] };
const tuber = { id: "nateherk", name: "Nate Herk", youtube_channel_id: "UC2ojq-nuP8ceeHqiroeKhBA" };

test("validatePeople cleans and normalises", () => {
  const [p] = validatePeople({ people: [person] });
  assert.equal(p.id, "simonw");
  assert.equal(p.ticker, "SIMW");
  assert.equal(p.starred, false);
  assert.deepEqual(p.orgs, ["datasette"]);
});

test("validatePeople accepts a YouTube-only person with an id", () => {
  const [p] = validatePeople({ people: [tuber] });
  assert.equal(p.id, "nateherk");
  assert.equal(p.github, "");
  assert.equal(p.youtube_channel_id, "UC2ojq-nuP8ceeHqiroeKhBA");
});

test("validatePeople rejects bad input", () => {
  assert.throws(() => validatePeople({}), /list of people/);
  assert.throws(() => validatePeople({ people: [{ github: "not valid!" }] }), /GitHub username/);
  assert.throws(() => validatePeople({ people: [person, person] }), /twice/);
  assert.throws(() => validatePeople({ people: [{ ...person, links: [{ label: "x", url: "javascript:alert(1)" }] }] }), /http/);
  assert.throws(() => validatePeople({ people: [{ ...person, name: "a\nb" }] }), /control/);
  assert.throws(() => validatePeople({ people: [{ name: "nobody" }] }), /username or an id/);
  assert.throws(() => validatePeople({ people: [{ id: "x" }] }), /to follow/);
  assert.throws(() => validatePeople({ people: [{ ...tuber, youtube_channel_id: "nope" }] }), /YouTube channel id/);
  assert.throws(() => validatePeople({ people: [{ ...tuber, id: "Bad Id" }] }), /lowercase/);
  assert.throws(() => validatePeople({ people: [{ ...person, avatar: "javascript:alert(1)" }] }), /http/);
});

test("replacePeople keeps the top of the file and round-trips through TOML rules", () => {
  const toml = readFileSync(new URL("../demo/watchlist.toml", import.meta.url), "utf8");
  const out = replacePeople(toml, validatePeople({ people: [{ ...person, known_for: 'says "hi" \\ there', starred: true }, tuber] }));
  assert.ok(out.startsWith(toml.slice(0, toml.indexOf(PEOPLE_MARKER))));
  assert.match(out, /\[site\]/);
  assert.match(out, /known_for = "says \\"hi\\" \\\\ there"/);
  assert.match(out, /starred = true/);
  assert.match(out, /links = \[\n  \{ label = "X", url = "https:\/\/x.com\/simonw" \},\n\]/);
  assert.match(out, /\[\[people\]\]\nid = "nateherk"\nname = "Nate Herk"\nyoutube_channel_id = "UC2ojq-nuP8ceeHqiroeKhBA"/);
  assert.doesNotMatch(out, /github = ""/);
});

test("checkClaims enforces audience, issuer and expiry", () => {
  const ok = { aud: ["abc"], iss: "https://team.cloudflareaccess.com", exp: 2e9 };
  const opts = { aud: "abc", teamDomain: "team.cloudflareaccess.com", now: 1.8e9 };
  assert.equal(checkClaims(ok, opts), null);
  assert.equal(checkClaims({ ...ok, aud: ["other"] }, opts), "wrong audience");
  assert.equal(checkClaims({ ...ok, iss: "https://evil.cloudflareaccess.com" }, opts), "wrong issuer");
  assert.equal(checkClaims({ ...ok, exp: 1 }, opts), "expired");
});

test("decodeJwt rejects malformed tokens", () => {
  assert.throws(() => decodeJwt("nope"), /malformed/);
});

test("realm names the paper in the sign-in prompt and cannot break the header", () => {
  assert.equal(realm({}), "pr-post");
  assert.equal(realm({ SITE_TITLE: "The Test Gazette" }), "The Test Gazette");
  assert.equal(realm({ SITE_TITLE: 'Bad "title"\r\nX-Evil: 1' }), "Bad titleX-Evil: 1");
});

test("the Worker serves nothing without sign-in", async () => {
  const worker = (await import("../worker/index.js")).default;
  const testPassword = "x".repeat(12); // a throwaway value, built here so it never looks like a real secret
  let served = false;
  const ASSETS = { fetch: async () => { served = true; return new Response("content"); } };

  const locked = await worker.fetch(new Request("https://paper.example/"), { ASSETS });
  assert.equal(locked.status, 503);

  const prompt = await worker.fetch(new Request("https://paper.example/p/octo/"), { ASSETS, SITE_PASSWORD: testPassword, SITE_TITLE: "The Test Gazette" });
  assert.equal(prompt.status, 401);
  assert.equal(prompt.headers.get("WWW-Authenticate"), 'Basic realm="The Test Gazette", charset="UTF-8"');
  assert.equal(prompt.headers.get("Cache-Control"), "private, no-store");
  assert.doesNotMatch(await prompt.text(), /content/);

  const wrong = await worker.fetch(new Request("https://paper.example/", { headers: { Authorization: "Basic !!!" } }), { ASSETS, SITE_PASSWORD: testPassword });
  assert.equal(wrong.status, 401);
  assert.equal(served, false);
});

// ---------- one-click actions ----------

import worker from "../worker/index.js";
import { signAction, verifyAction } from "../worker/lib.js";

const KEY = "test-key-0123456789abcdef";
const SITE = "https://paper.example";

function withGitHub({ secret = 200, log = "" } = {}, run) {
  const calls = [];
  const real = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), method: init.method || "GET" });
    if (String(url).includes("/actions/secrets/")) return new Response("{}", { status: secret });
    if (String(url).includes("/contents/follow/actions.jsonl")) return new Response(log, { status: log ? 200 : 404 });
    if (String(url).includes("/dispatches")) return new Response(null, { status: 204 });
    return new Response("unexpected", { status: 500 });
  };
  return run(calls).finally(() => { globalThis.fetch = real; });
}

const env = { ACTION_SIGNING_KEY: KEY, ACTIONS_TOKEN: "t", GITHUB_REPO: "me/paper", SITE_PASSWORD: "pw", SITE_TITLE: "The Gazette" };
// Flip a character in the middle of the signature (the last one may only carry padding bits).
const tamper = (t) => { const i = t.length - 10; return t.slice(0, i) + (t[i] === "A" ? "B" : "A") + t.slice(i + 1); };
const act = (token, init) => worker.fetch(new Request(`${SITE}/act/${token}`, init), env);

test("signAction and verifyAction round-trip, and refuse tampering", async () => {
  const t = await signAction(KEY, "follow", "jane");
  assert.equal((await verifyAction(KEY, t)).l, "jane");
  await assert.rejects(verifyAction("other", t));
  await assert.rejects(verifyAction(KEY, tamper(t)));
  await assert.rejects(verifyAction(KEY, t, Math.floor(Date.now() / 1000) + 8 * 24 * 3600), /expired/);
});

test("a GET never acts, needs no sign-in, and shows a page that posts", async () => {
  const t = await signAction(KEY, "follow", "jane");
  await withGitHub({}, async (calls) => {
    const r = await act(t);
    assert.equal(r.status, 200);
    const html = await r.text();
    assert.match(html, /Follow @jane on GitHub\?/);
    assert.match(html, /<form id="act" method="post">/);
    assert.match(r.headers.get("Content-Security-Policy"), /script-src 'sha256-/);
    assert.ok(!calls.some((c) => c.url.includes("/dispatches")));
  });
});

test("the POST dispatches the act workflow and offers an undo", async () => {
  const t = await signAction(KEY, "follow", "jane");
  await withGitHub({}, async (calls) => {
    const r = await act(t, { method: "POST", headers: { Origin: SITE } });
    assert.equal(r.status, 200);
    const html = await r.text();
    assert.match(html, /Followed @jane ✓/);
    const undo = html.match(/href="\/act\/([^"]+)">Undo/)[1];
    assert.equal((await verifyAction(KEY, undo)).a, "unfollow");
    assert.ok(calls.some((c) => c.url.endsWith("/repos/me/paper/actions/workflows/act.yml/dispatches") && c.method === "POST"));
  });
});

test("a cross-site POST, a used link and a missing follow token all refuse", async () => {
  const t = await signAction(KEY, "follow", "jane");
  await withGitHub({}, async (calls) => {
    assert.equal((await act(t, { method: "POST", headers: { Origin: "https://evil.example" } })).status, 403);
    assert.equal((await act(t, { method: "POST" })).status, 403);
    assert.ok(!calls.some((c) => c.url.includes("/dispatches")));
  });
  const nonce = (await verifyAction(KEY, t)).n;
  await withGitHub({ log: `{"nonce":"${nonce}"}\n` }, async () => {
    assert.match(await (await act(t)).text(), /already used/);
  });
  await withGitHub({ secret: 404 }, async () => {
    const r = await act(t);
    assert.equal(r.status, 503);
    assert.match(await r.text(), /Follow isn&#39;t set up yet|Follow isn't set up yet/);
  });
});

test("without a signing key or with a bad token nothing happens", async () => {
  const r = await worker.fetch(new Request(`${SITE}/act/abc.def`), { ...env, ACTION_SIGNING_KEY: "" });
  assert.equal(r.status, 404);
  const t = await signAction(KEY, "add", "jane");
  assert.equal((await worker.fetch(new Request(`${SITE}/act/${t}`), { ...env, ACTION_SIGNING_KEY: "" })).status, 503);
  assert.equal((await act(tamper(t))).status, 400);
});
