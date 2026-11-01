// pr-post: sidebar drawer, people filter, keyboard shortcuts
// and the settings editor. No dependencies.
(() => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  // ----- sidebar drawer (phones) -----
  const toggle = $("#rail-toggle");
  if (toggle) {
    const setOpen = (open) => {
      document.body.classList.toggle("rail-open", open);
      toggle.setAttribute("aria-expanded", String(open));
      if (open) $("#rail-search")?.focus();
    };
    toggle.addEventListener("click", () => setOpen(!document.body.classList.contains("rail-open")));
    document.addEventListener("click", (e) => {
      if (document.body.classList.contains("rail-open") && !e.target.closest(".rail") && e.target !== toggle) setOpen(false);
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") setOpen(false); });
  }

  // ----- people filter -----
  const search = $("#rail-search");
  if (search) {
    search.addEventListener("input", () => {
      const q = search.value.trim().toLowerCase();
      $$(".person-link").forEach((a) => { a.hidden = q && !a.dataset.search.includes(q); });
      $$(".rail-group").forEach((g) => { g.hidden = !$$(".person-link", g).some((a) => !a.hidden); });
    });
    search.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { const first = $$(".person-link").find((a) => !a.hidden); if (first) location.href = first.href; }
    });
  }

  // ----- keyboard: [ and ] move between people, / focuses the filter -----
  document.addEventListener("keydown", (e) => {
    if (e.target.closest("input, textarea, select") || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === "/" && search) { e.preventDefault(); document.body.classList.add("rail-open"); search.focus(); return; }
    const links = $$(".person-link");
    if (!links.length || (e.key !== "[" && e.key !== "]")) return;
    const i = links.findIndex((a) => a.getAttribute("aria-current") === "page");
    const next = e.key === "]" ? (i + 1) % links.length : (i - 1 + links.length) % links.length;
    location.href = links[i === -1 ? 0 : next].href;
  });

  // ----- day bars: one panel open at a time -----
  const dayButtons = $$(".sp[aria-controls]");
  const closeDays = () => dayButtons.forEach((b) => { b.setAttribute("aria-expanded", "false"); $("#" + b.getAttribute("aria-controls")).hidden = true; });
  const openDay = (b, scroll) => {
    const open = b.getAttribute("aria-expanded") === "true";
    closeDays();
    if (open) return;
    const panel = $("#" + b.getAttribute("aria-controls"));
    b.setAttribute("aria-expanded", "true");
    panel.hidden = false;
    if (scroll) panel.scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  };
  dayButtons.forEach((b) => b.addEventListener("click", () => openDay(b, true)));
  $$(".dp-close").forEach((b) => b.addEventListener("click", closeDays));
  // A link like /p/name/#on-2026-10-28 opens that day without jumping down the page.
  const linked = location.hash.startsWith("#on-") && $(`.sp[aria-controls="${CSS.escape("day-" + location.hash.slice(4))}"]`);
  if (linked) openDay(linked, false);

  // ----- settings -----
  const list = $("#set-list");
  if (!list) return;
  const people = JSON.parse($("#watchlist-data").textContent);
  const status = $("#status");
  const saveBtn = $("#save");
  let dirty = false;
  let dragIndex = null;

  const say = (text, kind = "") => { status.textContent = text; status.className = "status " + kind; };
  const markDirty = () => { dirty = true; saveBtn.disabled = false; say("Unsaved changes."); };

  function render() {
    list.innerHTML = "";
    people.forEach((p, i) => {
      const li = document.createElement("li");
      li.className = "set-row";
      li.draggable = true;
      li.dataset.index = i;
      li.innerHTML = `
        <span class="handle" aria-hidden="true" title="Drag to reorder">⋮⋮</span>
        <button class="star-btn" type="button" aria-pressed="${!!p.starred}" aria-label="${p.starred ? "Unstar" : "Star"} ${esc(p.name)}">${p.starred ? "★" : "☆"}</button>
        <div class="set-name"><b>${esc(p.name)}</b><span>${p.github ? "@" + esc(p.github) : esc(p.id)}${p.youtube_channel_id ? " · YouTube" : ""}${p.self ? " · you" : ""}</span></div>
        <div class="set-actions">
          <button class="btn icon" type="button" data-move="-1" aria-label="Move ${esc(p.name)} up" ${i === 0 ? "disabled" : ""}>↑</button>
          <button class="btn icon" type="button" data-move="1" aria-label="Move ${esc(p.name)} down" ${i === people.length - 1 ? "disabled" : ""}>↓</button>
          <button class="btn danger" type="button" data-remove>Remove</button>
        </div>`;
      $(".star-btn", li).addEventListener("click", () => { p.starred = !p.starred; markDirty(); render(); });
      $$("[data-move]", li).forEach((b) => b.addEventListener("click", () => move(i, i + Number(b.dataset.move))));
      $("[data-remove]", li).addEventListener("click", (e) => {
        const b = e.currentTarget;
        if (b.dataset.confirm) { people.splice(i, 1); markDirty(); render(); return; }
        b.dataset.confirm = "1";
        b.textContent = "Confirm remove";
        b.classList.add("solid");
        setTimeout(() => { if (b.isConnected) { delete b.dataset.confirm; b.textContent = "Remove"; b.classList.remove("solid"); } }, 4000);
      });
      li.addEventListener("dragstart", () => { dragIndex = i; li.classList.add("dragging"); });
      li.addEventListener("dragend", () => { li.classList.remove("dragging"); $$(".drop-before").forEach((x) => x.classList.remove("drop-before")); });
      li.addEventListener("dragover", (e) => { e.preventDefault(); li.classList.add("drop-before"); });
      li.addEventListener("dragleave", () => li.classList.remove("drop-before"));
      li.addEventListener("drop", (e) => { e.preventDefault(); if (dragIndex !== null) move(dragIndex, i); dragIndex = null; });
      list.appendChild(li);
    });
  }

  function move(from, to) {
    if (to < 0 || to >= people.length || from === to) return;
    const [p] = people.splice(from, 1);
    people.splice(to, 0, p);
    markDirty();
    render();
  }

  function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

  $("#add-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.currentTarget;
    const github = f.github.value.trim().replace(/^@/, "").replace(/^https?:\/\/github\.com\//, "").replace(/\/.*$/, "");
    if (!/^[A-Za-z0-9-]{1,39}$/.test(github)) { say("That doesn't look like a GitHub username.", "err"); f.github.focus(); return; }
    if (people.some((p) => (p.id || (p.github || "").toLowerCase()) === github.toLowerCase())) { say(`@${github} is already on the list.`, "err"); return; }
    const orgs = f.orgs.value.split(",").map((s) => s.trim()).filter(Boolean);
    people.push({ id: github.toLowerCase(), github, name: f.name.value.trim() || github, orgs, known_for: f.known_for.value.trim(), starred: false });
    f.reset();
    markDirty();
    render();
    say(`Added @${github}. Save to start following them; their first report comes with the next run.`);
  });

  saveBtn.addEventListener("click", async () => {
    saveBtn.disabled = true;
    say("Saving…");
    try {
      const res = await fetch("/api/watchlist", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ people }) });
      if (res.status === 404 || res.status === 405) throw new Error("Saving turns on once the site is deployed. Your changes are still on this page.");
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.error || `Saving failed (${res.status}). Your changes are still on this page.`);
      dirty = false;
      say("Saved. The site rebuilds in about a minute, and the next run uses the new list.", "ok");
    } catch (err) {
      saveBtn.disabled = false;
      say(err.message, "err");
    }
  });

  window.addEventListener("beforeunload", (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } });
  render();
})();
