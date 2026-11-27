import { syncAllPopoverSelects } from "/static/popover-select.js";
import { formatPageSize } from "/static/style-picker.js";
import { animateCardsIn, animateModalOpen } from "/static/motion.js";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
}

const CATEGORY_LABEL = {
  cozy: "Bold & Easy",
  kids: "Infantil",
  manga: "Mangá",
  manhwa: "Manhwa",
  kawaii: "Kawaii",
  adult: "Adulto",
  custom: "Personalizado",
};

const state = {
  presets: [],
  books: [],
  selectedBookIds: new Set(),
  filterCategory: "all",
  searchQuery: "",
  defaults: null,
  codex: null,
  codexModels: [],
  codexCheapest: null,
  codexTimer: null,
};

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (!(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, { ...options, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body && body.detail) detail = body.detail;
    } catch (_) {}
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

let toastTimer = null;
function toast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.toggle("error", isError);
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 3200);
}

function animateBooks() {
  animateCardsIn(".book-card");
}

function costRank(slug) {
  const low = String(slug || "").toLowerCase();
  if (low.includes("nano") || low.includes("spark") || low.includes("lite") || low.includes("flash") || low.includes("schnell")) return 0;
  if (low.includes("mini") || low.includes("fast")) return 1;
  if (low.includes("luna") || low.includes("small")) return 2;
  if (low.includes("terra")) return 3;
  return 4;
}

/** Provider and model for a new book: the ChatGPT plan when it is the only one connected. */
function defaultEngine() {
  const useCodex = state.codex?.authenticated && !state.defaults?.openai_configured;
  if (useCodex) {
    const models = [...state.codexModels].sort((a, b) => costRank(a) - costRank(b));
    return { provider: "codex", model: state.codexCheapest || models[0] || state.defaults?.codex_model };
  }
  return { provider: state.defaults?.provider, model: state.defaults?.model };
}

function filteredBooks() {
  const q = state.searchQuery.trim().toLowerCase();
  return state.books.filter((book) => {
    if (state.filterCategory !== "all" && book.category !== state.filterCategory) {
      return false;
    }
    if (q && !book.title.toLowerCase().includes(q)) {
      return false;
    }
    return true;
  });
}

