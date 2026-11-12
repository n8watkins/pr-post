// pr-post: the installable app. Registers the service worker (sw.js, built by
// scripts/build_site.py) and offers one small, dismissible Install card: a
// button where the browser fires beforeinstallprompt, a short Add to Home
// Screen hint on iPhone and iPad. Dismissing or installing is remembered.
(() => {
  const here = document.currentScript && document.currentScript.src;
  if (here && "serviceWorker" in navigator) {
    // sw.js and its scope sit one folder above assets/, so a paper under a base path works too.
    const register = () => navigator.serviceWorker.register(new URL("../sw.js", here).href, { scope: new URL("../", here).href }).catch(() => {});
    if (document.readyState === "complete") register();
    else window.addEventListener("load", register);
  }

  const KEY = "pr-post-install";
  const card = document.getElementById("install");
  if (!card) return;
  const store = (() => { try { return window.localStorage; } catch { return null; } })();
  const remembered = () => { try { return store && store.getItem(KEY); } catch { return null; } };
  const remember = (value) => { try { if (store) store.setItem(KEY, value); } catch { /* storage blocked */ } };
  const installed = () => window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
  if (installed() || remembered()) return;

  const hide = (why) => { card.hidden = true; if (why) remember(why); };
  const show = (kind) => {
    for (const el of card.querySelectorAll("[data-for]")) el.hidden = el.dataset.for !== kind;
    card.hidden = false;
  };

  let deferred = null;
  window.addEventListener("beforeinstallprompt", (e) => {
    if (remembered()) return;
    e.preventDefault();
    deferred = e;
    show("prompt");
  });
  window.addEventListener("appinstalled", () => hide("installed"));
  card.querySelector("[data-install]").addEventListener("click", async () => {
    if (!deferred) return;
    const prompt = deferred;
    deferred = null;
    await prompt.prompt();
    const { outcome } = await prompt.userChoice;
    hide(outcome === "accepted" ? "installed" : "dismissed");
  });
  card.querySelector("[data-dismiss]").addEventListener("click", () => hide("dismissed"));

  // iOS has no install event: Safari adds to the Home Screen from the Share menu.
  // Only Safari itself gets the hint: in-app browsers (Gmail, Slack, Instagram) cannot add
  // to the Home Screen, and their user agents lack Safari's "Version/... Safari/" pair.
  // iPadOS reports itself as a Mac with touch.
  const ua = window.navigator.userAgent || "";
  const ios = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && window.navigator.maxTouchPoints > 1);
  const safari = /Version\/[\d.]+.*Safari\//.test(ua) && !/CriOS|FxiOS|EdgiOS|OPiOS|GSA\/|FBAN|FBAV|Instagram|Line\//.test(ua);
  if (ios && safari) show("ios");
})();
