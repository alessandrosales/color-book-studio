// Step 0 — Briefing: the author's story context, AI premises and the story bible.
import { syncAllPopoverSelects } from "/static/popover-select.js";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const STRUCTURE_LABEL = {
  picture_book: "Arco de livro ilustrado (apresentação, 3 tentativas, clímax, moral)",
  kishotenketsu: "Kishōtenketsu (introdução, desenvolvimento, virada, conclusão)",
  webtoon_beats: "Batidas de webtoon (uma emoção por página, gancho no fim)",
  collection: "Coleção temática (páginas independentes com um fio condutor)",
};

let deps = null;
let view = null;
let saveTimer = null;

function el(tag, attrs = {}, text = "") {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else node.setAttribute(key, value);
  }
  if (text) node.textContent = text;
  return node;
}

function sid() {
  return deps.getState().sid;
}

function showError(message) {
  const err = $("#brief-error");
  err.textContent = message || "";
  err.classList.toggle("is-hidden", !message);
}

function setSaveState(text) {
  $("#brief-save-state").textContent = text;
}

/* ---------- brief form <-> data ---------- */

function collectBrief() {
  const brief = { extras: {} };
  $$("[data-brief]").forEach((input) => {
    brief[input.dataset.brief] = input.value.trim();
  });
  $$("[data-brief-extra]").forEach((input) => {
    const value = input.value.trim();
    if (value) brief.extras[input.dataset.briefExtra] = value;
  });
  return brief;
}

function fillBrief(brief) {
  $$("[data-brief]").forEach((input) => {
    input.value = brief[input.dataset.brief] ?? "";
  });
}

function renderExtras() {
  const root = $("#brief-extras");
  root.replaceChildren();
  const questions = view?.questions || [];
  if (!questions.length) return;
  root.appendChild(el("h3", { class: "brief-subhead" }, "Perguntas deste estilo"));
  for (const q of questions) {
    const label = el("label", { class: "field-label" });
    label.appendChild(el("span", {}, q.label));
    let input;
    if (q.options) {
      input = el("select", { class: "field" });
      input.appendChild(el("option", { value: "" }, "Não definido"));
      for (const option of q.options) input.appendChild(el("option", { value: option }, option));
    } else {
      input = el("input", { class: "field", type: "text", placeholder: q.placeholder || "" });
    }
    input.dataset.briefExtra = q.key;
    input.value = view.brief.extras?.[q.key] || "";
    label.appendChild(input);
    root.appendChild(label);
  }
}

function renderMeta() {
  const wrap = $("#brief-preset-wrap");
  const chips = $("#brief-preset-suggestions");
  chips.replaceChildren();
  const suggestions = view?.suggested_presets || [];
  const current = deps.getState().session?.preset_id;
  wrap.classList.toggle("is-hidden", !suggestions.length);
  for (const preset of suggestions) {
    const chip = el(
      "button",
      { class: `brief-chip${preset.id === current ? " is-active" : ""}`, type: "button", title: preset.tagline },
      preset.name,
    );
    chip.addEventListener("click", () => applyPreset(preset));
    chips.appendChild(chip);
  }
  const structure = STRUCTURE_LABEL[view?.structure] || "";
  $("#brief-structure-hint").textContent = structure ? `Estrutura usada: ${structure}.` : "";

  const bible = view?.bible;
  const pill = $("#brief-status-pill");
  pill.className = "pill";
  if (!bible) {
    pill.textContent = "Sem bíblia";
  } else if (bible.locked) {
    pill.textContent = view.bible_stale ? "Bíblia travada · briefing mudou" : "Bíblia travada";
    pill.classList.add(view.bible_stale ? "pill-warn" : "pill-ok");
  } else {
    pill.textContent = "Bíblia em rascunho";
    pill.classList.add("pill-warn");
  }
  $("#step-0-sub").textContent = bible?.locked ? "Bíblia travada" : "Contexto da história";
  $("#bible-stale").classList.toggle("is-hidden", !view?.bible_stale);
}

