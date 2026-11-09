// node --test tests/ : SES bounce and complaint notices over SNS (worker/sns.js).
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { generateKeyPairSync, sign as rsaSign } from "node:crypto";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import worker from "../worker/index.js";
import { stringToSign, spkiFromCertificate, eventRows, snsUrl } from "../worker/sns.js";
import { fakeD1, fakeR2 } from "./fakes.mjs";

const TOPIC = "arn:aws:sns:us-east-1:123456789012:pr-post-ses-events";
const CERT_URL = "https://sns.us-east-1.amazonaws.com/SimpleNotificationService-test.pem";
const JOBS = "j".repeat(40);

const { privateKey, publicKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
const dir = mkdtempSync(join(tmpdir(), "sns-"));
writeFileSync(join(dir, "key.pem"), privateKey.export({ type: "pkcs8", format: "pem" }));
const CERT = execFileSync("openssl", ["req", "-x509", "-key", join(dir, "key.pem"), "-subj", "/CN=sns.test", "-days", "1"], { encoding: "utf8" });
rmSync(dir, { recursive: true });

function signed(fields, key = privateKey) {
  const m = { SignatureVersion: "2", SigningCertURL: CERT_URL, TopicArn: TOPIC, Timestamp: "2026-11-09T10:00:00.000Z", MessageId: `id-${Math.random()}`, ...fields };
  m.Signature = rsaSign("sha256", Buffer.from(stringToSign(m)), key).toString("base64");
  return m;
}

const bounce = (type = "Permanent") => JSON.stringify({
  eventType: "Bounce", mail: { messageId: "m-1", timestamp: "2026-11-09T09:59:00Z", destination: ["Reader@Example.com"] },
  bounce: { bounceType: type, bounceSubType: "General", timestamp: "2026-11-09T10:00:00Z", bouncedRecipients: [{ emailAddress: "Reader@Example.com", diagnosticCode: "550 no such user" }] },
});

function withFetch(run) {
  const calls = [];
  const real = globalThis.fetch;
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    if (String(url) === CERT_URL) return new Response(CERT);
    if (String(url).startsWith("https://sns.us-east-1.amazonaws.com/?Action=ConfirmSubscription")) return new Response("<ok/>");
    return new Response("unexpected", { status: 500 });
  };
  return run(calls).finally(() => { globalThis.fetch = real; });
}

const env = () => ({ DB: fakeD1(), STORE: fakeR2(), SES_TOPIC_ARN: TOPIC, JOBS_TOKEN: JOBS });
const post = (e, body) => worker.fetch(new Request("https://paper.example/api/ses/events", { method: "POST", body: typeof body === "string" ? body : JSON.stringify(body) }), e);

test("the public key comes out of a real X.509 certificate", () => {
  const der = Uint8Array.from(Buffer.from(CERT.replace(/-----[A-Z ]+-----|\s/g, ""), "base64"));
  const spki = Buffer.from(spkiFromCertificate(der));
  assert.deepEqual(spki, publicKey.export({ type: "spki", format: "der" }));
  assert.throws(() => spkiFromCertificate(new Uint8Array([0x30, 0x05, 1])), /truncated/);
});

test("only https SNS addresses count", () => {
  assert.ok(snsUrl(CERT_URL));
  assert.equal(snsUrl("http://sns.us-east-1.amazonaws.com/x.pem"), null);
  assert.equal(snsUrl("https://sns.us-east-1.amazonaws.com.evil.example/x.pem"), null);
  assert.equal(snsUrl("https://evil.example/sns.us-east-1.amazonaws.com"), null);
});

