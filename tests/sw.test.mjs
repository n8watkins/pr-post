// node --test tests/ : the service worker (site/sw.js) without a browser. It runs in a
// vm with fake caches and fetch, and these tests check what it answers, what it
// leaves to the network, and that sign-in redirects and API answers are never kept.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const SOURCE = readFileSync(new URL("../site/sw.js", import.meta.url), "utf8");
const FONT_CSS = "https://fonts.googleapis.com/css2?family=Inter&display=swap";

/** A fake answer shaped like a fetch Response, with the fields the worker reads. */
function answer(body, { status = 200, type = "basic", redirected = false, url = "" } = {}) {
  return { status, type, redirected, url, body, ok: status >= 200 && status < 300, clone() { return answer(body, { status, type, redirected, url }); }, async text() { return body; } };
}

function fakeCaches() {
  const stores = new Map();
  const key = (r) => (typeof r === "string" ? r : r.url);
  const open = async (name) => {
    if (!stores.has(name)) stores.set(name, new Map());
    const s = stores.get(name);
    return {
      async put(r, res) { s.set(key(r), res); },
      async match(r) { return s.get(key(r)); },
      async keys() { return [...s.keys()]; },
    };
  };
  return { stores, open, async keys() { return [...stores.keys()]; }, async delete(name) { return stores.delete(name); } };
}

/** Load sw.js for a scope, with `net(url, init)` playing the network. */
function load({ scope = "https://paper.example/", net = async () => answer("ok"), build = "b1", pages = ["./", "p/octo/"] } = {}) {
  const handlers = {};
  const caches = fakeCaches();
  const fetched = [];
  const ctx = {
    URL, Response: { error: () => answer("", { status: 0, type: "error" }) }, setTimeout, clearTimeout, Promise, console,
    caches,
    fetch: async (req, init) => { const url = typeof req === "string" ? req : req.url; fetched.push(url); return net(url, init, req); },
    self: {
      registration: { scope },
      addEventListener: (type, fn) => { handlers[type] = fn; },
      skipWaiting: async () => {},
      clients: { claim: async () => {} },
    },
  };
  const code = SOURCE.replace("__BUILD__", build)
    .replace("__SHELL__", JSON.stringify(["assets/style.css?v=1", "manifest.webmanifest"]))
    .replace("__PAGES__", JSON.stringify(pages))
    .replace("__FONT_CSS__", JSON.stringify(FONT_CSS));
  vm.runInNewContext(code, ctx);
  return { ctx, handlers, caches, fetched, scope: new URL(scope) };
}

/** Dispatch a fetch event; returns { handled, response, settled } where settled waits for every waitUntil. */
async function dispatch(sw, url, { method = "GET", mode = "no-cors", headers = {} } = {}) {
  const request = { url, method, mode, headers: { get: (k) => headers[k.toLowerCase()] ?? null } };
  let responded = null;
  const extra = [];
  sw.handlers.fetch({ request, respondWith: (p) => { responded = p; }, waitUntil: (p) => extra.push(p) });
  const response = responded ? await responded : undefined;
  await Promise.all(extra);
  return { handled: Boolean(responded), response };
}

async function install(sw) {
  const waits = [];
  sw.handlers.install({ waitUntil: (p) => waits.push(p) });
  await Promise.all(waits);
}

async function cached(sw) {
  const all = [];
  for (const name of await sw.caches.keys()) all.push(...await (await sw.caches.open(name)).keys());
  return all;
}

test("routing: pages, assets and fonts are handled; everything else goes to the network", () => {
  const sw = load();
  const r = (url, extra = {}) => sw.ctx.route({ url, method: "GET", mode: "no-cors", headers: { get: () => null }, ...extra }, sw.scope);
  assert.equal(r("https://paper.example/", { mode: "navigate" }), "page");
  assert.equal(r("https://paper.example/p/octo/", { mode: "navigate" }), "page");
  assert.equal(r("https://paper.example/assets/style.css?v=1"), "asset");
  assert.equal(r("https://paper.example/manifest.webmanifest"), "asset");
  assert.equal(r("https://fonts.googleapis.com/css2?family=Inter"), "font");
  assert.equal(r("https://fonts.gstatic.com/s/inter/v20/a.woff2"), "font");
  for (const path of ["api/people", "api/watchlist", "api", "act/abc.def", "files/jobs/x.log", "cdn-cgi/access/authorized"]) {
    assert.equal(r(`https://paper.example/${path}`, { mode: "navigate" }), "network", path);
    assert.equal(r(`https://paper.example/${path}`), "network", path);
  }
  assert.equal(r("https://paper.example/offline.html", { mode: "navigate" }), "page");
  for (const file of ["feed.json", "sw.js", "manifest.webmanifest"]) {
    assert.notEqual(r(`https://paper.example/${file}`, { mode: "navigate" }), "page", file);
  }
  assert.equal(r("https://paper.example/feed.json"), "network");
  assert.equal(r("https://paper.example/sw.js"), "network");
  assert.equal(r("https://team.cloudflareaccess.com/cdn-cgi/access/login", { mode: "navigate" }), "network");
  assert.equal(r("https://avatars.githubusercontent.com/u/1"), "network");
  assert.equal(r("https://paper.example/", { mode: "navigate", method: "POST" }), "network");
  assert.equal(r("https://paper.example/act/abc.def", { mode: "navigate", method: "POST" }), "network");
  assert.equal(r("https://paper.example/assets/a.mp4", { headers: { get: (k) => (k === "range" ? "bytes=0-" : null) } }), "network");
});

