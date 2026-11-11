// pr-post: sidebar drawer, people filter, keyboard shortcuts, the undo toast,
// the People page and the settings editor. No dependencies.
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

  // ----- a jump link to a folded section or a Who to follow card opens it -----
  const openTarget = () => {
    const id = decodeURIComponent(location.hash.slice(1));
    const el = id && document.getElementById(id);
    if (!el) return;
    const details = el.matches("details") ? el : el.querySelector("details.fc-report, details.rsect");
    if (details) details.open = true;
    const outer = el.parentElement && el.parentElement.closest("details");
    if (outer) { outer.open = true; el.scrollIntoView(); }
  };
  openTarget();
  window.addEventListener("hashchange", openTarget);

  // ----- toast: one at a time, with an optional Undo (also Ctrl/Cmd+Z while it shows) -----
  let toastTimer = null;
  let toastUndo = null;
  function toast(text, { undo = null, kind = "" } = {}) {
    let el = $("#toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "toast";
      el.className = "toast";
      el.setAttribute("role", "status");
      document.body.appendChild(el);
    }
    clearTimeout(toastTimer);
    toastUndo = undo;
    el.className = `toast ${kind}`;
    el.innerHTML = '<span class="toast-text"></span>';
    $(".toast-text", el).textContent = text;
    if (undo) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "btn sm";
      b.textContent = "Undo";
      b.setAttribute("aria-keyshortcuts", "Control+Z Meta+Z");
      b.addEventListener("click", () => runUndo());
      el.appendChild(b);
    }
    el.hidden = false;
    toastTimer = setTimeout(hideToast, undo ? 6000 : 4000);
  }
  function hideToast() {
    clearTimeout(toastTimer);
    const el = $("#toast");
    if (el) el.hidden = true;
    const u = toastUndo;
    toastUndo = null;
    return u;
  }
  function runUndo() {
    const u = hideToast();
    if (u) u();
  }
  document.addEventListener("keydown", (e) => {
    if (toastUndo && (e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === "z" && !e.target.closest("input, textarea, select")) {
      e.preventDefault();
      runUndo();
    }
  });

  // ----- people (a paper kept in Cloudflare: the Worker's /api/people) -----
  const peopleApp = $("#people-app");
  if (peopleApp) peoplePage(peopleApp);

  function peoplePage(app) {
    const escp = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    let data = { people: [], suppressed: [], changes: [] };
    const open = new Set(); // ids whose edit form is open

    async function api(method, path, body) {
      const res = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
      const out = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(out.error || `That didn't work (${res.status}).`);
      return out;
    }

    async function load() {
      try {
        data = await api("GET", app.dataset.api);
        render();
      } catch (err) {
        app.innerHTML = `<p class="status err">Couldn't load the watch list: ${escp(err.message)}</p>`;
      }
    }

    function flash(text, kind = "") {
      const el = $("#people-status", app);
      if (el) { el.textContent = text; el.className = "status " + kind; }
    }

    async function act(fn, done) {
      try { await fn(); flash(done, "ok"); await load(); flash(done, "ok"); } catch (err) { flash(err.message, "err"); }
    }

    const who = (p) => {
      const e = p.entry;
      const bits = [e.github ? "@" + escp(e.github) : escp(p.id)];
      if (e.youtube_channel_id) bits.push("YouTube");
      if ((e.links || []).length) bits.push(`${e.links.length} link${e.links.length === 1 ? "" : "s"}`);
      if (e.self) bits.push("you");
      return bits.join(" · ");
    };

    // Remove and Discard happen at once with an Undo toast; the DELETE is sent when the
    // toast runs out, when another removal starts, or when the page is left.
    let pending = null; // { id, name, timer }
    async function commitRemoval(leaving = false) {
      if (!pending) return;
      const { id, name, timer } = pending;
      clearTimeout(timer);
      pending = null;
      try {
        const res = await fetch(`/api/people/${id}`, { method: "DELETE", keepalive: leaving });
        if (!res.ok) {
          const out = await res.json().catch(() => ({}));
          throw new Error(out.error || `That didn't work (${res.status}).`);
        }
        if (!leaving) await load();
      } catch (err) {
        if (leaving) return;
        await load();
        flash(`Could not remove ${name}, so they are still here. ${err.message}`, "err");
      }
    }
    function remove(p) {
      commitRemoval();
      const verb = p.status === "draft" ? "Discarded" : "Removed";
      const id = p.id;
      const name = p.entry.name;
      pending = { id, name, timer: setTimeout(() => commitRemoval(), 6000) };
      render();
      toast(`${verb} ${name}.`, {
        undo: () => {
          if (!pending || pending.id !== id) return;
          clearTimeout(pending.timer);
          pending = null;
          render();
          flash(`${name} is back.`, "ok");
        },
      });
    }
    window.addEventListener("pagehide", () => commitRemoval(true));

    function button(label, onClick, extra = "") {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "btn " + extra;
      b.textContent = label;
      b.addEventListener("click", onClick);
      return b;
    }

    function editForm(p) {
      const e = p.entry;
      const f = document.createElement("form");
      f.className = "add-form people-edit";
      f.autocomplete = "off";
      f.innerHTML = `
        <label>Name<input name="name" value="${escp(e.name)}" required maxlength="60"></label>
        <label>Ticker (up to 6)<input name="ticker" value="${escp(e.ticker)}" maxlength="6"></label>
        <label class="wide">Known for<input name="known_for" value="${escp(e.known_for)}" maxlength="240"></label>
        <label>YouTube channel id<input name="youtube_channel_id" value="${escp(e.youtube_channel_id)}" placeholder="UC..."></label>
        <label>Orgs (comma separated)<input name="orgs" value="${escp((e.orgs || []).join(", "))}"></label>
        <label class="wide">Links, one per line: label | https://...<textarea name="links" rows="3">${escp((e.links || []).map((l) => `${l.label} | ${l.url}`).join("\n"))}</textarea></label>
        <label class="wide">Avatar (optional link)<input name="avatar" value="${escp(e.avatar)}"></label>
        <label class="check"><input type="checkbox" name="starred" ${e.starred ? "checked" : ""}> Starred</label>
        <label class="check"><input type="checkbox" name="self" ${e.self ? "checked" : ""}> This is me</label>
        <div class="wide"><button class="btn solid" type="submit">Save</button> <button class="btn" type="button" data-cancel>Cancel</button></div>`;
      $("[data-cancel]", f).addEventListener("click", () => { open.delete(p.id); render(); });
      f.addEventListener("submit", (ev) => {
        ev.preventDefault();
        const links = f.links.value.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
          const at = l.lastIndexOf("|");
          return at === -1 ? { label: "Link", url: l } : { label: l.slice(0, at).trim() || "Link", url: l.slice(at + 1).trim() };
        });
        const entry = { ...e, name: f.name.value.trim(), ticker: f.ticker.value.trim(), known_for: f.known_for.value.trim(),
          youtube_channel_id: f.youtube_channel_id.value.trim(), orgs: f.orgs.value.split(",").map((o) => o.trim()).filter(Boolean),
          links, avatar: f.avatar.value.trim(), starred: f.starred.checked, self: f.self.checked };
        act(async () => { await api("PUT", `/api/people/${p.id}`, { entry }); open.delete(p.id); }, `Saved ${entry.name}.`);
      });
      return f;
    }

    function row(p, list) {
      const li = document.createElement("li");
      li.className = "set-row people-row";
      const e = p.entry;
      const i = list.indexOf(p);
      li.innerHTML = `
        <button class="star-btn" type="button" aria-pressed="${!!e.starred}" aria-label="${e.starred ? "Unstar" : "Star"} ${escp(e.name)}" ${p.status === "draft" ? "hidden" : ""}>${e.starred ? "★" : "☆"}</button>
        <div class="set-name"><b>${escp(e.name)}</b><span>${who(p)}</span></div>
        <div class="set-actions"></div>`;
      const actions = $(".set-actions", li);
      $(".star-btn", li).addEventListener("click", () =>
        act(() => api("PUT", `/api/people/${p.id}`, { entry: { ...e, starred: !e.starred } }), `${e.starred ? "Unstarred" : "Starred"} ${e.name}.`));
      if (p.status === "active") {
        const ids = list.map((x) => x.id);
        const move = (d) => { const j = i + d; [ids[i], ids[j]] = [ids[j], ids[i]]; act(() => api("PUT", "/api/people/order", { ids }), "Order saved."); };
        const up = button("↑", () => move(-1), "icon"); up.disabled = i === 0; up.setAttribute("aria-label", `Move ${e.name} up`);
        const down = button("↓", () => move(1), "icon"); down.disabled = i === list.length - 1; down.setAttribute("aria-label", `Move ${e.name} down`);
        actions.append(up, down);
      }
      if (p.status === "draft") {
        actions.append(button("Approve", () => act(() => api("POST", `/api/people/${p.id}/status`, { status: "active" }), `Now following ${e.name}; their first report comes with the next run.`), "solid"));
      }
      actions.append(button(open.has(p.id) ? "Close" : "Edit", () => { open.has(p.id) ? open.delete(p.id) : open.add(p.id); render(); }));
      if (p.status === "active") actions.append(button("Pause", () => act(() => api("POST", `/api/people/${p.id}/status`, { status: "paused" }), `Paused ${e.name}.`)));
      if (p.status === "paused") actions.append(button("Resume", () => act(() => api("POST", `/api/people/${p.id}/status`, { status: "active" }), `Resumed ${e.name}.`)));
      actions.append(button(p.status === "draft" ? "Discard" : "Remove", () => remove(p), "danger"));
      if (p.note || p.profile) {
        const d = document.createElement("details");
        d.className = "people-more";
        d.open = p.status === "draft";
        d.innerHTML = `<summary>${p.status === "draft" ? "Draft profile and what to check" : "Profile"}</summary>${p.note ? `<p class="muted">${escp(p.note)}</p>` : ""}<pre class="people-profile">${escp(p.profile)}</pre>`;
        li.appendChild(d);
      }
      if (open.has(p.id)) li.appendChild(editForm(p));
      return li;
    }

    function section(title, people, empty) {
      const wrap = document.createDocumentFragment();
      const h = document.createElement("div");
      h.className = "sect";
      h.textContent = `${title} (${people.length})`;
      wrap.appendChild(h);
      if (!people.length) {
        const pmt = document.createElement("p");
        pmt.className = "muted";
        pmt.textContent = empty;
        wrap.appendChild(pmt);
        return wrap;
      }
      const ul = document.createElement("ul");
      ul.className = "set-list";
      people.forEach((p) => ul.appendChild(row(p, people)));
      wrap.appendChild(ul);
      return wrap;
    }

    function render() {
      const by = (s) => data.people.filter((p) => p.status === s && p.id !== pending?.id);
      const drafts = by("draft");
      app.innerHTML = `<p class="status" id="people-status" role="status"></p>`;
      if (drafts.length) app.appendChild(section("Drafts waiting for you", drafts, ""));
      app.appendChild(section("Following", by("active"), "Nobody yet. Add someone below."));
      app.appendChild(section("Paused", by("paused"), "Nobody is paused. Paused people stay on the list but are not collected or written about."));
      if (data.suppressed.length) {
        const h = document.createElement("div");
        h.className = "sect";
        h.textContent = "Email is stopped for";
        const ul = document.createElement("ul");
        ul.className = "set-list";
        for (const s of data.suppressed) {
          const li = document.createElement("li");
          li.className = "set-row people-row";
          li.innerHTML = `<span></span><div class="set-name"><b>${escp(s.address)}</b><span>${escp(s.reason)} · ${escp(String(s.at).slice(0, 10))}</span></div><div class="set-actions"></div>`;
          $(".set-actions", li).appendChild(button("Send again", () => act(() => api("POST", "/api/email/clear", { address: s.address }), "Email will go to that address again.")));
          ul.appendChild(li);
        }
        app.append(h, ul);
      }
      if (data.changes.length) {
        const d = document.createElement("details");
        d.className = "people-more people-changes";
        d.innerHTML = `<summary>Recent changes</summary><ul>${data.changes.map((c) => `<li><span class="label">${escp(c.at.slice(0, 16).replace("T", " "))}</span> ${escp(c.what)} <span class="muted">(${escp(c.who)})</span></li>`).join("")}</ul>`;
        app.appendChild(d);
      }
    }

    const add = $("#people-add");
    if (add) {
      add.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const st = $("#people-add-status");
        const github = add.github.value.trim();
        st.className = "status";
        st.textContent = "Starting…";
        try {
          const out = await api("POST", "/api/people/drafts", { github });
          st.className = "status ok";
          st.textContent = out.message;
          add.reset();
          let tries = 0;
          const before = data.people.length;
          const poll = setInterval(async () => {
            tries += 1;
            await load();
            if (data.people.length > before || tries >= 12) clearInterval(poll);
          }, 15000);
        } catch (err) {
          st.className = "status err";
          st.textContent = err.message;
        }
      });
    }
    load();
  }

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
      $("[data-remove]", li).addEventListener("click", () => {
        const [gone] = people.splice(i, 1);
        markDirty();
        render();
        toast(`Removed ${gone.name}. Save changes to keep it.`, {
          undo: () => { people.splice(Math.min(i, people.length), 0, gone); markDirty(); render(); },
        });
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
