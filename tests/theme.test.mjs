// node --test tests/ : the theme script (site/assets/theme.js) and the install card
// (site/assets/pwa.js) without a browser, against a small fake document.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const THEME = readFileSync(new URL("../site/assets/theme.js", import.meta.url), "utf8");
const PWA = readFileSync(new URL("../site/assets/pwa.js", import.meta.url), "utf8");
const LIGHT = "#f8f7f3";
const DARK = "#18181c";

function storage(initial = {}, { broken = false } = {}) {
  const data = { ...initial };
  const guard = () => { if (broken) throw new Error("SecurityError"); };
  return {
    data,
    getItem: (k) => { guard(); return k in data ? data[k] : null; },
    setItem: (k, v) => { guard(); data[k] = String(v); },
    removeItem: (k) => { guard(); delete data[k]; },
  };
}

function element(attrs = {}) {
  const a = { ...attrs };
  return {
    attrs: a, hidden: Boolean(a.hidden), dataset: {}, listeners: {},
    getAttribute: (k) => (k in a ? a[k] : null),
    setAttribute: (k, v) => { a[k] = String(v); },
    removeAttribute: (k) => { delete a[k]; },
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
    fire(type, e = {}) { if (!("target" in e)) e.target = this; for (const fn of this.listeners[type] || []) fn(e); },
  };
}

/** A page with the two theme-color metas and the three theme radios. */
function page({ stored = {}, broken = false } = {}) {
  const root = element();
  const metas = [element({ name: "theme-color", content: LIGHT, media: "(prefers-color-scheme: light)" }),
    element({ name: "theme-color", content: DARK, media: "(prefers-color-scheme: dark)" })];
  const radios = ["system", "light", "dark"].map((value) => Object.assign(element(), { name: "pp-theme", value, checked: value === "system" }));
  const doc = element();
  doc.documentElement = root;
  doc.querySelectorAll = (sel) => (sel.includes("theme-color") ? metas : sel.includes("pp-theme") ? radios : []);
  const win = element();
  win.localStorage = storage(stored, { broken });
  const ctx = { document: doc, window: win };
  vm.runInNewContext(THEME, ctx);
  const choose = (value) => {
    const r = radios.find((x) => x.value === value);
    radios.forEach((x) => { x.checked = x === r; });
    doc.fire("change", { target: r });
  };
  return { root, metas, radios, doc, win, choose, colors: () => metas.map((m) => m.getAttribute("content")) };
}

test("with nothing stored the page follows the system and each meta keeps its color", () => {
  const p = page();
  assert.equal(p.root.getAttribute("data-theme"), null);
  assert.deepEqual(p.colors(), [LIGHT, DARK]);
});

test("a stored choice is on <html> before the page paints, with matching theme colors", () => {
  const dark = page({ stored: { "pr-post-theme": "dark" } });
  assert.equal(dark.root.getAttribute("data-theme"), "dark");
  assert.deepEqual(dark.colors(), [DARK, DARK]);
  const light = page({ stored: { "pr-post-theme": "light" } });
  assert.equal(light.root.getAttribute("data-theme"), "light");
  assert.deepEqual(light.colors(), [LIGHT, LIGHT]);
});

test("anything else stored, or storage that throws, means System", () => {
  assert.equal(page({ stored: { "pr-post-theme": "neon" } }).root.getAttribute("data-theme"), null);
  const blocked = page({ broken: true });
  assert.equal(blocked.root.getAttribute("data-theme"), null);
  blocked.doc.fire("DOMContentLoaded");
  blocked.choose("dark"); // still applies for this page
  assert.equal(blocked.root.getAttribute("data-theme"), "dark");
});

test("the radios show the stored choice, and choosing saves and applies it", () => {
  const p = page({ stored: { "pr-post-theme": "dark" } });
  p.doc.fire("DOMContentLoaded");
  assert.deepEqual(p.radios.map((r) => r.checked), [false, false, true]);
  p.choose("light");
  assert.equal(p.win.localStorage.data["pr-post-theme"], "light");
  assert.equal(p.root.getAttribute("data-theme"), "light");
  assert.deepEqual(p.colors(), [LIGHT, LIGHT]);
  p.choose("system");
  assert.equal("pr-post-theme" in p.win.localStorage.data, false, "System stores nothing");
  assert.equal(p.root.getAttribute("data-theme"), null);
  assert.deepEqual(p.colors(), [LIGHT, DARK]);
});