function renderBooks({ animate = false } = {}) {
  const grid = $("#book-grid");
  const empty = $("#empty-state");
  const totalCount = state.books.length;
  $("#books-count").textContent = `${totalCount} ${totalCount === 1 ? "livro" : "livros"}`;

  const visible = filteredBooks();
  grid.innerHTML = "";
  empty.classList.toggle("is-hidden", visible.length > 0);
  if (!visible.length) {
    $("#empty-state-msg").textContent = totalCount
      ? "Nenhum livro corresponde a este filtro."
      : "Sua biblioteca está vazia. Crie o primeiro livro: você começa contando a história no briefing e depois escolhe o estilo, os personagens e as páginas.";
    return;
  }

  for (const book of visible) {
    const li = document.createElement("li");
    li.className = "book-card";
    const pct = book.pages ? Math.round((book.done / book.pages) * 100) : 0;

    const coverHtml = book.cover_url
      ? `<img src="${book.cover_url}" alt="" loading="lazy" />`
      : `<div class="book-cover-empty">
           <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.6">
             <rect x="4" y="3" width="16" height="18" rx="2.5" />
             <path d="M8 8h8M8 12h6" />
           </svg>
           <span>Rascunho</span>
         </div>`;

    const catLabel = CATEGORY_LABEL[book.category] || book.category || "Colorir";
    const isChecked = state.selectedBookIds.has(book.id);
    if (isChecked) li.classList.add("is-selected");
    li.dataset.bookId = book.id;
    li.innerHTML = `
      <div class="book-cover-wrap">
        <label class="book-select-box" title="Selecionar para exportar">
          <input type="checkbox" class="book-select-input" ${isChecked ? "checked" : ""} aria-label="Selecionar livro" />
        </label>
        <a class="book-cover" href="/books/${book.id}" aria-label="Abrir livro">
          ${coverHtml}
        </a>
        <div class="book-gen-overlay is-hidden" aria-hidden="true">
          <div class="spinner"></div>
          <strong class="book-gen-pct"></strong>
        </div>
      </div>
      <div class="book-body">
        <div class="book-head-row">
          <h3 class="book-title"></h3>
          <span class="pill book-cat-pill"></span>
        </div>
        <div class="book-progress-track" title="${pct}% concluído">
          <div class="book-progress-fill" style="width: ${pct}%;"></div>
        </div>
        <p class="book-gen-label is-hidden" role="status" aria-live="polite"></p>
        <div class="book-gen-actions is-hidden">
          <button class="btn btn-outline btn-sm" type="button" data-gen="stop">Parar</button>
          <button class="btn btn-primary btn-sm" type="button" data-gen="resume">Continuar</button>
          <button class="btn btn-danger-ghost btn-sm" type="button" data-gen="cancel">Cancelar</button>
        </div>
        <div class="book-meta-line">
          <span>${book.done}/${book.pages} págs</span>
          <span>${formatPageSize(book.page_size)}</span>
          <span>${book.characters_count || 0} pers.</span>
        </div>
        <div class="book-actions">
          <div class="book-actions-primary">
            <a class="btn btn-primary btn-sm" href="/books/${book.id}">Abrir</a>
            <a class="btn btn-outline btn-sm" href="/api/sessions/${book.id}/export">Exportar .zip</a>
          </div>
          <button class="btn btn-danger-ghost btn-sm" type="button" data-delete="${book.id}">Excluir</button>
        </div>
      </div>
    `;
    li.querySelector(".book-title").textContent = book.title;
    li.querySelector(".book-cat-pill").textContent = catLabel;
    applyAutopilotToCard(li, book);
    li.querySelectorAll("[data-gen]").forEach((btn) => {
      btn.addEventListener("click", () => controlGeneration(li, book.id, btn.dataset.gen));
    });
    li.querySelector(".book-select-input").addEventListener("change", (ev) => {
      if (ev.target.checked) {
        state.selectedBookIds.add(book.id);
      } else {
        state.selectedBookIds.delete(book.id);
      }
      li.classList.toggle("is-selected", ev.target.checked);
      updateSelectionBar();
    });
    li.querySelector("[data-delete]").addEventListener("click", async () => {
      if (!confirm(`Excluir "${book.title}" e todas as suas páginas?`)) return;
      try {
        await api(`/api/sessions/${book.id}`, { method: "DELETE" });
        state.selectedBookIds.delete(book.id);
        updateSelectionBar();
        toast("Livro excluído.");
        await loadBooks();
      } catch (err) {
        toast(err.message, true);
      }
    });
    grid.appendChild(li);
  }

  updateSelectionBar();
  if (animate) animateBooks();
}

const GEN_STATE_TEXT = {
  error: "A geração parou com erro. Clique em Continuar para tentar de onde parou.",
  cancelled: "Geração pausada. Clique em Continuar para seguir de onde parou.",
};

function applyGenerationButtons(li, ap) {
  const running = ap?.status === "running";
  li.querySelector(".book-gen-actions").classList.toggle("is-hidden", !ap);
  const stop = li.querySelector('[data-gen="stop"]');
  stop.classList.toggle("is-hidden", !running);
  stop.disabled = Boolean(ap?.cancelling);
  stop.textContent = ap?.cancelling ? "Parando…" : "Parar";
  li.querySelector('[data-gen="resume"]').classList.toggle("is-hidden", !ap || running);
}

const GEN_ACTIONS = {
  stop: { path: "stop", done: "A geração para depois da página atual." },
  resume: { path: "resume", done: "Geração retomada de onde parou." },
  cancel: { path: "cancel", done: "Geração cancelada. As páginas já geradas foram mantidas." },
};

async function controlGeneration(li, id, action) {
  const { path, done } = GEN_ACTIONS[action];
  const buttons = li.querySelectorAll("[data-gen]");
  buttons.forEach((btn) => { btn.disabled = true; });
  try {
    await api(`/api/sessions/${id}/generation/${path}`, { method: "POST" });
    toast(done);
    await loadBooks();
  } catch (err) {
    toast(err.message, true);
    buttons.forEach((btn) => { btn.disabled = false; });
  }
}