test("routing under a base path stays inside the scope", () => {
  const sw = load({ scope: "https://me.github.io/pr-post/" });
  const r = (url, mode = "navigate") => sw.ctx.route({ url, method: "GET", mode, headers: { get: () => null } }, sw.scope);
  assert.equal(r("https://me.github.io/pr-post/"), "page");
  assert.equal(r("https://me.github.io/pr-post/assets/app.js?v=1", "no-cors"), "asset");
  assert.equal(r("https://me.github.io/pr-post/api/people"), "network");
  assert.equal(r("https://me.github.io/other-project/"), "network");
});

test("only plain 200 answers are cacheable", () => {
  const { ctx } = load();
  assert.equal(ctx.cacheable(answer("x")), true);
  assert.equal(ctx.cacheable(answer("x", { type: "cors" })), true);
  assert.equal(ctx.cacheable(answer("", { status: 0, type: "opaqueredirect" })), false);
  assert.equal(ctx.cacheable(answer("", { status: 0, type: "opaque" })), false);
  assert.equal(ctx.cacheable(answer("x", { redirected: true })), false);
  assert.equal(ctx.cacheable(answer("x", { url: "https://team.cloudflareaccess.com/cdn-cgi/access/login" })), false);
  assert.equal(ctx.cacheable(answer("x", { status: 404 })), false);
  assert.equal(ctx.cacheable(answer("x", { status: 302 })), false);
  assert.equal(ctx.cacheable(null), false);
});

test("API, action, file and POST requests are never answered or cached by the worker", async () => {
  const sw = load();
  for (const [url, init] of [["https://paper.example/api/people"], ["https://paper.example/api/watchlist", { method: "PUT" }],
    ["https://paper.example/act/abc.def", { method: "POST", mode: "navigate" }], ["https://paper.example/files/x.log", { mode: "navigate" }],
    ["https://paper.example/", { method: "POST", mode: "navigate" }]]) {
    const { handled } = await dispatch(sw, url, init);
    assert.equal(handled, false, url);
  }
  assert.deepEqual(sw.fetched, []);
  assert.deepEqual(await cached(sw), []);
});

test("a page that redirects to Access sign-in is handed to the browser and not kept", async () => {
  const redirect = answer("", { status: 0, type: "opaqueredirect" });
  const sw = load({ net: async () => redirect });
  const { handled, response } = await dispatch(sw, "https://paper.example/p/octo/", { mode: "navigate" });
  assert.equal(handled, true);
  assert.equal(response, redirect);
  assert.deepEqual(await cached(sw), []);
});

test("a signed-out page is not kept, even when a saved copy exists", async () => {
  let net = async () => answer("<h1>Octo</h1>");
  const sw = load({ net: (...a) => net(...a) });
  await dispatch(sw, "https://paper.example/p/octo/", { mode: "navigate" });
  assert.deepEqual(await cached(sw), ["https://paper.example/p/octo/"]);
  // The session expired: Access redirects. The browser gets the redirect, not the saved page.
  const redirect = answer("", { status: 0, type: "opaqueredirect" });
  net = async () => redirect;
  const { response } = await dispatch(sw, "https://paper.example/p/octo/", { mode: "navigate" });
  assert.equal(response, redirect);
  const store = await sw.caches.open("pr-post-b1");
  assert.equal((await store.match("https://paper.example/p/octo/")).body, "<h1>Octo</h1>");
  // A login page reached through a followed redirect is not kept either.
  net = async () => answer("<h1>Sign in</h1>", { redirected: true, url: "https://team.cloudflareaccess.com/cdn-cgi/access/login" });
  await dispatch(sw, "https://paper.example/archive/", { mode: "navigate" });
  assert.deepEqual(await cached(sw), ["https://paper.example/p/octo/"]);
});

test("pages always come from the network while it answers, however slowly", async () => {
  let release;
  const sw = load({ net: async () => new Promise((r) => { release = () => r(answer("fresh")); }) });
  const store = await sw.caches.open("pr-post-b1");
  await store.put("https://paper.example/p/octo/", answer("saved"));
  const pending = dispatch(sw, "https://paper.example/p/octo/", { mode: "navigate" });
  await new Promise((r) => setTimeout(r, 20));
  release();
  assert.equal((await pending).response.body, "fresh");
});