test("other inputs on the page do not change the theme", () => {
  const p = page();
  p.doc.fire("DOMContentLoaded");
  p.doc.fire("change", { target: { name: "github", value: "dark", checked: true } });
  assert.equal(p.root.getAttribute("data-theme"), null);
});

test("a change in another tab, or a page restored from the back-forward cache, catches up", () => {
  const p = page();
  p.doc.fire("DOMContentLoaded");
  p.win.localStorage.data["pr-post-theme"] = "dark";
  p.win.fire("storage", { key: "pr-post-theme" });
  assert.equal(p.root.getAttribute("data-theme"), "dark");
  assert.equal(p.radios[2].checked, true);
  p.win.localStorage.data["pr-post-theme"] = "light";
  p.win.fire("pageshow", { persisted: true });
  assert.equal(p.root.getAttribute("data-theme"), "light");
});

// ----- the install card -----

function installPage({ ua = "Mozilla/5.0 (X11; Linux x86_64) Chrome/140", stored = {}, standalone = false } = {}) {
  const parts = { "[data-install]": element(), "[data-dismiss]": element() };
  const texts = [Object.assign(element(), { dataset: { for: "prompt" } }), Object.assign(element({ hidden: "" }), { dataset: { for: "ios" } })];
  texts[1].hidden = true;
  const card = Object.assign(element({ hidden: "" }), { hidden: true });
  card.querySelector = (sel) => parts[sel];
  card.querySelectorAll = () => [...texts, Object.assign(parts["[data-install]"], { dataset: { for: "prompt" } })];
  const doc = element();
  doc.getElementById = (id) => (id === "install" ? card : null);
  doc.currentScript = null; // no service worker registration in these tests
  const win = element();
  win.localStorage = storage(stored);
  win.matchMedia = () => ({ matches: standalone });
  win.navigator = { userAgent: ua, maxTouchPoints: 0, standalone: false };
  vm.runInNewContext(PWA, { document: doc, window: win, navigator: win.navigator });
  return { card, texts, parts, win };
}

function installEvent(outcome) {
  const e = { prevented: false, prompted: false, preventDefault() { this.prevented = true; }, async prompt() { e.prompted = true; }, userChoice: Promise.resolve({ outcome }) };
  return e;
}

test("the install card waits for the browser to offer installing", async () => {
  const p = installPage();
  assert.equal(p.card.hidden, true);
  const e = installEvent("accepted");
  p.win.fire("beforeinstallprompt", e);
  assert.equal(e.prevented, true);
  assert.equal(p.card.hidden, false);
  assert.equal(p.texts[0].hidden, false);
  assert.equal(p.texts[1].hidden, true);
  p.parts["[data-install]"].fire("click");
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(e.prompted, true);
  assert.equal(p.card.hidden, true);
  assert.equal(p.win.localStorage.data["pr-post-install"], "installed");
});

test("Not now is remembered, and a remembered card never comes back", () => {
  const p = installPage();
  p.win.fire("beforeinstallprompt", installEvent("dismissed"));
  p.parts["[data-dismiss]"].fire("click");
  assert.equal(p.card.hidden, true);
  assert.equal(p.win.localStorage.data["pr-post-install"], "dismissed");
  const again = installPage({ stored: { "pr-post-install": "dismissed" } });
  const e = installEvent("accepted");
  again.win.fire("beforeinstallprompt", e);
  assert.equal(again.card.hidden, true);
  assert.equal(e.prevented, false, "the browser keeps its own install entry point");
});

test("iPhone gets the Add to Home Screen hint; an installed app gets nothing", () => {
  const ios = installPage({ ua: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1" });
  assert.equal(ios.card.hidden, false);
  assert.equal(ios.texts[1].hidden, false);
  assert.equal(ios.texts[0].hidden, true);
  assert.equal(ios.parts["[data-install]"].hidden, true, "no Install button where there is no install event");
  const installed = installPage({ ua: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)", standalone: true });
  assert.equal(installed.card.hidden, true);
  // In-app browsers cannot add to the Home Screen, so they get no hint.
  for (const ua of ["Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 [FBAN/FBIOS;FBAV/450.0]",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) GSA/300.0 Mobile/15E148 Safari/604.1"]) {
    assert.equal(installPage({ ua }).card.hidden, true, ua);
  }
});
