import { syncAllPopoverSelects } from "/static/popover-select.js";
import { formatPageSize, renderStylePicker } from "/static/style-picker.js";
import { animateLightboxOpen, animateModalOpen, animateStageEnter } from "/static/motion.js";
import { fetchFontPresetFile, renderFontPresetPicker } from "/static/font-presets.js";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
}

const STATUS_LABEL = {
  pending: "pendente",
  generating: "gerando…",
  done: "pronta",
  error: "erro",
};

const SHEET_STATUS_LABEL = {
  empty: "sem folha",
  generating: "gerando…",
  ready: "folha pronta",
  error: "erro",
};

const state = {
  sid: null,
  session: null,
  defaults: null,
  presets: [],
  categories: [],
  globalCharacters: [],
  codex: null,
  codexModels: [],
  codexCheapest: null,
  codexTimer: null,
  currentStep: 1,
  busyPages: new Set(),
  insertingAfter: null,
  lastInsertedIndex: null,
  selectedFontPresetId: null,
  lightboxItems: [],
  lightboxIndex: -1,
  autopilotTimer: null,
  lastAutopilotPhase: "",
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

function debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

function sessionIdFromUrl() {
  const match = window.location.pathname.match(/^\/books\/([a-f0-9]{8,32})/);
  return match ? match[1] : null;
}

/* ==================== STEPPER & GSAP TRANSITIONS ==================== */
function setStep(step, { animate = true, persist = true } = {}) {
  const raw = Number(step) || 1;
  const next = Math.max(1, Math.min(4, raw >= 5 ? 4 : raw));
  state.currentStep = next;

  $$(".step-tab").forEach((tab) => {
    const isActive = Number(tab.dataset.step) === next;
    tab.classList.toggle("is-active", isActive);
    if (isActive) {
      tab.setAttribute("aria-current", "step");
    } else {
      tab.removeAttribute("aria-current");
    }
  });

  for (let i = 1; i <= 4; i++) {
    const panel = $(`#stage-${i}`);
    if (!panel) continue;
    const isTarget = i === next;
    panel.classList.toggle("is-hidden", !isTarget);
    if (isTarget && animate) {
      animateStageEnter(panel);
    }
  }

  if (persist && state.sid) {
    api(`/api/sessions/${state.sid}`, {
      method: "PATCH",
      body: JSON.stringify({ current_step: next }),
    }).catch(() => {});
  }
}

/* ==================== RENDERING ==================== */
function renderHeaderAndStepper() {
  const s = state.session;
  if (!s) return;

  $("#session-title").value = s.title;
  document.title = `${s.title} | Color Book Studio`;
  $("#meta-format").textContent = `${s.trim_in[0]} x ${s.trim_in[1]} in, ${s.dpi} DPI`;
  if ($("#export-book-zip")) $("#export-book-zip").href = `/api/sessions/${s.id}/export`;

  const doneCount = s.pages.filter((p) => p.status === "done").length;
  const totalCount = s.pages.length;

  const activePreset = state.presets.find((p) => p.id === s.preset_id);
  $("#step-1-sub").textContent = activePreset ? activePreset.name : formatPageSize(s.page_size);
  $("#step-2-sub").textContent = plural(s.characters.length, "personagem", "personagens");
  $("#step-3-sub").textContent = `${doneCount} / ${totalCount} prontas`;
  $("#step-4-sub").textContent = s.cover_status === "done" ? "Capa pronta" : "Capa e PDF";
  $("#progress-pill").textContent = `${doneCount} / ${totalCount} prontas`;

  const canCompose = doneCount > 0;
  $("#compose-btn").disabled = !canCompose;
  const composeFinal = $("#compose-final-btn");
  if (composeFinal) composeFinal.disabled = !canCompose;

  const pdfLink = $("#pdf-link");
  pdfLink.classList.toggle("is-hidden", !s.pdf_ready || !s.pdf_url);
  if (s.pdf_url) pdfLink.href = s.pdf_url;
}

function renderPresets() {
  renderStylePicker({
    root: $("#studio-style-picker"),
    presets: state.presets,
    categories: state.categories,
    selectedId: state.session?.preset_id,
    onSelect: async (preset) => {
      try {
        await api(`/api/sessions/${state.sid}/apply-preset`, {
          method: "POST",
          body: JSON.stringify({ preset_id: preset.id }),
        });
        await reloadSession();
        toast(`Estilo "${preset.name}" aplicado ao livro.`);
      } catch (err) {
        toast(err.message, true);
      }
    },
  });
}

function renderStage1Form() {
  const s = state.session;
  if (!s) return;

  $("#context").value = s.context_prompt || "";
  $("#negative-prompt").value = s.negative_prompt || "";
  $("#page-size").value = s.page_size || "letter_8.5x11";

  const provSelect = $("#provider");
  const providers = state.defaults?.providers || ["codex", "openai", "gemini", "replicate", "local"];
  provSelect.innerHTML = "";
  for (const p of providers) {
    const opt = document.createElement("option");
    opt.value = p;
    opt.textContent = p;
    provSelect.appendChild(opt);
  }
  provSelect.value = s.provider;
  renderModelSuggestions({ preferred: s.model });

  const proc = s.processing || {};
  $("#proc-threshold").value = proc.threshold || "adaptive";
  $("#proc-min-px").value = proc.min_component_px ?? 24;
  $("#proc-denoise").checked = proc.denoise ?? true;
  $("#proc-crop").checked = proc.crop_to_content ?? true;
  syncAllPopoverSelects();

  // Text style reference preview
  const preview = $("#style-preview");
  const removeBtn = $("#style-remove");
  preview.innerHTML = "";
  if (s.style_url) {
    const img = document.createElement("img");
    img.src = s.style_url;
    img.alt = "Amostra de texto de referência";
    img.addEventListener("click", () => {
      openLightbox([{ url: s.style_url, caption: "Amostra de texto de referência" }], 0);
    });
    preview.appendChild(img);
    removeBtn.classList.remove("is-hidden");
  } else {
    const span = document.createElement("span");
    span.textContent = "Nenhuma amostra de texto definida ainda. Envie uma imagem com o estilo de fonte desejado.";
    preview.appendChild(span);
    removeBtn.classList.add("is-hidden");
  }

  renderCaptionSettings(s);
  $("#style-detect-btn").classList.toggle("is-hidden", !s.style_url);
  renderStudioFontPresets();
  renderAutopilotSummary();
}

function renderStudioFontPresets() {
  renderFontPresetPicker({
    root: $("#studio-font-presets"),
    selectedId: state.selectedFontPresetId,
    onSelect: async (fontPreset) => {
      try {
        const file = await fetchFontPresetFile(fontPreset);
        const form = new FormData();
        form.append("image", file);
        await api(`/api/sessions/${state.sid}/style`, { method: "POST", body: form });
        const cap = fontPreset.caption;
        state.session = await api(`/api/sessions/${state.sid}`, {
          method: "PATCH",
          body: JSON.stringify({
            caption_enabled: Boolean(cap.enabled),
            caption_style: cap.style,
            caption_position: cap.position,
            caption_font_family: cap.font_family,
            caption_font_bold: Boolean(cap.bold),
            caption_font_size: cap.font_size_ratio,
          }),
        });
        state.selectedFontPresetId = fontPreset.id;
        await reloadSession();
        toast(`Referência de fonte "${fontPreset.name}" aplicada.`);
      } catch (err) {
        toast(err.message, true);
      }
    },
  });
}

function renderAutopilotSummary() {
  const el = $("#autopilot-config-summary");
  const s = state.session;
  if (!el || !s) return;
  const pageCount = s.pages?.length || 12;
  const fmtOpt = $("#page-size")?.selectedOptions?.[0]?.textContent || s.page_size;
  const prov = $("#provider")?.value || s.provider;
  const model = $("#model")?.value || s.model;
  const capOn = $("#caption-enabled")?.checked;
  const fontLabels = { sans: "Sans", serif: "Serif", mono: "Mono" };
  const font = fontLabels[$("#caption-font")?.value || s.caption_font_family] || "Sans";
  const weight = ($("#caption-weight")?.value || (s.caption_font_bold ? "bold" : "normal")) === "bold" ? "Negrito" : "Normal";
  const capText = capOn ? `legenda ativa (${font}, ${weight})` : "sem legenda";
  el.textContent = `Configuração atual: ${plural(pageCount, "página", "páginas")}, ${fmtOpt}, ${prov} (${model}), ${capText}.`;
}

function renderCaptionSettings(s) {
  $("#caption-enabled").checked = Boolean(s.caption_enabled);
  $("#caption-style").value = s.caption_style || "balloon";
  $("#caption-position").value = s.caption_position || "bottom";
  $("#caption-font").value = s.caption_font_family || "sans";
  $("#caption-weight").value = s.caption_font_bold ? "bold" : "normal";
  $("#caption-size").value = String(s.caption_font_size || 0.032);
  $("#caption-options").style.opacity = s.caption_enabled ? "1" : "0.5";

  const fontLabels = { sans: "Sans", serif: "Serif", mono: "Mono" };
  $("#style-typography-status").textContent =
    `Fonte ativa: ${fontLabels[s.caption_font_family] || "Sans"}, ` +
    (s.caption_font_bold ? "Negrito" : "Normal");
}

function costRank(slug) {
  const low = String(slug || "").toLowerCase();
  if (low.includes("nano") || low.includes("spark") || low.includes("lite") || low.includes("flash") || low.includes("schnell")) return 0;
  if (low.includes("mini") || low.includes("fast")) return 1;
  if (low.includes("luna") || low.includes("small")) return 2;
  if (low.includes("terra")) return 3;
  return 4;
}

function renderModelSuggestions({ preferred = null, forceCheapest = false } = {}) {
  const select = $("#model");
  const provider = $("#provider").value;
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

  const current = forceCheapest ? cheapest : preferred || select.value || cheapest;
  if (current && !models.includes(current)) {
    models.push(current);
  }

  select.innerHTML = "";
  for (const m of models) {
    const opt = document.createElement("option");
    opt.value = m;
    opt.textContent = m;
    if (m === cheapest) opt.dataset.badge = "Mais barato";
    select.appendChild(opt);
  }
  select.value = current || models[0] || "";
  syncAllPopoverSelects();
}

function renderStage2Characters() {
  const s = state.session;
  if (!s) return;

  const grid = $("#book-char-grid");
  const empty = $("#book-char-empty");
  grid.innerHTML = "";
  empty.classList.toggle("is-hidden", s.characters.length > 0);

  for (const char of s.characters) {
    const card = document.createElement("div");
    card.className = "char-card";
    const statusClass =
      char.sheet_status === "ready"
        ? "pill-ok"
        : char.sheet_status === "error"
        ? "pill-err"
        : char.sheet_status === "generating"
        ? "pill-warn"
        : "";

    card.innerHTML = `
      <div class="char-sheet-thumb"></div>
      <div class="char-card-body">
        <div class="char-card-head">
          <span class="char-card-name"></span>
          <span class="pill ${statusClass}"></span>
        </div>
        <p class="char-card-desc"></p>
        ${char.error ? `<p class="error-msg" style="margin: 0;"></p>` : ""}
        <label class="check-toggle" style="font-size: 0.82rem;">
          <input type="checkbox" class="char-approve-toggle" ${char.approved ? "checked" : ""} />
          <span>Aprovado para usar nas páginas</span>
        </label>
        <div class="char-card-actions">
          <button class="btn btn-accent btn-sm char-gen-btn" type="button">
            ${char.image_url ? "Refazer folha" : "Gerar folha de referência"}
          </button>
          <button class="btn btn-danger-ghost btn-sm char-del-btn" type="button">Remover</button>
        </div>
      </div>
    `;
    const thumb = card.querySelector(".char-sheet-thumb");
    if (char.image_url) {
      const img = document.createElement("img");
      img.src = char.image_url;
      img.alt = `Folha de referência de ${char.name}`;
      img.addEventListener("click", () => {
        openLightbox([{ url: char.image_url, caption: `Folha de referência: ${char.name}` }], 0);
      });
      thumb.appendChild(img);
    } else {
      const emptyHint = document.createElement("span");
      emptyHint.className = "hint hint-xs";
      emptyHint.style.cssText = "padding: 12px; text-align: center;";
      emptyHint.textContent = "Folha de referência ainda não gerada.";
      thumb.appendChild(emptyHint);
    }
    card.querySelector(".char-card-name").textContent = char.name;
    card.querySelector(".char-card-head .pill").textContent = SHEET_STATUS_LABEL[char.sheet_status] || char.sheet_status;
    card.querySelector(".char-card-desc").textContent = char.description || "Sem descrição visual cadastrada.";
    if (char.error) card.querySelector(".error-msg").textContent = char.error;

    card.querySelector(".char-approve-toggle").addEventListener("change", async (ev) => {
      try {
        await api(`/api/sessions/${state.sid}/characters/${char.id}`, {
          method: "PATCH",
          body: JSON.stringify({ approved: ev.target.checked }),
        });
        await reloadSession();
      } catch (err) {
        toast(err.message, true);
      }
    });

    card.querySelector(".char-gen-btn").addEventListener("click", async (ev) => {
      const btn = ev.currentTarget;
      btn.disabled = true;
      btn.textContent = "Gerando folha…";
      try {
        await api(`/api/sessions/${state.sid}/characters/${char.id}/generate-sheet`, {
          method: "POST",
          body: JSON.stringify({}),
        });
        await reloadSession();
        toast(`Folha de referência de ${char.name} atualizada.`);
      } catch (err) {
        toast(err.message, true);
      } finally {
        btn.disabled = false;
      }
    });

    card.querySelector(".char-del-btn").addEventListener("click", async () => {
      try {
        await api(`/api/sessions/${state.sid}/characters/${char.id}`, { method: "DELETE" });
        await reloadSession();
        toast(`Personagem ${char.name} removido.`);
      } catch (err) {
        toast(err.message, true);
      }
    });

    grid.appendChild(card);
  }

  // Global character library selector
  const select = $("#global-char-select");
  const bookIds = new Set(s.characters.map((c) => c.id));
  const available = state.globalCharacters.filter((c) => !bookIds.has(c.id));
  $("#global-import-wrap").classList.toggle("is-hidden", available.length === 0);
  select.innerHTML = "";
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "Importar da biblioteca global…";
  select.appendChild(placeholder);
  for (const c of available) {
    const opt = document.createElement("option");
    opt.value = c.id;
    opt.textContent = c.name;
    select.appendChild(opt);
  }
}

function renderStage3StoryAndPages() {
  const s = state.session;
  if (!s) return;

  $$("[data-story-mode]").forEach((btn) => {
    btn.classList.toggle("is-active", btn.dataset.storyMode === s.story_mode);
  });
  $("#story-premise").value = s.story_premise || "";
  $("#story-pages").value = s.pages.length || 12;
  if ($("#story-language")) $("#story-language").value = s.language || "en";

  const list = $("#page-list");
  if (!list) return;
  list.innerHTML = "";
  const allChars = allSelectableCharacters();
  const donePages = s.pages.filter((p) => p.status === "done" && p.view_url);
  const insertingAny = state.insertingAfter !== null;

  for (const page of s.pages) {
    const li = document.createElement("li");
    li.className = "page-card";
    li.dataset.pageIndex = String(page.index);
    if (state.lastInsertedIndex === page.index) {
      li.classList.add("is-just-inserted");
    }
    const busy = state.busyPages.has(page.index) || page.status === "generating";
    const insertingHere = state.insertingAfter === page.index;
    const statusClass =
      page.status === "done"
        ? "pill-ok"
        : page.status === "error"
        ? "pill-err"
        : busy
        ? "pill-warn"
        : "";

    li.innerHTML = `
      <div class="page-thumb">
        ${
          page.preview_url
            ? `<img src="${page.preview_url}" alt="Página ${page.index}" loading="lazy" />`
            : `<span>${busy ? "Gerando…" : `Página ${page.index}`}</span>`
        }
      </div>
      <div class="page-card-body">
        <div class="page-card-head">
          <div class="page-card-title-group">
            <span class="page-index-badge">${page.index}</span>
            <strong class="page-scene-title"></strong>
            <span class="pill ${statusClass}">${STATUS_LABEL[busy ? "generating" : page.status] || page.status}</span>
          </div>
        </div>
        <div class="field-row">
          <label class="field-label">
            <span>Título da cena</span>
            <input class="field page-title-input" type="text" placeholder="Página ${page.index}" />
          </label>
          <label class="field-label">
            <span>Texto ou legenda</span>
            <input class="field page-caption-input" type="text" placeholder="Fala ou legenda desta página (opcional)" />
          </label>
        </div>
        <label class="field-label field-grow">
          <span>Prompt visual da cena</span>
          <textarea class="field page-prompt-input" rows="3"></textarea>
        </label>
        <div class="chip-row page-chips"></div>
        ${page.error ? `<p class="error-msg"></p>` : ""}
      </div>
      <div class="page-card-footer">
        <div class="card-actions">
          <button class="btn btn-primary btn-sm page-gen-btn" type="button" ${busy ? "disabled" : ""}>
            ${page.status === "done" ? "Refazer" : "Gerar página"}
          </button>
          <button class="btn btn-outline btn-sm page-insert-btn" type="button" ${insertingAny ? "disabled" : ""} title="Gera uma nova página conectada entre esta cena e a próxima">
            ${insertingHere ? "Criando cena…" : "Nova página"}
          </button>
        </div>
        ${
          page.status === "done"
            ? `<div class="card-actions">
                 <button class="btn btn-outline btn-sm page-style-btn" type="button">Usar fonte desta página</button>
                 <a class="btn btn-ghost btn-sm" href="${page.image_url}" download>PNG 300 DPI</a>
               </div>`
            : ""
        }
      </div>
    `;

    const sceneTitleEl = li.querySelector(".page-scene-title");
    const titleEl = li.querySelector(".page-title-input");
    const capEl = li.querySelector(".page-caption-input");
    const promptEl = li.querySelector(".page-prompt-input");
    sceneTitleEl.textContent = page.title || `Página ${page.index}`;
    titleEl.value = page.title || "";
    capEl.value = page.caption_text || "";
    promptEl.value = page.prompt || "";
    if (page.error) li.querySelector(".error-msg").textContent = page.error;

    const thumbImg = li.querySelector(".page-thumb img");
    if (thumbImg) {
      thumbImg.addEventListener("click", () => {
        const items = donePages.map((p) => ({
          url: p.view_url,
          caption: `Página ${p.index}${p.title ? `: ${p.title}` : ""}`,
        }));
        const idx = donePages.findIndex((p) => p.index === page.index);
        openLightbox(items, Math.max(0, idx));
      });
    }

    const saveEdits = debounce(async () => {
      try {
        await api(`/api/sessions/${state.sid}/pages/${page.index}`, {
          method: "PATCH",
          body: JSON.stringify({
            title: titleEl.value,
            prompt: promptEl.value,
            caption_text: capEl.value,
          }),
        });
        page.title = titleEl.value;
        page.prompt = promptEl.value;
        page.caption_text = capEl.value;
        sceneTitleEl.textContent = titleEl.value.trim() || `Página ${page.index}`;
      } catch (err) {
        toast(err.message, true);
      }
    }, 450);
    titleEl.addEventListener("input", () => {
      sceneTitleEl.textContent = titleEl.value.trim() || `Página ${page.index}`;
      saveEdits();
    });
    promptEl.addEventListener("input", saveEdits);
    capEl.addEventListener("input", saveEdits);

    li.querySelector(".page-gen-btn").addEventListener("click", () =>
      generateOnePage(page.index, promptEl.value, capEl.value, page.character_ids)
    );

    li.querySelector(".page-insert-btn").addEventListener("click", () =>
      insertContextualPage(page.index)
    );

    const styleBtn = li.querySelector(".page-style-btn");
    if (styleBtn) {
      styleBtn.addEventListener("click", async () => {
        try {
          await api(`/api/sessions/${state.sid}/pages/${page.index}/use-as-style`, { method: "POST" });
          await reloadSession();
          toast(`Página ${page.index} definida como referência de texto.`);
          await detectReferenceTypography(true);
        } catch (err) {
          toast(err.message, true);
        }
      });
    }

    renderCharacterChips(li.querySelector(".page-chips"), page, allChars);
    list.appendChild(li);
  }
}

async function insertContextualPage(afterIndex) {
  if (state.insertingAfter !== null) return;
  state.insertingAfter = afterIndex;
  renderStage3StoryAndPages();
  try {
    const res = await api(`/api/sessions/${state.sid}/pages/insert`, {
      method: "POST",
      body: JSON.stringify({ after_index: afterIndex }),
    });
    state.lastInsertedIndex = res.inserted_index;
    state.insertingAfter = null;
    await reloadSession();
    const insertedCard = document.querySelector(`.page-card[data-page-index="${res.inserted_index}"]`);
    if (insertedCard) {
      animateStageEnter(insertedCard);
      insertedCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
    const hasNext = res.inserted_index < res.total_pages;
    toast(
      hasNext
        ? `Página ${res.inserted_index} criada entre as páginas ${afterIndex} e ${res.inserted_index + 1}.`
        : `Página ${res.inserted_index} criada na sequência da página ${afterIndex}.`
    );
    setTimeout(() => {
      if (state.lastInsertedIndex === res.inserted_index) {
        state.lastInsertedIndex = null;
        insertedCard?.classList.remove("is-just-inserted");
      }
    }, 3500);
  } catch (err) {
    state.insertingAfter = null;
    renderStage3StoryAndPages();
    toast(err.message, true);
  }
}

function allSelectableCharacters() {
  const s = state.session;
  if (!s) return [];
  return s.characters.map((c) => ({ id: c.id, name: c.name }));
}

function renderCharacterChips(container, page, chars) {
  container.innerHTML = "";
  if (!chars.length) return;
  for (const c of chars) {
    const active = page.character_ids.includes(c.id);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chip" + (active ? " is-active" : "");
    btn.textContent = c.name;
    btn.addEventListener("click", async () => {
      const nextIds = active
        ? page.character_ids.filter((id) => id !== c.id)
        : [...page.character_ids, c.id];
      page.character_ids = nextIds;
      try {
        await api(`/api/sessions/${state.sid}/pages/${page.index}`, {
          method: "PATCH",
          body: JSON.stringify({ character_ids: nextIds }),
        });
        renderStage3StoryAndPages();
      } catch (err) {
        toast(err.message, true);
      }
    });
    container.appendChild(btn);
  }
}


function updateExtraThumb(card, page) {
  const thumb = card.querySelector(".extra-page-thumb");
  if (!thumb) return;
  if (!page.preview_url) {
    thumb.innerHTML = `<span>${STATUS_LABEL[page.status] || "sem imagem"}</span>`;
    return;
  }
  let img = thumb.querySelector("img");
  if (!img) {
    thumb.innerHTML = "";
    img = document.createElement("img");
    thumb.appendChild(img);
  }
  img.src = page.preview_url;
  img.alt = "Página de texto gerada";
  img.onclick = () =>
    openLightbox([{ url: page.preview_url, caption: page.title || "Página de texto" }], 0);
}

function renderExtraPageList(pages, listEl, emptyEl) {
  listEl.innerHTML = "";
  emptyEl.classList.toggle("is-hidden", pages.length > 0);
  for (const page of pages) {
    const card = document.createElement("div");
    card.className = "extra-page-card";
    card.innerHTML = `
      <div class="extra-page-thumb"></div>
      <div class="extra-page-body">
        <div class="extra-page-head">
          <strong>${page.placement === "front" ? "Página inicial" : "Página final"}</strong>
          <label class="check-toggle">
            <input type="checkbox" class="extra-include" />
            <span>Incluir no PDF</span>
          </label>
        </div>
        <label class="field-label">
          <span>Título</span>
          <input class="field extra-title" type="text" placeholder="Título da página (opcional)" />
        </label>
        <label class="field-label">
          <span>Texto</span>
          <textarea class="field extra-text" rows="5" placeholder="Escreva o conteúdo desta página."></textarea>
        </label>
        <label class="field-label">
          <span>Prompt da página</span>
          <textarea class="field extra-prompt" rows="3" placeholder="Ex: texto centralizado horizontal e verticalmente, sem bordas, com uma imagem pequena do Lumi acima."></textarea>
        </label>
        <div class="topbar-actions">
          <button class="btn btn-accent btn-sm extra-visual-btn" type="button">Gerar página</button>
          <a class="btn btn-ghost btn-sm extra-download is-hidden" href="#" download>PNG</a>
          <button class="btn btn-danger-ghost btn-sm extra-remove-btn" type="button">Remover</button>
        </div>
        ${page.error ? `<p class="error-msg" style="margin: 0;"></p>` : ""}
      </div>
    `;
    const titleEl = card.querySelector(".extra-title");
    const textEl = card.querySelector(".extra-text");
    const promptEl = card.querySelector(".extra-prompt");
    const includeEl = card.querySelector(".extra-include");
    const downloadEl = card.querySelector(".extra-download");
    titleEl.value = page.title || "";
    textEl.value = page.text || "";
    promptEl.value = page.prompt || "";
    includeEl.checked = Boolean(page.include_in_pdf);
    if (page.download_url) {
      downloadEl.href = page.download_url;
      downloadEl.classList.remove("is-hidden");
    }
    if (page.error) card.querySelector(".error-msg").textContent = page.error;
    const visualBtn = card.querySelector(".extra-visual-btn");
    visualBtn.textContent = page.preview_url ? "Refazer página" : "Gerar página";
    updateExtraThumb(card, page);

    const applyUpdate = (updated) => {
      Object.assign(page, updated);
      includeEl.checked = Boolean(page.include_in_pdf);
      if (page.download_url) {
        downloadEl.href = page.download_url;
        downloadEl.classList.remove("is-hidden");
      }
      card.querySelector(".extra-visual-btn").textContent = page.preview_url
        ? "Refazer página"
        : "Gerar página";
      updateExtraThumb(card, page);
    };

    const saveText = debounce(async () => {
      try {
        const updated = await api(
          `/api/sessions/${state.sid}/extra-pages/${page.id}`,
          {
            method: "PATCH",
            body: JSON.stringify({
              title: titleEl.value,
              text: textEl.value,
              prompt: promptEl.value,
            }),
          }
        );
        applyUpdate(updated);
        state.session.pdf_ready = false;
        renderHeaderAndStepper();
      } catch (err) {
        toast(err.message, true);
      }
    }, 700);
    titleEl.addEventListener("input", saveText);
    textEl.addEventListener("input", saveText);
    promptEl.addEventListener("input", saveText);


    card.querySelector(".extra-visual-btn").addEventListener("click", async () => {
      const btn = card.querySelector(".extra-visual-btn");
      if (!promptEl.value.trim()) {
        toast("Informe um prompt visual para esta página.", true);
        return;
      }
      btn.disabled = true;
      btn.textContent = "Gerando página…";
      try {
        await api(`/api/sessions/${state.sid}/extra-pages/${page.id}`, {
          method: "PATCH",
          body: JSON.stringify({
            title: titleEl.value,
            text: textEl.value,
            prompt: promptEl.value,
          }),
        });
        const updated = await api(
          `/api/sessions/${state.sid}/extra-pages/${page.id}/visual`,
          { method: "POST", body: JSON.stringify({}) }
        );
        applyUpdate(updated);
        state.session.pdf_ready = false;
        renderHeaderAndStepper();
        toast("Página gerada.");
      } catch (err) {
        toast(err.message, true);
      } finally {
        btn.disabled = false;
        btn.textContent = page.preview_url ? "Refazer página" : "Gerar página";
      }
    });

    includeEl.addEventListener("change", async () => {
      try {
        const updated = await api(
          `/api/sessions/${state.sid}/extra-pages/${page.id}`,
          {
            method: "PATCH",
            body: JSON.stringify({ include_in_pdf: includeEl.checked }),
          }
        );
        applyUpdate(updated);
        state.session.pdf_ready = false;
        renderHeaderAndStepper();
      } catch (err) {
        toast(err.message, true);
      }
    });

    card.querySelector(".extra-remove-btn").addEventListener("click", async () => {
      try {
        await api(`/api/sessions/${state.sid}/extra-pages/${page.id}`, {
          method: "DELETE",
        });
        await reloadSession();
        toast("Página removida.");
      } catch (err) {
        toast(err.message, true);
      }
    });

    listEl.appendChild(card);
  }
}

function renderStage5Finalize() {
  const s = state.session;
  if (!s) return;

  $("#cover-include").checked = Boolean(s.cover_include);
  $("#cover-color").checked = s.cover_color !== false;
  $("#cover-title").value = s.cover_title || "";
  $("#cover-prompt").value = s.cover_prompt || "";
  const preview = $("#cover-preview");
  preview.innerHTML = "";
  if (s.cover_preview_url) {
    const img = document.createElement("img");
    img.src = s.cover_preview_url;
    img.alt = "Capa do livro";
    img.addEventListener("click", () =>
      openLightbox([{ url: s.cover_preview_url, caption: "Capa do livro" }], 0)
    );
    preview.appendChild(img);
  } else {
    const span = document.createElement("span");
    span.textContent =
      s.cover_status === "generating"
        ? "Gerando capa…"
        : "Nenhuma capa gerada ainda.";
    preview.appendChild(span);
  }
  const coverBtn = $("#cover-generate-btn");
  coverBtn.disabled = s.cover_status === "generating";
  coverBtn.textContent =
    s.cover_status === "generating"
      ? "Gerando capa…"
      : s.cover_preview_url
      ? "Refazer capa"
      : "Gerar capa";
  const download = $("#cover-download");
  download.classList.toggle("is-hidden", !s.cover_download_url);
  if (s.cover_download_url) download.href = s.cover_download_url;
  $("#cover-remove-btn").classList.toggle("is-hidden", !s.cover_preview_url);
  const coverError = $("#cover-error");
  coverError.classList.toggle("is-hidden", !s.cover_error);
  coverError.textContent = s.cover_error || "";

  $("#book-author").value = s.author || "";
  $("#story-synopsis").value = s.story_synopsis || "";

  $("#kdp-blank-verso").checked = Boolean(s.blank_verso);
  $("#kdp-title-page").checked = Boolean(s.include_title_page);
  $("#kdp-belongs-to").checked = Boolean(s.include_belongs_to_page);
  $("#kdp-color-test").checked = Boolean(s.include_color_test_page);

  const pdfReady = Boolean(s.pdf_ready && s.pdf_url);
  $("#pdf-final-status").textContent = pdfReady ? "Prévia pronta" : "PDF ainda não gerado";
  const pdfLink = $("#pdf-final-link");
  pdfLink.classList.toggle("is-hidden", !pdfReady);
  if (pdfReady) pdfLink.href = s.pdf_url;
  $("#compose-final-btn").disabled = !s.pages.some((p) => p.status === "done");

  renderExtraPageList(s.front_extra_pages || [], $("#front-extra-list"), $("#front-extra-empty"));
  renderExtraPageList(s.back_extra_pages || [], $("#back-extra-list"), $("#back-extra-empty"));
  renderPublisherLogo(s);
}

function renderPublisherLogo(s) {
  $("#publisher-logo-include").checked = s.include_publisher_logo !== false;
  const preview = $("#publisher-logo-preview");
  preview.innerHTML = "";
  if (s.publisher_logo_url) {
    const img = document.createElement("img");
    img.src = s.publisher_logo_url;
    img.alt = "Logo da editora";
    preview.appendChild(img);
  } else {
    const span = document.createElement("span");
    span.textContent = "Nenhuma logo enviada.";
    preview.appendChild(span);
  }
  $("#publisher-logo-remove").classList.toggle("is-hidden", !s.publisher_logo_url);
}


function renderAll() {
  renderHeaderAndStepper();
  renderPresets();
  renderStage1Form();
  renderStage2Characters();
  renderStage3StoryAndPages();
  renderStage5Finalize();
  syncAllPopoverSelects();
}

/* ==================== ACTIONS ==================== */
async function reloadSession() {
  state.session = await api(`/api/sessions/${state.sid}`);
  renderAll();
}

async function saveStyleConfig({ silent = false } = {}) {
  const payload = {
    title: $("#session-title").value.trim(),
    context_prompt: $("#context").value.trim(),
    negative_prompt: $("#negative-prompt").value.trim(),
    page_size: $("#page-size").value,
    provider: $("#provider").value,
    model: $("#model").value.trim(),
    processing: {
      threshold: $("#proc-threshold").value,
      min_component_px: Math.max(0, Number($("#proc-min-px").value) || 0),
      denoise: $("#proc-denoise").checked,
      crop_to_content: $("#proc-crop").checked,
    },
    caption_enabled: $("#caption-enabled").checked,
    caption_style: $("#caption-style").value,
    caption_position: $("#caption-position").value,
    caption_font_family: $("#caption-font").value,
    caption_font_bold: $("#caption-weight").value === "bold",
    caption_font_size: Number($("#caption-size").value) || 0.032,
  };
  try {
    state.session = await api(`/api/sessions/${state.sid}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
    renderAll();
    if (!silent) toast("Padrões do livro salvos.");
    return true;
  } catch (err) {
    toast(err.message, true);
    return false;
  }
}

async function detectReferenceTypography(silent = false) {
  const btn = $("#style-detect-btn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Detectando fonte…";
  }
  try {
    const res = await api(`/api/sessions/${state.sid}/style/detect`, { method: "POST" });
    await reloadSession();
    if (res.typography_error) {
      toast("Não foi possível detectar a fonte na imagem. Escolha a fonte manualmente.", true);
      return;
    }
    const fontLabels = { sans: "Sans", serif: "Serif", mono: "Mono" };
    const label = fontLabels[res.font_family] || "Sans";
    const weight = res.font_bold ? "Negrito" : "Normal";
    if (!silent) toast(`Fonte detectada: ${label}, ${weight}.`);
  } catch (err) {
    if (!silent) toast(err.message || "Falha ao detectar a fonte.", true);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Detectar fonte na imagem";
    }
  }
}

async function generateOnePage(index, prompt, captionText, characterIds) {
  state.busyPages.add(index);
  renderStage3StoryAndPages();
  try {
    await api(`/api/sessions/${state.sid}/pages/${index}/generate`, {
      method: "POST",
      body: JSON.stringify({
        prompt,
        caption_text: captionText,
        character_ids: characterIds,
        provider: $("#provider").value || state.session.provider,
        model: $("#model").value.trim() || state.session.model,
      }),
    });
    await reloadSession();
    toast(`Página ${index} gerada.`);
  } catch (err) {
    toast(err.message, true);
  } finally {
    state.busyPages.delete(index);
    renderStage3StoryAndPages();
  }
}

async function generateAllPending() {
  const pending = state.session.pages.filter((p) => p.status !== "done");
  if (!pending.length) {
    toast("Todas as páginas já estão prontas.");
    return;
  }
  const btn = $("#generate-pending");
  btn.disabled = true;
  for (const page of pending) {
    await generateOnePage(page.index, page.prompt, page.caption_text, page.character_ids);
  }
  btn.disabled = false;
  toast("Fila de geração concluída.");
}

async function composePdfAction() {
  const topBtn = $("#compose-btn");
  const finalBtn = $("#compose-final-btn");
  const errorEl = $("#pdf-final-error");
  if (errorEl) errorEl.classList.add("is-hidden");
  try {
    state.session = await api(`/api/sessions/${state.sid}`, {
      method: "PATCH",
      body: JSON.stringify({
        author: $("#book-author").value,
        story_synopsis: $("#story-synopsis").value,
      }),
    });
  } catch (_) {}

  topBtn.disabled = true;
  topBtn.textContent = "Gerando PDF…";
  if (finalBtn) {
    finalBtn.disabled = true;
    finalBtn.textContent = "Gerando PDF…";
  }
  try {
    state.session = await api(`/api/sessions/${state.sid}/compose`, { method: "POST" });
    renderHeaderAndStepper();
    renderStage5Finalize();
    toast("Prévia em PDF pronta.");
  } catch (err) {
    if (errorEl) {
      errorEl.textContent = err.message;
      errorEl.classList.remove("is-hidden");
    }
    toast(err.message, true);
  } finally {
    topBtn.textContent = "Gerar prévia PDF";
    if (finalBtn) finalBtn.textContent = "Gerar prévia PDF";
    renderHeaderAndStepper();
    renderStage5Finalize();
  }
}

/* ==================== LIGHTBOX ==================== */
function openLightbox(items, index) {
  if (!items.length) return;
  state.lightboxItems = items;
  state.lightboxIndex = index;
  updateLightbox();
  const box = $("#lightbox");
  box.classList.remove("is-hidden");
  animateLightboxOpen(box);
}

function updateLightbox() {
  const item = state.lightboxItems[state.lightboxIndex];
  if (!item) return;
  $("#lightbox-img").src = item.url;
  $("#lightbox-caption").textContent = item.caption || "";
  const multi = state.lightboxItems.length > 1;
  $("#lightbox-prev").classList.toggle("is-hidden", !multi);
  $("#lightbox-next").classList.toggle("is-hidden", !multi);
}

function closeLightbox() {
  $("#lightbox").classList.add("is-hidden");
}

/* ==================== AUTOPILOT POLLING ==================== */
function sessionHasResumableProgress() {
  const s = state.session;
  if (!s) return false;
  const hasBlueprint =
    Boolean(s.story_synopsis && s.story_synopsis.trim()) ||
    s.characters.length > 0 ||
    s.pages.some((p) => p.status === "done" || (p.title && p.title.trim()));
  if (!hasBlueprint) return false;
  const pendingSheets = s.characters.some((c) => c.sheet_status !== "ready");
  const pendingPages = s.pages.some((p) => p.status !== "done");
  return pendingSheets || pendingPages || !s.pdf_ready;
}

function renderAutopilot(st) {
  const bar = $("#autopilot-bar");
  const isVisible = st && st.status && st.status !== "idle" && st.status !== "done";
  bar.classList.toggle("is-hidden", !isVisible);

  const running = Boolean(st && st.status === "running");
  const canResumeStatus = Boolean(st && (st.status === "cancelled" || st.status === "error"));
  const resumeCardBtn = $("#autopilot-resume-btn");
  if (resumeCardBtn) {
    resumeCardBtn.classList.toggle("is-hidden", running || (!canResumeStatus && !sessionHasResumableProgress()));
    resumeCardBtn.disabled = running;
  }

  $("#autopilot-full-btn").disabled = running;
  $("#autopilot-plan-btn").disabled = running;

  if (!isVisible) return;

  const badge = $("#autopilot-badge");
  badge.classList.remove("pill-ok", "pill-warn", "pill-err");
  if (st.status === "running") {
    badge.classList.add("pill-warn");
    badge.textContent = st.cancelling ? "Cancelando…" : "Gerando com IA";
  } else if (st.status === "done") {
    badge.classList.add("pill-ok");
    badge.textContent = "Concluído";
  } else if (st.status === "error") {
    badge.classList.add("pill-err");
    badge.textContent = "Erro";
  } else {
    badge.textContent = "Cancelado";
  }

  $("#autopilot-label").textContent = st.label || "";
  $("#autopilot-pct").textContent = `${st.percent || 0}%`;
  $("#autopilot-fill").style.width = `${st.percent || 0}%`;

  const cancelBtn = $("#autopilot-cancel");
  cancelBtn.classList.toggle("is-hidden", !running);
  cancelBtn.disabled = Boolean(st.cancelling);
  cancelBtn.textContent = st.cancelling ? "Cancelando…" : "Cancelar";

  const resumeBtn = $("#autopilot-resume");
  if (resumeBtn) {
    resumeBtn.classList.toggle("is-hidden", !canResumeStatus);
    resumeBtn.disabled = running;
  }

  const errEl = $("#autopilot-error");
  errEl.classList.toggle("is-hidden", !st.error);
  if (st.error) errEl.textContent = st.error;
}

async function pollAutopilot() {
  try {
    const st = await api(`/api/sessions/${state.sid}/autopilot`);
    renderAutopilot(st);
    if (st.status === "running") {
      if (st.phase !== state.lastAutopilotPhase || st.phase === "pages" || st.phase === "sheets") {
        state.lastAutopilotPhase = st.phase;
        await reloadSession();
      }
      if (!state.autopilotTimer) {
        state.autopilotTimer = setInterval(pollAutopilot, 1800);
      }
    } else {
      if (state.autopilotTimer) {
        clearInterval(state.autopilotTimer);
        state.autopilotTimer = null;
        await reloadSession();
        if (st.status === "done") {
          setStep(st.current_step || 4, { animate: true, persist: false });
          toast(st.label || "Geração automática concluída.");
        } else if (st.status === "cancelled") {
          toast("Geração cancelada. Você pode continuar de onde parou.");
        }
      }
    }
  } catch (_) {}
}

async function triggerAutopilot(stopAfter, { resume = false } = {}) {
  try {
    const saved = await saveStyleConfig({ silent: true });
    if (!saved) return;
    const st = await api(`/api/sessions/${state.sid}/autopilot`, {
      method: "POST",
      body: JSON.stringify({
        pages: state.session.pages.length || 12,
        premise_hint: $("#story-premise").value.trim(),
        language: $("#story-language")?.value || state.session.language || "en",
        stop_after: stopAfter,
        resume,
      }),
    });
    renderAutopilot(st);
    if (!state.autopilotTimer) {
      state.autopilotTimer = setInterval(pollAutopilot, 1800);
    }
    toast(
      resume
        ? "Retomando geração de onde parou…"
        : "Geração automática iniciada. Acompanhe o progresso acima."
    );
  } catch (err) {
    toast(err.message, true);
  }
}

/* ==================== AUTH MODAL ==================== */
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
      const m = await api("/api/auth/codex/models").catch(() => ({ models: [], cheapest: null }));
      state.codexModels = m.models || [];
      state.codexCheapest = m.cheapest || null;
      renderModelSuggestions({ preferred: state.session?.model });
    }
  } catch (_) {}
}

/* ==================== BINDINGS & INIT ==================== */
function bindEvents() {
  $("#kdp-interior-btn").addEventListener("click", async () => {
    const button = $("#kdp-interior-btn");
    const result = $("#kdp-preflight-result");
    button.disabled = true;
    try {
      const response = await fetch(`/api/sessions/${state.sid}/kdp-interior`, { method: "POST" });
      if (!response.ok) {
        const data = await response.json();
        throw new Error(Array.isArray(data.detail) ? data.detail.join(" ") : data.detail);
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = "miolo-kdp.pdf";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      result.textContent = "Miolo gerado para revisão.";
    } catch (err) {
      result.textContent = err.message;
    } finally {
      button.disabled = false;
    }
  });
  $("#kdp-preflight-btn").addEventListener("click", async () => {
    const result = $("#kdp-preflight-result");
    result.replaceChildren();
    try {
      const report = await api(`/api/sessions/${state.sid}/kdp-preflight`);
      const count = document.createElement("p");
      count.textContent = `Páginas previstas: ${report.physical_pages}. Contagem KDP: ${report.kdp_pages}.`;
      result.appendChild(count);
      for (const [label, items] of [["Pendências", report.errors], ["Revisões", report.warnings]]) {
        if (!items.length) continue;
        const heading = document.createElement("strong");
        heading.textContent = label;
        const list = document.createElement("ul");
        for (const message of items) {
          const item = document.createElement("li");
          item.textContent = message;
          list.appendChild(item);
        }
        result.append(heading, list);
      }
    } catch (err) {
      result.textContent = err.message;
    }
  });
  $$(".step-tab").forEach((tab) => {
    tab.addEventListener("click", () => setStep(Number(tab.dataset.step)));
  });
  $$("[data-goto-step]").forEach((btn) => {
    btn.addEventListener("click", () => setStep(Number(btn.dataset.gotoStep)));
  });

  $("#session-title").addEventListener(
    "input",
    debounce(async () => {
      const title = $("#session-title").value.trim();
      if (!title) return;
      state.session = await api(`/api/sessions/${state.sid}`, {
        method: "PATCH",
        body: JSON.stringify({ title }),
      }).catch(() => state.session);
      renderHeaderAndStepper();
    }, 500)
  );

  $("#provider").addEventListener("change", () => {
    renderModelSuggestions({ forceCheapest: true });
    renderAutopilotSummary();
  });
  for (const sel of ["#page-size", "#model", "#caption-enabled", "#caption-font", "#caption-weight"]) {
    $(sel).addEventListener("change", renderAutopilotSummary);
  }
  $("#save-style-config").addEventListener("click", () => saveStyleConfig());
  $("#style-detect-btn").addEventListener("click", () => detectReferenceTypography());
  $("#autopilot-full-btn").addEventListener("click", () => triggerAutopilot("pdf"));
  $("#autopilot-plan-btn").addEventListener("click", () => triggerAutopilot("blueprint"));
  $("#autopilot-resume")?.addEventListener("click", () => triggerAutopilot("pdf", { resume: true }));
  $("#autopilot-resume-btn")?.addEventListener("click", () => triggerAutopilot("pdf", { resume: true }));
  $("#autopilot-cancel").addEventListener("click", async () => {
    try {
      const btn = $("#autopilot-cancel");
      btn.disabled = true;
      btn.textContent = "Cancelando…";
      const st = await api(`/api/sessions/${state.sid}/autopilot/cancel`, { method: "POST" });
      renderAutopilot(st);
      if (st.status === "running" && !state.autopilotTimer) {
        state.autopilotTimer = setInterval(pollAutopilot, 1000);
      } else if (st.status === "cancelled") {
        await reloadSession();
        toast("Geração cancelada. Você pode continuar de onde parou.");
      }
    } catch (err) {
      toast(err.message, true);
    }
  });

  // Save custom preset modal
  $("#save-custom-preset-btn").addEventListener("click", () => {
    $("#custom-preset-name").value = "";
    $("#custom-preset-tagline").value = "";
    const modal = $("#preset-modal");
    modal.classList.remove("is-hidden");
    animateModalOpen(modal);
  });
  $("#preset-modal-close").addEventListener("click", () => $("#preset-modal").classList.add("is-hidden"));
  $("#preset-modal").addEventListener("click", (ev) => {
    if (ev.target === $("#preset-modal")) $("#preset-modal").classList.add("is-hidden");
  });
  $("#custom-preset-save").addEventListener("click", async () => {
    const name = $("#custom-preset-name").value.trim();
    if (!name) return toast("Informe um nome para o preset.", true);
    try {
      const created = await api("/api/presets", {
        method: "POST",
        body: JSON.stringify({
          name,
          tagline: $("#custom-preset-tagline").value.trim(),
          page_size: $("#page-size").value,
          context_prompt: $("#context").value.trim(),
          negative_prompt: $("#negative-prompt").value.trim(),
          processing: {
            threshold: $("#proc-threshold").value,
            min_component_px: Math.max(0, Number($("#proc-min-px").value) || 0),
            denoise: $("#proc-denoise").checked,
            crop_to_content: $("#proc-crop").checked,
          },
        }),
      });
      state.presets = await api("/api/presets");
      $("#preset-modal").classList.add("is-hidden");
      renderPresets();
      toast(`Preset "${created.name}" salvo.`);
    } catch (err) {
      toast(err.message, true);
    }
  });

  // Per-book style anchor
  $("#style-generate-btn").addEventListener("click", async () => {
    const btn = $("#style-generate-btn");
    const errEl = $("#style-error");
    errEl.classList.add("is-hidden");
    btn.disabled = true;
    btn.textContent = "Gerando amostra visual…";
    try {
      await saveStyleConfig();
      await api(`/api/sessions/${state.sid}/style/generate`, {
        method: "POST",
        body: JSON.stringify({ sample_subject: $("#style-sample-subject").value.trim() }),
      });
      await reloadSession();
      toast("Amostra de traço gerada.");
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove("is-hidden");
    } finally {
      btn.disabled = false;
      btn.textContent = "Gerar amostra de traço";
    }
  });

  $("#style-upload").addEventListener("click", async () => {
    const file = $("#style-file").files?.[0];
    if (!file) return toast("Selecione uma imagem com uma amostra de texto.", true);
    const form = new FormData();
    form.append("image", file);
    try {
      await api(`/api/sessions/${state.sid}/style`, { method: "POST", body: form });
      $("#style-file").value = "";
      await reloadSession();
      toast("Amostra de texto enviada!");
      await detectReferenceTypography(true);
    } catch (err) {
      toast(err.message, true);
    }
  });

  $("#style-remove").addEventListener("click", async () => {
    await api(`/api/sessions/${state.sid}/style`, { method: "DELETE" });
    await reloadSession();
    toast("Referência de texto removida.");
  });

  // Stage 2: Character Sheet
  $("#char-add-btn").addEventListener("click", async () => {
    const errEl = $("#char-error");
    errEl.classList.add("is-hidden");
    const name = $("#char-name").value.trim();
    const desc = $("#char-desc").value.trim();
    const file = $("#char-file").files?.[0];
    if (!name) {
      errEl.textContent = "Informe o nome do personagem.";
      errEl.classList.remove("is-hidden");
      return;
    }
    const form = new FormData();
    form.append("name", name);
    form.append("description", desc);
    if (file) form.append("image", file);
    try {
      await api(`/api/sessions/${state.sid}/characters`, { method: "POST", body: form });
      $("#char-name").value = "";
      $("#char-desc").value = "";
      $("#char-file").value = "";
      await reloadSession();
      toast(`Personagem "${name}" adicionado!`);
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove("is-hidden");
    }
  });

  $("#global-char-import").addEventListener("click", async () => {
    const cid = $("#global-char-select").value;
    if (!cid) return;
    try {
      await api(`/api/sessions/${state.sid}/characters/import-global/${cid}`, { method: "POST" });
      await reloadSession();
      toast("Personagem importado para o livro!");
    } catch (err) {
      toast(err.message, true);
    }
  });

  // Stage 3: Story & Captions
  $$("[data-story-mode]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const mode = btn.dataset.storyMode;
      await api(`/api/sessions/${state.sid}/story`, {
        method: "PATCH",
        body: JSON.stringify({ mode }),
      });
      state.session.story_mode = mode;
      renderStage3StoryAndPages();
    });
  });

  $("#story-language").addEventListener("change", async () => {
    state.session = await api(`/api/sessions/${state.sid}`, {
      method: "PATCH",
      body: JSON.stringify({ language: $("#story-language").value }),
    });
  });

  $("#story-save-btn").addEventListener("click", async () => {
    const errEl = $("#story-error");
    errEl.classList.add("is-hidden");
    try {
      state.session = await api(`/api/sessions/${state.sid}`, {
        method: "PATCH",
        body: JSON.stringify({
          story_mode: state.session.story_mode || "narrative",
          story_premise: $("#story-premise").value,
          language: $("#story-language").value,
          pages: Math.max(1, Math.min(200, Number($("#story-pages").value) || 12)),
        }),
      });
      renderAll();
      toast("Roteiro, idioma e páginas salvos.");
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove("is-hidden");
      toast(err.message, true);
    }
  });

  $("#story-generate-btn").addEventListener("click", async () => {
    const errEl = $("#story-error");
    errEl.classList.add("is-hidden");
    const premise = $("#story-premise").value.trim();
    if (!premise) {
      errEl.textContent = "Descreva a ideia ou tema do livro para gerar o roteiro.";
      errEl.classList.remove("is-hidden");
      return;
    }
    const btn = $("#story-generate-btn");
    btn.disabled = true;
    btn.textContent = "Roteirizando páginas…";
    try {
      const res = await api(`/api/sessions/${state.sid}/story/generate`, {
        method: "POST",
        body: JSON.stringify({
          premise,
          mode: state.session.story_mode || "narrative",
          pages: Math.max(1, Math.min(200, Number($("#story-pages").value) || 12)),
          language: $("#story-language").value,
        }),
      });
      await reloadSession();
      toast(`Roteiro criado com ${plural(res.pages_updated, "página pronta", "páginas prontas")}.`);
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove("is-hidden");
    } finally {
      btn.disabled = false;
      btn.textContent = "Gerar roteiro";
    }
  });

  const saveCaptionSettings = async () => {
    state.session = await api(`/api/sessions/${state.sid}`, {
      method: "PATCH",
      body: JSON.stringify({
        caption_enabled: $("#caption-enabled").checked,
        caption_style: $("#caption-style").value,
        caption_position: $("#caption-position").value,
        caption_font_family: $("#caption-font").value,
        caption_font_bold: $("#caption-weight").value === "bold",
        caption_font_size: Number($("#caption-size").value),
      }),
    });
    renderCaptionSettings(state.session);
    toast("Estilo do texto atualizado.");
  };
  for (const sel of ["#caption-enabled", "#caption-style", "#caption-position", "#caption-font", "#caption-weight", "#caption-size"]) {
    $(sel).addEventListener("change", saveCaptionSettings);
  }

  // Stage 5: PDF finalization options
  const saveKdpOptions = async () => {
    state.session = await api(`/api/sessions/${state.sid}`, {
      method: "PATCH",
      body: JSON.stringify({
        blank_verso: $("#kdp-blank-verso").checked,
        include_title_page: $("#kdp-title-page").checked,
        include_belongs_to_page: $("#kdp-belongs-to").checked,
        include_color_test_page: $("#kdp-color-test").checked,
      }),
    });
    renderHeaderAndStepper();
    renderStage5Finalize();
  };
  for (const sel of ["#kdp-blank-verso", "#kdp-title-page", "#kdp-belongs-to", "#kdp-color-test"]) {
    $(sel).addEventListener("change", saveKdpOptions);
  }

  const saveBookInfo = async () => {
    const errEl = $("#book-info-error");
    errEl.classList.add("is-hidden");
    try {
      state.session = await api(`/api/sessions/${state.sid}`, {
        method: "PATCH",
        body: JSON.stringify({
          author: $("#book-author").value,
          story_synopsis: $("#story-synopsis").value,
        }),
      });
      renderHeaderAndStepper();
      toast("Informações da folha de rosto salvas.");
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove("is-hidden");
      toast(err.message, true);
    }
  };
  $("#save-book-info-btn").addEventListener("click", saveBookInfo);
  const saveBookInfoDebounced = debounce(async () => {
    state.session = await api(`/api/sessions/${state.sid}`, {
      method: "PATCH",
      body: JSON.stringify({
        author: $("#book-author").value,
        story_synopsis: $("#story-synopsis").value,
      }),
    }).catch(() => state.session);
  }, 800);
  $("#book-author").addEventListener("input", saveBookInfoDebounced);
  $("#story-synopsis").addEventListener("input", saveBookInfoDebounced);

  $("#generate-pending").addEventListener("click", generateAllPending);
  $("#compose-btn").addEventListener("click", composePdfAction);
  $("#compose-final-btn").addEventListener("click", composePdfAction);

  $("#cover-include").addEventListener("change", async () => {
    try {
      state.session = await api(`/api/sessions/${state.sid}`, {
        method: "PATCH",
        body: JSON.stringify({ cover_include: $("#cover-include").checked }),
      });
      renderHeaderAndStepper();
      renderStage5Finalize();
    } catch (err) {
      toast(err.message, true);
    }
  });

  $("#cover-color").addEventListener("change", async () => {
    try {
      state.session = await api(`/api/sessions/${state.sid}`, {
        method: "PATCH",
        body: JSON.stringify({ cover_color: $("#cover-color").checked }),
      });
      toast($("#cover-color").checked ? "Capa será gerada colorida." : "Capa será gerada em P&B.");
    } catch (err) {
      toast(err.message, true);
    }
  });

  const saveCoverMeta = debounce(async () => {
    try {
      state.session = await api(`/api/sessions/${state.sid}`, {
        method: "PATCH",
        body: JSON.stringify({
          cover_title: $("#cover-title").value,
          cover_prompt: $("#cover-prompt").value,
        }),
      });
    } catch (err) {
      toast(err.message, true);
    }
  }, 500);
  $("#cover-title").addEventListener("input", saveCoverMeta);
  $("#cover-prompt").addEventListener("input", saveCoverMeta);

  $("#cover-generate-btn").addEventListener("click", async () => {
    const btn = $("#cover-generate-btn");
    const errEl = $("#cover-error");
    errEl.classList.add("is-hidden");
    btn.disabled = true;
    btn.textContent = "Gerando capa…";
    try {
      await api(`/api/sessions/${state.sid}/cover/generate`, {
        method: "POST",
        body: JSON.stringify({
          title: $("#cover-title").value.trim(),
          prompt: $("#cover-prompt").value.trim(),
          color: $("#cover-color").checked,
        }),
      });
      await reloadSession();
      toast("Capa gerada.");
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove("is-hidden");
      toast(err.message, true);
    } finally {
      renderStage5Finalize();
    }
  });

  $("#cover-remove-btn").addEventListener("click", async () => {
    try {
      await api(`/api/sessions/${state.sid}/cover`, { method: "DELETE" });
      await reloadSession();
      toast("Capa removida.");
    } catch (err) {
      toast(err.message, true);
    }
  });

  const addExtraPage = async (placement) => {
    try {
      await api(`/api/sessions/${state.sid}/extra-pages`, {
        method: "POST",
        body: JSON.stringify({
          placement,
          title: placement === "front" ? "Página inicial" : "Página final",
          text: "",
        }),
      });
      await reloadSession();
      toast("Página adicionada. Edite o texto abaixo.");
    } catch (err) {
      toast(err.message, true);
    }
  };

  $("#publisher-logo-upload").addEventListener("click", async () => {
    const file = $("#publisher-logo-file").files[0];
    if (!file) {
      toast("Escolha a imagem da logo.", true);
      return;
    }
    const form = new FormData();
    form.append("image", file);
    try {
      await api("/api/publisher-logo", { method: "POST", body: form });
      $("#publisher-logo-file").value = "";
      await reloadSession();
      toast("Logo da editora salva.");
    } catch (err) {
      toast(err.message, true);
    }
  });
  $("#publisher-logo-remove").addEventListener("click", async () => {
    try {
      await api("/api/publisher-logo", { method: "DELETE" });
      await reloadSession();
      toast("Logo removida.");
    } catch (err) {
      toast(err.message, true);
    }
  });
  $("#publisher-logo-include").addEventListener("change", async () => {
    try {
      state.session = await api(`/api/sessions/${state.sid}`, {
        method: "PATCH",
        body: JSON.stringify({ include_publisher_logo: $("#publisher-logo-include").checked }),
      });
      renderHeaderAndStepper();
      renderStage5Finalize();
    } catch (err) {
      toast(err.message, true);
    }
  });

  $("#add-front-extra-btn").addEventListener("click", () => addExtraPage("front"));
  $("#add-back-extra-btn").addEventListener("click", () => addExtraPage("back"));

  // Lightbox
  $("#lightbox-close").addEventListener("click", closeLightbox);
  $("#lightbox").addEventListener("click", (ev) => {
    if (ev.target === $("#lightbox")) closeLightbox();
  });
  $("#lightbox-prev").addEventListener("click", () => {
    state.lightboxIndex = (state.lightboxIndex - 1 + state.lightboxItems.length) % state.lightboxItems.length;
    updateLightbox();
  });
  $("#lightbox-next").addEventListener("click", () => {
    state.lightboxIndex = (state.lightboxIndex + 1) % state.lightboxItems.length;
    updateLightbox();
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") {
      $("#preset-modal")?.classList.add("is-hidden");
      $("#auth-modal")?.classList.add("is-hidden");
      if (!$("#lightbox").classList.contains("is-hidden")) closeLightbox();
      return;
    }
    if ($("#lightbox").classList.contains("is-hidden")) return;
    if (ev.key === "ArrowLeft") $("#lightbox-prev").click();
    if (ev.key === "ArrowRight") $("#lightbox-next").click();
  });

  // Auth modal
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
}

async function init() {
  state.sid = sessionIdFromUrl();
  if (!state.sid) {
    window.location.href = "/";
    return;
  }

  try {
    const meta = await api("/api/meta");
    if (!meta.features?.includes("presets")) {
      $("#server-banner").classList.remove("is-hidden");
    }
  } catch (_) {}

  const [defaults, presets, globalChars, session] = await Promise.all([
    api("/api/defaults").catch(() => null),
    api("/api/presets").catch(() => []),
    api("/api/characters").catch(() => []),
    api(`/api/sessions/${state.sid}`),
  ]);

  state.defaults = defaults;
  state.presets = presets;
  state.categories = await api("/api/presets/categories").catch(() => []);
  state.globalCharacters = globalChars;
  state.session = session;

  bindEvents();
  renderAll();
  setStep(session.current_step || 1, { animate: true, persist: false });
  await Promise.all([refreshAuth(), pollAutopilot()]);
}

init();