/* ---------- bible editor ---------- */

function removable(row) {
  const btn = el("button", { class: "btn btn-ghost btn-sm", type: "button", "aria-label": "Remover" }, "Remover");
  btn.addEventListener("click", () => row.remove());
  return btn;
}

function characterRow(char = {}) {
  const row = el("div", { class: "bible-row", dataset: { bibleCharacter: "1" } });
  for (const [key, placeholder] of [
    ["name", "Nome"],
    ["role", "Papel"],
    ["look", "Aparência para o traço"],
  ]) {
    const input = el("input", { class: "field", type: "text", placeholder, "aria-label": placeholder });
    input.dataset.key = key;
    input.value = char[key] || "";
    row.appendChild(input);
  }
  row.appendChild(removable(row));
  const personality = el("input", { class: "field", type: "text", placeholder: "Personalidade", "aria-label": "Personalidade" });
  personality.dataset.key = "personality";
  personality.value = char.personality || "";
  personality.style.gridColumn = "1 / -1";
  row.appendChild(personality);
  return row;
}

function beatRow(beat = {}) {
  const row = el("div", { class: "bible-row bible-row-beat", dataset: { bibleBeat: "1" } });
  for (const [key, placeholder] of [
    ["label", "Batida"],
    ["pages", "Páginas"],
    ["summary", "O que acontece"],
  ]) {
    const input = el("input", { class: "field", type: "text", placeholder, "aria-label": placeholder });
    input.dataset.key = key;
    input.value = beat[key] || "";
    row.appendChild(input);
  }
  row.appendChild(removable(row));
  return row;
}

function rowValues(row) {
  const out = {};
  row.querySelectorAll("[data-key]").forEach((input) => {
    out[input.dataset.key] = input.value.trim();
  });
  return out;
}

function collectBible(locked) {
  const base = view?.bible || {};
  return {
    ...base,
    title: $("#bible-title").value.trim(),
    tone: $("#bible-tone").value.trim(),
    logline: $("#bible-logline").value.trim(),
    summary: $("#bible-summary").value.trim(),
    moral: $("#bible-moral").value.trim(),
    world_rules: $("#bible-rules").value.split("\n").map((r) => r.trim()).filter(Boolean),
    characters: $$("[data-bible-character]").map(rowValues).filter((c) => c.name),
    beats: $$("[data-bible-beat]").map(rowValues).filter((b) => b.summary),
    locked,
  };
}

function renderBible() {
  const card = $("#brief-bible");
  const bible = view?.bible;
  card.classList.toggle("is-hidden", !bible);
  if (!bible) return;
  $("#bible-title").value = bible.title || "";
  $("#bible-tone").value = bible.tone || "";
  $("#bible-logline").value = bible.logline || "";
  $("#bible-summary").value = bible.summary || "";
  $("#bible-moral").value = bible.moral || "";
  $("#bible-rules").value = (bible.world_rules || []).join("\n");
  $("#bible-characters").replaceChildren(...(bible.characters || []).map(characterRow));
  $("#bible-beats").replaceChildren(...(bible.beats || []).map(beatRow));

  const locked = Boolean(bible.locked);
  card.querySelectorAll("input, textarea").forEach((input) => {
    input.readOnly = locked;
  });
  card.querySelectorAll(".bible-row .btn, #bible-add-character, #bible-add-beat, #bible-save, #bible-discard").forEach((btn) => {
    btn.disabled = locked;
  });
  $("#bible-lock").textContent = locked ? "Destravar bíblia" : "Travar bíblia";
  const lockPill = $("#bible-lock-pill");
  lockPill.textContent = locked ? "Travada" : "Rascunho";
  lockPill.className = `pill ${locked ? "pill-ok" : "pill-warn"}`;
}

/* ---------- premises ---------- */

