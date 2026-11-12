// pr-post private mode: the paper on Cloudflare Workers, behind sign-in.
//
// Every request must be signed in before anything is served:
//   1. Cloudflare Access (preferred): set ACCESS_TEAM_DOMAIN and ACCESS_AUD;
//      the Worker verifies the Access token on every request.
//   2. Until Access is set up: HTTP Basic auth with the SITE_PASSWORD secret
//      (the browser's prompt names SITE_TITLE, default "pr-post").
//   3. Neither configured: the site refuses to serve.
// /api/watchlist lets the settings page save the watch list: into D1 when the
// Worker has the paper's storage (worker/store.js), else into watchlist.toml
// on GitHub.
// Exceptions to sign-in:
//   /act/<token>: the one-click links in the Who to follow email
//     (scripts/act_lib.py). The signed, expiring token is the credential, a GET
//     only shows a page, and only that page's POST acts.
//   /api/paper/* and /api/site/* with "Authorization: Bearer <token>": the jobs
//     (JOBS_TOKEN) and the writer (WRITER_TOKEN) read and write the paper's
//     storage, each within its own lane (worker/store.js).
//   /api/email/send with JOBS_TOKEN: the jobs' digest and Who to follow
//     emails, sent through mailroom or the Worker's own binding (worker/email.js).
// With an R2 bucket bound as STORE the site is served from it (SITE_PREFIX);
// without one, from the static assets deployed with the Worker.

import { decodeJwt, checkClaims, validatePeople, replacePeople, realm, verifyAction, signAction, UNDO, timingSafeEqual, bearerRole } from "./lib.js";
import { handleStore, serveSite, saveActivePeople, actionUsed, serveOwnerFile } from "./store.js";
import { handleJobs, scheduled, dispatch, createRequest } from "./jobs.js";
import { handlePeople } from "./people.js";
import { handleEmailSend } from "./email.js";

const SECURITY_HEADERS = {
  "X-Robots-Tag": "noindex, nofollow",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "same-origin",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
  "Content-Security-Policy": [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src https://fonts.gstatic.com",
    "img-src 'self' data: https://avatars.githubusercontent.com https://i.ytimg.com",
    // The service worker (sw.js) keeps the Google Fonts files for reading offline.
    "connect-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com",
    "worker-src 'self'",
    "manifest-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "form-action 'self'",
  ].join("; "),
};

function withHeaders(response, extra = {}) {
  const r = new Response(response.body, response);
  for (const [k, v] of Object.entries({ ...SECURITY_HEADERS, ...extra })) r.headers.set(k, v);
  r.headers.set("Cache-Control", "private, no-store");
  return r;
}

const json = (status, body) => withHeaders(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

let jwksCache = { at: 0, keys: [] };
async function accessKeys(teamDomain) {
  if (Date.now() - jwksCache.at < 3600_000 && jwksCache.keys.length) return jwksCache.keys;
  const res = await fetch(`https://${teamDomain}/cdn-cgi/access/certs`);
  if (!res.ok) throw new Error(`Access certs ${res.status}`);
  jwksCache = { at: Date.now(), keys: (await res.json()).keys || [] };
  return jwksCache.keys;
}

async function verifyAccess(request, env) {
  const cookie = (request.headers.get("Cookie") || "").match(/(?:^|;\s*)CF_Authorization=([^;]+)/);
  const token = request.headers.get("Cf-Access-Jwt-Assertion") || (cookie && cookie[1]);
  if (!token) return null;
  const { header, payload, signed, signature } = decodeJwt(token);
  if (header.alg !== "RS256") return null;
  const jwk = (await accessKeys(env.ACCESS_TEAM_DOMAIN)).find((k) => k.kid === header.kid);
  if (!jwk) return null;
  const key = await crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
  const ok = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, signature, new TextEncoder().encode(signed));
  if (!ok || checkClaims(payload, { aud: env.ACCESS_AUD, teamDomain: env.ACCESS_TEAM_DOMAIN })) return null;
  const allowed = (env.ALLOWED_EMAILS || "").split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
  if (allowed.length && !allowed.includes(String(payload.email || "").toLowerCase())) return null;
  return { who: payload.email || "access" };
}

function verifyPassword(request, env) {
  const m = (request.headers.get("Authorization") || "").match(/^Basic\s+(.+)$/i);
  if (!m) return null;
  let decoded;
  try { decoded = atob(m[1]); } catch { return null; }
  const password = decoded.slice(decoded.indexOf(":") + 1);
  return timingSafeEqual(password, env.SITE_PASSWORD) ? { who: "password" } : null;
}

