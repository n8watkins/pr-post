// node --test tests/
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { validatePeople, replacePeople, checkClaims, decodeJwt, PEOPLE_MARKER } from "../worker/lib.js";

const person = { github: "simonw", name: "Simon", ticker: "simw", orgs: ["datasette"], links: [{ label: "X", url: "https://x.com/simonw" }] };
const tuber = { id: "nateherk", name: "Nate Herk", youtube_channel_id: "UC2ojq-nuP8ceeHqiroeKhBA" };

test("validatePeople cleans and normalises", () => {
  const [p] = validatePeople({ people: [person] });
  assert.equal(p.id, "simonw");
  assert.equal(p.ticker, "SIMW");
  assert.equal(p.starred, false);
  assert.deepEqual(p.orgs, ["datasette"]);
});

test("validatePeople accepts a YouTube-only person with an id", () => {
  const [p] = validatePeople({ people: [tuber] });
  assert.equal(p.id, "nateherk");
  assert.equal(p.github, "");
  assert.equal(p.youtube_channel_id, "UC2ojq-nuP8ceeHqiroeKhBA");
});

test("validatePeople rejects bad input", () => {
  assert.throws(() => validatePeople({}), /list of people/);
  assert.throws(() => validatePeople({ people: [{ github: "not valid!" }] }), /GitHub username/);
  assert.throws(() => validatePeople({ people: [person, person] }), /twice/);
  assert.throws(() => validatePeople({ people: [{ ...person, links: [{ label: "x", url: "javascript:alert(1)" }] }] }), /http/);
  assert.throws(() => validatePeople({ people: [{ ...person, name: "a\nb" }] }), /control/);
  assert.throws(() => validatePeople({ people: [{ name: "nobody" }] }), /username or an id/);
  assert.throws(() => validatePeople({ people: [{ id: "x" }] }), /to follow/);
  assert.throws(() => validatePeople({ people: [{ ...tuber, youtube_channel_id: "nope" }] }), /YouTube channel id/);
  assert.throws(() => validatePeople({ people: [{ ...tuber, id: "Bad Id" }] }), /lowercase/);
  assert.throws(() => validatePeople({ people: [{ ...person, avatar: "javascript:alert(1)" }] }), /http/);
});

test("replacePeople keeps the top of the file and round-trips through TOML rules", () => {
  const toml = readFileSync(new URL("../demo/watchlist.toml", import.meta.url), "utf8");
  const out = replacePeople(toml, validatePeople({ people: [{ ...person, known_for: 'says "hi" \\ there', starred: true }, tuber] }));
  assert.ok(out.startsWith(toml.slice(0, toml.indexOf(PEOPLE_MARKER))));
  assert.match(out, /\[site\]/);
  assert.match(out, /known_for = "says \\"hi\\" \\\\ there"/);
  assert.match(out, /starred = true/);
  assert.match(out, /links = \[\n  \{ label = "X", url = "https:\/\/x.com\/simonw" \},\n\]/);
  assert.match(out, /\[\[people\]\]\nid = "nateherk"\nname = "Nate Herk"\nyoutube_channel_id = "UC2ojq-nuP8ceeHqiroeKhBA"/);
  assert.doesNotMatch(out, /github = ""/);
});

test("checkClaims enforces audience, issuer and expiry", () => {
  const ok = { aud: ["abc"], iss: "https://team.cloudflareaccess.com", exp: 2e9 };
  const opts = { aud: "abc", teamDomain: "team.cloudflareaccess.com", now: 1.8e9 };
  assert.equal(checkClaims(ok, opts), null);
  assert.equal(checkClaims({ ...ok, aud: ["other"] }, opts), "wrong audience");
  assert.equal(checkClaims({ ...ok, iss: "https://evil.cloudflareaccess.com" }, opts), "wrong issuer");
  assert.equal(checkClaims({ ...ok, exp: 1 }, opts), "expired");
});

test("decodeJwt rejects malformed tokens", () => {
  assert.throws(() => decodeJwt("nope"), /malformed/);
});
