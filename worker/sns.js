// Amazon SES bounce and complaint notices, delivered by Amazon SNS over HTTPS
// to POST /api/ses/events (docs/email.md). Nothing here trusts the request:
// each message must carry a valid SNS signature (SignatureVersion 2, RSA with
// SHA-256) made with a certificate fetched from an sns.<region>.amazonaws.com
// address, and must come from the one topic named in SES_TOPIC_ARN.

const SNS_HOST = /^sns\.[a-z0-9-]+\.amazonaws\.com$/;
const certCache = new Map();

/** The DER-encoded SubjectPublicKeyInfo inside an X.509 certificate (DER bytes). */
export function spkiFromCertificate(der) {
  const tlv = (pos) => {
    if (pos + 2 > der.length) throw new Error("truncated certificate");
    let len = der[pos + 1];
    let off = pos + 2;
    if (len & 0x80) {
      const n = len & 0x7f;
      if (n < 1 || n > 4) throw new Error("bad length in certificate");
      len = 0;
      for (let i = 0; i < n; i++) len = len * 256 + der[off++];
    }
    if (off + len > der.length) throw new Error("truncated certificate");
    return { tag: der[pos], start: pos, body: off, end: off + len };
  };
  const cert = tlv(0);
  const tbs = tlv(cert.body);
  if (cert.tag !== 0x30 || tbs.tag !== 0x30) throw new Error("not a certificate");
  const fields = [];
  for (let p = tbs.body; p < tbs.end && fields.length < 8; ) { const f = tlv(p); fields.push(f); p = f.end; }
  // version [0] is optional; then serial, signature algorithm, issuer, validity, subject, subjectPublicKeyInfo.
  const spki = fields[fields[0] && fields[0].tag === 0xa0 ? 6 : 5];
  if (!spki || spki.tag !== 0x30) throw new Error("no public key in certificate");
  return der.slice(spki.start, spki.end);
}

function pemToDer(pem) {
  const b64 = pem.replace(/-----(BEGIN|END) CERTIFICATE-----/g, "").replace(/\s+/g, "");
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
}

/** The exact text SNS signs for this message type. */
export function stringToSign(m) {
  const keys = m.Type === "Notification"
    ? ["Message", "MessageId", ...(m.Subject !== undefined ? ["Subject"] : []), "Timestamp", "TopicArn", "Type"]
    : ["Message", "MessageId", "SubscribeURL", "Timestamp", "Token", "TopicArn", "Type"];
  return keys.map((k) => `${k}\n${m[k]}\n`).join("");
}

export function snsUrl(value) {
  let u;
  try { u = new URL(value); } catch { return null; }
  return u.protocol === "https:" && SNS_HOST.test(u.hostname) ? u : null;
}