function renderPremises(premises) {
  const root = $("#brief-premises");
  root.replaceChildren();
  root.classList.toggle("is-hidden", !premises.length);
  for (const premise of premises) {
    const card = el("article", { class: "floating-card premise-card" });
    card.appendChild(el("h3", {}, premise.title || "Sem título"));
    card.appendChild(el("p", {}, premise.logline));
    if (premise.hook) card.appendChild(el("p", {}, premise.hook));
    const meta = [premise.tone && `Tom: ${premise.tone}`, premise.moral && `Moral: ${premise.moral}`]
      .filter(Boolean)
      .join(" · ");
    if (meta) card.appendChild(el("p", { class: "premise-meta" }, meta));
    const use = el("button", { class: "btn btn-primary btn-sm", type: "button" }, "Usar esta premissa");
    use.addEventListener("click", () => generateBible(premise, use));
    card.appendChild(use);
    root.appendChild(card);
  }
}

/* ---------- API actions ---------- */

async function saveBrief() {
  const brief = collectBrief();
  setSaveState("Salvando…");
  try {
    view = await deps.api(`/api/sessions/${sid()}/brief`, {
      method: "PUT",
      body: JSON.stringify(brief),
    });
    renderMeta();
    setSaveState("Briefing salvo.");
  } catch (err) {
    setSaveState("");
    showError(err.message);
  }
}

function scheduleSave() {
  clearTimeout(saveTimer);
  setSaveState("Alterações não salvas…");
  saveTimer = setTimeout(saveBrief, 800);
}

async function flushSave() {
  if (saveTimer) {
    clearTimeout(saveTimer);
    saveTimer = null;
    await saveBrief();
  }
}

function requireLogline() {
  if ($("#brief-logline").value.trim()) return true;
  showError("Escreva a ideia central (ou use Sugerir) antes de continuar.");
  $("#brief-logline").focus();
  return false;
}

async function withBusy(button, label, fn) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = label;
  showError("");
  try {
    await fn();
  } catch (err) {
    showError(err.message);
  } finally {
    button.disabled = false;
    // Keep a label the action itself set (e.g. "Destravar bíblia" after locking).
    if (button.textContent === label) button.textContent = original;
  }
}

async function suggestField(button) {
  const field = button.dataset.suggest;
  await withBusy(button, "…", async () => {
    const res = await deps.api(`/api/sessions/${sid()}/brief/suggest`, {
      method: "POST",
      body: JSON.stringify({ field, brief: collectBrief() }),
    });
    const input = $(`[data-brief="${field}"]`);
    if (input) input.value = res.value;
    await saveBrief();
  });
}

async function proposePremises() {
  if (!requireLogline()) return;
  const button = $("#brief-premises-btn");
  await withBusy(button, "Pensando em premissas…", async () => {
    const res = await deps.api(`/api/sessions/${sid()}/brief/premises`, {
      method: "POST",
      body: JSON.stringify({ brief: collectBrief() }),
    });
    renderPremises(res.premises || []);
  });
}

async function generateBible(premise, button) {
  if (!requireLogline()) return;
  if (view?.bible?.locked) {
    showError("Destrave a bíblia atual antes de gerar outra.");
    return;
  }
  if (view?.bible && !window.confirm("Substituir a bíblia em rascunho por uma nova?")) return;
  await withBusy(button, "Montando a bíblia…", async () => {
    view = await deps.api(`/api/sessions/${sid()}/bible/generate`, {
      method: "POST",
      body: JSON.stringify({ premise, brief: collectBrief() }),
    });
    renderMeta();
    renderBible();
    $("#brief-bible").scrollIntoView({ behavior: "smooth", block: "start" });
    deps.toast("Bíblia da história pronta. Revise e trave.");
  });
}

async function putBible(locked) {
  view = await deps.api(`/api/sessions/${sid()}/bible`, {
    method: "PUT",
    body: JSON.stringify(collectBible(locked)),
  });
  renderMeta();
  renderBible();
}

