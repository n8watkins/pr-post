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

  // ----- who to follow: "Add to PR Post" copies the one-liner that drafts the entry -----
  $$("[data-copy]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const note = btn.parentElement.querySelector(".fc-copied");
      try {
        await navigator.clipboard.writeText(btn.dataset.copy);
        if (note) note.textContent = "Command copied. Run it to draft a pull request.";
      } catch {
        const details = btn.closest(".fcard")?.querySelector(".fc-report");
        if (details) details.open = true;
        if (note) note.textContent = "Copy the command from the mini report.";
      }
    });
  });

  // ----- this week's picks, one card at a time (PREVIEW) -----
  const picksApp = $("#picks-app");
  if (picksApp) picksPage(picksApp);

  function picksPage(app) {
    const escp = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const fmt = (x) => Number(x || 0).toLocaleString();
    const { week, cards } = JSON.parse($("#picks-data").textContent);
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const key = `pr-post-picks-${week}`;
    // Decisions survive a reload for this week only; the paper itself is the record.
    let done = {};
    try { done = JSON.parse(localStorage.getItem(key) || "{}"); } catch { done = {}; }
    const save = () => { try { localStorage.setItem(key, JSON.stringify(done)); } catch { /* private window: decisions last until reload */ } };
    let stubbed = false;
    let busy = false;
    let last = { kind: "", text: "" }; // the latest status line, kept across re-renders

    const remaining = () => cards.filter((c) => !done[c.login]);
    const position = () => cards.length - remaining().length + 1;

    async function add(c) {
      try {
        const res = await fetch(app.dataset.api, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ github: c.login }) });
        if ([404, 405, 501].includes(res.status)) { stubbed = true; return { kind: "stub", text: `Preview: nothing was saved for @${c.login}.` }; }
        const out = await res.json().catch(() => ({}));
        if (res.status === 409) return { kind: "ok", text: `@${c.login} is already on the paper.` };
        if (!res.ok) return { kind: "err", text: out.error || `Could not add @${c.login} (${res.status}).` };
        return { kind: "ok", text: `Researching @${c.login}. Their draft waits on the People page.` };
      } catch {
        return { kind: "err", text: `Could not reach the paper. @${c.login} was not added.` };
      }
    }

    function toast(r) {
      const t = $("#picks-toast", app);
      last = r;
      if (!t) return;
      t.className = `picks-toast ${r.kind}`;
      t.textContent = r.text;
    }

    function decide(dir) {
      const c = remaining()[0];
      if (!c || busy) return;
      busy = true;
      const card = $(".pick-card", app);
      done[c.login] = dir === "add" ? "added" : "skipped";
      save();
      if (dir === "add") add(c).then((r) => {
        if (r.kind === "err") { delete done[c.login]; save(); render(); }
        if (r.kind === "stub") $(".picks-stub", app)?.removeAttribute("hidden");
        toast(r);
      });
      else toast({ kind: "", text: `Skipped @${c.login}.` });
      const go = () => { busy = false; render(); };
      if (reduce || !card) { go(); return; }
      card.style.transition = "transform .22s ease-in, opacity .22s ease-in";
      card.style.transform = `translateX(${dir === "add" ? 120 : -120}%) rotate(${dir === "add" ? 10 : -10}deg)`;
      card.style.opacity = "0";
      setTimeout(go, 220);
    }

    function undo() {
      const prev = Object.keys(done).pop();
      if (!prev || busy) return;
      const was = done[prev];
      delete done[prev];
      save();
      toast({ kind: "", text: was === "added" ? `Back to @${prev}. A draft already requested stays on the People page.` : `Back to @${prev}.` });
      render();
    }

    function cardHtml(c) {
      const avatar = c.avatar ? `<img class="pick-avatar" src="${escp(c.avatar)}" alt="" width="96" height="96">` : `<span class="pick-avatar pick-initial" aria-hidden="true">${escp(c.name[0])}</span>`;
      const top = c.top && c.top.repo ? `<div><b>${escp(c.top.repo.split("/").pop())}</b><span>top repo · ${fmt(c.top.stars)} stars</span></div>` : "";
      return `<article class="pick-card" tabindex="-1" aria-label="${escp(c.name)}, fit ${c.score}">
        <div class="pick-top">${avatar}<div class="pick-id"><h2>${escp(c.name)}</h2><div class="pick-login">@${escp(c.login)}</div></div>
          <div class="pick-score pick-${escp(c.rec.toLowerCase())}"><b>${c.score}</b><span>${escp(c.rec)}</span></div></div>
        <p class="pick-why">${escp(c.why)}</p>
        <div class="pick-nums"><div><b>${fmt(c.active)}</b><span>contributions in 12 weeks</span></div>
          <div><b>${fmt(c.stars)}</b><span>stars on their repos</span></div><div><b>${fmt(c.followers)}</b><span>followers</span></div>${top}</div>
        <a class="pick-more" href="${escp(app.dataset.all)}#${escp(c.login.toLowerCase())}">Full mini report</a>
      </article>`;
    }

    function render() {
      const left = remaining();
      const counts = Object.values(done).reduce((a, v) => ({ ...a, [v]: (a[v] || 0) + 1 }), {});
      const note = `<p class="picks-stub" ${stubbed ? "" : "hidden"}>Preview build: Add is not connected here, so nothing is saved.</p>`;
      if (!left.length) {
        if (last.kind !== "err") last = { kind: "", text: "" }; // the summary says it all
        app.innerHTML = `<div class="picks-end"><div class="picks-end-mark" aria-hidden="true">✓</div><h2>That's this week's picks</h2>
          <p>You added ${counts.added || 0} and skipped ${counts.skipped || 0}. Added people wait as drafts on the People page until you approve them.</p>
          <div class="picks-buttons"><a class="btn solid" href="${escp(app.dataset.people)}">Review drafts</a><a class="btn" href="${escp(app.dataset.all)}">See the full list</a><button class="btn" type="button" data-restart>Start over</button></div>
          ${note}<p class="picks-toast ${last.kind}" id="picks-toast" role="status">${escp(last.text)}</p></div>`;
        $("[data-restart]", app).addEventListener("click", () => { done = {}; save(); render(); });
        return;
      }
      const c = left[0];
      app.innerHTML = `<div class="picks">
        <div class="picks-progress"><span>${position()} of ${cards.length}</span><span class="picks-bar" aria-hidden="true"><span style="width:${100 * (position() - 1) / cards.length}%"></span></span>
          <button class="btn sm" type="button" data-undo ${Object.keys(done).length ? "" : "disabled"}>Back</button></div>
        <div class="pick-stack">${left[1] ? `<div class="pick-under" aria-hidden="true"></div>` : ""}${cardHtml(c)}</div>
        <div class="picks-buttons">
          <button class="btn pick-skip" type="button" data-skip aria-keyshortcuts="ArrowLeft">Skip</button>
          <a class="btn" href="${escp(c.profile)}" target="_blank" rel="noopener" aria-keyshortcuts="o">Open GitHub</a>
          <button class="btn solid pick-add" type="button" data-add aria-keyshortcuts="ArrowRight">Add to PR Post</button>
        </div>
        ${note}<p class="picks-toast ${last.kind}" id="picks-toast" role="status">${escp(last.text)}</p></div>`;
      $("[data-skip]", app).addEventListener("click", () => decide("skip"));
      $("[data-add]", app).addEventListener("click", () => decide("add"));
      $("[data-undo]", app).addEventListener("click", undo);
      swipe($(".pick-card", app));
    }

    // Touch and mouse: drag the card past a third of its width to decide.
    function swipe(card) {
      let x0 = null, dx = 0;
      card.addEventListener("pointerdown", (e) => {
        if (e.target.closest("a, button")) return;
        x0 = e.clientX; dx = 0; card.setPointerCapture(e.pointerId); card.style.transition = "none";
      });
      card.addEventListener("pointermove", (e) => {
        if (x0 === null) return;
        dx = e.clientX - x0;
        if (!reduce) card.style.transform = `translateX(${dx}px) rotate(${dx / 30}deg)`;
        card.dataset.lean = dx > 40 ? "add" : dx < -40 ? "skip" : "";
      });
      const end = () => {
        if (x0 === null) return;
        x0 = null;
        const far = Math.abs(dx) > card.offsetWidth / 3;
        if (far) { decide(dx > 0 ? "add" : "skip"); return; }
        card.style.transition = reduce ? "none" : "transform .18s ease-out";
        card.style.transform = "";
        card.dataset.lean = "";
      };
      card.addEventListener("pointerup", end);
      card.addEventListener("pointercancel", end);
    }

    document.addEventListener("keydown", (e) => {
      if (e.target.closest("input, textarea, select") || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "ArrowRight") { e.preventDefault(); decide("add"); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); decide("skip"); }
      else if (e.key === "o" || e.key === "O") { const c = remaining()[0]; if (c) window.open(c.profile, "_blank", "noopener"); }
      else if (e.key === "Backspace" || e.key === "z") undo();
    });
    render();
  }

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

    function confirmButton(label, onConfirm) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "btn danger";
      b.textContent = label;
      b.addEventListener("click", () => {
        if (b.dataset.confirm) { onConfirm(); return; }
        b.dataset.confirm = "1";
        b.textContent = "Confirm " + label.toLowerCase();
        b.classList.add("solid");
        setTimeout(() => { if (b.isConnected) { delete b.dataset.confirm; b.textContent = label; b.classList.remove("solid"); } }, 4000);
      });
      return b;
    }

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
      actions.append(confirmButton(p.status === "draft" ? "Discard" : "Remove", () => act(() => api("DELETE", `/api/people/${p.id}`), `${p.status === "draft" ? "Discarded" : "Removed"} ${e.name}.`)));
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
      const by = (s) => data.people.filter((p) => p.status === s);
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