async function authorize(request, env) {
  if (env.ACCESS_TEAM_DOMAIN && env.ACCESS_AUD) {
    try {
      const user = await verifyAccess(request, env);
      if (user) return { user };
    } catch (err) {
      console.error("Access verification failed", err);
    }
    return { response: withHeaders(new Response("Sign in through Cloudflare Access to read this paper.", { status: 403 })) };
  }
  if (env.SITE_PASSWORD) {
    const user = verifyPassword(request, env);
    if (user) return { user };
    return { response: withHeaders(new Response("This paper is private.", { status: 401, headers: { "WWW-Authenticate": `Basic realm="${realm(env)}", charset="UTF-8"` } })) };
  }
  return { response: withHeaders(new Response("This paper is locked: no sign-in is configured yet. See docs/private-mode.md.", { status: 503 })) };
}

async function github(env, path, init = {}) {
  return fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${env.GITHUB_TOKEN}`, Accept: "application/vnd.github+json", "User-Agent": "pull-request-post", ...(init.headers || {}) },
  });
}

async function saveWatchlist(request, env, user) {
  // Same-origin only: a signed-in browser must not be tricked into saving from another site.
  const origin = request.headers.get("Origin");
  if (!origin || new URL(origin).host !== new URL(request.url).host) return json(403, { error: "Saving is only allowed from this site." });
  if (!(request.headers.get("Content-Type") || "").startsWith("application/json")) return json(415, { error: "Expected JSON." });
  if (!env.DB && !env.GITHUB_TOKEN) return json(501, { error: "Saving needs a GitHub token on the Worker. See docs/private-mode.md." });

  let people;
  try {
    const text = await request.text();
    if (text.length > 100_000) return json(413, { error: "That list is too large." });
    people = validatePeople(JSON.parse(text));
  } catch (err) {
    return json(400, { error: err.message });
  }
  if (env.DB) {
    await saveActivePeople(env, people, user.who);
    console.log(`watch list saved by ${user.who}: ${people.length} people`);
    return json(200, { ok: true });
  }

  const path = env.WATCHLIST_PATH || "watchlist.toml";
  const current = await github(env, `contents/${path}?ref=main`);
  if (!current.ok) return json(502, { error: `Could not read the watch list from GitHub (${current.status}).` });
  const file = await current.json();
  const toml = new TextDecoder().decode(Uint8Array.from(atob(file.content.replace(/\n/g, "")), (c) => c.charCodeAt(0)));
  let updated;
  try { updated = replacePeople(toml, people); } catch (err) { return json(500, { error: err.message }); }
  if (updated === toml) return json(200, { ok: true, unchanged: true });

  const bytes = new TextEncoder().encode(updated);
  let binary = "";
  bytes.forEach((b) => { binary += String.fromCharCode(b); });
  const put = await github(env, `contents/${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: `settings: update watch list (${people.length} people)`, content: btoa(binary), sha: file.sha, branch: "main" }),
  });
  if (put.status === 409) return json(409, { error: "The watch list changed since this page loaded. Reload and try again." });
  if (!put.ok) return json(502, { error: `GitHub refused the change (${put.status}).` });
  console.log(`watch list saved by ${user.who}: ${people.length} people`);
  return json(200, { ok: true });
}


// ---------- one-click actions from the Who to follow email ----------

const ACT_PATH = /^\/act\/([A-Za-z0-9_-]{8,320}\.[A-Za-z0-9_-]{20,90})\/?$/;
const ACT_SCRIPT = "if(!navigator.webdriver){setTimeout(function(){var f=document.getElementById('act');if(f&&!f.dataset.sent){f.dataset.sent='1';f.submit();}},500);}"
  + "document.addEventListener('submit',function(e){var b=e.target.querySelector('button');if(b){b.disabled=true;b.textContent='Working...';}});";
let actScriptHash;
async function scriptHash() {
  if (!actScriptHash) {
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(ACT_SCRIPT)));
    actScriptHash = btoa(String.fromCharCode(...digest));
  }
  return actScriptHash;
}

const WORDS = {
  follow: { ask: (l) => `Follow @${l} on GitHub?`, go: "Follow", done: (l) => `Followed @${l} ✓`, note: "GitHub applies it within a minute." },
  unfollow: { ask: (l) => `Unfollow @${l} on GitHub?`, go: "Unfollow", done: (l) => `Unfollowed @${l} ✓`, note: "GitHub applies it within a minute." },
  add: { ask: (l) => `Add @${l} to the paper?`, go: "Add to PR Post", done: (l) => `Adding @${l} ✓`,
    note: "A pull request with their draft watch-list entry and profile opens within a minute or two. Nothing changes until you merge it." },
  unadd: { ask: (l) => `Remove the draft for @${l}?`, go: "Remove the draft", done: (l) => `Removed the draft for @${l} ✓`,
    note: "Its pull request closes and its branch is deleted within a minute. A draft you already merged stays; remove them from the settings page." },
};

const escHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

async function actPage(env, status, title, body, { script = false } = {}) {
  const csp = ["default-src 'none'", "style-src 'unsafe-inline'", "img-src https://avatars.githubusercontent.com", "form-action 'self'",
    "base-uri 'none'", "frame-ancestors 'none'", script ? `script-src 'sha256-${await scriptHash()}'` : "script-src 'none'"].join("; ");
  const paper = escHtml(env.SITE_TITLE || "pr-post");
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><meta name="referrer" content="no-referrer"><title>${escHtml(title)} · ${paper}</title>
<style>
:root{--bg:#e9e8e3;--sheet:#f8f7f3;--ink:#17171a;--soft:#46464c;--rule:#cfcdc5;--extra:#c4161c;color-scheme:light dark}
@media (prefers-color-scheme:dark){:root{--bg:#0f0f11;--sheet:#18181c;--ink:#ecebe6;--soft:#c4c3c9;--rule:#34343a;--extra:#ff5459}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
main{max-width:520px;margin:9vh auto;padding:26px 24px 22px;background:var(--sheet);border:1px solid var(--rule)}
.k{font:700 17px Georgia,serif;border-bottom:3px double var(--ink);padding-bottom:10px;margin-bottom:18px}
h1{font:700 28px/1.2 Georgia,serif;margin:0 0 10px;overflow-wrap:anywhere}p{margin:0 0 14px;color:var(--soft)}
.row{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-top:18px}
button,.btn{font:700 16px/1 inherit;font-family:inherit;padding:13px 20px;border:1.5px solid var(--ink);background:var(--ink);color:var(--sheet);border-radius:6px;cursor:pointer;text-decoration:none}
.btn.ghost{background:transparent;color:var(--ink)}button:disabled{opacity:.6;cursor:default}
a{color:inherit;text-decoration-color:var(--extra);text-underline-offset:3px}
@media (max-width:560px){main{margin:0;border:0;min-height:100vh}}
</style></head><body><main><div class="k">${paper}</div>${body}</main>${script ? `<script>${ACT_SCRIPT}</script>` : ""}</body></html>`;
  return new Response(html, { status, headers: {
    "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", "Content-Security-Policy": csp,
    "X-Robots-Tag": "noindex, nofollow", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
  } });
}

async function actionsApi(env, path, init = {}) {
  return fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${env.ACTIONS_TOKEN}`, Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "pull-request-post", ...(init.headers || {}) },
  });
}

/** Why this action cannot run yet, as page text, or null when it is set up. */
async function notReady(env, action) {
  if (!env.ACTIONS_TOKEN || !env.GITHUB_REPO) return "One-click actions aren't set up yet: the Worker has no ACTIONS_TOKEN to start the workflow. The setup steps are in docs/cloudflare-setup.md.";
  // With D1 storage the dispatch token only has Actions access; the job itself reports a missing follow token.
  if (env.DB) return null;
  if (action === "follow" || action === "unfollow") {
    const r = await actionsApi(env, "actions/secrets/FOLLOW_GITHUB_TOKEN");
    if (r.status === 404) return "Follow isn't set up yet: the FOLLOW_GITHUB_TOKEN secret is missing, so nothing was followed. The setup steps are in docs/cloudflare-setup.md; this link keeps working until it expires.";
    if (!r.ok) return `Could not check the setup with GitHub (${r.status}). Nothing was done; try again in a minute.`;
  }
  return null;
}

/** True when the paper's action log already has this link's nonce (the act workflow refuses it too). */
async function alreadyUsed(env, nonce) {
  if (env.DB) return actionUsed(env, nonce);
  const r = await actionsApi(env, "contents/follow/actions.jsonl?ref=main", { headers: { Accept: "application/vnd.github.raw+json" } });
  if (!r.ok) return false;
  return (await r.text()).includes(`"nonce":"${nonce}"`);
}

