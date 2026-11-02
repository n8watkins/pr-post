// Pure helpers for the Worker, kept separate so they can be tested with node --test.

export const GITHUB_RE = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/;
const ID_RE = /^[a-z0-9](?:[a-z0-9-]{0,38})$/;
const CHANNEL_RE = /^UC[A-Za-z0-9_-]{22}$/;
const TICKER_RE = /^[A-Z0-9]{1,6}$/;
export const PEOPLE_MARKER = "# People to follow.";

function str(v, max, field) {
  if (v === undefined || v === null || v === "") return "";
  if (typeof v !== "string") throw new Error(`${field} must be text`);
  const s = v.trim();
  if (s.length > max) throw new Error(`${field} is longer than ${max} characters`);
  if (/[\u0000-\u001f]/.test(s)) throw new Error(`${field} contains control characters`);
  return s;
}

function httpUrl(v, field) {
  const s = str(v, 300, field);
  let u;
  try { u = new URL(s); } catch { throw new Error(`${field} is not a valid link`); }
  if (u.protocol !== "https:" && u.protocol !== "http:") throw new Error(`${field} must start with http:// or https://`);
  return s;
}

/** Validate what the settings page sends; returns a clean people list or throws. */
export function validatePeople(body) {
  if (!body || !Array.isArray(body.people)) throw new Error("Expected a list of people");
  if (body.people.length > 60) throw new Error("That is more than 60 people");
  const seen = new Set();
  return body.people.map((p, i) => {
    const where = `Person ${i + 1}`;
    if (!p || typeof p !== "object") throw new Error(`${where} is not valid`);
    const github = str(p.github, 39, `${where} username`);
    if (github && !GITHUB_RE.test(github)) throw new Error(`${where}: "${github}" is not a GitHub username`);
    const id = str(p.id, 39, `${where} id`) || github.toLowerCase();
    if (!id) throw new Error(`${where} needs a GitHub username or an id`);
    if (!ID_RE.test(id)) throw new Error(`${where}: id "${id}" must be lowercase letters, digits and dashes`);
    const channel = str(p.youtube_channel_id, 40, `${where} YouTube channel`);
    if (channel && !CHANNEL_RE.test(channel)) throw new Error(`${where}: "${channel}" is not a YouTube channel id`);
    if (!github && !channel) throw new Error(`${where} needs a GitHub username or a YouTube channel to follow`);
    if (seen.has(id)) throw new Error(`${id} is listed twice`);
    seen.add(id);
    const orgs = (Array.isArray(p.orgs) ? p.orgs : []).slice(0, 10).map((o) => {
      const org = str(o, 39, `${where} org`);
      if (!GITHUB_RE.test(org)) throw new Error(`${where}: "${org}" is not a GitHub org`);
      return org;
    });
    const ticker = str(p.ticker, 6, `${where} ticker`).toUpperCase();
    if (ticker && !TICKER_RE.test(ticker)) throw new Error(`${where}: tickers are up to 6 letters or digits`);
    const links = (Array.isArray(p.links) ? p.links : []).slice(0, 12).map((l, j) => ({
      label: str(l && l.label, 40, `${where} link ${j + 1} label`) || "Link",
      url: httpUrl(l && l.url, `${where} link ${j + 1}`),
    }));
    const avatar = p.avatar ? httpUrl(p.avatar, `${where} avatar`) : "";
    return {
      id,
      github,
      name: str(p.name, 60, `${where} name`) || github || id,
      ticker,
      starred: p.starred === true,
      self: p.self === true,
      orgs,
      known_for: str(p.known_for, 240, `${where} known for`),
      youtube_channel_id: channel,
      avatar,
      links,
    };
  });
}

const q = (s) => JSON.stringify(s); // TOML basic strings accept JSON escapes

/** Render the [[people]] section of watchlist.toml. */
export function renderPeople(people) {
  const out = [
    PEOPLE_MARKER + " Add a [[people]] block per person.",
    "#",
    "# github: their GitHub username (optional if they have a YouTube channel).",
    "# id:     the page address, /p/<id>/; defaults to the GitHub username.",
    "# name:   what you call them.",
    "# orgs:   organizations where their real work lands.",
    "# youtube_channel_id: the UC... id of their channel, for recent videos.",
    "# known_for: a one-line reminder of who they are, used in their reports.",
    "# ticker:  their symbol on the paper's Commit Index.",
    "# starred: true to keep them at the top of the sidebar and the front page.",
    "# self:    true for you; your report is written as \"what a stranger sees\".",
    "# links:   socials to show that are not on their GitHub profile.",
    "# The settings page on the web edition rewrites this section.",
    "",
  ];
  for (const p of people) {
    out.push("[[people]]");
    if (p.github) out.push(`github = ${q(p.github)}`);
    if (!p.github || p.id !== p.github.toLowerCase()) out.push(`id = ${q(p.id)}`);
    if (p.ticker) out.push(`ticker = ${q(p.ticker)}`);
    if (p.starred) out.push("starred = true");
    if (p.self) out.push("self = true");
    out.push(`name = ${q(p.name)}`);
    if (p.orgs.length) out.push(`orgs = [${p.orgs.map(q).join(", ")}]`);
    if (p.known_for) out.push(`known_for = ${q(p.known_for)}`);
    if (p.youtube_channel_id) out.push(`youtube_channel_id = ${q(p.youtube_channel_id)}`);
    if (p.avatar) out.push(`avatar = ${q(p.avatar)}`);
    if (p.links.length) {
      out.push("links = [");
      for (const l of p.links) out.push(`  { label = ${q(l.label)}, url = ${q(l.url)} },`);
      out.push("]");
    }
    out.push("");
  }
  return out.join("\n");
}

/** Replace everything from the people marker on, keeping [site], [writer], [delivery], [email] and [links]. */
export function replacePeople(toml, people) {
  const at = toml.indexOf(PEOPLE_MARKER);
  if (at === -1) throw new Error("watchlist.toml is missing the people marker");
  return toml.slice(0, at) + renderPeople(people);
}

export function b64urlToBytes(s) {
  const b64 = s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4);
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
}

export function decodeJwt(token) {
  const parts = String(token || "").split(".");
  if (parts.length !== 3) throw new Error("malformed token");
  const json = (part) => JSON.parse(new TextDecoder().decode(b64urlToBytes(part)));
  return { header: json(parts[0]), payload: json(parts[1]), signed: `${parts[0]}.${parts[1]}`, signature: b64urlToBytes(parts[2]) };
}

/** Claims check for a Cloudflare Access token (signature is checked separately). */
export function checkClaims(payload, { aud, teamDomain, now = Date.now() / 1000 }) {
  const auds = Array.isArray(payload.aud) ? payload.aud : [payload.aud];
  if (!auds.includes(aud)) return "wrong audience";
  if (payload.iss !== `https://${teamDomain}`) return "wrong issuer";
  if (typeof payload.exp !== "number" || payload.exp < now) return "expired";
  if (typeof payload.nbf === "number" && payload.nbf > now + 60) return "not yet valid";
  return null;
}

/** The name the browser's sign-in prompt shows: SITE_TITLE without characters that would break the header. */
export function realm(env) {
  return String(env.SITE_TITLE || "pr-post").replace(/["\\\r\n]/g, "").slice(0, 80) || "pr-post";
}
