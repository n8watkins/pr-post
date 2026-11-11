// Serves the Worker over HTTP with fake D1 and R2 bindings, for
// tests/test_store.py (store.py against the real Worker code).
//   node tests/serve_worker.mjs   -> prints "listening <port>"
import { createServer } from "node:http";
import worker from "../worker/index.js";
import { fakeD1, fakeR2 } from "./fakes.mjs";

const env = {
  DB: fakeD1(), STORE: fakeR2(), SITE_PREFIX: "site/",
  JOBS_TOKEN: process.env.JOBS_TOKEN, WRITER_TOKEN: process.env.WRITER_TOKEN, SITE_PASSWORD: process.env.SITE_PASSWORD,
};
if (process.env.SEED_SQL) env.DB.raw.exec(process.env.SEED_SQL);
// A stand-in for mailroom (tests/test_email_worker.py): answers with an id made
// from the kind and key, and refuses one address the way a spent cap does.
if (process.env.FAKE_MAILROOM) {
  env.MAILROOM = {
    async send(m) {
      if (m.to === "capped@example.com") return { ok: false, code: "cap_app_day", retryable: true, message: "This app reached its daily email cap." };
      return { ok: true, messageId: `mr-${m.kind}-${m.key}` };
    },
  };
}

createServer(async (req, res) => {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const body = ["GET", "HEAD"].includes(req.method) ? undefined : Buffer.concat(chunks);
  const r = await worker.fetch(new Request(`http://127.0.0.1${req.url}`, { method: req.method, headers: req.headers, body }), env);
  res.writeHead(r.status, Object.fromEntries(r.headers));
  res.end(Buffer.from(await r.arrayBuffer()));
}).listen(0, "127.0.0.1", function () { console.log(`listening ${this.address().port}`); });
