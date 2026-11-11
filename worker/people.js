// The People page's API, for the signed-in owner only (docs/private-mode.md).
// The watch list lives in D1: each person is active (collected and written
// about), paused (kept, not collected) or a draft (suggested by a job, waiting
// for approval). Every change is same-origin only and logged in `changes`.
//
//   GET    /api/people                  everyone, with profiles, plus suppressed addresses and recent changes
//   PUT    /api/people/order            {ids}: the order of the people listed
//   POST   /api/people/drafts           {github}: start the job that researches and drafts them
//   PUT    /api/people/<id>             {entry}: their sources, socials and settings
//   POST   /api/people/<id>/status      {status: "active" | "paused"}: approve a draft, pause, resume
//   DELETE /api/people/<id>             remove them (their back issues stay)
//   POST   /api/email/clear             {address}: let email go to a suppressed address again

import { validatePeople, GITHUB_RE } from "./lib.js";
import { suppressedList } from "./email.js";
import { createRequest, dispatch } from "./jobs.js";

const ID_RE = /^[a-z0-9](?:[a-z0-9-]{0,38})$/;
const now = () => new Date().toISOString().replace(/\.\d{3}Z$/, "Z");

function reply(status, body) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

async function body(request) {
  if (!(request.headers.get("Content-Type") || "").startsWith("application/json")) throw new Error("Expected JSON.");
  const text = await request.text();
  if (text.length > 100_000) throw new Error("That is too large.");
  return JSON.parse(text);
}

function logChange(env, who, what) {
  return env.DB.prepare("INSERT INTO changes (at, who, what) VALUES (?1, ?2, ?3)").bind(now(), who, what);
}

async function list(env) {
  const [people, changes] = await env.DB.batch([
    env.DB.prepare("SELECT id, status, position, entry, profile, note, updated_at FROM people ORDER BY position, id"),
    env.DB.prepare("SELECT at, who, what FROM changes ORDER BY rowid DESC LIMIT 30"),
  ]);
  return {
    people: people.results.map((r) => ({ ...r, entry: JSON.parse(r.entry) })),
    suppressed: await suppressedList(env),
    changes: changes.results,
  };
}

/** Handle /api/people* and /api/email/*. Returns a Response, or null when the path is not one of these. */
export async function handlePeople(request, env, url, user) {
  const p = url.pathname;
  if (!p.startsWith("/api/people") && !p.startsWith("/api/email/")) return null;
  if (!env.DB) return reply(501, { error: "The People page needs the paper's D1 database." });
  const m = request.method;
  if (m !== "GET") {
    const origin = request.headers.get("Origin");
    if (!origin || origin === "null" || new URL(origin).host !== url.host) return reply(403, { error: "Changes are only allowed from this site." });
  }
  const who = user.who;
  let b = {};
  if (m === "POST" || m === "PUT") {
    try { b = await body(request); } catch (err) { return reply(400, { error: err.message }); }
  }

  if (p === "/api/people" && m === "GET") return reply(200, await list(env));

  if (p === "/api/people/order" && m === "PUT") {
    const ids = Array.isArray(b.ids) ? b.ids : null;
    if (!ids || ids.length > 200 || !ids.every((i) => typeof i === "string" && ID_RE.test(i))) return reply(400, { error: "Expected {ids: [...]}." });
    await env.DB.batch([
      env.DB.prepare("UPDATE people SET position = (SELECT CAST(key AS INTEGER) FROM json_each(?1) WHERE value = people.id), updated_at = ?2 WHERE id IN (SELECT value FROM json_each(?1))")
        .bind(JSON.stringify(ids), now()),
      logChange(env, who, `order changed (${ids.length} people)`),
    ]);
    return reply(200, { ok: true });
  }

  if (p === "/api/people/drafts" && m === "POST") {
    const github = String(b.github || "").trim().replace(/^@/, "").replace(/^https?:\/\/github\.com\//i, "").replace(/\/.*$/, "");
    if (!GITHUB_RE.test(github)) return reply(400, { error: "That doesn't look like a GitHub username." });
    const taken = await env.DB.prepare("SELECT id, status FROM people WHERE id = ?1 OR lower(json_extract(entry, '$.github')) = ?1").bind(github.toLowerCase()).first();
    if (taken) return reply(409, { error: `@${github} is already here (${taken.status}).` });
    if (!env.ACTIONS_TOKEN) return reply(503, { error: "Drafting needs the ACTIONS_TOKEN Worker secret (paper/SETUP.md, step C2)." });
    const status = await dispatch(env, "paper-people.yml", { request: await createRequest(env, "draft", github) });
    if (status !== 204) return reply(502, { error: `GitHub refused to start the job (${status}).` });
    await logChange(env, who, `draft requested for ${github}`).run();
    return reply(202, { ok: true, message: `Researching @${github}. The draft appears here in a minute or two.` });
  }

  if (p === "/api/email/clear" && m === "POST") {
    const address = String(b.address || "").toLowerCase();
    if (!address.includes("@") || address.length > 254) return reply(400, { error: "Not an email address." });
    const [r] = await env.DB.batch([
      env.DB.prepare("UPDATE email_events SET cleared_at = ?2 WHERE address = ?1 AND cleared_at IS NULL").bind(address, now()),
      logChange(env, who, "email suppression cleared"),
    ]);
    return reply(r.meta.changes ? 200 : 404, r.meta.changes ? { ok: true } : { error: "That address is not suppressed." });
  }

  const one = p.match(/^\/api\/people\/([a-z0-9-]{1,39})(\/status)?$/);
  if (!one || !ID_RE.test(one[1])) return reply(404, { error: "Not found." });
  const id = one[1];
  const row = await env.DB.prepare("SELECT status, entry FROM people WHERE id = ?1").bind(id).first();
  if (!row) return reply(404, { error: "No such person." });

  if (one[2] && m === "POST") {
    if (!["active", "paused"].includes(b.status)) return reply(400, { error: 'Status must be "active" or "paused".' });
    const verb = row.status === "draft" ? "approved" : b.status === "paused" ? "paused" : "resumed";
    await env.DB.batch([
      env.DB.prepare("UPDATE people SET status = ?2, note = CASE WHEN ?2 = 'active' THEN '' ELSE note END, updated_at = ?3 WHERE id = ?1").bind(id, b.status, now()),
      logChange(env, who, `${id} ${verb}`),
    ]);
    return reply(200, { ok: true });
  }
  if (!one[2] && m === "PUT") {
    let person;
    try { [person] = validatePeople({ people: [{ ...(b.entry || {}), id }] }); } catch (err) { return reply(400, { error: err.message }); }
    const old = JSON.parse(row.entry);
    if ((old.github || "").toLowerCase() !== (person.github || "").toLowerCase()) return reply(400, { error: "The GitHub username can't change; remove them and add the new account instead." });
    await env.DB.batch([
      env.DB.prepare("UPDATE people SET entry = ?2, updated_at = ?3 WHERE id = ?1").bind(id, JSON.stringify(person), now()),
      logChange(env, who, `${id} edited`),
    ]);
    return reply(200, { ok: true, entry: person });
  }
  if (!one[2] && m === "DELETE") {
    await env.DB.batch([env.DB.prepare("DELETE FROM people WHERE id = ?1").bind(id), logChange(env, who, `${id} removed`)]);
    return reply(200, { ok: true });
  }
  return reply(405, { error: "Not allowed." });
}
