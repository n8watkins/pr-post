// node --test tests/ : the jobs' email through the Worker and mailroom, and bounces coming back (worker/email.js).
import { test } from "node:test";
import assert from "node:assert/strict";
import worker from "../worker/index.js";
import { recordEmailEvent, suppressedList } from "../worker/email.js";
import { fakeD1, fakeR2 } from "./fakes.mjs";

const JOBS = "j".repeat(40);
const WRITER = "w".repeat(40);
const email = { kind: "digest", to: "Owner@Example.com", subject: "This week in the paper", html: "<p>Hi</p>", text: "Hi", key: "digest-1" };

function mailroom(answer = { ok: true, messageId: "mr-1" }) {
  const calls = [];
  return { calls, async send(req) { calls.push(req); if (answer instanceof Error) throw answer; return answer; } };
}

const env = (extra = {}) => ({ DB: fakeD1(), STORE: fakeR2(), JOBS_TOKEN: JOBS, WRITER_TOKEN: WRITER, SITE_TITLE: "The Pull Request Post", ...extra });
const post = (e, body, token = JOBS) =>
  worker.fetch(new Request("https://paper.example/api/email/send", { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify(body) }), e);

test("the jobs' email goes to mailroom with the paper's name, kind and key", async () => {
  const m = mailroom();
  const e = env({ MAILROOM: m, EMAIL_FROM_NAME: "The Pull Request Post" });
  const r = await post(e, email);
  assert.equal(r.status, 200);
  assert.deepEqual(await r.json(), { ok: true, id: "mr-1", duplicate: false });
  assert.deepEqual(m.calls[0], { kind: "digest", to: "Owner@Example.com", subject: "This week in the paper", text: "Hi", html: "<p>Hi</p>", fromName: "The Pull Request Post", key: "digest-1" });
});

test("only the jobs may send, and only well-formed email", async () => {
  const e = env({ MAILROOM: mailroom() });
  assert.equal((await post(e, email, WRITER)).status, 403);
  assert.equal((await post(e, email, "x".repeat(40))).status, 401);
  for (const bad of [{ ...email, kind: "newsletter" }, { ...email, to: "a@b.c, d@e.f" }, { ...email, subject: "a\r\nBcc: x@y.z" }, { ...email, text: "" }, { ...email, key: "has spaces" }]) {
    assert.equal((await post(e, bad)).status, 400);
  }
  const unauthenticated = await worker.fetch(new Request("https://paper.example/api/email/send", { method: "POST", body: "{}" }), e);
  assert.notEqual(unauthenticated.status, 200);
});

test("mailroom's refusals come back with its code, and a dead mailroom is retryable", async () => {
  let r = await post(env({ MAILROOM: mailroom({ ok: false, code: "cap_app_day", retryable: true, message: "This app reached its daily email cap." }) }), email);
  assert.equal(r.status, 503);
  assert.equal((await r.json()).code, "cap_app_day");
  r = await post(env({ MAILROOM: mailroom({ ok: false, code: "suppressed", retryable: false, message: "bounced before" }) }), email);
  assert.equal(r.status, 422);
  r = await post(env({ MAILROOM: mailroom(new Error("connection reset")) }), email);
  assert.equal(r.status, 503);
  assert.equal((await r.json()).retryable, true);
});

test("a paper on another account can send with its own send_email binding", async () => {
  const sent = [];
  const e = env({ EMAIL: { async send(m) { sent.push(m); return { messageId: "cf-1" }; } }, EMAIL_FROM: "paper@example.com" });
  const r = await post(e, email);
  assert.equal(r.status, 200);
  assert.deepEqual(sent[0].from, { email: "paper@example.com", name: "The Pull Request Post" });
  assert.equal((await post(env(), email)).status, 503);
});

test("a hard bounce from mailroom suppresses the address, and the next send is refused before mailroom", async () => {
  const m = mailroom();
  const e = env({ MAILROOM: m });
  const bounce = { eventId: "ev-1", app: "prpost", type: "bounced", messageId: "mr-1", recipient: "owner@example.com", bounceType: "hard", smtpStatus: "5.1.1", reason: "550 5.1.1 user unknown", occurredAt: "2026-11-11T03:00:00.000Z", suppressed: true };
  assert.deepEqual(await recordEmailEvent(e, bounce), { stored: true });
  assert.deepEqual(await recordEmailEvent(e, bounce), { stored: false });
  const list = await suppressedList(e);
  assert.equal(list.length, 1);
  assert.equal(list[0].reason, "Permanent bounce");
  const r = await post(e, email);
  assert.equal(r.status, 409);
  assert.equal((await r.json()).code, "suppressed");
  assert.equal(m.calls.length, 0);
});

test("soft bounces and deliveries are kept but do not suppress; complaints do", async () => {
  const e = env();
  await recordEmailEvent(e, { eventId: "ev-2", type: "bounced", recipient: "a@example.com", bounceType: "soft", reason: "mailbox full" });
  await recordEmailEvent(e, { eventId: "ev-3", type: "delivered", recipient: "b@example.com" });
  await recordEmailEvent(e, { eventId: "ev-4", type: "complained", recipient: "c@example.com" });
  assert.deepEqual(await recordEmailEvent(e, { eventId: "ev-5", type: "opened", recipient: "d@example.com" }), { stored: false });
  assert.deepEqual((await suppressedList(e)).map((s) => [s.address, s.reason]), [["c@example.com", "Complaint"]]);
});
