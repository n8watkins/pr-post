// The paper's email, sent by the Worker for the jobs (docs/email.md).
//
// The GitHub Actions jobs render the digest and the Who to follow email
// (scripts/send_email.py, scripts/send_follow.py) and POST them to
// /api/email/send with JOBS_TOKEN. The Worker sends them through one of:
//   - MAILROOM: a service binding to mailroom (github.com/n8watkins/mailroom),
//     the account's one sender with caps and bounce handling; the owner's paper;
//   - EMAIL: a Cloudflare Email Service send_email binding of its own, for a
//     paper on another Cloudflare account.
// Nobody holds an email API key. Bounces and complaints come back through
// EmailEvents (worker/main.js) into email_events, where a hard bounce or a
// complaint suppresses the address until the owner clears it on the People page.

const KINDS = new Set(["digest", "follow"]);
const EMAIL_RE = /^[^\s@<>(),;:"[\]\\]+@[^\s@<>(),;:"[\]\\]+\.[^\s@<>(),;:"[\]\\]+$/;
const MAX_HTML = 500 * 1024;

function reply(status, body) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** Addresses that must not get email: a permanent bounce or a complaint not yet cleared. */
export async function suppressedList(env) {
  const rows = await env.DB.prepare(`SELECT address, MAX(at) AS at, CASE WHEN SUM(type = 'Complaint') > 0 THEN 'Complaint' ELSE 'Permanent bounce' END AS reason
    FROM email_events WHERE cleared_at IS NULL AND (type = 'Complaint' OR (type = 'Bounce' AND detail LIKE 'Permanent%'))
    GROUP BY address ORDER BY address`).all();
  return rows.results;
}

/** Checks a job's email. Returns an error message, or null. */
export function invalid(b) {
  if (!b || typeof b !== "object") return "Send a JSON object.";
  if (!KINDS.has(b.kind)) return "kind must be digest or follow.";
  if (typeof b.to !== "string" || !EMAIL_RE.test(b.to.trim())) return "to must be one email address.";
  if (typeof b.subject !== "string" || !b.subject.trim() || /[\r\n]/.test(b.subject) || b.subject.length > 300) return "subject must be one line of up to 300 characters.";
  if (typeof b.text !== "string" || !b.text.trim()) return "text is required.";
  if (typeof b.html !== "string" || !b.html.trim() || b.html.length > MAX_HTML) return "html is required, up to 500 KB.";
  if (b.key !== undefined && (typeof b.key !== "string" || !/^[\w.:-]{1,180}$/.test(b.key))) return "key must be letters, digits and . : _ - (up to 180).";
  return null;
}

/** POST /api/email/send, for the jobs (JOBS_TOKEN). */
export async function handleEmailSend(request, env, role) {
  if (request.method !== "POST") return reply(405, { error: "Use POST." });
  if (role !== "jobs") return reply(403, { error: "Not allowed." });
  let b;
  try {
    const text = await request.text();
    if (text.length > MAX_HTML + 64 * 1024) return reply(413, { error: "Too large." });
    b = JSON.parse(text);
  } catch {
    return reply(400, { error: "Send a JSON object." });
  }
  const problem = invalid(b);
  if (problem) return reply(400, { error: problem });
  const to = b.to.trim();

  if (env.DB) {
    const blocked = (await suppressedList(env)).find((r) => r.address === to.toLowerCase());
    if (blocked) return reply(409, { error: `The recipient is suppressed (${blocked.reason} on ${String(blocked.at).slice(0, 10)}). Clear it on the People page.`, code: "suppressed" });
  }

  const name = env.EMAIL_FROM_NAME || env.SITE_TITLE || "PR Post";
  if (env.MAILROOM) {
    let r;
    try {
      r = await env.MAILROOM.send({ kind: b.kind, to, subject: b.subject, text: b.text, html: b.html, fromName: name, ...(b.key ? { key: b.key } : {}) });
    } catch (err) {
      console.error(`email: mailroom did not answer: ${err.message}`);
      return reply(503, { error: "mailroom did not answer; try again later.", code: "unavailable", retryable: true });
    }
    if (r.ok) {
      console.log(`email: ${b.kind} sent${r.duplicate ? " (already sent)" : ""}`);
      return reply(200, { ok: true, id: r.messageId, duplicate: Boolean(r.duplicate) });
    }
    console.warn(`email: ${b.kind} refused: ${r.code}`);
    return reply(r.retryable ? 503 : 422, { error: r.message, code: r.code, retryable: r.retryable });
  }
  if (env.EMAIL && env.EMAIL_FROM) {
    try {
      const r = await env.EMAIL.send({ to, from: { email: env.EMAIL_FROM, name }, subject: b.subject, text: b.text, html: b.html });
      return reply(200, { ok: true, id: r.messageId });
    } catch (err) {
      const code = err && err.code ? String(err.code) : "unavailable";
      const retryable = ["E_RATE_LIMIT_EXCEEDED", "E_DAILY_LIMIT_EXCEEDED", "E_INTERNAL_SERVER_ERROR", "unavailable"].includes(code);
      return reply(retryable ? 503 : 422, { error: String(err && err.message ? err.message : err), code, retryable });
    }
  }
  return reply(503, { error: "Email is not set up on this Worker: bind MAILROOM, or EMAIL with an EMAIL_FROM (docs/email.md).", code: "not_configured" });
}

// mailroom's event types, as email_events has always named them.
const TYPES = { bounced: "Bounce", complained: "Complaint", delivered: "Delivery", deferred: "DeliveryDelay", rejected: "Reject", failed: "Reject" };

/** One mailroom event (its MailEvent) into email_events. A hard bounce's detail starts "Permanent", which suppresses. */
export async function recordEmailEvent(env, event) {
  const type = TYPES[event && event.type];
  if (!type || !event.recipient || !event.eventId) return { stored: false };
  const kind = type === "Bounce" ? (event.bounceType === "hard" ? "Permanent" : "Transient") : "";
  const detail = [kind, event.smtpStatus, event.reason].filter(Boolean).join(" ").slice(0, 300);
  const r = await env.DB.prepare(`INSERT INTO email_events (at, type, address, detail, message_id, event_id) VALUES (?1, ?2, ?3, ?4, ?5, ?6)
    ON CONFLICT (event_id, address) DO NOTHING`)
    .bind(event.occurredAt || new Date().toISOString(), type, String(event.recipient).toLowerCase().slice(0, 254), detail, String(event.messageId || ""), String(event.eventId))
    .run();
  console.log(`email event: ${event.type} stored ${r.meta.changes ? "now" : "already"}`);
  return { stored: Boolean(r.meta.changes) };
}