async function handleAct(request, env, url) {
  const m = url.pathname.match(ACT_PATH);
  if (!m) return actPage(env, 404, "Not a link", "<h1>That link isn't valid</h1><p>Open the button from the email again.</p>");
  if (!env.ACTION_SIGNING_KEY) return actPage(env, 503, "Not set up", "<h1>One-click actions aren't set up yet</h1><p>The Worker has no ACTION_SIGNING_KEY. The setup steps are in docs/cloudflare-setup.md.</p>");
  if (request.method !== "GET" && request.method !== "POST") return actPage(env, 405, "Not allowed", "<h1>Not allowed</h1>");
  let p;
  try {
    p = await verifyAction(env.ACTION_SIGNING_KEY, m[1]);
  } catch (err) {
    const expired = err.message === "expired";
    return actPage(env, expired ? 410 : 400, expired ? "Expired" : "Not valid",
      expired ? "<h1>This link has expired</h1><p>Links in the email work for seven days. Use the Who to follow page on the site instead.</p>"
        : "<h1>That link isn't valid</h1><p>Open the button from the email again.</p>");
  }
  const w = WORDS[p.a];
  const login = escHtml(p.l);
  const why = await notReady(env, p.a);
  if (why) return actPage(env, 503, "Not set up", `<h1>${p.a.includes("follow") ? "Follow isn't set up yet" : "Not set up yet"}</h1><p>${escHtml(why)}</p>`);
  if (await alreadyUsed(env, p.n)) {
    return actPage(env, 409, "Already used", `<h1>This link was already used</h1><p>${escHtml(w.done(p.l))} earlier. Each link works once.</p>`);
  }

  if (request.method === "GET") {
    // A bare GET never acts: email scanners open every link. The page acts through a POST.
    return actPage(env, 200, w.ask(p.l), `<h1>${escHtml(w.ask(p.l))}</h1><p>Doing it now. If nothing happens, press the button.</p>
<form id="act" method="post"><div class="row"><button type="submit">${escHtml(w.go)}</button><a class="btn ghost" href="https://github.com/${login}">GitHub profile</a></div></form>`, { script: true });
  }

  const origin = request.headers.get("Origin");
  if (!origin || origin === "null" || new URL(origin).host !== url.host) {
    return actPage(env, 403, "Not allowed", "<h1>Not allowed</h1><p>Use the button on this site's own page.</p>");
  }
  // With D1 storage the run gets an opaque request id: workflow inputs are public on a public repository.
  const workflow = env.DB ? "paper-act.yml" : "act.yml";
  const status = env.DB
    ? await dispatch(env, workflow, { request: await createRequest(env, "act", m[1]) })
    : (await actionsApi(env, "actions/workflows/act.yml/dispatches", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ref: "main", inputs: { token: m[1] } }),
    })).status;
  if (status < 200 || status >= 300) {
    console.error(`act dispatch failed: ${status}`);
    return actPage(env, 502, "Failed", `<h1>That didn't work</h1><p>GitHub refused to start the workflow (${status}). Nothing was done.</p>`);
  }
  console.log(`act: ${p.a} ${p.l}`);
  const undo = UNDO[p.a] ? `<a class="btn ghost" href="/act/${await signAction(env.ACTION_SIGNING_KEY, UNDO[p.a], p.l)}">Undo</a>` : "";
  return actPage(env, 200, w.done(p.l), `<h1>${escHtml(w.done(p.l))}</h1><p>${escHtml(w.note)}</p>
<div class="row">${undo}<a class="btn ghost" href="https://github.com/${login}">GitHub profile</a></div>
<p style="margin-top:18px;font-size:14px">Every action is logged in the paper. <a href="https://github.com/${escHtml(env.GITHUB_REPO)}/actions/workflows/${workflow}">See the runs</a></p>`);
}

export default {
  scheduled,
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (path === "/act" || path.startsWith("/act/")) return handleAct(request, env, new URL(request.url));
    const url = new URL(request.url);
    const machine = bearerRole(request, env);
    if (machine) {
      // A bearer token is only ever for the storage API; a wrong one is refused outright.
      if (machine === "invalid") return json(401, { error: "That token is not valid." });
      if (url.pathname === "/api/email/send") return withHeaders(await handleEmailSend(request, env, machine));
      const jobs = await handleJobs(request, env, url, machine);
      if (jobs) return withHeaders(jobs);
      return withHeaders((await handleStore(request, env, url, machine)) || new Response(JSON.stringify({ error: "Not found." }), { status: 404, headers: { "Content-Type": "application/json" } }));
    }
    const auth = await authorize(request, env);
    if (auth.response) return auth.response;
    if (url.pathname === "/api/watchlist") {
      if (request.method === "PUT") return saveWatchlist(request, env, auth.user);
      return json(405, { error: "Use PUT." });
    }
    const people = await handlePeople(request, env, url, auth.user);
    if (people) return withHeaders(people);
    if (url.pathname.startsWith("/api/")) {
      const stored = await handleStore(request, env, url, "owner");
      return stored ? withHeaders(stored) : json(404, { error: "Not found." });
    }
    if (url.pathname.startsWith("/files/") && env.STORE) return withHeaders(await serveOwnerFile(request, env, url));
    // With D1 the watch list is edited on the People page; old links to the settings page land there.
    if (env.DB && /^\/settings\/?$/.test(url.pathname)) return withHeaders(new Response(null, { status: 308, headers: { Location: "/people/" } }));
    if (env.STORE) return withHeaders(await serveSite(request, env));
    return withHeaders(await env.ASSETS.fetch(request));
  },
};