async function applyPreset(preset) {
  try {
    await deps.api(`/api/sessions/${sid()}/apply-preset`, {
      method: "POST",
      body: JSON.stringify({ preset_id: preset.id }),
    });
    await deps.reloadSession();
    deps.toast(`Estilo "${preset.name}" aplicado ao livro.`);
  } catch (err) {
    deps.toast(err.message, true);
  }
}

/* ---------- public API ---------- */

export async function refreshBriefing() {
  if (!deps) return;
  try {
    view = await deps.api(`/api/sessions/${sid()}/brief`);
  } catch (err) {
    showError(err.message);
    return;
  }
  fillBrief(view.brief);
  renderExtras();
  renderMeta();
  renderBible();
  syncAllPopoverSelects($("#stage-0"));
}

/** Called on every session render: refresh only when the style category changed. */
export function syncBriefing() {
  const session = deps?.getState().session;
  if (!session) return;
  $("#brief-language").value = session.language || "en";
  if (view && view.category !== session.category) {
    flushSave().then(refreshBriefing).catch((err) => showError(err.message));
  } else if (view) {
    renderMeta();
  }
}

export function initBriefing(options) {
  deps = options;
  const stage = $("#stage-0");
  stage.addEventListener("input", (event) => {
    if (event.target.closest("[data-brief], [data-brief-extra]")) scheduleSave();
  });
  stage.addEventListener("change", (event) => {
    if (event.target.closest("[data-brief], [data-brief-extra]")) scheduleSave();
  });
  $$(".brief-suggest").forEach((btn) => btn.addEventListener("click", () => suggestField(btn)));
  $("#brief-premises-btn").addEventListener("click", proposePremises);
  $("#brief-bible-direct").addEventListener("click", () => generateBible(null, $("#brief-bible-direct")));
  $("#brief-language").addEventListener("change", async () => {
    try {
      await deps.api(`/api/sessions/${sid()}`, {
        method: "PATCH",
        body: JSON.stringify({ language: $("#brief-language").value }),
      });
      await deps.reloadSession();
    } catch (err) {
      deps.toast(err.message, true);
    }
  });
  $("#bible-add-character").addEventListener("click", () => $("#bible-characters").appendChild(characterRow()));
  $("#bible-add-beat").addEventListener("click", () => $("#bible-beats").appendChild(beatRow()));
  $("#bible-save").addEventListener("click", () =>
    withBusy($("#bible-save"), "Salvando…", async () => {
      await putBible(false);
      deps.toast("Bíblia salva.");
    }),
  );
  $("#bible-lock").addEventListener("click", () => {
    const locking = !view?.bible?.locked;
    return withBusy($("#bible-lock"), locking ? "Travando…" : "Destravando…", async () => {
      await flushSave();
      await putBible(locking);
      await deps.reloadSession();
      deps.toast(locking ? "Bíblia travada: ela agora guia o roteiro e a capa." : "Bíblia destravada para edição.");
    });
  });
  $("#bible-discard").addEventListener("click", async () => {
    if (!window.confirm("Descartar a bíblia da história? O briefing continua salvo.")) return;
    try {
      await deps.api(`/api/sessions/${sid()}/bible`, { method: "DELETE" });
      await refreshBriefing();
    } catch (err) {
      showError(err.message);
    }
  });
  $("#bible-characters-btn").addEventListener("click", () =>
    withBusy($("#bible-characters-btn"), "Criando…", async () => {
      if (!view?.bible?.locked) await putBible(false);
      const res = await deps.api(`/api/sessions/${sid()}/bible/characters`, { method: "POST" });
      view = res.view;
      await deps.reloadSession();
      deps.toast(
        res.created
          ? `${res.created} personagem(ns) criado(s). Gere as folhas de referência no passo Personagens.`
          : "Todos os personagens da bíblia já estão no livro.",
      );
    }),
  );
  return refreshBriefing();
}
