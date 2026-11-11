// pr-post: Who to follow. "Add to PR Post" on the cards, and the picks deck
// (/follow/picks/), which deals this week's picks and then the maybes one card
// at a time. The logic at the top touches no DOM, so tests/follow_ui.test.mjs
// runs it in Node. No dependencies.
(function () {
  "use strict";

  // ----- decisions: [{ login, as: "added" | "skipped" }] in the order they were made -----
  const remaining = (cards, decisions) => {
    const done = new Set(decisions.map((d) => d.login));
    return cards.filter((c) => !done.has(c.login));
  };
  const decide = (decisions, login, as) => decisions.filter((d) => d.login !== login).concat({ login, as });
  const undo = (decisions) => decisions.slice(0, -1);
  const unskip = (decisions) => decisions.filter((d) => d.as !== "skipped");
  const counts = (decisions) => ({
    added: decisions.filter((d) => d.as === "added").length,
    skipped: decisions.filter((d) => d.as === "skipped").length,
  });
  // The commands for everyone added by copying (a paper without the People page), in deck order.
  const addedCommands = (cards, decisions) => {
    const added = new Set(decisions.filter((d) => d.as === "added").map((d) => d.login));
    return cards.filter((c) => added.has(c.login) && c.cmd).map((c) => c.cmd);
  };
  const position = (cards, decisions) => {
    const left = remaining(cards, decisions).length;
    return { at: Math.min(cards.length - left + 1, cards.length), of: cards.length, left };
  };

  // Decisions stay in this browser for the week they belong to: a skip means "not this week".
  const storageKey = (week) => `pr-post-picks-${week}`;
  function loadDecisions(storage, week, cards) {
    try {
      const saved = JSON.parse(storage.getItem(storageKey(week)) || "[]");
      if (!Array.isArray(saved)) return [];
      const logins = new Set(cards.map((c) => c.login));
      return saved.filter((d) => d && logins.has(d.login) && (d.as === "added" || d.as === "skipped"));
    } catch {
      return [];
    }
  }
  function saveDecisions(storage, week, decisions) {
    try { storage.setItem(storageKey(week), JSON.stringify(decisions)); } catch { /* private window: kept until reload */ }
  }

  // ----- Add to PR Post -----
  // A paper kept in Cloudflare drafts them through the People page's endpoint, the same
  // path as "Research and draft them"; the draft waits there until the owner approves it.
  async function requestDraft(fetchFn, api, login) {
    let res;
    try {
      res = await fetchFn(api, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ github: login }),
        credentials: "same-origin",
      });
    } catch {
      return { ok: false, kind: "err", text: `Could not reach the paper, so @${login} was not added. Check that you are online and still signed in, then try again.` };
    }
    const out = await res.json().catch(() => ({}));
    if (res.ok && out.ok) return { ok: true, kind: "ok", text: `Added @${login} as a draft; approve it on People once the research finishes.` };
    if (res.status === 409) return { ok: true, kind: "ok", text: `@${login} is already on the People page.` };
    const why = res.ok ? "The paper sent an answer this page did not expect." : out.error || `The paper refused it (${res.status}).`;
    return { ok: false, kind: "err", text: `${why} @${login} was not added.` };
  }

  // Elsewhere (a paper kept in git, or a public page) Add copies the command that drafts them.
  async function copyCommand(clipboard, cmd, login) {
    try {
      await clipboard.writeText(cmd);
      return { ok: true, kind: "ok", text: `Copied the command that adds @${login}; run it to draft a pull request.` };
    } catch {
      return { ok: false, kind: "err", text: `Could not copy. To add @${login}, run:`, cmd };
    }
  }

  const core = { remaining, decide, undo, unskip, counts, addedCommands, position, storageKey, loadDecisions, saveDecisions, requestDraft, copyCommand };
  if (typeof document === "undefined") {
    if (typeof module === "object" && module) module.exports = core;
    return;
  }

  const $ = (sel, root = document) => root.querySelector(sel);
  const escp = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (x) => Number(x || 0).toLocaleString();
  const store = (() => { try { return window.localStorage; } catch { return null; } })();
  const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  const off = (b) => b.getAttribute("aria-disabled") === "true";

  // ----- the cards on /follow/ -----
  document.querySelectorAll("[data-draft], [data-copy]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (off(btn)) return;
      const note = btn.parentElement.querySelector(".fc-status");
      const label = btn.textContent;
      let r;
      if (btn.dataset.draft) {
        btn.setAttribute("aria-disabled", "true");
        btn.textContent = "Adding…";
        r = await requestDraft(window.fetch.bind(window), btn.dataset.api, btn.dataset.draft);
        btn.textContent = r.ok ? "Added" : label;
        btn.setAttribute("aria-disabled", String(r.ok));
      } else {
        r = await copyCommand(navigator.clipboard, btn.dataset.copy, btn.closest(".fcard")?.id || "them");
        if (!r.ok) { const d = btn.closest(".fcard")?.querySelector(".fc-report"); if (d) d.open = true; r.text = "Could not copy. The command is in the mini report below."; }
      }
      if (note) { note.className = `fc-status ${r.kind}`; note.textContent = r.text; }
    });
  });

  // ----- the picks deck on /follow/picks/ -----
  const app = $("#picks-app");
  if (app) deck(app);

  function deck(app) {
    const data = JSON.parse($("#picks-data").textContent);
    const cards = data.cards;
    const api = app.dataset.api || "";
    const all = app.dataset.all;
    let decisions = store ? loadDecisions(store, data.week, cards) : [];
    let busy = false;
    const save = () => { if (store) saveDecisions(store, data.week, decisions); };

    if (!cards.length) {
      app.innerHTML = `<p class="muted">No picks this week. <a href="${escp(all)}">The full list</a> has everyone who was researched.</p>`;
      return;
    }

    app.innerHTML = `
      <div class="picks">
        <div class="picks-progress">
          <span class="picks-pos"></span>
          <span class="picks-bar" aria-hidden="true"><span></span></span>
          <button class="btn sm" type="button" data-undo aria-keyshortcuts="Z">Back</button>
        </div>
        <div class="pick-stack"><div class="pick-under" aria-hidden="true"></div><article class="pick-card" tabindex="-1"></article></div>
        <div class="picks-buttons">
          <button class="btn pick-skip" type="button" data-skip aria-keyshortcuts="ArrowLeft">Skip</button>
          <button class="btn solid pick-add" type="button" data-add aria-keyshortcuts="ArrowRight">Add to PR Post</button>
        </div>
      </div>
      <div class="picks-end" hidden>
        <div class="picks-end-mark" aria-hidden="true"><svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg></div>
        <h2 tabindex="-1">That's this week's picks</h2>
        <p class="picks-summary"></p>
        ${api ? "" : `<div class="picks-cmds" hidden><pre></pre><button class="btn" type="button" data-copyall>Copy all commands</button></div>`}
        <div class="picks-buttons">
          ${api ? `<a class="btn solid" href="${escp(app.dataset.people)}">Review drafts on People</a>` : ""}
          <a class="btn${api ? "" : " solid"}" href="${escp(all)}">See the full list</a>
          <button class="btn" type="button" data-unskip>Show skipped people again</button>
        </div>
      </div>
      <p class="picks-toast" aria-hidden="true"></p>
      <p class="sr-only" aria-live="polite"></p>`;

    const view = $(".picks", app);
    const end = $(".picks-end", app);
    const card = $(".pick-card", app);
    const add = $("[data-add]", app);
    const skip = $("[data-skip]", app);
    const back = $("[data-undo]", app);
    const unskipBtn = $("[data-unskip]", app);
    const toastEl = $(".picks-toast", app);
    const live = $("[aria-live]", app);

    // What happened shows under the buttons; screen readers hear it with the next card.
    function say(r) {
      toastEl.className = `picks-toast ${r.kind || ""}`;
      // The end screen sums it up, so only a failure stays visible there.
      toastEl.textContent = end.hidden || r.kind === "err" ? r.text : "";
      if (r.cmd) {
        const code = document.createElement("code");
        code.textContent = r.cmd;
        toastEl.append(" ", code);
      }
      const next = remaining(cards, decisions)[0];
      const p = position(cards, decisions);
      const tail = r.kind === "err" ? "" : next ? ` Now ${p.at} of ${p.of}: ${next.name}, fit ${next.score}, ${next.rec}.` : " That was the last one.";
      live.textContent = r.text + (r.cmd ? ` ${r.cmd}` : "") + tail;
    }

    function fill(c) {
      const avatar = c.avatar
        ? `<img class="pick-avatar" src="${escp(c.avatar)}" alt="" width="72" height="72">`
        : `<span class="pick-avatar pick-initial" aria-hidden="true">${escp((c.name || c.login)[0])}</span>`;
      const top = c.top && c.top.repo ? `<div><b>${escp(c.top.repo.split("/").pop())}</b><span>top repo, ${fmt(c.top.stars)} stars</span></div>` : "";
      card.innerHTML = `
        <div class="pick-top">${avatar}<div class="pick-id"><h2 id="pick-name">${escp(c.name)}</h2><div class="pick-login">@${escp(c.login)}</div></div>
          <div class="pick-score pick-${escp(c.rec.toLowerCase())}" title="Fit score out of 100"><b>${escp(c.score)}</b><span>${escp(c.rec)}</span></div></div>
        <p class="pick-why">${escp(c.why)}</p>
        <div class="pick-nums"><div><b>${fmt(c.active)}</b><span>contributions in 12 weeks</span></div>
          <div><b>${fmt(c.stars)}</b><span>stars on their repos</span></div><div><b>${fmt(c.followers)}</b><span>followers</span></div>${top}</div>
        <div class="pick-links"><a class="btn pick-open" href="${escp(c.profile)}" target="_blank" rel="noopener" aria-keyshortcuts="O">Open GitHub profile</a>
          <a class="pick-more" href="${escp(all)}#${escp(c.login.toLowerCase())}">Full mini report</a></div>`;
      card.setAttribute("aria-label", `${c.name}, fit ${c.score}, ${c.rec}`);
    }

    function render() {
      const left = remaining(cards, decisions);
      const focusInside = app.contains(document.activeElement);
      const wasEnd = !end.hidden;
      card.style.transition = "none";
      card.style.transform = "";
      card.style.opacity = "";
      card.dataset.lean = "";
      back.setAttribute("aria-disabled", String(!decisions.length));
      if (!left.length) {
        const n = counts(decisions);
        $(".picks-summary", end).textContent = `You added ${n.added} and skipped ${n.skipped}. ` +
          (api ? "Added people wait as drafts on the People page until you approve them."
            : n.added ? "Run these commands to draft each of them as a pull request:" : "");
        const cmds = $(".picks-cmds", end);
        if (cmds) {
          const list = addedCommands(cards, decisions);
          cmds.hidden = !list.length;
          $("pre", cmds).replaceChildren(...list.map((cmd) => Object.assign(document.createElement("code"), { textContent: cmd })));
        }
        unskipBtn.hidden = !n.skipped;
        view.hidden = true;
        end.hidden = false;
        if (focusInside) $("h2", end).focus();
        return;
      }
      view.hidden = false;
      end.hidden = true;
      const p = position(cards, decisions);
      $(".picks-pos", view).textContent = `${p.at} of ${p.of}`;
      $(".picks-bar span", view).style.width = `${(100 * (p.at - 1)) / p.of}%`;
      $(".pick-under", view).hidden = left.length < 2;
      fill(left[0]);
      if (focusInside && wasEnd) card.focus();
    }

    function setBusy(on) {
      busy = on;
      add.textContent = on ? "Adding…" : "Add to PR Post";
      for (const b of [add, skip]) b.setAttribute("aria-disabled", String(on));
      app.setAttribute("aria-busy", String(on));
    }

    function leave(dir, done) {
      if (reduced()) { done(); return; }
      busy = true;
      card.style.transition = "transform .22s ease-in, opacity .22s ease-in";
      card.style.transform = `translateX(${dir > 0 ? 120 : -120}%) rotate(${dir > 0 ? 10 : -10}deg)`;
      card.style.opacity = "0";
      setTimeout(() => { busy = false; done(); }, 220);
    }

    function snapBack() {
      card.style.transition = reduced() ? "none" : "transform .18s ease-out";
      card.style.transform = "";
      card.dataset.lean = "";
    }

    async function choose(as) {
      const c = remaining(cards, decisions)[0];
      if (!c || busy) return;
      let r = { ok: true, kind: "", text: `Skipped @${c.login} for this week.` };
      if (as === "added") {
        setBusy(true);
        card.dataset.lean = "add";
        r = api ? await requestDraft(window.fetch.bind(window), api, c.login) : await copyCommand(navigator.clipboard, c.cmd, c.login);
        setBusy(false);
        if (!r.ok) { snapBack(); say(r); return; }
      }
      leave(as === "added" ? 1 : -1, () => {
        decisions = decide(decisions, c.login, as);
        save();
        render();
        say(r);
      });
    }

    function goBack() {
      if (busy || !decisions.length) return;
      const last = decisions[decisions.length - 1];
      decisions = undo(decisions);
      save();
      render();
      say({ kind: "", text: last.as === "added" && api ? `Back to @${last.login}. The draft you asked for stays on the People page.` : `Back to @${last.login}.` });
    }

    add.addEventListener("click", () => { if (!off(add)) choose("added"); });
    skip.addEventListener("click", () => { if (!off(skip)) choose("skipped"); });
    back.addEventListener("click", () => { if (!off(back)) goBack(); });
    const copyAll = $("[data-copyall]", end);
    copyAll?.addEventListener("click", async () => {
      const r = await copyCommand(navigator.clipboard, addedCommands(cards, decisions).join("\n"), "them");
      copyAll.textContent = r.ok ? "Copied" : "Could not copy; select the commands above";
    });
    unskipBtn.addEventListener("click", () => {
      decisions = unskip(decisions);
      save();
      render();
      card.focus();
      say({ kind: "", text: "The people you skipped are back in the deck." });
    });

    // Touch and mouse: drag the card past a third of its width to decide.
    let x0 = null;
    let dx = 0;
    card.addEventListener("pointerdown", (e) => {
      if (busy || e.button !== 0 || e.target.closest("a, button")) return;
      x0 = e.clientX;
      dx = 0;
      card.setPointerCapture(e.pointerId);
      card.style.transition = "none";
    });
    card.addEventListener("pointermove", (e) => {
      if (x0 === null) return;
      dx = e.clientX - x0;
      if (!reduced()) card.style.transform = `translateX(${dx}px) rotate(${dx / 30}deg)`;
      card.dataset.lean = dx > 40 ? "add" : dx < -40 ? "skip" : "";
    });
    const release = () => {
      if (x0 === null) return;
      x0 = null;
      if (Math.abs(dx) > card.offsetWidth / 3) choose(dx > 0 ? "added" : "skipped");
      else snapBack();
    };
    card.addEventListener("pointerup", release);
    card.addEventListener("pointercancel", release);

    document.addEventListener("keydown", (e) => {
      if (e.repeat || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.target.closest("input, textarea, select, [contenteditable], .rail")) return;
      const k = e.key.toLowerCase();
      if (k === "z" || e.key === "Backspace") { e.preventDefault(); goBack(); return; }
      if (view.hidden) return;
      if (e.key === "ArrowRight") { e.preventDefault(); choose("added"); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); choose("skipped"); }
      else if (k === "o") { const c = remaining(cards, decisions)[0]; if (c) window.open(c.profile, "_blank", "noopener"); }
    });

    render();
  }
})();
