// Stand-ins for the Worker's D1 (on node:sqlite, with the real migrations) and
// R2 bindings, for tests/worker_store.test.mjs and tests/serve_worker.mjs.
import { DatabaseSync } from "node:sqlite";
import { readFileSync, readdirSync } from "node:fs";
import { createHash } from "node:crypto";

const MIGRATIONS = new URL("../paper/migrations/", import.meta.url);

class Statement {
  constructor(db, sql, args = []) { this.db = db; this.sql = sql; this.args = args; }
  bind(...args) { return new Statement(this.db, this.sql, args); }
  _stmt() { return this.db.prepare(this.sql); }
  async all() { return this._all(); }
  _all() {
    const s = this._stmt();
    if (/^\s*(SELECT|WITH)/i.test(this.sql)) return { results: s.all(...this.args).map((r) => ({ ...r })), meta: { changes: 0 } };
    const r = s.run(...this.args);
    return { results: [], meta: { changes: Number(r.changes) } };
  }
  async first(col) { const r = this._all().results[0]; return r === undefined ? null : col ? r[col] : r; }
  async run() { return this._all(); }
}

export function fakeD1() {
  const db = new DatabaseSync(":memory:");
  for (const f of readdirSync(MIGRATIONS).filter((n) => n.endsWith(".sql")).sort()) db.exec(readFileSync(new URL(f, MIGRATIONS), "utf8"));
  return {
    raw: db,
    prepare: (sql) => new Statement(db, sql),
    async batch(stmts) {
      db.exec("BEGIN");
      try { const out = stmts.map((s) => s._all()); db.exec("COMMIT"); return out; } catch (e) { db.exec("ROLLBACK"); throw e; }
    },
  };
}

function bytes(value) {
  if (value instanceof Uint8Array) return value;
  if (value instanceof ArrayBuffer) return new Uint8Array(value);
  if (typeof value === "string") return new TextEncoder().encode(value);
  throw new Error("fake R2 takes bytes or text");
}

export function fakeR2() {
  const objects = new Map();
  const view = (key, o, withBody) => {
    const r = { key, size: o.data.length, customMetadata: o.customMetadata, httpMetadata: o.httpMetadata, httpEtag: `"${o.etag}"`, etag: o.etag };
    if (withBody) Object.assign(r, { body: o.data, arrayBuffer: async () => o.data.buffer.slice(0), text: async () => new TextDecoder().decode(o.data) });
    return r;
  };
  return {
    objects,
    async put(key, value, opts = {}) {
      const data = bytes(value);
      objects.set(key, { data, etag: createHash("md5").update(data).digest("hex"), customMetadata: opts.customMetadata || {}, httpMetadata: opts.httpMetadata || {} });
    },
    async get(key, opts = {}) {
      const o = objects.get(key);
      if (!o) return null;
      const inm = opts.onlyIf && opts.onlyIf.get && opts.onlyIf.get("If-None-Match");
      return view(key, o, !(inm && inm === `"${o.etag}"`));
    },
    async head(key) { const o = objects.get(key); return o ? view(key, o, false) : null; },
    async delete(key) { objects.delete(key); },
    async list({ prefix = "", cursor, limit = 2 } = {}) {
      // A tiny page size, so the tests exercise the cursor.
      const keys = [...objects.keys()].filter((k) => k.startsWith(prefix)).sort();
      const start = cursor ? Number(cursor) : 0;
      const page = keys.slice(start, start + limit);
      const truncated = start + limit < keys.length;
      return { objects: page.map((k) => view(k, objects.get(k), false)), truncated, cursor: truncated ? String(start + limit) : undefined };
    },
  };
}