function applyAutopilotToCard(li, book) {
  const ap = book.autopilot;
  const running = ap?.status === "running";
  const overlay = li.querySelector(".book-gen-overlay");
  const label = li.querySelector(".book-gen-label");
  const fill = li.querySelector(".book-progress-fill");
  const track = li.querySelector(".book-progress-track");
  li.classList.toggle("is-generating", running);
  li.classList.toggle("is-gen-stopped", Boolean(ap) && !running);
  li.classList.toggle("is-gen-error", ap?.status === "error");
  overlay.classList.toggle("is-hidden", !running);
  label.classList.toggle("is-hidden", !ap);
  applyGenerationButtons(li, ap);
  if (!ap) {
    const pct = book.pages ? Math.round((book.done / book.pages) * 100) : 0;
    fill.style.width = `${pct}%`;
    track.title = `${pct}% concluído`;
    return;
  }
  const pct = Math.max(0, Math.min(100, ap.percent || 0));
  fill.style.width = `${running ? pct : (book.pages ? Math.round((book.done / book.pages) * 100) : 0)}%`;
  track.title = running ? `Gerando: ${pct}%` : "Geração pausada";
  li.querySelector(".book-gen-pct").textContent = `${pct}%`;
  label.textContent = running
    ? ap.label || "Gerando livro…"
    : `${GEN_STATE_TEXT[ap.status] || ap.label || ""}${ap.error ? ` (${ap.error})` : ""}`;
}

let generationTimer = null;

function scheduleGenerationPolling() {
  const anyRunning = state.books.some((b) => b.autopilot?.status === "running");
  if (anyRunning && !generationTimer) {
    generationTimer = setInterval(pollGeneration, 2500);
  } else if (!anyRunning && generationTimer) {
    clearInterval(generationTimer);
    generationTimer = null;
  }
}

async function pollGeneration() {
  let books;
  try {
    books = await api("/api/sessions");
  } catch (_) {
    return;
  }
  const wasRunning = new Set(
    state.books.filter((b) => b.autopilot?.status === "running").map((b) => b.id)
  );
  const sameList =
    books.length === state.books.length && books.every((b, i) => b.id === state.books[i].id);
  state.books = books;
  const finished = books.filter((b) => wasRunning.has(b.id) && b.autopilot?.status !== "running");
  if (!sameList || finished.length) {
    renderBooks();
  } else {
    for (const book of books) {
      const li = document.querySelector(`.book-card[data-book-id="${book.id}"]`);
      if (!li) continue;
      li.querySelector(".book-title").textContent = book.title;
      li.querySelector(".book-meta-line span").textContent = `${book.done}/${book.pages} págs`;
      applyAutopilotToCard(li, book);
    }
  }
  for (const book of finished) {
    if (book.autopilot?.status === "cancelled") toast(`"${book.title}": geração pausada.`);
    else if (book.autopilot) toast(`"${book.title}": a geração parou. Clique em Continuar no card.`, true);
    else if (book.pages && book.done >= book.pages) toast(`"${book.title}" está pronto!`);
  }
  scheduleGenerationPolling();
}

function focusGeneratingBook(id) {
  const li = document.querySelector(`.book-card[data-book-id="${id}"]`);
  if (!li) return;
  li.scrollIntoView({ behavior: "smooth", block: "center" });
  li.classList.add("is-just-started");
  setTimeout(() => li.classList.remove("is-just-started"), 2400);
}

function updateSelectionBar() {
  const count = state.selectedBookIds.size;
  const expBtn = $("#export-selected-btn");
  const clrBtn = $("#clear-selection-btn");
  const modalExpBtn = $("#modal-export-selected-btn");
  if (expBtn && clrBtn) {
    expBtn.classList.toggle("is-hidden", count === 0);
    clrBtn.classList.toggle("is-hidden", count === 0);
    expBtn.textContent = `Exportar selecionados (${count})`;
  }
  if (modalExpBtn) {
    modalExpBtn.classList.toggle("is-hidden", count === 0);
    modalExpBtn.textContent = `Exportar selecionados (${count})`;
  }
}

