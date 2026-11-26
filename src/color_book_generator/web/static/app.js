const $ = (sel) => document.querySelector(sel);

function pathSessionId() {
  const match = window.location.pathname.match(/^\/books\/([a-f0-9]{8,32})$/);
  return match ? match[1] : null;
}

const STATUS_LABEL = {
  pending: "pendente",
  generating: "gerando",
  done: "pronto",
  error: "erro",
};

const state = {
  defaults: null,
  session: null,
  busy: new Set(),
  codex: null,
  codexModels: [],
  settings: null,
  characters: [],
  sessions: [],
  styleUrl: null,
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

function debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

function preferredProvider() {
  const selected = $("#provider").value;
  if (selected) return selected;
  const useCodex = state.codex && state.codex.authenticated && !state.defaults.openai_configured;
  return useCodex ? "codex" : state.defaults.provider;
}

function clampPages(value) {
  const n = Math.round(Number(value));
  if (!Number.isFinite(n)) return 12;
  return Math.max(1, Math.min(200, n));
}

function effectiveSettings() {
  const d = state.defaults || {};
  const s = state.settings || {};
  return {
    title: s.title || d.title || "",
    pages: clampPages(s.pages || d.pages || 12),
    context_prompt: s.context_prompt || d.context_prompt || "",
    provider: s.provider || d.provider || "",
    model: s.model || d.model || "",
    caption_enabled: Boolean(s.caption_enabled ?? d.caption_enabled ?? false),
    caption_style: s.caption_style || d.caption_style || "balloon",
    caption_position: s.caption_position || d.caption_position || "bottom",
    caption_font_family: s.caption_font_family || d.caption_font_family || "sans",
    caption_font_bold: Boolean(s.caption_font_bold ?? d.caption_font_bold ?? false),
    caption_font_size: String(
      s.caption_font_size ?? d.caption_font_size ?? 0.032
    ),
  };
}

async function loadCharacters() {
  try {
    state.characters = await api("/api/characters");
  } catch (_) {
    state.characters = [];
  }
}

async function loadSettings() {
  try {
    state.settings = await api("/api/settings");
  } catch (_) {
    state.settings = null;
  }
}

async function createSession(pages, title) {
  const provider = preferredProvider();
  const model =
    provider === "codex"
      ? state.codexModels[0] || state.defaults.codex_model
      : $("#model").value || state.defaults.model;
  const body = {
    pages,
    title: title || undefined,
    context_prompt: $("#context").value || state.defaults.context_prompt,
    provider,
    model,
    caption_enabled: $("#caption-enabled").checked,
    caption_style: $("#caption-style").value,
    caption_position: $("#caption-position").value,
    caption_font_family: $("#caption-font").value,
    caption_font_bold: $("#caption-weight").value === "bold",
    caption_font_size: Number($("#caption-size").value) || 0.032,
  };
  const session = await api("/api/sessions", {
    method: "POST",
    body: JSON.stringify(body),
  });
  window.location.href = `/books/${session.id}`;
  return session;
}

async function refreshSession() {
  state.session = await api(`/api/sessions/${state.session.id}`);
}

async function saveSettings(skipSession = false) {
  const rawFontSize = Number($("#caption-size").value);
  const fontSize =
    Number.isFinite(rawFontSize) && rawFontSize >= 0.01 && rawFontSize <= 0.08
      ? rawFontSize
      : 0.032;
  const payload = {
    title: $("#session-title").value.trim() || state.defaults.title,
    pages: clampPages($("#pages-count").value),
    context_prompt: $("#context").value,
    provider: $("#provider").value || state.defaults.provider,
    model: $("#model").value || state.defaults.model,
    caption_enabled: $("#caption-enabled").checked,
    caption_style: $("#caption-style").value || "balloon",
    caption_position: $("#caption-position").value || "bottom",
    caption_font_family: $("#caption-font").value || "sans",
    caption_font_bold: $("#caption-weight").value === "bold",
    caption_font_size: fontSize,
  };
  state.settings = await api("/api/settings", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
  if (!skipSession && state.session) {
    const patch = {
      context_prompt: payload.context_prompt,
      provider: payload.provider,
      pages: payload.pages,
      caption_enabled: payload.caption_enabled,
      caption_style: payload.caption_style,
      caption_position: payload.caption_position,
      caption_font_family: payload.caption_font_family,
      caption_font_bold: payload.caption_font_bold,
      caption_font_size: payload.caption_font_size,
    };
    if (payload.title) patch.title = payload.title;
    if (payload.model) patch.model = payload.model;
    state.session = await api(`/api/sessions/${state.session.id}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
    payload.pages = state.session.pages.length;
    $("#pages-count").value = payload.pages;
  } else {
    $("#pages-count").value = payload.pages;
  }
  const note = $("#saved-note");
  note.textContent = "Padrões salvos.";
  setTimeout(() => {
    if (note.textContent === "Padrões salvos.") note.textContent = "";
  }, 2200);
}

function renderCharacters() {
  const list = $("#character-list");
  list.innerHTML = "";
  $("#char-empty").classList.toggle("is-hidden", state.characters.length > 0);
  for (const character of state.characters) {
    const item = document.createElement("li");
    item.className = "character-item";

    const img = document.createElement("img");
    img.src = character.url;
    img.alt = character.name;
    item.appendChild(img);

    const name = document.createElement("span");
    name.className = "name";
    name.textContent = character.name;
    item.appendChild(name);

    const remove = document.createElement("button");
    remove.className = "remove";
    remove.type = "button";
    remove.dataset.characterRemove = character.id;
    remove.setAttribute("aria-label", `Remover ${character.name}`);
    remove.textContent = "×";
    item.appendChild(remove);

    list.appendChild(item);
  }
}

function setSelectValue(select, value) {
  const text = String(value);
  const exists = Array.from(select.options).some((opt) => opt.value === text);
  if (!exists) {
    const option = document.createElement("option");
    option.value = text;
    option.textContent = text;
    select.appendChild(option);
  }
  select.value = text;
}

function updateCaptionFields() {
  const enabled = $("#caption-enabled").checked;
  $("#caption-options").classList.toggle("is-disabled", !enabled);
  for (const select of $("#caption-options").querySelectorAll("select")) {
    select.disabled = !enabled;
  }
  $("#page-list").classList.toggle("captions-off", !enabled);
}

async function loadSessions() {
  try {
    state.sessions = await api("/api/sessions");
  } catch (_) {
    state.sessions = [];
  }
  renderSessionOptions();
}

function renderSessionOptions() {
  const select = $("#session-select");
  select.innerHTML = "";
  const current = state.session ? state.session.id : null;
  for (const summary of state.sessions) {
    const option = document.createElement("option");
    option.value = summary.id;
    option.textContent = `${summary.title} · ${summary.done}/${summary.pages}`;
    select.appendChild(option);
  }
  if (current && !state.sessions.some((item) => item.id === current)) {
    const option = document.createElement("option");
    option.value = current;
    option.textContent = state.session ? state.session.title : current;
    select.appendChild(option);
  }
  if (current) select.value = current;
}

async function loadStyle() {
  try {
    const data = await api("/api/style");
    state.styleUrl = data.url || null;
  } catch (_) {
    state.styleUrl = null;
  }
  renderStyle();
}

function renderStyle() {
  const preview = $("#style-preview");
  preview.innerHTML = "";
  if (state.styleUrl) {
    const img = document.createElement("img");
    img.src = state.styleUrl;
    img.alt = "Referência de estilo";
    preview.appendChild(img);
    $("#style-remove").classList.remove("is-hidden");
  } else {
    const empty = document.createElement("span");
    empty.textContent = "Nenhum estilo definido.";
    preview.appendChild(empty);
    $("#style-remove").classList.add("is-hidden");
  }
}

async function uploadStyle() {
  const fileInput = $("#style-file");
  const errorBox = $("#style-error");
  errorBox.classList.add("is-hidden");
  const file = fileInput.files && fileInput.files[0];
  if (!file) {
    errorBox.textContent = "Selecione uma imagem.";
    errorBox.classList.remove("is-hidden");
    return;
  }
  const form = new FormData();
  form.append("image", file);
  const button = $("#style-upload");
  button.disabled = true;
  button.textContent = "Enviando…";
  try {
    await api("/api/style", { method: "POST", body: form });
    fileInput.value = "";
    await loadStyle();
    toast("Estilo de referência definido.");
  } catch (err) {
    errorBox.textContent = err.message || "Falha ao enviar o estilo.";
    errorBox.classList.remove("is-hidden");
  } finally {
    button.disabled = false;
    button.textContent = "Definir estilo";
  }
}

async function removeStyle() {
  try {
    await api("/api/style", { method: "DELETE" });
    state.styleUrl = null;
    renderStyle();
    toast("Estilo removido.");
  } catch (err) {
    toast(err.message || "Falha ao remover o estilo", true);
  }
}

async function usePageAsStyle(index) {
  if (!state.session) return;
  try {
    const data = await api(
      `/api/sessions/${state.session.id}/pages/${index}/use-as-style`,
      { method: "POST" }
    );
    state.styleUrl = data.url || null;
    renderStyle();
    toast("Página definida como referência de estilo.");
  } catch (err) {
    toast(err.message || "Falha ao definir o estilo", true);
  }
}

function fillProviderOptions() {
  const select = $("#provider");
  if (select.options.length || !state.defaults) return;
  for (const name of state.defaults.providers) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    select.appendChild(opt);
  }
}

function pageCard(page) {
  const card = document.createElement("li");
  card.className = "page-card";
  card.dataset.index = String(page.index);
  card.dataset.status = page.status;

  const thumb = document.createElement("div");
  thumb.className = "thumb";
  if (page.status === "generating") {
    thumb.classList.add("is-loading");
    thumb.innerHTML = `<div class="thumb-placeholder"><div class="spinner"></div><span>gerando</span></div>`;
  } else if (page.preview_url) {
    const img = document.createElement("img");
    img.src = page.preview_url;
    img.alt = `Página ${page.index}`;
    img.loading = "lazy";
    thumb.appendChild(img);
    thumb.classList.add("is-clickable");
    thumb.dataset.action = "lightbox";
    thumb.tabIndex = 0;
    thumb.setAttribute("role", "button");
    thumb.setAttribute("aria-label", `Ampliar página ${page.index}`);
  } else {
    thumb.innerHTML = `
      <div class="thumb-placeholder">
        <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.3">
          <rect x="3.5" y="3.5" width="17" height="17" rx="2.5" />
          <path d="M3.5 15.5l4.8-4.8 4 4 2.4-2.4 5.8 5.8" />
          <circle cx="9" cy="8.6" r="1.6" />
        </svg>
        <span>sem imagem</span>
      </div>`;
  }
  card.appendChild(thumb);

  const body = document.createElement("div");
  body.className = "card-body";

  const head = document.createElement("div");
  head.className = "card-head";
  head.innerHTML = `
    <span class="page-num">${String(page.index).padStart(2, "0")}</span>
    <span class="status" data-kind="${page.status}">${STATUS_LABEL[page.status] || page.status}</span>
    <span class="spacer"></span>
    <span class="dims">${page.width_px ? `${page.width_px}×${page.height_px}px` : ""}</span>`;
  body.appendChild(head);

  const textarea = document.createElement("textarea");
  textarea.className = "page-textarea";
  textarea.rows = 2;
  textarea.spellcheck = false;
  textarea.value = page.prompt;
  textarea.setAttribute("aria-label", `Prompt da página ${page.index}`);
  body.appendChild(textarea);

  const captionInput = document.createElement("input");
  captionInput.type = "text";
  captionInput.className = "page-caption";
  captionInput.placeholder = "Texto da legenda / balão (opcional)";
  captionInput.value = page.caption_text || "";
  captionInput.setAttribute("aria-label", `Legenda da página ${page.index}`);
  body.appendChild(captionInput);

  if (state.characters.length) {
    const chips = document.createElement("div");
    chips.className = "char-chips";
    const selected = new Set(page.character_ids || []);
    for (const character of state.characters) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "char-chip";
      chip.dataset.character = character.id;
      chip.setAttribute("aria-pressed", selected.has(character.id) ? "true" : "false");
      if (selected.has(character.id)) chip.classList.add("active");
      const thumb = document.createElement("img");
      thumb.src = character.url;
      thumb.alt = "";
      chip.appendChild(thumb);
      const label = document.createElement("span");
      label.textContent = character.name;
      chip.appendChild(label);
      chips.appendChild(chip);
    }
    body.appendChild(chips);
  }

  if (page.error) {
    const err = document.createElement("p");
    err.className = "error-msg";
    err.textContent = page.error;
    body.appendChild(err);
  }

  const actions = document.createElement("div");
  actions.className = "card-actions";

  const generateBtn = document.createElement("button");
  generateBtn.className = "btn btn-primary btn-small";
  generateBtn.dataset.action = "generate";
  generateBtn.disabled = page.status === "generating" || state.busy.has(page.index);
  if (page.status === "generating") {
    generateBtn.innerHTML = `<span class="spinner"></span> Gerando`;
  } else {
    generateBtn.textContent = page.status === "done" ? "Regenerar" : "Gerar imagem";
  }
  actions.appendChild(generateBtn);

  if (page.image_url) {
    const link = document.createElement("a");
    link.className = "btn btn-outline btn-small";
    link.href = page.image_url;
    link.textContent = "Baixar PNG";
    actions.appendChild(link);

    const styleBtn = document.createElement("button");
    styleBtn.className = "btn btn-ghost btn-small";
    styleBtn.dataset.action = "use-style";
    styleBtn.type = "button";
    styleBtn.textContent = "Usar como estilo";
    actions.appendChild(styleBtn);
  }

  const hint = document.createElement("span");
  hint.className = "hint-inline";
  hint.textContent = page.status === "done" ? "300 dpi" : "";
  actions.appendChild(hint);

  body.appendChild(actions);
  card.appendChild(body);
  return card;
}

function render() {
  const session = state.session;
  const defaults = state.defaults;
  const eff = effectiveSettings();

  const trim = defaults.trim_in;
  $("#meta").innerHTML = `
    <span>${escapeHtml(session ? session.title : eff.title)}</span>
    <span>${trim[0]}×${trim[1]} in</span>
    <span>${defaults.dpi} dpi</span>`;

  if (document.activeElement !== $("#context")) {
    $("#context").value = session ? session.context_prompt : eff.context_prompt;
  }
  if (document.activeElement !== $("#provider")) {
    fillProviderOptions();
    $("#provider").value = session ? session.provider : eff.provider;
  }
  if (document.activeElement !== $("#model")) {
    const codexModels = state.codexModels;
    const list = $("#model-options");
    list.innerHTML = "";
    for (const slug of codexModels) {
      const opt = document.createElement("option");
      opt.value = slug;
      list.appendChild(opt);
    }

    let modelValue = session ? session.model : eff.model;
    if (session && session.provider === "codex") {
      if (codexModels.length && !codexModels.includes(modelValue)) {
        modelValue = codexModels[0];
      } else if (OPENAI_IMAGE_MODELS.has(modelValue)) {
        modelValue = defaults.codex_model;
      }
    }
    $("#model").value = modelValue;
  }
  if (document.activeElement !== $("#session-title")) {
    $("#session-title").value = session ? session.title : eff.title;
  }
  if (document.activeElement !== $("#pages-count")) {
    $("#pages-count").value = session ? session.pages.length : eff.pages;
  }
  const capEnabled = session ? session.caption_enabled : eff.caption_enabled;
  const capStyle = session ? session.caption_style : eff.caption_style;
  const capPosition = session ? session.caption_position : eff.caption_position;
  const capFont = session ? session.caption_font_family : eff.caption_font_family;
  const capBold = session ? session.caption_font_bold : eff.caption_font_bold;
  const capSize = session ? session.caption_font_size : eff.caption_font_size;
  $("#caption-enabled").checked = Boolean(capEnabled);
  $("#caption-style").value = capStyle;
  $("#caption-position").value = capPosition;
  setSelectValue($("#caption-font"), capFont || "sans");
  setSelectValue($("#caption-weight"), capBold ? "bold" : "normal");
  setSelectValue($("#caption-size"), capSize || 0.032);
  updateCaptionFields();

  const list = $("#page-list");
  list.innerHTML = "";
  const pages = session ? session.pages : [];
  $("#empty-state").classList.toggle("is-hidden", pages.length > 0);

  for (const page of pages) list.appendChild(pageCard(page));

  const done = pages.filter((p) => p.status === "done").length;
  $("#progress-pill").textContent = `${done} / ${pages.length}`;
  $("#pages-subtitle").textContent = pages.length
    ? `${pages.length} páginas · ${pages.filter((p) => p.status === "pending").length} pendentes`
    : "Nenhuma página";

  const composeBtn = $("#compose-btn");
  composeBtn.disabled = done === 0 || state.busy.size > 0;
  if (session && session.pdf_url) {
    composeBtn.textContent = "Recompor PDF";
    const link = $("#pdf-link");
    link.href = session.pdf_url;
    link.classList.remove("is-hidden");
  } else {
    composeBtn.textContent = "Gerar PDF";
    $("#pdf-link").classList.add("is-hidden");
  }

  const pending = pages.some((p) => p.status !== "done");
  $("#generate-pending").disabled = !pending || state.busy.size > 0;
}

const OPENAI_IMAGE_MODELS = new Set(["gpt-image-1", "gpt-image-2", "dall-e-3"]);

function applyProviderDefaults(providerName) {
  const d = state.defaults;
  if (!d) return;
  const modelInput = $("#model");
  const codexModels = state.codexModels;
  if (providerName === "codex") {
    if (
      !modelInput.value ||
      OPENAI_IMAGE_MODELS.has(modelInput.value) ||
      (codexModels.length && !codexModels.includes(modelInput.value))
    ) {
      modelInput.value = codexModels[0] || d.codex_model;
    }
  } else if (providerName === "openai") {
    if (!modelInput.value || modelInput.value === d.codex_model) {
      modelInput.value = "gpt-image-1";
    }
  }
}

async function useCodexProvider() {
  if (!state.session || state.session.provider === "codex") return;
  const codexModels = state.codexModels;
  const codexModel = codexModels[0] || state.defaults.codex_model;
  state.session = await api(`/api/sessions/${state.session.id}`, {
    method: "PATCH",
    body: JSON.stringify({
      provider: "codex",
      model: codexModel,
    }),
  });
  $("#model").value = codexModel;
  render();
  toast("Provedor alterado para codex (seu plano ChatGPT).");
}

function renderCodex() {
  const info = state.codex;
  const pill = $("#codex-status");
  const codeBox = $("#codex-code");
  const errorBox = $("#codex-error");
  const connectBtn = $("#codex-connect");
  const logoutBtn = $("#codex-logout");

  let kind = "pending";
  let label = "desconectado";
  if (info && info.authenticated) {
    kind = "done";
    label = "conectado";
  } else if (info && info.pending) {
    kind = "generating";
    label = "aguardando";
  }
  pill.dataset.kind = kind;
  pill.textContent = label;

  const showCode = Boolean(info && info.pending && info.user_code);
  codeBox.classList.toggle("is-hidden", !showCode);
  if (showCode) {
    $("#codex-user-code").textContent = info.user_code;
    $("#codex-link").href = info.verification_url || "https://auth.openai.com/codex/device";
  }

  const error = info && info.error;
  errorBox.classList.toggle("is-hidden", !error);
  errorBox.textContent = error || "";

  const connected = Boolean(info && info.authenticated);
  connectBtn.classList.toggle("is-hidden", connected);
  connectBtn.disabled = Boolean(info && info.pending);
  connectBtn.textContent = info && info.pending ? "Aguardando autorização…" : "Conectar ChatGPT";
  logoutBtn.classList.toggle("is-hidden", !connected || Boolean(info && info.pending));
}

async function loadCodexModels() {
  if (!state.codex || !state.codex.authenticated) {
    state.codexModels = [];
    return;
  }
  try {
    const data = await api("/api/auth/codex/models");
    state.codexModels = data.models || [];
  } catch (_) {
    state.codexModels = [];
  }
}

async function refreshCodex() {
  try {
    state.codex = await api("/api/auth/codex");
  } catch (_) {}
  await loadCodexModels();
  renderCodex();
}

function pollCodex() {
  clearInterval(state.codexTimer);
  state.codexTimer = setInterval(async () => {
    try {
      state.codex = await api("/api/auth/codex");
    } catch (_) {
      return;
    }
    renderCodex();
    if (!state.codex.pending) {
      clearInterval(state.codexTimer);
      if (state.codex.authenticated) {
        toast("ChatGPT conectado.");
        try {
          state.defaults = await api("/api/defaults");
          await loadCodexModels();
          if (!state.defaults.openai_configured) {
            await useCodexProvider();
          }
        } catch (_) {}
        render();
      } else if (state.codex.error) {
        toast(state.codex.error, true);
      }
    }
  }, 2500);
}

async function connectCodex() {
  try {
    state.codex = await api("/api/auth/codex/start", { method: "POST" });
    renderCodex();
    if (state.codex.pending) pollCodex();
  } catch (err) {
    toast(err.message || "Falha ao iniciar login", true);
  }
}

async function logoutCodex() {
  try {
    state.codex = await api("/api/auth/codex/logout", { method: "POST" });
    state.codexModels = [];
    renderCodex();
    toast("ChatGPT desconectado.");
  } catch (err) {
    toast(err.message || "Falha ao desconectar", true);
  }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[c]);
}

function setBusy(index, busy) {
  if (busy) state.busy.add(index);
  else state.busy.delete(index);
}

async function generatePage(index) {
  const session = state.session;
  if (!session || state.busy.has(index)) return;
  const card = document.querySelector(`.page-card[data-index="${index}"]`);
  const textarea = card ? card.querySelector(".page-textarea") : null;
  const captionInput = card ? card.querySelector(".page-caption") : null;
  const prompt = textarea ? textarea.value : undefined;
  const captionText = captionInput ? captionInput.value : undefined;
  const characterIds = card
    ? Array.from(card.querySelectorAll(".char-chip.active")).map(
        (chip) => chip.dataset.character
      )
    : [];

  setBusy(index, true);
  render();

  try {
    const page = await api(
      `/api/sessions/${session.id}/pages/${index}/generate`,
      {
        method: "POST",
        body: JSON.stringify({
          prompt,
          caption_text: captionText,
          character_ids: characterIds,
          context_prompt: $("#context").value,
          provider: $("#provider").value || undefined,
          model: $("#model").value || undefined,
        }),
      }
    );
    const idx = session.pages.findIndex((p) => p.index === index);
    if (idx >= 0) session.pages[idx] = page;
    session.pdf_ready = false;
    if (page.status === "error") toast(page.error || "Falha ao gerar", true);
  } catch (err) {
    toast(err.message || "Falha ao gerar", true);
    try {
      await refreshSession();
    } catch (_) {}
  } finally {
    setBusy(index, false);
    render();
    loadSessions();
  }
}

async function addCharacter() {
  const nameInput = $("#character-name");
  const fileInput = $("#character-file");
  const errorBox = $("#character-error");
  errorBox.classList.add("is-hidden");
  const name = nameInput.value.trim();
  const file = fileInput.files && fileInput.files[0];
  if (!name) {
    errorBox.textContent = "Informe o nome do personagem.";
    errorBox.classList.remove("is-hidden");
    return;
  }
  if (!file) {
    errorBox.textContent = "Selecione uma imagem.";
    errorBox.classList.remove("is-hidden");
    return;
  }
  const form = new FormData();
  form.append("name", name);
  form.append("image", file);
  if (state.session) form.append("session_id", state.session.id);
  const button = $("#character-add");
  button.disabled = true;
  button.textContent = "Enviando…";
  try {
    await api("/api/characters", { method: "POST", body: form });
    nameInput.value = "";
    fileInput.value = "";
    await loadCharacters();
    if (state.session) await refreshSession();
    renderCharacters();
    render();
    toast("Personagem adicionado e aplicado às páginas desta sessão.");
  } catch (err) {
    errorBox.textContent = err.message || "Falha ao enviar personagem.";
    errorBox.classList.remove("is-hidden");
  } finally {
    button.disabled = false;
    button.textContent = "Adicionar personagem";
  }
}

async function removeCharacter(id) {
  if (!confirm("Remover este personagem?")) return;
  try {
    await api(`/api/characters/${id}`, { method: "DELETE" });
    await loadCharacters();
    renderCharacters();
    if (state.session) await refreshSession();
    render();
    toast("Personagem removido.");
  } catch (err) {
    toast(err.message || "Falha ao remover personagem", true);
  }
}

function showBusy(title, sub = "Não feche esta página.") {
  $("#busy-title").textContent = title;
  $("#busy-sub").textContent = sub;
  $("#busy-fill").style.width = "0%";
  $("#busy-bar").classList.remove("indeterminate");
  $("#busy-overlay").classList.remove("is-hidden");
  document.body.classList.add("lightbox-open");
}

function busyProgress(percent, title) {
  if (title) $("#busy-title").textContent = title;
  const clamped = Math.max(0, Math.min(100, percent));
  $("#busy-fill").style.width = `${clamped}%`;
  $("#busy-sub").textContent = `${Math.round(clamped)}%`;
}

function busyProcessing(title) {
  $("#busy-title").textContent = title;
  $("#busy-sub").textContent = "Processando… não feche esta página.";
  $("#busy-bar").classList.add("indeterminate");
}

function hideBusy() {
  $("#busy-overlay").classList.add("is-hidden");
  document.body.classList.remove("lightbox-open");
}

async function importBackup() {
  const fileInput = $("#import-file");
  const errorBox = $("#backup-error");
  errorBox.classList.add("is-hidden");
  const file = fileInput.files && fileInput.files[0];
  if (!file) {
    errorBox.textContent = "Selecione o arquivo .zip do backup.";
    errorBox.classList.remove("is-hidden");
    return;
  }
  const form = new FormData();
  form.append("archive", file);
  const button = $("#import-btn");
  button.disabled = true;

  showBusy("Enviando backup…", "0%");
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/import");
  xhr.responseType = "json";
  xhr.timeout = 20 * 60 * 1000;
  xhr.upload.onprogress = (event) => {
    if (event.lengthComputable) {
      busyProgress((event.loaded / event.total) * 100, "Enviando backup…");
    }
  };
  xhr.upload.onload = () => {
    busyProcessing("Importando sessões, imagens e personagens…");
  };
  xhr.onload = () => {
    if (xhr.status >= 200 && xhr.status < 300) {
      const result = xhr.response || {};
      busyProgress(100, "Importação concluída");
      const skipped = result.skipped
        ? ` · ${result.skipped} de teste ignoradas`
        : "";
      $("#busy-sub").textContent =
        `${result.sessions || 0} sessões · ${result.pages || 0} páginas · ` +
        `${result.characters || 0} personagens${skipped}`;
      const target = result.latest_session;
      setTimeout(() => {
        if (target) window.location.href = `/books/${target}`;
        else window.location.reload();
      }, 900);
    } else {
      const body = xhr.response || {};
      hideBusy();
      button.disabled = false;
      errorBox.textContent = body.detail || `Falha ao importar (HTTP ${xhr.status}).`;
      errorBox.classList.remove("is-hidden");
    }
  };
  xhr.onerror = () => {
    hideBusy();
    button.disabled = false;
    errorBox.textContent = "Falha de rede durante a importação.";
    errorBox.classList.remove("is-hidden");
  };
  xhr.ontimeout = () => {
    hideBusy();
    button.disabled = false;
    errorBox.textContent = "A importação demorou demais e foi interrompida.";
    errorBox.classList.remove("is-hidden");
  };
  xhr.send(form);
}

async function savePageChanges(index) {
  const session = state.session;
  if (!session) return;
  const card = document.querySelector(`.page-card[data-index="${index}"]`);
  if (!card) return;
  const textarea = card.querySelector(".page-textarea");
  const captionInput = card.querySelector(".page-caption");
  try {
    const page = await api(`/api/sessions/${session.id}/pages/${index}`, {
      method: "PATCH",
      body: JSON.stringify({
        prompt: textarea ? textarea.value : undefined,
        caption_text: captionInput ? captionInput.value : undefined,
        character_ids: Array.from(card.querySelectorAll(".char-chip.active")).map(
          (chip) => chip.dataset.character
        ),
      }),
    });
    const idx = session.pages.findIndex((p) => p.index === index);
    if (idx >= 0) session.pages[idx] = page;
  } catch (err) {
    toast(err.message || "Falha ao salvar a página", true);
  }
}

let lightboxIndex = null;

function generatedPages() {
  if (!state.session) return [];
  return state.session.pages.filter((p) => p.status === "done" && p.view_url);
}

function showLightbox() {
  const pages = generatedPages();
  if (!pages.length || lightboxIndex === null) return;
  lightboxIndex = ((lightboxIndex % pages.length) + pages.length) % pages.length;
  const page = pages[lightboxIndex];
  $("#lightbox-img").src = page.view_url;
  $("#lightbox-img").alt = `Página ${page.index}`;
  $("#lightbox-caption").textContent = `Página ${page.index} · ${pages.length} geradas`;
  const multi = pages.length > 1;
  $("#lightbox-prev").disabled = !multi;
  $("#lightbox-next").disabled = !multi;
}

function openLightbox(index) {
  const pages = generatedPages();
  const position = pages.findIndex((p) => p.index === index);
  if (position === -1) return;
  lightboxIndex = position;
  showLightbox();
  $("#lightbox").classList.remove("is-hidden");
  document.body.classList.add("lightbox-open");
  $("#lightbox-close").focus();
}

function closeLightbox() {
  $("#lightbox").classList.add("is-hidden");
  document.body.classList.remove("lightbox-open");
  $("#lightbox-img").removeAttribute("src");
  lightboxIndex = null;
}

function navigateLightbox(delta) {
  if (lightboxIndex === null) return;
  lightboxIndex += delta;
  showLightbox();
}

async function generatePending() {
  const session = state.session;
  if (!session) return;
  const pending = session.pages.filter((p) => p.status !== "done" && !state.busy.has(p.index));
  if (!pending.length) {
    toast("Nenhuma página pendente.");
    return;
  }
  for (const page of pending) {
    await generatePage(page.index);
    const latest = state.session.pages.find((p) => p.index === page.index);
    if (latest && latest.status === "error") break;
  }
}

async function composePdf() {
  const session = state.session;
  if (!session) return;
  const btn = $("#compose-btn");
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Compondo…";
  try {
    state.session = await api(`/api/sessions/${session.id}/compose`, { method: "POST" });
    toast("PDF pronto.");
    render();
  } catch (err) {
    toast(err.message || "Falha ao compor PDF", true);
  } finally {
    btn.textContent = original;
    btn.disabled = false;
  }
}

function bind() {
  const persist = () =>
    saveSettings().catch(async (err) => {
      toast(err.message, true);
      if (state.session) {
        try {
          await refreshSession();
        } catch (_) {}
        render();
      }
    });
  const debouncedSave = debounce(persist, 350);
  const saveNow = () => persist();

  $("#context").addEventListener("input", debouncedSave);
  $("#provider").addEventListener("change", saveNow);
  $("#model").addEventListener("change", saveNow);
  $("#session-title").addEventListener("input", debouncedSave);
  $("#session-title").addEventListener("change", saveNow);
  $("#pages-count").addEventListener("input", debouncedSave);
  $("#pages-count").addEventListener("change", saveNow);
  $("#pages-count").addEventListener("blur", saveNow);
  $("#save-context").addEventListener("click", () => {
    saveSettings().catch((err) => toast(err.message, true));
  });

  $("#caption-enabled").addEventListener("change", () => {
    updateCaptionFields();
    saveNow();
  });
  $("#caption-style").addEventListener("change", saveNow);
  $("#caption-position").addEventListener("change", saveNow);
  $("#caption-font").addEventListener("change", saveNow);
  $("#caption-weight").addEventListener("change", saveNow);
  $("#caption-size").addEventListener("change", saveNow);

  $("#page-list").addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const thumb = event.target.closest('.thumb[data-action="lightbox"]');
    if (!thumb) return;
    event.preventDefault();
    const card = thumb.closest(".page-card");
    openLightbox(Number(card.dataset.index));
  });

  $("#page-list").addEventListener("change", (event) => {
    const target = event.target;
    if (!target.matches(".page-textarea, .page-caption")) return;
    const card = target.closest(".page-card");
    if (card) savePageChanges(Number(card.dataset.index));
  });

  $("#page-list").addEventListener("click", (event) => {
    const btn = event.target.closest('[data-action="generate"]');
    if (!btn) return;
    const card = btn.closest(".page-card");
    generatePage(Number(card.dataset.index));
  });

  $("#lightbox-close").addEventListener("click", closeLightbox);
  $("#lightbox-prev").addEventListener("click", () => navigateLightbox(-1));
  $("#lightbox-next").addEventListener("click", () => navigateLightbox(1));
  $("#lightbox").addEventListener("click", (event) => {
    if (event.target === $("#lightbox")) closeLightbox();
  });
  document.addEventListener("keydown", (event) => {
    if ($("#lightbox").classList.contains("is-hidden")) return;
    if (event.key === "Escape") closeLightbox();
    else if (event.key === "ArrowLeft") navigateLightbox(-1);
    else if (event.key === "ArrowRight") navigateLightbox(1);
  });

  $("#session-select").addEventListener("change", (event) => {
    const value = event.target.value;
    if (!value) return;
    window.location.href = `/books/${value}`;
  });

  $("#import-btn").addEventListener("click", importBackup);

  $("#style-upload").addEventListener("click", uploadStyle);
  $("#style-remove").addEventListener("click", removeStyle);

  $("#character-add").addEventListener("click", addCharacter);
  $("#character-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-character-remove]");
    if (button) removeCharacter(button.dataset.characterRemove);
  });

  $("#page-list").addEventListener("click", (event) => {
    const thumb = event.target.closest('.thumb[data-action="lightbox"]');
    if (thumb) {
      const card = thumb.closest(".page-card");
      openLightbox(Number(card.dataset.index));
      return;
    }
    const styleBtn = event.target.closest('[data-action="use-style"]');
    if (styleBtn) {
      const card = styleBtn.closest(".page-card");
      usePageAsStyle(Number(card.dataset.index));
      return;
    }
    const chip = event.target.closest(".char-chip");
    if (!chip) return;
    chip.classList.toggle("active");
    const active = chip.classList.contains("active");
    chip.setAttribute("aria-pressed", active ? "true" : "false");
    const card = chip.closest(".page-card");
    if (card) savePageChanges(Number(card.dataset.index));
  });

  $("#codex-connect").addEventListener("click", connectCodex);
  $("#codex-logout").addEventListener("click", logoutCodex);

  $("#generate-pending").addEventListener("click", generatePending);
  $("#compose-btn").addEventListener("click", composePdf);

  $("#new-session").addEventListener("click", async () => {
    const count = clampPages($("#pages-count").value);
    try {
      await saveSettings(true);
      await createSession(count, $("#session-title").value);
    } catch (err) {
      toast(err.message || "Falha ao criar sessão", true);
    }
  });
}

async function checkServerVersion() {
  try {
    const meta = await api("/api/meta");
    const features = Array.isArray(meta.features) ? meta.features : [];
    const ok =
      features.includes("characters") &&
      features.includes("lightbox") &&
      features.includes("backup") &&
      features.includes("sessions-list") &&
      features.includes("multi-book");
    $("#server-banner").classList.toggle("is-hidden", ok);
  } catch (_) {
    $("#server-banner").classList.remove("is-hidden");
  }
}

async function boot() {
  bind();
  await checkServerVersion();
  try {
    state.defaults = await api("/api/defaults");
  } catch (err) {
    $("#pages-subtitle").textContent = "Falha ao carregar configuração.";
    toast(err.message, true);
    return;
  }

  await loadSettings();
  await loadCharacters();
  renderCharacters();
  await loadStyle();
  await refreshCodex();

  const eff = effectiveSettings();
  fillProviderOptions();
  $("#context").value = eff.context_prompt;
  $("#provider").value = eff.provider;
  $("#model").value = eff.model;
  applyProviderDefaults(eff.provider);
  $("#session-title").value = eff.title;
  $("#pages-count").value = eff.pages;
  $("#caption-enabled").checked = eff.caption_enabled;
  $("#caption-style").value = eff.caption_style;
  $("#caption-position").value = eff.caption_position;
  setSelectValue($("#caption-font"), eff.caption_font_family || "sans");
  setSelectValue($("#caption-weight"), eff.caption_font_bold ? "bold" : "normal");
  setSelectValue($("#caption-size"), eff.caption_font_size || 0.032);
  updateCaptionFields();

  const sessionId = pathSessionId();
  if (!sessionId) {
    window.location.replace("/");
    return;
  }
  try {
    state.session = await api(`/api/sessions/${sessionId}`);
  } catch (_) {
    window.location.replace("/");
    return;
  }
  if (!state.settings) {
    state.settings = {
      title: state.session.title,
      pages: state.session.pages.length,
      context_prompt: state.session.context_prompt,
      provider: state.session.provider,
      model: state.session.model,
      caption_enabled: state.session.caption_enabled,
      caption_style: state.session.caption_style,
      caption_position: state.session.caption_position,
      caption_font_family: state.session.caption_font_family,
      caption_font_bold: state.session.caption_font_bold,
      caption_font_size: state.session.caption_font_size,
    };
    try {
      state.settings = await api("/api/settings", {
        method: "PUT",
        body: JSON.stringify(state.settings),
      });
    } catch (_) {}
  }
  if (
    state.codex &&
    state.codex.authenticated &&
    !state.defaults.openai_configured &&
    state.session.provider === "openai"
  ) {
    try {
      await useCodexProvider();
    } catch (_) {}
  }
  await loadSessions();
  render();
}

boot();
