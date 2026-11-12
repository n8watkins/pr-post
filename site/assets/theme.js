// pr-post: the theme (System, Light or Dark). Loaded in <head> without defer, so
// the chosen theme is on <html> before the page first paints and nothing
// flashes. The choice lives in localStorage; System stores nothing.
(() => {
  const KEY = "pr-post-theme";
  const root = document.documentElement;
  // Each theme's color comes from the theme-color metas as the build wrote them (one per color scheme).
  const COLORS = {};
  for (const meta of document.querySelectorAll('meta[name="theme-color"]')) {
    COLORS[/dark/.test(meta.getAttribute("media") || "") ? "dark" : "light"] = meta.getAttribute("content");
  }

  const read = () => {
    try {
      const v = window.localStorage.getItem(KEY);
      return v === "light" || v === "dark" ? v : "system";
    } catch { return "system"; }
  };
  const save = (choice) => {
    try {
      if (choice === "system") window.localStorage.removeItem(KEY);
      else window.localStorage.setItem(KEY, choice);
    } catch { /* storage blocked: the choice lasts for this page only */ }
  };
  // System leaves the attribute off, so the stylesheet follows prefers-color-scheme.
  // Each theme-color meta keeps its own media query; a fixed choice gives both the same color.
  const apply = (choice) => {
    if (choice === "light" || choice === "dark") root.setAttribute("data-theme", choice);
    else root.removeAttribute("data-theme");
    for (const meta of document.querySelectorAll('meta[name="theme-color"]')) {
      const own = /dark/.test(meta.getAttribute("media") || "") ? "dark" : "light";
      meta.setAttribute("content", COLORS[choice === "light" || choice === "dark" ? choice : own]);
    }
  };
  const sync = () => {
    const choice = read();
    for (const input of document.querySelectorAll('input[name="pp-theme"]')) input.checked = input.value === choice;
  };

  apply(read());
  document.addEventListener("DOMContentLoaded", () => {
    sync();
    document.addEventListener("change", (e) => {
      const input = e.target;
      if (!input || input.name !== "pp-theme" || !input.checked) return;
      save(input.value);
      apply(input.value);
    });
  });
  // Another tab changed it, or this page came back from the back-forward cache.
  window.addEventListener("storage", (e) => { if (e.key === KEY || e.key === null) { apply(read()); sync(); } });
  window.addEventListener("pageshow", (e) => { if (e.persisted) { apply(read()); sync(); } });
})();
