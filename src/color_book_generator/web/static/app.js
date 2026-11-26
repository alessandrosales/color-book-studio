import { syncAllPopoverSelects } from "/static/popover-select.js";
import { renderStylePicker } from "/static/style-picker.js";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

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
  const next = Math.max(1, Math.min(4, Number(step) || 1));
  state.currentStep = next;

  $$(".step-tab").forEach((tab) => {
    tab.classList.toggle("is-active", Number(tab.dataset.step) === next);
  });

  for (let i = 1; i <= 4; i++) {
    const panel = $(`#stage-${i}`);
    const isTarget = i === next;
    panel.classList.toggle("is-hidden", !isTarget);
    if (isTarget && animate && typeof window.gsap !== "undefined") {
      const mm = window.gsap.matchMedia();
      mm.add("(prefers-reduced-motion: no-preference)", () => {
        window.gsap.fromTo(
          panel,
          { y: 14, autoAlpha: 0 },
          {
            y: 0,
            autoAlpha: 1,
            duration: 0.42,
            ease: "power3.out",
            overwrite: "auto",
            clearProps: "transform,opacity,visibility",
          }
        );
      });
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
  document.title = `${s.title} — Color Book Studio`;
  $("#meta-format").textContent = `${s.trim_in[0]}x${s.trim_in[1]} in · ${s.dpi} DPI`;
  if ($("#export-book-zip")) $("#export-book-zip").href = `/api/sessions/${s.id}/export`;

  const doneCount = s.pages.filter((p) => p.status === "done").length;
  const totalCount = s.pages.length;

  const activePreset = state.presets.find((p) => p.id === s.preset_id);
  $("#step-1-sub").textContent = activePreset ? activePreset.name : s.page_size.replace("_", " ");
  $("#step-2-sub").textContent = `${s.characters.length} ${s.characters.length === 1 ? "personagem" : "personagens"}`;
  $("#step-3-sub").textContent = s.story_synopsis ? "Roteiro pronto" : `${totalCount} páginas`;
  $("#step-4-sub").textContent = `${doneCount} / ${totalCount} prontas`;
  $("#progress-pill").textContent = `${doneCount} / ${totalCount} prontas`;

  const canCompose = doneCount > 0;
  $("#compose-btn").disabled = !canCompose;
  $("#compose-btn-stage").disabled = !canCompose;

  for (const linkSel of ["#pdf-link", "#pdf-link-stage"]) {
    const el = $(linkSel);
    el.classList.toggle("is-hidden", !s.pdf_ready || !s.pdf_url);
    if (s.pdf_url) el.href = s.pdf_url;
  }
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

  // Style anchor preview
  const preview = $("#style-preview");
  const removeBtn = $("#style-remove");
  preview.innerHTML = "";
  if (s.style_url) {
    const img = document.createElement("img");
    img.src = s.style_url;
    img.alt = "Âncora de estilo do livro";
    img.addEventListener("click", () => {
      openLightbox([{ url: s.style_url, caption: "Âncora visual de estilo do livro" }], 0);
    });
    preview.appendChild(img);
    removeBtn.classList.toggle("is-hidden", Boolean(s.style_inherited_global));
  } else {
    const span = document.createElement("span");
    span.textContent = "Nenhuma imagem âncora definida ainda. Gere uma com IA ou envie uma imagem de referência.";
    preview.appendChild(span);
    removeBtn.classList.add("is-hidden");
  }
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
            ${char.image_url ? "Regenerar Sheet" : "Gerar Character Sheet"}
          </button>
          <button class="btn btn-danger-ghost btn-sm char-del-btn" type="button">Remover</button>
        </div>
      </div>
    `;
    const thumb = card.querySelector(".char-sheet-thumb");
    if (char.image_url) {
      const img = document.createElement("img");
      img.src = char.image_url;
      img.alt = "";
      img.addEventListener("click", () => {
        openLightbox([{ url: char.image_url, caption: `Character Sheet — ${char.name}` }], 0);
      });
      thumb.appendChild(img);
    } else {
      const emptyHint = document.createElement("span");
      emptyHint.className = "hint";
      emptyHint.style.cssText = "font-size: 0.82rem; padding: 12px; text-align: center;";
      emptyHint.textContent = "Character Sheet ainda não gerado";
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
      btn.textContent = "Gerando Sheet…";
      try {
        await api(`/api/sessions/${state.sid}/characters/${char.id}/generate-sheet`, {
          method: "POST",
          body: JSON.stringify({}),
        });
        await reloadSession();
        toast(`Character Sheet de ${char.name} atualizado!`);
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

function renderStage3Story() {
  const s = state.session;
  if (!s) return;

  $$("[data-story-mode]").forEach((btn) => {
    btn.classList.toggle("is-active", btn.dataset.storyMode === s.story_mode);
  });
  $("#story-premise").value = s.story_premise || "";
  $("#story-synopsis").value = s.story_synopsis || "";
  $("#story-pages").value = s.pages.length || 12;
  if ($("#story-language")) $("#story-language").value = s.language || "en";

  $("#caption-enabled").checked = Boolean(s.caption_enabled);
  $("#caption-style").value = s.caption_style || "balloon";
  $("#caption-position").value = s.caption_position || "bottom";
  $("#caption-font").value = s.caption_font_family || "sans";
  $("#caption-weight").value = s.caption_font_bold ? "bold" : "normal";
  $("#caption-size").value = String(s.caption_font_size || 0.032);
  $("#caption-options").style.opacity = s.caption_enabled ? "1" : "0.5";

  const list = $("#script-list");
  list.innerHTML = "";
  const allChars = allSelectableCharacters();

  for (const page of s.pages) {
    const li = document.createElement("li");
    li.className = "script-card";
    li.innerHTML = `
      <span class="page-index-badge">${page.index}</span>
      <div class="page-card-body">
        <div class="field-row">
          <label class="field-label">
            <span>Título da cena</span>
            <input class="field script-title" type="text" placeholder="Página ${page.index}" />
          </label>
          <label class="field-label">
            <span>Texto / legenda da página</span>
            <input class="field script-caption" type="text" placeholder="Fala ou legenda que aparece no livro" />
          </label>
        </div>
        <label class="field-label">
          <span>Prompt visual da cena</span>
          <textarea class="field script-prompt" rows="2"></textarea>
        </label>
        <div class="chip-row script-chips"></div>
      </div>
    `;
    const titleEl = li.querySelector(".script-title");
    const capEl = li.querySelector(".script-caption");
    const promptEl = li.querySelector(".script-prompt");
    titleEl.value = page.title || "";
    capEl.value = page.caption_text || "";
    promptEl.value = page.prompt || "";

    const savePage = debounce(async () => {
      try {
        await api(`/api/sessions/${state.sid}/pages/${page.index}`, {
          method: "PATCH",
          body: JSON.stringify({
            title: titleEl.value,
            caption_text: capEl.value,
            prompt: promptEl.value,
          }),
        });
        page.title = titleEl.value;
        page.caption_text = capEl.value;
        page.prompt = promptEl.value;
      } catch (err) {
        toast(err.message, true);
      }
    }, 450);

    titleEl.addEventListener("input", savePage);
    capEl.addEventListener("input", savePage);
    promptEl.addEventListener("input", savePage);

    renderCharacterChips(li.querySelector(".script-chips"), page, allChars);
    list.appendChild(li);
  }
}

function allSelectableCharacters() {
  const s = state.session;
  if (!s) return [];
  const map = new Map();
  for (const c of s.characters) {
    map.set(c.id, { id: c.id, name: c.name });
  }
  for (const c of state.globalCharacters) {
    if (!map.has(c.id)) map.set(c.id, { id: c.id, name: `${c.name} (global)` });
  }
  return Array.from(map.values());
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
        renderStage3Story();
        renderStage4Pages();
      } catch (err) {
        toast(err.message, true);
      }
    });
    container.appendChild(btn);
  }
}

function renderStage4Pages() {
  const s = state.session;
  if (!s) return;

  $("#kdp-blank-verso").checked = Boolean(s.blank_verso);
  $("#kdp-title-page").checked = Boolean(s.include_title_page);
  $("#kdp-belongs-to").checked = Boolean(s.include_belongs_to_page);
  $("#kdp-color-test").checked = Boolean(s.include_color_test_page);

  const list = $("#page-list");
  list.innerHTML = "";
  const allChars = allSelectableCharacters();
  const donePages = s.pages.filter((p) => p.status === "done" && p.view_url);

  for (const page of s.pages) {
    const li = document.createElement("li");
    li.className = "page-card";
    const busy = state.busyPages.has(page.index) || page.status === "generating";
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
          <div style="display: flex; align-items: center; gap: 10px;">
            <span class="page-index-badge">${page.index}</span>
            <strong class="page-scene-title"></strong>
            <span class="pill ${statusClass}">${STATUS_LABEL[busy ? "generating" : page.status] || page.status}</span>
          </div>
          <div class="topbar-actions">
            <button class="btn btn-primary btn-sm page-gen-btn" type="button" ${busy ? "disabled" : ""}>
              ${page.status === "done" ? "Regenerar" : "Gerar página"}
            </button>
            ${
              page.status === "done"
                ? `<button class="btn btn-outline btn-sm page-style-btn" type="button">Usar como estilo</button>
                   <a class="btn btn-ghost btn-sm" href="${page.image_url}" download>PNG 300 DPI</a>`
                : ""
            }
          </div>
        </div>
        <textarea class="field page-prompt-input" rows="2"></textarea>
        <input class="field page-caption-input" type="text" placeholder="Texto / legenda desta página (opcional)" />
        <div class="chip-row page-chips"></div>
        ${page.error ? `<p class="error-msg" style="margin: 0;"></p>` : ""}
      </div>
    `;

    li.querySelector(".page-scene-title").textContent = page.title || `Página ${page.index}`;
    const promptEl = li.querySelector(".page-prompt-input");
    const capEl = li.querySelector(".page-caption-input");
    promptEl.value = page.prompt || "";
    capEl.value = page.caption_text || "";
    if (page.error) li.querySelector(".error-msg").textContent = page.error;

    const thumbImg = li.querySelector(".page-thumb img");
    if (thumbImg) {
      thumbImg.addEventListener("click", () => {
        const items = donePages.map((p) => ({
          url: p.view_url,
          caption: `Página ${p.index}${p.title ? ` — ${p.title}` : ""}`,
        }));
        const idx = donePages.findIndex((p) => p.index === page.index);
        openLightbox(items, Math.max(0, idx));
      });
    }

    const saveEdits = debounce(async () => {
      await api(`/api/sessions/${state.sid}/pages/${page.index}`, {
        method: "PATCH",
        body: JSON.stringify({ prompt: promptEl.value, caption_text: capEl.value }),
      }).catch(() => {});
      page.prompt = promptEl.value;
      page.caption_text = capEl.value;
    }, 450);
    promptEl.addEventListener("input", saveEdits);
    capEl.addEventListener("input", saveEdits);

    li.querySelector(".page-gen-btn").addEventListener("click", () =>
      generateOnePage(page.index, promptEl.value, capEl.value, page.character_ids)
    );

    const styleBtn = li.querySelector(".page-style-btn");
    if (styleBtn) {
      styleBtn.addEventListener("click", async () => {
        try {
          await api(`/api/sessions/${state.sid}/pages/${page.index}/use-as-style`, { method: "POST" });
          await reloadSession();
          toast(`Página ${page.index} definida como âncora de estilo deste livro!`);
        } catch (err) {
          toast(err.message, true);
        }
      });
    }

    renderCharacterChips(li.querySelector(".page-chips"), page, allChars);
    list.appendChild(li);
  }
}

