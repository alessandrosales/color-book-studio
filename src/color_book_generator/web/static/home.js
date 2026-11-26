import { syncAllPopoverSelects } from "/static/popover-select.js";
import { formatPageSize, renderStylePicker } from "/static/style-picker.js";
import { animateCardsIn, animateDrawerOpen, animateModalOpen } from "/static/motion.js";
import { fetchFontPresetFile, getFontPreset, renderFontPresetPicker } from "/static/font-presets.js";

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
  categories: [],
  selectedPresetId: "kids_storybook",
  books: [],
  selectedBookIds: new Set(),
  filterCategory: "all",
  searchQuery: "",
  defaults: null,
  codex: null,
  codexModels: [],
  codexCheapest: null,
  codexTimer: null,
  styleFile: null,
  stylePreviewUrl: null,
  styleIsCustomUpload: false,
  selectedFontPresetId: "cozy",
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

function renderBookProviders() {
  const provSelect = $("#book-provider");
  if (!provSelect) return;
  const providers = state.defaults?.providers || ["codex", "openai", "gemini", "replicate", "local"];
  const prev = provSelect.value;
  provSelect.innerHTML = "";
  for (const p of providers) {
    const opt = document.createElement("option");
    opt.value = p;
    opt.textContent = p;
    provSelect.appendChild(opt);
  }
  const useCodex = state.codex && state.codex.authenticated && !state.defaults?.openai_configured;
  const preferred = prev || (useCodex ? "codex" : state.defaults?.provider || "openai");
  provSelect.value = providers.includes(preferred) ? preferred : providers[0];
  renderBookModels({ forceCheapest: !prev });
}

function renderBookModels({ forceCheapest = false } = {}) {
  const select = $("#book-model");
  const provider = $("#book-provider")?.value;
  if (!select || !provider) return;
  let models = [];
  let cheapest = null;

  if (provider === "codex") {
    const raw = state.codexModels.length
      ? [...state.codexModels]
      : [state.defaults?.codex_model || "gpt-5.4-mini", "gpt-5.5", "gpt-6.2-sol"];
    models = [...raw].sort((a, b) => costRank(a) - costRank(b));
    cheapest = state.codexCheapest || models[0];
    if (cheapest && models.includes(cheapest)) {
      models = [cheapest, ...models.filter((m) => m !== cheapest)];
    }
  } else if (provider === "openai") {
    models = ["gpt-image-1", "dall-e-3"];
    cheapest = "gpt-image-1";
  } else if (provider === "gemini") {
    models = ["imagen-3.0-fast-generate-001", "imagen-3.0-generate-002"];
    cheapest = "imagen-3.0-fast-generate-001";
  } else if (provider === "replicate") {
    models = ["black-forest-labs/flux-schnell", "black-forest-labs/flux-dev"];
    cheapest = "black-forest-labs/flux-schnell";
  } else if (provider === "local") {
    models = ["stabilityai/stable-diffusion-xl-base-1.0"];
    cheapest = models[0];
  }

  const current = forceCheapest ? cheapest : select.value || cheapest;
  if (current && !models.includes(current)) models.push(current);

  select.innerHTML = "";
  for (const m of models) {
    const opt = document.createElement("option");
    opt.value = m;
    opt.textContent = m === cheapest ? `${m} (mais barato)` : m;
    select.appendChild(opt);
  }
  if (current) select.value = current;
  syncAllPopoverSelects();
}

function renderBookCaptionUI() {
  const enabled = Boolean($("#book-caption-enabled")?.checked);
  const opts = $("#book-caption-options");
  if (opts) opts.style.opacity = enabled ? "1" : "0.5";
  const fontLabels = { sans: "Sans", serif: "Serif", mono: "Mono" };
  const font = fontLabels[$("#book-caption-font")?.value] || "Sans";
  const weight = $("#book-caption-weight")?.value === "bold" ? "Negrito" : "Normal";
  const statusEl = $("#book-style-typography-status");
  if (statusEl) statusEl.textContent = `Fonte ativa: ${font}, ${weight}`;
}

function renderBookStylePreview() {
  const preview = $("#book-style-preview");
  const clearBtn = $("#book-style-clear");
  if (!preview) return;
  preview.innerHTML = "";
  if (state.stylePreviewUrl) {
    const img = document.createElement("img");
    img.src = state.stylePreviewUrl;
    img.alt = "Amostra de texto selecionada";
    preview.appendChild(img);
    clearBtn?.classList.remove("is-hidden");
  } else {
    const span = document.createElement("span");
    span.textContent = "Nenhuma amostra de texto selecionada.";
    preview.appendChild(span);
    clearBtn?.classList.add("is-hidden");
  }
}

function renderHomeFontPresets() {
  renderFontPresetPicker({
    root: $("#home-font-presets"),
    selectedId: state.selectedFontPresetId,
    onSelect: (fontPreset) => selectHomeFontPreset(fontPreset, { applyCaption: true }),
  });
}

