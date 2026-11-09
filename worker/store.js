// The private paper's storage, for a Worker with a D1 database (DB) and an R2
// bucket (STORE). docs/private-mode.md has the design; scripts/store.py is the
// client the jobs and the writer use.
//
// R2 holds files: the paper's folders under "paper/" (data/, stats/, briefs/,
// reports/, editions/, summaries/, follow/ mini reports, notes/, logs/,
// previews/, backups/) and the built site under SITE_PREFIX (default "site/").
// Each object carries its SHA-256 in customMetadata.sha256, so clients upload
// only what changed.
//
// D1 holds structured data: the watch list (people, settings), profiles, the
// Who to follow state and the one-click action log. The client turns them back
// into watchlist.toml, profiles/<id>.md, follow/state.json and
// follow/actions.jsonl, so the engine's scripts work on a plain folder.
//
// Who may do what:
//   owner  (signed in through the site)  read everything; write the watch list
//   jobs   (JOBS_TOKEN, GitHub Actions)  read and write everything except settings
//   writer (WRITER_TOKEN, the Claude routine)  read briefs, data, reports,
//          editions, summaries, the watch list and profiles; write reports/ and
//          editions/, and add to the end of a profile
// Each bulk write is one SQL statement (json_each), so a request stays far
// below the Workers free plan's per-request query limit.