test("a signed bounce is stored and suppresses the address; a repeat is stored once", async () => {
  const e = env();
  await withFetch(async () => {
    const m = signed({ Type: "Notification", Message: bounce() });
    const r = await post(e, m);
    assert.equal(r.status, 200);
    assert.equal((await r.json()).stored, 1);
    assert.equal((await post(e, m)).status, 200);
  });
  const rows = e.DB.raw.prepare("SELECT type, address, detail FROM email_events").all();
  assert.deepEqual(rows.map((r) => [r.type, r.address]), [["Bounce", "reader@example.com"]]);
  assert.match(rows[0].detail, /^Permanent General 550/);
  const sup = await (await worker.fetch(new Request("https://paper.example/api/paper/suppressed", { headers: { Authorization: `Bearer ${JOBS}` } }), e)).json();
  assert.deepEqual(sup.suppressed.map((s) => [s.address, s.reason]), [["reader@example.com", "Permanent bounce"]]);
});

test("a transient bounce and a delivery are logged but suppress nothing", async () => {
  const e = env();
  await withFetch(async () => {
    await post(e, signed({ Type: "Notification", Message: bounce("Transient") }));
    await post(e, signed({ Type: "Notification", Message: JSON.stringify({ eventType: "Delivery", mail: { messageId: "m-2" }, delivery: { recipients: ["a@b.c"], timestamp: "2026-11-09T10:00:00Z" } }) }));
  });
  assert.equal(e.DB.raw.prepare("SELECT COUNT(*) AS n FROM email_events").get().n, 2);
  const sup = await (await worker.fetch(new Request("https://paper.example/api/paper/suppressed", { headers: { Authorization: `Bearer ${JOBS}` } }), e)).json();
  assert.deepEqual(sup.suppressed, []);
});

test("forged, re-signed, wrong-topic and unsigned messages are refused", async () => {
  const e = env();
  const other = generateKeyPairSync("rsa", { modulusLength: 2048 }).privateKey;
  await withFetch(async () => {
    const good = signed({ Type: "Notification", Message: bounce() });
    assert.equal((await post(e, { ...good, Message: bounce("Transient") })).status, 403);
    assert.equal((await post(e, signed({ Type: "Notification", Message: bounce() }, other))).status, 403);
    assert.equal((await post(e, signed({ Type: "Notification", Message: bounce(), TopicArn: "arn:aws:sns:us-east-1:1:other" }))).status, 403);
    assert.equal((await post(e, signed({ Type: "Notification", Message: bounce(), SigningCertURL: "https://evil.example/x.pem" }))).status, 403);
    assert.equal((await post(e, signed({ Type: "Notification", Message: bounce(), SignatureVersion: "1" }))).status, 403);
    assert.equal((await post(e, "not json")).status, 403);
  });
  assert.equal(e.DB.raw.prepare("SELECT COUNT(*) AS n FROM email_events").get().n, 0);
  assert.equal((await post({ ...e, SES_TOPIC_ARN: "" }, {})).status, 503);
  assert.equal((await worker.fetch(new Request("https://paper.example/api/ses/events"), e)).status, 405);
});

test("a subscription is confirmed only through an SNS address", async () => {
  const e = env();
  await withFetch(async (calls) => {
    const ok = signed({ Type: "SubscriptionConfirmation", Message: "confirm", Token: "t", SubscribeURL: "https://sns.us-east-1.amazonaws.com/?Action=ConfirmSubscription&Token=t" });
    assert.equal((await post(e, ok)).status, 200);
    assert.ok(calls.some((c) => c.includes("ConfirmSubscription")));
    const bad = signed({ Type: "SubscriptionConfirmation", Message: "confirm", Token: "t", SubscribeURL: "https://evil.example/?Action=ConfirmSubscription" });
    assert.equal((await post(e, bad)).status, 400);
  });
});

test("complaints and rejects become rows", () => {
  assert.deepEqual(eventRows({ eventType: "Complaint", mail: {}, complaint: { complaintFeedbackType: "abuse", timestamp: "t", complainedRecipients: [{ emailAddress: "X@Y.z" }] } }).map((r) => [r.type, r.address, r.detail]), [["Complaint", "x@y.z", "abuse"]]);
  assert.deepEqual(eventRows({ eventType: "Reject", mail: { destination: ["a@b.c"] }, reject: { reason: "Bad content" } }).map((r) => r.detail), ["Bad content"]);
  assert.deepEqual(eventRows({ eventType: "Open", mail: {} }), []);
});