async function exportSelectedBooks() {
  const ids = Array.from(state.selectedBookIds);
  if (!ids.length) return;
  const btn = $("#export-selected-btn");
  const prev = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Gerando .zip…";
  try {
    const res = await fetch("/api/export/selected", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_ids: ids }),
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const err = await res.json();
        if (err?.detail) detail = err.detail;
      } catch (_) {}
      throw new Error(detail);
    }
    const blob = await res.blob();
    const disp = res.headers.get("content-disposition") || "";
    const match = disp.match(/filename="?([^"]+)"?/i);
    const filename = match ? match[1] : `color-books-${ids.length}.zip`;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    toast(`${ids.length} ${ids.length === 1 ? "livro exportado" : "livros exportados"}.`);
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = prev;
    updateSelectionBar();
  }
}

/** "Novo livro": create the book with the default style and open it on the briefing step. */
async function createBookFromBriefing() {
  const btn = $("#new-book-briefing");
  const prevLabel = btn?.textContent;
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Criando…";
  }
  try {
    const preset = state.presets.find((p) => p.badge === "Padrão") || state.presets[0];
    const engine = defaultEngine();
    const created = await api("/api/sessions", {
      method: "POST",
      body: JSON.stringify({
        title: "Novo livro",
        preset_id: preset?.id,
        provider: engine.provider || undefined,
        model: engine.model || undefined,
      }),
    });
    window.location.href = `/books/${created.id}`;
  } catch (err) {
    toast(err.message, true);
    if (btn) {
      btn.disabled = false;
      btn.textContent = prevLabel;
    }
  }
}

/* Auth modal */
function renderAuth() {
  const pill = $("#auth-pill");
  const label = $("#auth-pill-label");
  const c = state.codex;
  if (!c) return;
  pill.classList.remove("pill-ok", "pill-warn");
  if (c.authenticated) {
    pill.classList.add("pill-ok");
    label.textContent = "ChatGPT conectado";
  } else if (c.pending) {
    pill.classList.add("pill-warn");
    label.textContent = "Aguardando código…";
  } else {
    label.textContent = "Conectar ChatGPT";
  }

  $("#codex-connect").classList.toggle("is-hidden", c.authenticated);
  $("#codex-logout").classList.toggle("is-hidden", !c.authenticated);
  $("#codex-code").classList.toggle("is-hidden", !c.pending || !c.user_code);
  if (c.user_code) {
    $("#codex-user-code").textContent = c.user_code;
    $("#codex-link").href = c.verification_url;
  }
  const errEl = $("#codex-error");
  errEl.classList.toggle("is-hidden", !c.error);
  if (c.error) errEl.textContent = c.error;

  if (c.pending && !state.codexTimer) {
    state.codexTimer = setInterval(refreshAuth, 2500);
  } else if (!c.pending && state.codexTimer) {
    clearInterval(state.codexTimer);
    state.codexTimer = null;
  }
}

async function refreshAuth() {
  try {
    state.codex = await api("/api/auth/codex");
    renderAuth();
    if (state.codex.authenticated) {
      const m = await api("/api/auth/codex/models").catch(() => null);
      if (m?.models?.length) {
        state.codexModels = m.models;
        state.codexCheapest = m.cheapest || null;
      }
    }
  } catch (_) {}
}

async function importBackup() {
  const input = $("#import-file");
  const errEl = $("#backup-error");
  errEl.classList.add("is-hidden");
  const file = input.files?.[0];
  if (!file) {
    errEl.textContent = "Selecione um arquivo .zip de backup.";
    errEl.classList.remove("is-hidden");
    return;
  }
  const overlay = $("#busy-overlay");
  const fill = $("#busy-fill");
  overlay.classList.remove("is-hidden");
  fill.style.width = "35%";

  const form = new FormData();
  form.append("archive", file);
  try {
    const res = await api("/api/import", { method: "POST", body: form });
    fill.style.width = "100%";
    const extraCount = res.extra_pages || 0;
    const summaryParts = [
      plural(res.sessions, "livro", "livros"),
      plural(res.pages, "página ilustrada", "páginas ilustradas"),
    ];
    if (extraCount > 0) summaryParts.push(plural(extraCount, "página extra", "páginas extras"));
    toast(`Backup restaurado: ${summaryParts.join(", ")}.`);
    input.value = "";
    $("#backup-modal")?.classList.add("is-hidden");
    await Promise.all([loadBooks(), loadPresets()]);
  } catch (err) {
    errEl.textContent = err.message;
    errEl.classList.remove("is-hidden");
  } finally {
    setTimeout(() => overlay.classList.add("is-hidden"), 250);
  }
}