export const PATH_RE = /^[A-Za-z0-9_-][A-Za-z0-9._-]*(?:\/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$/;
const LOGIN_RE = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/;
const ID_RE = /^[a-z0-9](?:[a-z0-9-]{0,38})$/;
const NONCE_RE = /^[0-9a-f]{32}$/;
const SHA_RE = /^[0-9a-f]{64}$/;

// Folders of the paper kept in R2. follow/state.json and follow/actions.jsonl
// live in D1 instead, and watchlist.toml and profiles/ are rendered from D1.
export const PAPER_DIRS = ["data", "stats", "briefs", "reports", "editions", "summaries", "follow", "notes", "logs", "previews", "backups"];
const IN_D1 = new Set(["follow/state.json", "follow/actions.jsonl"]);
export const WRITER_READ = ["briefs", "data", "reports", "editions", "summaries"];
export const WRITER_WRITE = ["reports", "editions"];
const MAX_FILE = 20 * 1024 * 1024;
const MAX_JSON = 4 * 1024 * 1024;
const MAX_PROFILE = 64 * 1024;
const MAX_PROFILE_GROWTH = 4 * 1024;

const now = () => new Date().toISOString().replace(/\.\d{3}Z$/, "Z");

function reply(status, body) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

export function topDir(path) {
  return path.split("/")[0];
}

/** May `role` read (write = false) or write (write = true) this paper path? */
export function paperAllowed(role, path, write) {
  if (!PATH_RE.test(path) || IN_D1.has(path)) return false;
  const dir = topDir(path);
  if (!PAPER_DIRS.includes(dir) || path === dir) return false;
  if (role === "jobs") return true;
  if (role === "owner") return !write;
  if (role === "writer") return (write ? WRITER_WRITE : WRITER_READ).includes(dir);
  return false;
}

export function siteKey(env, path) {
  return (env.SITE_PREFIX || "site/") + path;
}

async function sha256Hex(buf) {
  const d = new Uint8Array(await crypto.subtle.digest("SHA-256", buf));
  return [...d].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function listAll(bucket, prefix) {
  const out = [];
  let cursor;
  do {
    const page = await bucket.list({ prefix, cursor, include: ["customMetadata"] });
    for (const o of page.objects) {
      out.push({ path: o.key.slice(prefix.length), size: o.size, sha256: (o.customMetadata && o.customMetadata.sha256) || "" });
    }
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);
  return out;
}

async function putFile(request, bucket, key) {
  const declared = (request.headers.get("X-Content-Sha256") || "").toLowerCase();
  if (!SHA_RE.test(declared)) return reply(400, { error: "Send the file's SHA-256 in X-Content-Sha256." });
  const length = Number(request.headers.get("Content-Length") || "0");
  if (length > MAX_FILE) return reply(413, { error: "That file is too large." });
  const body = await request.arrayBuffer();
  if (body.byteLength > MAX_FILE) return reply(413, { error: "That file is too large." });
  if ((await sha256Hex(body)) !== declared) return reply(400, { error: "The file does not match its SHA-256." });
  await bucket.put(key, body, {
    customMetadata: { sha256: declared },
    httpMetadata: { contentType: request.headers.get("Content-Type") || "application/octet-stream" },
  });
  return reply(200, { ok: true, sha256: declared, size: body.byteLength });
}

async function getFile(bucket, key) {
  const obj = await bucket.get(key);
  if (!obj) return reply(404, { error: "No such file." });
  return new Response(obj.body, { headers: { "Content-Type": "application/octet-stream", "X-Content-Sha256": (obj.customMetadata && obj.customMetadata.sha256) || "" } });
}

async function readJson(request) {
  const text = await request.text();
  if (text.length > MAX_JSON) throw new Error("That request is too large.");
  return JSON.parse(text);
}

// ---------- the watch list ----------

export async function readWatchlist(env, { activeOnly = false } = {}) {
  const [people, settings] = await env.DB.batch([
    env.DB.prepare(`SELECT id, status, position, entry FROM people ${activeOnly ? "WHERE status = 'active' " : ""}ORDER BY position, id`),
    env.DB.prepare("SELECT section, value FROM settings ORDER BY section"),
  ]);
  return {
    settings: Object.fromEntries(settings.results.map((r) => [r.section, JSON.parse(r.value)])),
    people: people.results.map((r) => ({ id: r.id, status: r.status, position: r.position, entry: JSON.parse(r.entry) })),
  };
}

/** Replace the active people with `people` (already validated), in order. Paused people and drafts stay. */
export async function saveActivePeople(env, people, who) {
  const list = JSON.stringify(people);
  const at = now();
  await env.DB.batch([
    env.DB.prepare("DELETE FROM people WHERE status = 'active' AND id NOT IN (SELECT json_extract(value, '$.id') FROM json_each(?1))").bind(list),
    env.DB.prepare(`INSERT INTO people (id, status, position, entry, created_at, updated_at)
      SELECT json_extract(value, '$.id'), 'active', CAST(key AS INTEGER), json(value), ?2, ?2 FROM json_each(?1) WHERE true
      ON CONFLICT (id) DO UPDATE SET status = 'active', position = excluded.position, entry = excluded.entry, updated_at = excluded.updated_at`).bind(list, at),
    env.DB.prepare("INSERT INTO changes (at, who, what) VALUES (?1, ?2, ?3)").bind(at, who, `watch list saved: ${people.length} active people`),
  ]);
}

// ---------- profiles ----------

async function putProfile(request, env, role, id) {
  if (!ID_RE.test(id)) return reply(400, { error: "Not a person id." });
  const text = await request.text();
  if (text.length > MAX_PROFILE) return reply(413, { error: "That profile is too large." });
  const row = await env.DB.prepare("SELECT profile FROM people WHERE id = ?1").bind(id).first();
  if (!row) return reply(404, { error: "No such person." });
  if (role === "writer") {
    // The writer reads other people's text, so it may only add to the end of a profile.
    if (!text.startsWith(row.profile)) return reply(403, { error: "The writer may only add to the end of a profile." });
    if (text.length - row.profile.length > MAX_PROFILE_GROWTH) return reply(413, { error: "That is too much to add at once." });
  }
  if (text === row.profile) return reply(200, { ok: true, unchanged: true });
  const at = now();
  await env.DB.batch([
    env.DB.prepare("UPDATE people SET profile = ?2, updated_at = ?3 WHERE id = ?1").bind(id, text, at),
    env.DB.prepare("INSERT INTO changes (at, who, what) VALUES (?1, ?2, ?3)").bind(at, role, `profile ${id} updated`),
  ]);
  return reply(200, { ok: true });
}

// ---------- Who to follow state ----------

/** follow/state.json, assembled from rows without parsing them. */
export async function readFollowState(env) {
  const [meta, rows] = await env.DB.batch([
    env.DB.prepare("SELECT key, value FROM follow_meta ORDER BY rowid"),
    env.DB.prepare("SELECT login, data FROM follow_candidates ORDER BY rowid"),
  ]);
  if (!meta.results.length && !rows.results.length) return null;
  const head = meta.results.map((r) => `${JSON.stringify(r.key)}:${r.value}`);
  const people = rows.results.map((r) => `${JSON.stringify(r.login)}:${r.data}`).join(",");
  return `{${[...head, `"people":{${people}}`].join(",")}}`;
}

async function writeFollowState(request, env) {
  let body;
  try { body = await readJson(request); } catch (err) { return reply(400, { error: err.message }); }
  const upsert = body.upsert || {};
  const remove = body.delete || [];
  const meta = body.meta || {};
  if (typeof upsert !== "object" || Array.isArray(upsert) || !Array.isArray(remove) || typeof meta !== "object" || Array.isArray(meta)) {
    return reply(400, { error: "Expected {meta, upsert, delete}." });
  }
  for (const login of [...Object.keys(upsert), ...remove]) {
    if (!LOGIN_RE.test(String(login))) return reply(400, { error: `Not a GitHub login: ${String(login).slice(0, 40)}` });
  }
  // meta maps each top-level key of state.json other than "people" to its value as JSON text.
  if ("people" in meta) return reply(400, { error: "meta cannot hold people." });
  for (const v of Object.values(meta)) {
    try { if (typeof v !== "string" || v.length > 100_000) throw new Error(); JSON.parse(v); } catch { return reply(400, { error: "meta values must be JSON text." }); }
  }
  const at = now();
  const stmts = [];
  if (Object.keys(meta).length) {
    stmts.push(env.DB.prepare(`INSERT INTO follow_meta (key, value) SELECT key, value FROM json_each(?1) WHERE true
      ON CONFLICT (key) DO UPDATE SET value = excluded.value`).bind(JSON.stringify(meta)));
  }
  if (Object.keys(upsert).length) {
    stmts.push(env.DB.prepare(`INSERT INTO follow_candidates (login, data, updated_at) SELECT key, json(value), ?2 FROM json_each(?1) WHERE true
      ON CONFLICT (login) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at`).bind(JSON.stringify(upsert), at));
  }
  if (remove.length) {
    stmts.push(env.DB.prepare("DELETE FROM follow_candidates WHERE login IN (SELECT value FROM json_each(?1))").bind(JSON.stringify(remove)));
  }
  if (stmts.length) await env.DB.batch(stmts);
  return reply(200, { ok: true, upserted: Object.keys(upsert).length, deleted: remove.length });
}

// ---------- one-click action log ----------

const ACTION_FIELDS = ["at", "action", "login", "nonce", "result", "detail"];

export async function readActions(env) {
  const rows = await env.DB.prepare("SELECT at, action, login, nonce, result, detail FROM actions ORDER BY at, rowid").all();
  return rows.results.map((r) => JSON.stringify(Object.fromEntries(ACTION_FIELDS.map((k) => [k, r[k]]))) + "\n").join("");
}

export async function actionUsed(env, nonce) {
  return !!(await env.DB.prepare("SELECT 1 FROM actions WHERE nonce = ?1").bind(nonce).first());
}

async function logAction(request, env) {
  let e;
  try { e = await readJson(request); } catch (err) { return reply(400, { error: err.message }); }
  if (!e || !NONCE_RE.test(String(e.nonce)) || !LOGIN_RE.test(String(e.login)) || typeof e.at !== "string"
      || !["follow", "unfollow", "add", "unadd"].includes(e.action) || !["ok", "failed", "refused"].includes(e.result)) {
    return reply(400, { error: "Not an action log entry." });
  }
  const r = await env.DB.prepare("INSERT INTO actions (nonce, at, action, login, result, detail) VALUES (?1, ?2, ?3, ?4, ?5, ?6) ON CONFLICT (nonce) DO NOTHING")
    .bind(e.nonce, e.at.slice(0, 40), e.action, e.login, e.result, String(e.detail || "").slice(0, 200)).run();
  if (!r.meta.changes) return reply(409, { error: "That link is already in the log." });
  return reply(200, { ok: true });
}

// ---------- the router ----------

function sameOrigin(request, url) {
  const origin = request.headers.get("Origin");
  return !!origin && origin !== "null" && new URL(origin).host === url.host;
}

/**
 * Handle /api/paper/* and /api/site/* for `role` ("owner", "jobs" or "writer").
 * Returns a Response, or null when the path is not one of these.
 */
export async function handleStore(request, env, url, role) {
  const p = url.pathname;
  if (!p.startsWith("/api/paper/") && !p.startsWith("/api/site/")) return null;
  if (!env.DB || !env.STORE) return reply(501, { error: "This Worker has no D1 database or R2 bucket bound." });
  const m = request.method;
  if (role === "owner" && m !== "GET" && m !== "HEAD" && !sameOrigin(request, url)) return reply(403, { error: "Changes are only allowed from this site." });
  const rest = (prefix) => decodeURIComponent(p.slice(prefix.length));

  if (p === "/api/paper/manifest" && m === "GET") {
    const files = (await listAll(env.STORE, "paper/")).filter((f) => paperAllowed(role, f.path, false));
    return reply(200, { files });
  }
  if (p.startsWith("/api/paper/files/")) {
    const path = rest("/api/paper/files/");
    if (m === "GET") return paperAllowed(role, path, false) ? getFile(env.STORE, `paper/${path}`) : reply(403, { error: "Not allowed." });
    if (m === "PUT") return paperAllowed(role, path, true) ? putFile(request, env.STORE, `paper/${path}`) : reply(403, { error: "Not allowed." });
    if (m === "DELETE") {
      if (role !== "jobs" || !paperAllowed(role, path, true)) return reply(403, { error: "Not allowed." });
      await env.STORE.delete(`paper/${path}`);
      return reply(200, { ok: true });
    }
    return reply(405, { error: "Use GET, PUT or DELETE." });
  }

  if (p.startsWith("/api/site/")) {
    if (role !== "jobs") return reply(403, { error: "Only the publish job changes the site." });
    if (p === "/api/site/manifest" && m === "GET") return reply(200, { files: await listAll(env.STORE, siteKey(env, "")) });
    if (p.startsWith("/api/site/files/")) {
      const path = rest("/api/site/files/");
      if (!PATH_RE.test(path)) return reply(400, { error: "Not a file path." });
      if (m === "PUT") return putFile(request, env.STORE, siteKey(env, path));
      if (m === "DELETE") { await env.STORE.delete(siteKey(env, path)); return reply(200, { ok: true }); }
    }
    return reply(404, { error: "Not found." });
  }

  if (p === "/api/paper/watchlist" && m === "GET") return reply(200, await readWatchlist(env, { activeOnly: role === "writer" }));

  if (p === "/api/paper/profiles" && m === "GET") {
    const rows = await env.DB.prepare(`SELECT id, profile FROM people WHERE profile != ''${role === "writer" ? " AND status = 'active'" : ""} ORDER BY id`).all();
    return reply(200, { profiles: Object.fromEntries(rows.results.map((r) => [r.id, r.profile])) });
  }
  if (p.startsWith("/api/paper/profiles/") && m === "PUT") return putProfile(request, env, role, rest("/api/paper/profiles/"));

  if (p === "/api/paper/follow") {
    if (role === "writer") return reply(403, { error: "Not allowed." });
    if (m === "GET") {
      const text = await readFollowState(env);
      return text === null ? reply(404, { error: "No Who to follow state yet." }) : new Response(text, { headers: { "Content-Type": "application/json" } });
    }
    if (m === "POST" && role === "jobs") return writeFollowState(request, env);
    return reply(405, { error: "Not allowed." });
  }

  if (p === "/api/paper/actions") {
    if (role === "writer") return reply(403, { error: "Not allowed." });
    if (m === "GET") return new Response(await readActions(env), { headers: { "Content-Type": "application/x-ndjson" } });
    if (m === "POST" && role === "jobs") return logAction(request, env);
    return reply(405, { error: "Not allowed." });
  }

  return reply(404, { error: "Not found." });
}

// ---------- serving the built site from R2 ----------

const TYPES = {
  html: "text/html; charset=utf-8", css: "text/css; charset=utf-8", js: "text/javascript; charset=utf-8",
  json: "application/json", xml: "application/xml", txt: "text/plain; charset=utf-8", svg: "image/svg+xml",
  png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", webp: "image/webp", ico: "image/x-icon",
  woff2: "font/woff2", woff: "font/woff", md: "text/plain; charset=utf-8",
};

export function contentType(path) {
  const ext = (path.match(/\.([A-Za-z0-9]+)$/) || [])[1];
  return TYPES[(ext || "").toLowerCase()] || "application/octet-stream";
}

/** The built site from R2, the way Workers static assets would serve it. */
export async function serveSite(request, env) {
  if (request.method !== "GET" && request.method !== "HEAD") return new Response("Method not allowed", { status: 405 });
  const url = new URL(request.url);
  let path;
  try { path = decodeURIComponent(url.pathname).replace(/^\/+/, ""); } catch { return new Response("Bad path", { status: 400 }); }
  if (path && !PATH_RE.test(path.replace(/\/$/, ""))) return notFound(env, request);
  const key = path === "" || path.endsWith("/") ? `${path}index.html` : path;
  let obj = await env.STORE.get(siteKey(env, key), { onlyIf: request.headers });
  if (!obj && !/\.[A-Za-z0-9]+$/.test(path.split("/").pop() || "")) {
    // /p/someone -> /p/someone/ when that page exists.
    if (await env.STORE.head(siteKey(env, `${path}/index.html`))) {
      return new Response(null, { status: 307, headers: { Location: `${url.pathname}/${url.search}` } });
    }
  }
  if (!obj) return notFound(env, request);
  const headers = { "Content-Type": contentType(key), ETag: obj.httpEtag };
  if (!("body" in obj) || !obj.body) return new Response(null, { status: 304, headers });
  return new Response(request.method === "HEAD" ? null : obj.body, { headers });
}

async function notFound(env, request) {
  const page = await env.STORE.get(siteKey(env, "404.html"));
  return new Response(page && request.method !== "HEAD" ? page.body : "Not found", { status: 404, headers: { "Content-Type": "text/html; charset=utf-8" } });
}