async function selectHomeFontPreset(fontPreset, { applyCaption = true } = {}) {
  if (!fontPreset) return;
  if (state.stylePreviewUrl && state.stylePreviewUrl.startsWith("blob:")) {
    URL.revokeObjectURL(state.stylePreviewUrl);
  }
  state.selectedFontPresetId = fontPreset.id;
  state.styleIsCustomUpload = false;
  state.stylePreviewUrl = fontPreset.imageUrl;
  $("#book-style-file").value = "";
  if (applyCaption && fontPreset.caption) {
    const cap = fontPreset.caption;
    $("#book-caption-enabled").checked = Boolean(cap.enabled);
    $("#book-caption-style").value = cap.style || "caption";
    $("#book-caption-position").value = cap.position || "bottom";
    $("#book-caption-font").value = cap.font_family || "sans";
    $("#book-caption-weight").value = cap.bold ? "bold" : "normal";
    $("#book-caption-size").value = String(cap.font_size_ratio || 0.032);
    renderBookCaptionUI();
    syncAllPopoverSelects();
  }
  renderBookStylePreview();
  renderHomeFontPresets();
  try {
    state.styleFile = await fetchFontPresetFile(fontPreset);
  } catch (_) {}
}

function applyPresetToNewBookForm(preset) {
  if (!preset) return;
  $("#book-context").value = preset.context_prompt || "";
  $("#book-negative-prompt").value = preset.negative_prompt || "";
  $("#book-page-size").value = preset.page_size || "letter_8.5x11";

  const proc = preset.processing || {};
  $("#book-proc-threshold").value = proc.threshold || "adaptive";
  $("#book-proc-min-px").value = proc.min_component_px ?? 24;
  $("#book-proc-denoise").checked = proc.denoise ?? true;
  $("#book-proc-crop").checked = proc.crop_to_content ?? true;

  $("#book-kdp-blank-verso").checked = Boolean(preset.blank_verso);
  $("#book-kdp-title-page").checked = preset.include_title_page ?? true;
  $("#book-kdp-belongs-to").checked = Boolean(preset.include_belongs_to_page);
  $("#book-kdp-color-test").checked = Boolean(preset.include_color_test_page);

  const cap = preset.caption || {};
  $("#book-caption-enabled").checked = Boolean(cap.enabled);
  $("#book-caption-style").value = cap.style || "balloon";
  $("#book-caption-position").value = cap.position || "bottom";
  $("#book-caption-font").value = cap.font_family || "sans";
  $("#book-caption-weight").value = cap.bold ? "bold" : "normal";
  $("#book-caption-size").value = String(cap.font_size_ratio || 0.032);
  renderBookCaptionUI();
  syncAllPopoverSelects();

  if (!state.styleIsCustomUpload) {
    const matchedFont = getFontPreset(preset.category);
    if (matchedFont) {
      selectHomeFontPreset(matchedFont, { applyCaption: false });
    }
  }
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
      applyPresetToNewBookForm(preset);
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
      ? "Nenhum livro corresponde a este filtro."
      : "Sua biblioteca está vazia. Crie o primeiro livro para escolher o estilo, montar os personagens e gerar as páginas.";
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
  error: "A geração parou com erro. Abra o livro para continuar de onde parou.",
  cancelled: "Geração cancelada. Abra o livro para continuar de onde parou.",
};

