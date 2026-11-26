import { syncAllPopoverSelects } from "/static/popover-select.js";
import { renderStylePicker } from "/static/style-picker.js";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const CATEGORY_LABEL = {
  cozy: "Bold & Easy",
  kids: "Infantil",
  manga: "Mangá",
  manhwa: "Manhwa",
  kawaii: "Kawaii",
  adult: "Adulto",
  custom: "Custom",
};

const state = {
  presets: [],
  categories: [],
  selectedPresetId: "bold_easy_cozy",
  books: [],
  selectedBookIds: new Set(),
  filterCategory: "all",
  searchQuery: "",
  defaults: null,
  codex: null,
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
  if (typeof window.gsap === "undefined") return;
  const mm = window.gsap.matchMedia();
  mm.add("(prefers-reduced-motion: no-preference)", () => {
    window.gsap.from(".book-card", {
      y: 12,
      autoAlpha: 0,
      duration: 0.36,
      stagger: 0.04,
      ease: "power2.out",
      clearProps: "transform,opacity,visibility",
    });
  });
}

function renderPresets() {
  renderStylePicker({
    root: $("#home-style-picker"),
    presets: state.presets,
    categories: state.categories,
    selectedId: state.selectedPresetId,
    onSelect: (preset) => {
      state.selectedPresetId = preset.id;
      renderPresets();
    },
  });
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
      ? "Nenhum livro corresponde a esse filtro."
      : "Nenhum livro no workspace ainda. Crie o primeiro para começar.";
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
    li.innerHTML = `
      <div class="book-cover-wrap">
        <label class="book-select-box" title="Selecionar para exportar">
          <input type="checkbox" class="book-select-input" ${isChecked ? "checked" : ""} aria-label="Selecionar livro" />
        </label>
        <a class="book-cover" href="/books/${book.id}" aria-label="Abrir livro">
          ${coverHtml}
        </a>
      </div>
      <div class="book-body">
        <div class="book-head-row">
          <h3 class="book-title"></h3>
          <span class="pill book-cat-pill"></span>
        </div>
        <div class="book-progress-track" title="${pct}% concluído">
          <div class="book-progress-fill" style="width: ${pct}%;"></div>
        </div>
        <div class="book-meta-line">
          <span>${book.done}/${book.pages} págs</span>
          <span>${(book.page_size || "letter").replace("_", " ")}</span>
          <span>${book.characters_count || 0} pers.</span>
        </div>
        <div class="book-actions">
          <div style="display: flex; align-items: center; gap: 6px;">
            <a class="btn btn-primary btn-sm" href="/books/${book.id}">Abrir</a>
            <a class="btn btn-outline btn-sm" href="/api/sessions/${book.id}/export">Exportar .zip</a>
          </div>
          <button class="btn btn-danger-ghost btn-sm" type="button" data-delete="${book.id}">Excluir</button>
        </div>
      </div>
    `;
    li.querySelector(".book-title").textContent = book.title;
    li.querySelector(".book-cat-pill").textContent = catLabel;
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

function toggleCreator(open) {
  const form = $("#new-book-form");
  const shouldOpen = open === undefined ? form.classList.contains("is-hidden") : open;
  form.classList.toggle("is-hidden", !shouldOpen);
  if (shouldOpen) {
    if (typeof window.gsap !== "undefined") {
      window.gsap.fromTo(
        form,
        { y: -8, autoAlpha: 0 },
        { y: 0, autoAlpha: 1, duration: 0.3, ease: "power2.out", clearProps: "transform,opacity,visibility" }
      );
    }
    $("#book-title").focus();
  }
}

async function createBook(ev, { autopilot = false } = {}) {
  if (ev) ev.preventDefault();
  const errEl = $("#new-book-error");
  errEl.classList.add("is-hidden");
  const preset = state.presets.find((p) => p.id === state.selectedPresetId);
  const rawTitle = $("#book-title").value.trim();
  const title = rawTitle || (preset ? `Novo livro ${preset.name}` : "Novo Livro de Colorir");
  const pages = Math.max(1, Math.min(200, Number($("#book-pages").value) || 12));
  const premise = $("#book-premise").value.trim();
  const language = $("#book-language")?.value || "en";

  if (!autopilot && !rawTitle) {
    errEl.textContent = "Informe um título para criar em modo manual (ou use 'Gerar livro completo').";
    errEl.classList.remove("is-hidden");
    return;
  }

  const useCodex = state.codex && state.codex.authenticated && !state.defaults?.openai_configured;
  const provider = useCodex ? "codex" : state.defaults?.provider || "openai";
  const cheapestByProvider = {
    codex: state.defaults?.codex_model || "gpt-5.4-mini",
    openai: "gpt-image-1",
    gemini: "imagen-3.0-fast-generate-001",
    replicate: "black-forest-labs/flux-schnell",
    local: "stabilityai/stable-diffusion-xl-base-1.0",
  };
  const model = cheapestByProvider[provider] || state.defaults?.model;

  const btn = autopilot ? $("#book-autopilot") : $("#book-create");
  const prevLabel = btn.textContent;
  btn.disabled = true;
  btn.textContent = autopilot ? "Iniciando geração completa…" : "Criando…";
  try {
    const created = await api("/api/sessions", {
      method: "POST",
      body: JSON.stringify({
        title,
        pages,
        language,
        preset_id: state.selectedPresetId,
        story_premise: premise,
        provider,
        model,
      }),
    });
    if (autopilot) {
      await api(`/api/sessions/${created.id}/autopilot`, {
        method: "POST",
        body: JSON.stringify({
          preset_id: state.selectedPresetId,
          pages,
          premise_hint: premise,
          language,
          stop_after: "pdf",
        }),
      });
    }
    window.location.href = `/books/${created.id}`;
  } catch (err) {
    errEl.textContent = err.message;
    errEl.classList.remove("is-hidden");
  } finally {
    btn.disabled = false;
    btn.textContent = prevLabel;
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
    toast(`Backup restaurado: ${res.sessions} livro(s), ${res.pages} página(s).`);
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
}

async function loadPresets() {
  const [presets, categories] = await Promise.all([
    api("/api/presets"),
    api("/api/presets/categories").catch(() => []),
  ]);
  state.presets = presets;
  state.categories = categories;
  renderPresets();
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

  $("#new-book-toggle").addEventListener("click", () => toggleCreator());
  $("#empty-create-btn").addEventListener("click", () => toggleCreator(true));
  $("#book-cancel").addEventListener("click", () => toggleCreator(false));
  $("#book-cancel-2").addEventListener("click", () => toggleCreator(false));
  $("#new-book-form").addEventListener("submit", (ev) => createBook(ev, { autopilot: false }));
  $("#book-autopilot").addEventListener("click", () => createBook(null, { autopilot: true }));
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

  $("#backup-modal-open").addEventListener("click", () => $("#backup-modal").classList.remove("is-hidden"));
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

  $("#auth-pill").addEventListener("click", () => $("#auth-modal").classList.remove("is-hidden"));
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

init();