test("pages are network first, and come from the cache when the network is down", async () => {
  let online = true;
  const sw = load({ net: async (url) => { if (!online) throw new TypeError("Failed to fetch"); return answer(`page ${url}`); } });
  await install(sw);
  assert.ok((await cached(sw)).includes("https://paper.example/"));
  const fresh = await dispatch(sw, "https://paper.example/?utm=x", { mode: "navigate" });
  assert.equal(fresh.response.body, "page https://paper.example/?utm=x");
  online = false;
  const offline = await dispatch(sw, "https://paper.example/?utm=y", { mode: "navigate" });
  assert.equal(offline.response.body, "page https://paper.example/?utm=x");
  const missing = await dispatch(sw, "https://paper.example/never/", { mode: "navigate" });
  assert.equal(missing.response.body, "page https://paper.example/offline.html");
});

test("install keeps the shell, the offline page and the newest issue, but no redirects", async () => {
  const sw = load({
    net: async (url) => {
      if (url.endsWith("/p/octo/")) return answer("signin", { redirected: true, url: "https://team.cloudflareaccess.com/login" });
      if (url === FONT_CSS) return answer("/* cyrillic */\n@font-face{src:url(https://fonts.gstatic.com/c.woff2)}\n/* latin */\n@font-face{src:url(https://fonts.gstatic.com/l.woff2) format('woff2')}", { type: "cors" });
      if (url.startsWith("https://fonts.gstatic.com/")) return answer("font", { type: "cors" });
      return answer("ok");
    },
  });
  await install(sw);
  assert.deepEqual((await cached(sw)).sort(), [
    "https://fonts.gstatic.com/l.woff2",
    FONT_CSS,
    "https://paper.example/",
    "https://paper.example/assets/style.css?v=1",
    "https://paper.example/manifest.webmanifest",
    "https://paper.example/offline.html",
  ].sort());
  assert.deepEqual(await sw.caches.keys(), ["pr-post-b1"]);
});

test("an install that cannot keep the shell fails, so the previous version stays", async () => {
  const sw = load({ build: "new", net: async (url) => { if (url.endsWith("style.css?v=1")) throw new TypeError("Failed to fetch"); return answer("ok"); } });
  sw.caches.stores.set("pr-post-old", new Map([["https://paper.example/", answer("old")]]));
  await assert.rejects(install(sw));
  assert.deepEqual(await sw.caches.keys(), ["pr-post-old"]);
  // The same when the front page redirects to Access sign-in during the install.
  const signedOut = load({ build: "new", net: async (url) => (url === "https://paper.example/" ? answer("", { redirected: true, url: "https://team.cloudflareaccess.com/" }) : answer("ok")) });
  await assert.rejects(install(signedOut));
  assert.deepEqual(await signedOut.caches.keys(), []);
});

test("a new build replaces the old cache when it activates", async () => {
  const old = load({ build: "old" });
  await install(old);
  const sw = load({ build: "new" });
  sw.caches.stores.set("pr-post-old", new Map([["https://paper.example/", answer("old")]]));
  sw.caches.stores.set("someone-else", new Map());
  await install(sw);
  const waits = [];
  sw.handlers.activate({ waitUntil: (p) => waits.push(p) });
  await Promise.all(waits);
  assert.deepEqual((await sw.caches.keys()).sort(), ["pr-post-new", "someone-else"]);
});

test("assets and font files come from the cache first; the font stylesheet refreshes in the background", async () => {
  let n = 0;
  const sw = load({ net: async (url) => answer(`v${++n} ${url}`, { type: url.includes("fonts.") ? "cors" : "basic" }) });
  const a1 = await dispatch(sw, "https://paper.example/assets/style.css?v=1");
  const a2 = await dispatch(sw, "https://paper.example/assets/style.css?v=1");
  assert.equal(a1.response.body, a2.response.body);
  const f1 = await dispatch(sw, "https://fonts.gstatic.com/s/a.woff2");
  const before = sw.fetched.length;
  const f2 = await dispatch(sw, "https://fonts.gstatic.com/s/a.woff2");
  assert.equal(f2.response.body, f1.response.body);
  assert.equal(sw.fetched.length, before, "a saved font file is not fetched again");
  const c1 = await dispatch(sw, FONT_CSS);
  const c2 = await dispatch(sw, FONT_CSS);
  assert.equal(c2.response.body, c1.response.body); // the saved stylesheet answers at once
  const c3 = await dispatch(sw, FONT_CSS);
  assert.notEqual(c3.response.body, c1.response.body); // ...while the background fetch refreshed it
});
