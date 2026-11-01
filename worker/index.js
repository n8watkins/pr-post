// pr-post private mode: the paper on Cloudflare Workers, behind sign-in.
//
// Every request must be signed in before anything is served:
//   1. Cloudflare Access (preferred): set ACCESS_TEAM_DOMAIN and ACCESS_AUD;
//      the Worker verifies the Access token on every request.
//   2. Until Access is set up: HTTP Basic auth with the SITE_PASSWORD secret.
//   3. Neither configured: the site refuses to serve.
// /api/watchlist lets the settings page commit watchlist.toml via GitHub.

import { decodeJwt, checkClaims, validatePeople, replacePeople } from "./lib.js";

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
    "connect-src 'self'",
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

function timingSafeEqual(a, b) {
  const ea = new TextEncoder().encode(a);
  const eb = new TextEncoder().encode(b);
  if (ea.length !== eb.length) return false;
  return crypto.subtle.timingSafeEqual(ea, eb);
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
    return { response: withHeaders(new Response("This paper is private.", { status: 401, headers: { "WWW-Authenticate": 'Basic realm="pr-post", charset="UTF-8"' } })) };
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
  if (!env.GITHUB_TOKEN) return json(501, { error: "Saving needs a GitHub token on the Worker. See docs/private-mode.md." });

  let people;
  try {
    const text = await request.text();
    if (text.length > 100_000) return json(413, { error: "That list is too large." });
    people = validatePeople(JSON.parse(text));
  } catch (err) {
    return json(400, { error: err.message });
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

export default {
  async fetch(request, env) {
    const auth = await authorize(request, env);
    if (auth.response) return auth.response;
    const url = new URL(request.url);
    if (url.pathname === "/api/watchlist") {
      if (request.method === "PUT") return saveWatchlist(request, env, auth.user);
      return json(405, { error: "Use PUT." });
    }
    if (url.pathname.startsWith("/api/")) return json(404, { error: "Not found." });
    return withHeaders(await env.ASSETS.fetch(request));
  },
};