async function loadBooks({ animate = false } = {}) {
  state.books = await api("/api/sessions");
  renderBooks({ animate });
  scheduleGenerationPolling();
}

async function loadPresets() {
  // Only needed to pick the default style of a new book; the style is chosen inside the book.
  state.presets = await api("/api/presets");
}

async function init() {
  try {
    const meta = await api("/api/meta");
    if (!meta.features?.includes("presets")) {
      $("#server-banner").classList.remove("is-hidden");
    }
  } catch (_) {}

  const [defaults] = await Promise.all([
    api("/api/defaults").catch(() => null),
    loadPresets().catch(() => {}),
    loadBooks({ animate: true }).catch(() => {}),
    refreshAuth(),
  ]);
  state.defaults = defaults;
  const startedId = new URLSearchParams(window.location.search).get("gerando");
  if (startedId) {
    history.replaceState(null, "", window.location.pathname);
    focusGeneratingBook(startedId);
    toast("Geração iniciada. Acompanhe o progresso no card do livro.");
  }

  $("#new-book-briefing").addEventListener("click", createBookFromBriefing);
  $("#empty-create-btn").addEventListener("click", createBookFromBriefing);
  $("#import-btn").addEventListener("click", importBackup);

  $$("[data-cat-filter]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.filterCategory = btn.dataset.catFilter;
      $$("[data-cat-filter]").forEach((b) => b.classList.toggle("is-active", b === btn));
      renderBooks();
    });
  });

  $("#book-search").addEventListener("input", (ev) => {
    state.searchQuery = ev.target.value;
    renderBooks();
  });
  $("#export-selected-btn").addEventListener("click", exportSelectedBooks);
  $("#modal-export-selected-btn").addEventListener("click", exportSelectedBooks);
  $("#clear-selection-btn").addEventListener("click", () => {
    state.selectedBookIds.clear();
    renderBooks();
  });

  $("#backup-modal-open").addEventListener("click", () => {
    const modal = $("#backup-modal");
    modal.classList.remove("is-hidden");
    animateModalOpen(modal);
  });
  $("#backup-modal-close").addEventListener("click", () => $("#backup-modal").classList.add("is-hidden"));
  $("#backup-modal").addEventListener("click", (ev) => {
    if (ev.target === $("#backup-modal")) $("#backup-modal").classList.add("is-hidden");
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") {
      $("#backup-modal")?.classList.add("is-hidden");
      $("#auth-modal")?.classList.add("is-hidden");
    }
  });

  $("#auth-pill").addEventListener("click", () => {
    const modal = $("#auth-modal");
    modal.classList.remove("is-hidden");
    animateModalOpen(modal);
  });
  $("#auth-modal-close").addEventListener("click", () => $("#auth-modal").classList.add("is-hidden"));
  $("#auth-modal").addEventListener("click", (ev) => {
    if (ev.target === $("#auth-modal")) $("#auth-modal").classList.add("is-hidden");
  });
  $("#codex-connect").addEventListener("click", async () => {
    state.codex = await api("/api/auth/codex/start", { method: "POST" });
    renderAuth();
  });
  $("#codex-logout").addEventListener("click", async () => {
    state.codex = await api("/api/auth/codex/logout", { method: "POST" });
    renderAuth();
    toast("Conta ChatGPT desconectada.");
  });

  syncAllPopoverSelects();
}

init().catch((err) => toast(`Não foi possível carregar a biblioteca: ${err.message}`, true));