function renderAll() {
  renderHeaderAndStepper();
  renderPresets();
  renderStage1Form();
  renderStage2Characters();
  renderStage3Story();
  renderStage4Pages();
  syncAllPopoverSelects();
}

/* ==================== ACTIONS ==================== */
async function reloadSession() {
  state.session = await api(`/api/sessions/${state.sid}`);
  renderAll();
}

async function saveStyleConfig() {
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
  };
  try {
    state.session = await api(`/api/sessions/${state.sid}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
    renderAll();
    toast("Padrões do livro salvos.");
  } catch (err) {
    toast(err.message, true);
  }
}

async function generateOnePage(index, prompt, captionText, characterIds) {
  state.busyPages.add(index);
  renderStage4Pages();
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
    toast(`Página ${index} gerada!`);
  } catch (err) {
    toast(err.message, true);
  } finally {
    state.busyPages.delete(index);
    renderStage4Pages();
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
  toast("Fila de geração concluída!");
}

async function composePdfAction() {
  for (const sel of ["#compose-btn", "#compose-btn-stage"]) {
    $(sel).disabled = true;
    $(sel).textContent = "Gerando PDF…";
  }
  try {
    state.session = await api(`/api/sessions/${state.sid}/compose`, { method: "POST" });
    renderHeaderAndStepper();
    toast("PDF KDP pronto para download!");
  } catch (err) {
    toast(err.message, true);
  } finally {
    for (const sel of ["#compose-btn", "#compose-btn-stage"]) {
      $(sel).textContent = "Gerar PDF KDP";
    }
    renderHeaderAndStepper();
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
  if (typeof window.gsap !== "undefined") {
    window.gsap.fromTo(box, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.2, clearProps: "opacity,visibility" });
  }
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
function renderAutopilot(st) {
  const bar = $("#autopilot-bar");
  const isVisible = st && st.status && st.status !== "idle";
  bar.classList.toggle("is-hidden", !isVisible);
  if (!isVisible) return;

  const badge = $("#autopilot-badge");
  badge.classList.remove("pill-ok", "pill-warn", "pill-err");
  if (st.status === "running") {
    badge.classList.add("pill-warn");
    badge.textContent = "Gerando com IA";
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
  $("#autopilot-cancel").classList.toggle("is-hidden", st.status !== "running");

  const errEl = $("#autopilot-error");
  errEl.classList.toggle("is-hidden", !st.error);
  if (st.error) errEl.textContent = st.error;

  const running = st.status === "running";
  $("#autopilot-full-btn").disabled = running;
  $("#autopilot-plan-btn").disabled = running;
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
          toast(st.label || "Autopilot concluído!");
        }
      }
    }
  } catch (_) {}
}

async function triggerAutopilot(stopAfter) {
  try {
    await saveStyleConfig();
    const st = await api(`/api/sessions/${state.sid}/autopilot`, {
      method: "POST",
      body: JSON.stringify({
        preset_id: state.session.preset_id,
        pages: state.session.pages.length || 12,
        premise_hint: $("#story-premise").value.trim(),
        language: $("#story-language")?.value || state.session.language || "en",
        stop_after: stopAfter,
      }),
    });
    renderAutopilot(st);
    if (!state.autopilotTimer) {
      state.autopilotTimer = setInterval(pollAutopilot, 1800);
    }
    toast("Autopilot iniciado! Acompanhe o progresso acima.");
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

  $("#provider").addEventListener("change", () => renderModelSuggestions({ forceCheapest: true }));
  $("#save-style-config").addEventListener("click", saveStyleConfig);
  $("#autopilot-full-btn").addEventListener("click", () => triggerAutopilot("pdf"));
  $("#autopilot-plan-btn").addEventListener("click", () => triggerAutopilot("blueprint"));
  $("#autopilot-cancel").addEventListener("click", async () => {
    const st = await api(`/api/sessions/${state.sid}/autopilot/cancel`, { method: "POST" });
    renderAutopilot(st);
  });

  // Save custom preset modal
  $("#save-custom-preset-btn").addEventListener("click", () => {
    $("#custom-preset-name").value = "";
    $("#custom-preset-tagline").value = "";
    $("#preset-modal").classList.remove("is-hidden");
  });
  $("#preset-modal-close").addEventListener("click", () => $("#preset-modal").classList.add("is-hidden"));
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
      toast(`Preset "${created.name}" salvo!`);
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
    btn.textContent = "Gerando âncora de estilo…";
    try {
      await saveStyleConfig();
      await api(`/api/sessions/${state.sid}/style/generate`, {
        method: "POST",
        body: JSON.stringify({ sample_subject: $("#style-sample-subject").value.trim() }),
      });
      await reloadSession();
      toast("Âncora visual de estilo gerada!");
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove("is-hidden");
    } finally {
      btn.disabled = false;
      btn.textContent = "Gerar âncora de estilo com IA";
    }
  });

  $("#style-upload").addEventListener("click", async () => {
    const file = $("#style-file").files?.[0];
    if (!file) return toast("Selecione uma imagem de estilo.", true);
    const form = new FormData();
    form.append("image", file);
    try {
      await api(`/api/sessions/${state.sid}/style`, { method: "POST", body: form });
      $("#style-file").value = "";
      await reloadSession();
      toast("Âncora de estilo enviada!");
    } catch (err) {
      toast(err.message, true);
    }
  });

  $("#style-remove").addEventListener("click", async () => {
    await api(`/api/sessions/${state.sid}/style`, { method: "DELETE" });
    await reloadSession();
    toast("Âncora de estilo removida.");
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
      renderStage3Story();
    });
  });

  $("#story-language").addEventListener("change", async () => {
    state.session = await api(`/api/sessions/${state.sid}`, {
      method: "PATCH",
      body: JSON.stringify({ language: $("#story-language").value }),
    });
  });

  $("#story-save-btn").addEventListener("click", async () => {
    try {
      await api(`/api/sessions/${state.sid}/story`, {
        method: "PATCH",
        body: JSON.stringify({
          premise: $("#story-premise").value,
          synopsis: $("#story-synopsis").value,
        }),
      });
      toast("Sinopse salva.");
    } catch (err) {
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
      toast(`Roteiro criado com ${res.pages_updated} página(s) prontas!`);
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove("is-hidden");
    } finally {
      btn.disabled = false;
      btn.textContent = "Gerar roteiro e dividir páginas";
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
    renderStage3Story();
  };
  for (const sel of ["#caption-enabled", "#caption-style", "#caption-position", "#caption-font", "#caption-weight", "#caption-size"]) {
    $(sel).addEventListener("change", saveCaptionSettings);
  }

  // Stage 4: KDP options & PDF
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
  };
  for (const sel of ["#kdp-blank-verso", "#kdp-title-page", "#kdp-belongs-to", "#kdp-color-test"]) {
    $(sel).addEventListener("change", saveKdpOptions);
  }

  $("#generate-pending").addEventListener("click", generateAllPending);
  $("#compose-btn").addEventListener("click", composePdfAction);
  $("#compose-btn-stage").addEventListener("click", composePdfAction);

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
    if ($("#lightbox").classList.contains("is-hidden")) return;
    if (ev.key === "Escape") closeLightbox();
    if (ev.key === "ArrowLeft") $("#lightbox-prev").click();
    if (ev.key === "ArrowRight") $("#lightbox-next").click();
  });

  // Auth modal
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
