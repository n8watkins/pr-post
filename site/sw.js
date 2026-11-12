// pr-post service worker. scripts/build_site.py fills in the build id and the
// lists below and writes this file to the site's root as sw.js; every publish
// that changes the site gets a new build id, so a new cache replaces the old.
//
// What it does:
//   - the app shell (stylesheet, scripts, icons, manifest, fonts) comes from the cache;
//   - pages come from the network, and from the cache only when the network is
//     down, so the newest issue (cached at install) opens offline;
//   - everything else goes straight to the network, untouched: any request that is
//     not a GET, /api/*, /act/*, /files/*, /cdn-cgi/*, other sites, and Cloudflare
//     Access. A page that redirects to Access sign-in is handed to the browser as
//     the redirect, so signing in works as if this worker were not there.
//   - only plain 200 answers are cached: never a redirect, an opaque answer or
//     anything from *.cloudflareaccess.com, so a sign-in page is never kept.
"use strict";

const BUILD = "__BUILD__";
const CACHE = "pr-post-" + BUILD;
const SHELL = __SHELL__; // relative to the scope
const PAGES = __PAGES__; // the newest issue: front page, its people, back issues
const OFFLINE = "offline.html";
const FONT_CSS = __FONT_CSS__;
const FONT_HOSTS = ["fonts.googleapis.com", "fonts.gstatic.com"];
const PASS = /^(api|act|files|cdn-cgi)(\/|$)/;

const isAccess = (url) => /(^|\.)cloudflareaccess\.com$/i.test(new URL(url).hostname);

/** How to answer a request: "page", "asset", "font", or "network" (not handled at all). */
function route(request, scope) {
  if (request.method !== "GET") return "network";
  if (request.headers && request.headers.get && request.headers.get("range")) return "network";
  const url = new URL(request.url);
  if (isAccess(url.href)) return "network";
  if (url.origin !== scope.origin) return FONT_HOSTS.includes(url.hostname) ? "font" : "network";
  if (!url.pathname.startsWith(scope.pathname)) return "network";
  const rest = url.pathname.slice(scope.pathname.length);
  if (PASS.test(rest)) return "network";
  if (rest.startsWith("assets/") || rest === "manifest.webmanifest") return "asset";
  // Pages are folders ("p/octo/") or .html files; opening feed.json or sw.js directly is left alone.
  if (request.mode === "navigate" && (rest === "" || rest.endsWith("/") || rest.endsWith(".html"))) return "page";
  return "network";
}

/** Only a plain 200 from this site or a CORS font host is kept. */
function cacheable(response) {
  return Boolean(response) && response.status === 200 && !response.redirected
    && (response.type === "basic" || response.type === "cors" || response.type === "default")
    && !(response.url && isAccess(response.url));
}

const scopeUrl = () => new URL(self.registration.scope);
const pageKey = (url) => { const u = new URL(url); u.search = ""; u.hash = ""; return u.href; };

/** Fetch `url` into the cache; true when it was kept. */
async function keep(cache, url, init) {
  try {
    const response = await fetch(url, { credentials: "same-origin", cache: "no-cache", ...init });
    if (!cacheable(response)) return false;
    await cache.put(url, response);
    return true;
  } catch { return false; }
}

/** Google Fonts serves one file per script; keep the Latin ones so the paper reads the same offline. */
async function keepFonts(cache) {
  if (!FONT_CSS) return;
  try {
    const response = await fetch(FONT_CSS, { mode: "cors", credentials: "omit" });
    if (!cacheable(response)) return;
    const css = await response.clone().text();
    await cache.put(FONT_CSS, response);
    const files = [];
    for (const block of css.split("/*").slice(1)) {
      if (!/^\s*latin\s*\*\//.test(block)) continue;
      const m = block.match(/url\((https:\/\/fonts\.gstatic\.com\/[^)\s]+)\)/);
      if (m) files.push(m[1]);
    }
    await Promise.all(files.map((f) => keep(cache, f, { mode: "cors", credentials: "omit", cache: "default" })));
  } catch { /* fonts fall back to the stylesheet's local faces */ }
}

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    const scope = scopeUrl();
    const at = (p) => new URL(p, scope).href;
    // The shell, the offline page and the front page must all be kept, or this install fails and the
    // previous worker, with its complete cache, stays in charge; the next visit tries again.
    // A session that ran out redirects to Access, so a signed-out install fails the same way.
    const needed = [...SHELL, OFFLINE, PAGES[0]];
    const [ok] = await Promise.all([
      Promise.all(needed.map((p) => keep(cache, at(p)))).then((kept) => kept.every(Boolean)),
      Promise.all(PAGES.slice(1).map((p) => keep(cache, at(p)))), // the other pages: best effort
      keepFonts(cache),
    ]);
    if (!ok) {
      await caches.delete(CACHE);
      throw new Error("Could not keep the app shell; keeping the previous version.");
    }
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const name of await caches.keys()) {
      if (name.startsWith("pr-post-") && name !== CACHE) await caches.delete(name);
    }
    await self.clients.claim();
  })());
});

/** Fetch, and keep a cacheable answer under `key`; the write finishes even after the answer is used. */
function fetchAndKeep(event, cache, key, request, init) {
  let written = Promise.resolve();
  const network = fetch(request, init).then((response) => {
    if (cacheable(response)) written = cache.put(key, response.clone()).catch(() => {});
    return response;
  });
  event.waitUntil(network.then(() => written, () => {}));
  return network;
}

async function page(event) {
  const request = event.request;
  const cache = await caches.open(CACHE);
  try {
    // Whatever the network answers is handed on, redirects included: an Access sign-in redirect must
    // reach the browser, so a saved page is never shown to someone whose session ran out while online.
    return await fetchAndKeep(event, cache, pageKey(request.url), request);
  } catch {
    return (await cache.match(pageKey(request.url))) || (await cache.match(new URL(OFFLINE, scopeUrl()).href)) || Response.error();
  }
}

async function asset(event) {
  const cache = await caches.open(CACHE);
  const saved = await cache.match(event.request);
  return saved || fetchAndKeep(event, cache, event.request, event.request);
}

/** Font files never change at a given URL, so they come from the cache; the stylesheet is refreshed in the background. */
async function font(event) {
  const cache = await caches.open(CACHE);
  const url = event.request.url;
  const saved = await cache.match(url);
  if (saved && new URL(url).hostname === "fonts.gstatic.com") return saved;
  const network = fetchAndKeep(event, cache, url, url, { mode: "cors", credentials: "omit" });
  return saved || network;
}

self.addEventListener("fetch", (event) => {
  const how = route(event.request, scopeUrl());
  if (how === "page") event.respondWith(page(event));
  else if (how === "asset") event.respondWith(asset(event));
  else if (how === "font") event.respondWith(font(event));
  // "network": no respondWith, so the browser fetches it exactly as it would without this worker.
});