function applyAutopilotToCard(li, book) {
  const ap = book.autopilot;
  const running = ap?.status === "running";
  const overlay = li.querySelector(".book-gen-overlay");
  const label = li.querySelector(".book-gen-label");
  const fill = li.querySelector(".book-progress-fill");
  const track = li.querySelector(".book-progress-track");
  li.classList.toggle("is-generating", running);
  li.classList.toggle("is-gen-stopped", Boolean(ap) && !running);
  overlay.classList.toggle("is-hidden", !running);
  label.classList.toggle("is-hidden", !ap);
  if (!ap) {
    const pct = book.pages ? Math.round((book.done / book.pages) * 100) : 0;
    fill.style.width = `${pct}%`;
    track.title = `${pct}% concluído`;
    return;
  }
  const pct = Math.max(0, Math.min(100, ap.percent || 0));
  fill.style.width = `${running ? pct : (book.pages ? Math.round((book.done / book.pages) * 100) : 0)}%`;
  track.title = running ? `Gerando: ${pct}%` : "Geração interrompida";
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
    if (book.autopilot) toast(`"${book.title}": a geração parou. Abra o livro para continuar.`, true);
    else toast(`"${book.title}" está pronto!`);
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

function toggleCreator(open) {
  const form = $("#new-book-form");
  const toolbar = $(".workspace-toolbar");
  const library = $("#library-section");
  const toggleBtn = $("#new-book-toggle");
  const shouldOpen = open === undefined ? form.classList.contains("is-hidden") : open;
  form.classList.toggle("is-hidden", !shouldOpen);
  toolbar?.classList.toggle("is-hidden", shouldOpen);
  library?.classList.toggle("is-hidden", shouldOpen);
  if (toggleBtn) {
    toggleBtn.setAttribute("aria-expanded", shouldOpen ? "true" : "false");
    toggleBtn.textContent = shouldOpen ? "Voltar para livros" : "Novo livro";
  }
  if (shouldOpen) {
    animateDrawerOpen(form);
  }
}

async function createBook(ev, { autopilot = false } = {}) {
  if (ev) ev.preventDefault();
  const errEl = $("#new-book-error");
  errEl.classList.add("is-hidden");
  const preset = state.presets.find((p) => p.id === state.selectedPresetId);
  // The final title is written by the autopilot once the story is finished.
  const title = preset ? `Novo livro ${preset.name}` : "Novo Livro de Colorir";
  const pages = Math.max(1, Math.min(200, Number($("#book-pages").value) || 12));
  const premise = $("#book-premise").value.trim();
  const language = $("#book-language")?.value || "en";

  const provider = $("#book-provider")?.value || state.defaults?.provider || "openai";
  const model = $("#book-model")?.value || state.defaults?.model || "gpt-image-1";

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
        context_prompt: $("#book-context").value.trim(),
        negative_prompt: $("#book-negative-prompt").value.trim(),
        page_size: $("#book-page-size").value,
        provider,
        model,
        processing: {
          threshold: $("#book-proc-threshold").value,
          min_component_px: Math.max(0, Number($("#book-proc-min-px").value) || 0),
          denoise: $("#book-proc-denoise").checked,
          crop_to_content: $("#book-proc-crop").checked,
        },
        blank_verso: $("#book-kdp-blank-verso").checked,
        include_title_page: $("#book-kdp-title-page").checked,
        include_belongs_to_page: $("#book-kdp-belongs-to").checked,
        include_color_test_page: $("#book-kdp-color-test").checked,
        caption_enabled: $("#book-caption-enabled").checked,
        caption_style: $("#book-caption-style").value,
        caption_position: $("#book-caption-position").value,
        caption_font_family: $("#book-caption-font").value,
        caption_font_bold: $("#book-caption-weight").value === "bold",
        caption_font_size: Number($("#book-caption-size").value) || 0.032,
      }),
    });
    if (state.styleFile) {
      const form = new FormData();
      form.append("image", state.styleFile);
      await api(`/api/sessions/${created.id}/style`, { method: "POST", body: form });
    }
    if (autopilot) {
      await api(`/api/sessions/${created.id}/autopilot`, {
        method: "POST",
        body: JSON.stringify({
          pages,
          premise_hint: premise,
          language,
          stop_after: "pdf",
        }),
      });
      // Generation runs in the background: follow it from the library card.
      toggleCreator(false);
      await loadBooks();
      focusGeneratingBook(created.id);
      toast("Geração iniciada. Acompanhe o progresso no card do livro.");
      return;
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
    if (state.codex.authenticated) {
      const m = await api("/api/auth/codex/models").catch(() => null);
      if (m?.models?.length) {
        state.codexModels = m.models;
        state.codexCheapest = m.cheapest || null;
      }
    }
    renderBookProviders();
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
  const [presets, categories] = await Promise.all([
    api("/api/presets"),
    api("/api/presets/categories").catch(() => []),
  ]);
  state.presets = presets;
  state.categories = categories;
  renderPresets();
  const initialPreset = presets.find((p) => p.id === state.selectedPresetId) || presets[0];
  if (initialPreset) {
    state.selectedPresetId = initialPreset.id;
    applyPresetToNewBookForm(initialPreset);
  }
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
  renderBookProviders();
  renderBookCaptionUI();

  $("#new-book-toggle").addEventListener("click", () => toggleCreator());
  $("#empty-create-btn").addEventListener("click", () => toggleCreator(true));
  $("#book-cancel").addEventListener("click", () => toggleCreator(false));
  $("#book-cancel-2").addEventListener("click", () => toggleCreator(false));
  $("#new-book-form").addEventListener("submit", (ev) => createBook(ev, { autopilot: false }));
  $("#book-autopilot").addEventListener("click", () => createBook(null, { autopilot: true }));
  $("#book-provider").addEventListener("change", () => renderBookModels({ forceCheapest: true }));
  for (const sel of ["#book-caption-enabled", "#book-caption-font", "#book-caption-weight"]) {
    $(sel).addEventListener("change", renderBookCaptionUI);
  }
  $("#book-style-file").addEventListener("change", (ev) => {
    const file = ev.target.files?.[0] || null;
    if (state.stylePreviewUrl && state.stylePreviewUrl.startsWith("blob:")) {
      URL.revokeObjectURL(state.stylePreviewUrl);
    }
    state.styleFile = file;
    state.styleIsCustomUpload = Boolean(file);
    state.selectedFontPresetId = null;
    state.stylePreviewUrl = file ? URL.createObjectURL(file) : null;
    renderBookStylePreview();
    renderHomeFontPresets();
  });
  $("#book-style-clear").addEventListener("click", () => {
    if (state.stylePreviewUrl && state.stylePreviewUrl.startsWith("blob:")) {
      URL.revokeObjectURL(state.stylePreviewUrl);
    }
    state.styleFile = null;
    state.styleIsCustomUpload = false;
    state.selectedFontPresetId = null;
    state.stylePreviewUrl = null;
    $("#book-style-file").value = "";
    renderBookStylePreview();
    renderHomeFontPresets();
  });
  renderHomeFontPresets();
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

init();