async function publicKey(certUrl) {
  if (certCache.has(certUrl)) return certCache.get(certUrl);
  const r = await fetch(certUrl);
  if (!r.ok) throw new Error(`certificate fetch ${r.status}`);
  const key = await crypto.subtle.importKey("spki", spkiFromCertificate(pemToDer(await r.text())), { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
  certCache.set(certUrl, key);
  return key;
}

/** Throws unless `m` is a genuine SNS message from `topicArn`. */
export async function verifySns(m, topicArn) {
  if (!m || typeof m !== "object") throw new Error("not an SNS message");
  if (!["Notification", "SubscriptionConfirmation", "UnsubscribeConfirmation"].includes(m.Type)) throw new Error("unknown message type");
  if (!topicArn || m.TopicArn !== topicArn) throw new Error("wrong topic");
  if (m.SignatureVersion !== "2") throw new Error("only SignatureVersion 2 is accepted");
  const cert = snsUrl(m.SigningCertURL);
  if (!cert || !cert.pathname.endsWith(".pem")) throw new Error("certificate is not on an SNS address");
  const sig = Uint8Array.from(atob(String(m.Signature || "")), (c) => c.charCodeAt(0));
  const ok = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", await publicKey(cert.href), sig, new TextEncoder().encode(stringToSign(m)));
  if (!ok) throw new Error("bad signature");
}

/** Rows for email_events from one SES event (the Message of an SNS notification). */
export function eventRows(event) {
  const type = event.eventType || event.notificationType;
  const mail = event.mail || {};
  const rows = [];
  const add = (address, detail, at) => address && rows.push({ type, address: String(address).toLowerCase().slice(0, 254), detail: String(detail || "").slice(0, 300), at: at || mail.timestamp || new Date().toISOString(), message_id: String(mail.messageId || "") });
  if (type === "Bounce" && event.bounce) {
    for (const r of event.bounce.bouncedRecipients || []) add(r.emailAddress, `${event.bounce.bounceType} ${event.bounce.bounceSubType || ""} ${r.diagnosticCode || ""}`.trim(), event.bounce.timestamp);
  } else if (type === "Complaint" && event.complaint) {
    for (const r of event.complaint.complainedRecipients || []) add(r.emailAddress, event.complaint.complaintFeedbackType || "complaint", event.complaint.timestamp);
  } else if (type === "Delivery" && event.delivery) {
    for (const a of event.delivery.recipients || []) add(a, "", event.delivery.timestamp);
  } else if (type === "Reject") {
    for (const a of mail.destination || []) add(a, event.reject && event.reject.reason);
  } else if (type === "DeliveryDelay" && event.deliveryDelay) {
    for (const r of event.deliveryDelay.delayedRecipients || []) add(r.emailAddress, event.deliveryDelay.delayType, event.deliveryDelay.timestamp);
  }
  return rows;
}

function reply(status, body) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** POST /api/ses/events. */
export async function handleSesEvents(request, env) {
  if (request.method !== "POST") return reply(405, { error: "Use POST." });
  if (!env.DB || !env.SES_TOPIC_ARN) return reply(503, { error: "SES events are not set up on this Worker." });
  let m;
  try {
    const text = await request.text();
    if (text.length > 256 * 1024) return reply(413, { error: "Too large." });
    m = JSON.parse(text);
    await verifySns(m, env.SES_TOPIC_ARN);
  } catch (err) {
    console.warn(`SES event refused: ${err.message}`);
    return reply(403, { error: "Not a valid SNS message." });
  }
  if (m.Type === "SubscriptionConfirmation") {
    const url = snsUrl(m.SubscribeURL);
    if (!url) return reply(400, { error: "Bad SubscribeURL." });
    const r = await fetch(url.href);
    console.log(`SES events: subscription confirmation ${r.status}`);
    return reply(r.ok ? 200 : 502, { ok: r.ok });
  }
  if (m.Type !== "Notification") return reply(200, { ok: true });
  let event;
  try { event = JSON.parse(m.Message); } catch { return reply(200, { ok: true, ignored: "not JSON" }); }
  const rows = eventRows(event);
  if (rows.length) {
    await env.DB.prepare(`INSERT INTO email_events (at, type, address, detail, message_id, sns_id)
      SELECT json_extract(value, '$.at'), json_extract(value, '$.type'), json_extract(value, '$.address'), json_extract(value, '$.detail'), json_extract(value, '$.message_id'), ?2
      FROM json_each(?1) WHERE true ON CONFLICT (sns_id, address) DO NOTHING`).bind(JSON.stringify(rows), String(m.MessageId)).run();
  }
  console.log(`SES event: ${event.eventType || event.notificationType || "unknown"} (${rows.length} recipient(s))`);
  return reply(200, { ok: true, stored: rows.length });
}

/** Addresses that must not get email: a permanent bounce or a complaint not yet cleared. */
export async function suppressedList(env) {
  const rows = await env.DB.prepare(`SELECT address, MAX(at) AS at, CASE WHEN SUM(type = 'Complaint') > 0 THEN 'Complaint' ELSE 'Permanent bounce' END AS reason
    FROM email_events WHERE cleared_at IS NULL AND (type = 'Complaint' OR (type = 'Bounce' AND detail LIKE 'Permanent%'))
    GROUP BY address ORDER BY address`).all();
  return rows.results;
}
