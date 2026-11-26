const $ = (sel) => document.querySelector(sel);

const state = {
  sessions: [],
  defaults: null,
  settings: null,
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

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[c]);
}

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString("pt-BR", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

function renderBooks() {
  const grid = $("#book-grid");
  grid.innerHTML = "";
  $("#books-count").textContent =
    state.sessions.length === 1 ? "1 livro" : `${state.sessions.length} livros`;
  $("#empty-state").classList.toggle("is-hidden", state.sessions.length > 0);

  for (const book of state.sessions) {
    const item = document.createElement("li");
    item.className = "book-card";

    const link = document.createElement("a");
    link.className = "book-cover";
    link.href = `/books/${book.id}`;
    if (book.cover_url) {
      const img = document.createElement("img");
      img.src = book.cover_url;
      img.alt = `Capa de ${book.title}`;
      img.loading = "lazy";
      link.appendChild(img);
    } else {
      link.innerHTML = `
        <span class="book-cover-empty">
          <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.3">
            <rect x="4" y="3.5" width="16" height="17" rx="2.5" />
            <path d="M8 8h8M8 12h8M8 16h5" />
          </svg>
          sem imagens
        </span>`;
    }
    const progress = document.createElement("span");
    progress.className = "book-progress";
    progress.textContent = `${book.done}/${book.pages}`;
    link.appendChild(progress);
    item.appendChild(link);

    const info = document.createElement("div");
    info.className = "book-info";
    info.innerHTML = `
      <h3>${escapeHtml(book.title)}</h3>
      <p>${book.done} de ${book.pages} páginas · atualizado em ${formatDate(book.updated_at)}</p>
      <p class="book-model">${escapeHtml(book.provider)} / ${escapeHtml(book.model)}</p>
      <div class="book-actions">
        <a class="btn btn-outline btn-small" href="/books/${book.id}">Abrir livro</a>
        <button class="btn btn-ghost btn-small danger" type="button" data-delete="${book.id}" data-title="${escapeHtml(book.title)}">Excluir</button>
      </div>`;
    item.appendChild(info);
    grid.appendChild(item);
  }
}

async function loadSessions() {
  state.sessions = await api("/api/sessions");
  renderBooks();
}

async function createBook(event) {
  event.preventDefault();
  const errorBox = $("#new-book-error");
  errorBox.classList.add("is-hidden");
  const title = $("#book-title").value.trim() || "Novo livro";
  const pages = Math.max(1, Math.min(200, Number($("#book-pages").value) || 10));
  const settings = state.settings || {};
  const defaults = state.defaults || {};
  const body = {
    title,
    pages,
    context_prompt: settings.context_prompt || defaults.context_prompt,
    provider: settings.provider || defaults.provider,
    model: settings.model || defaults.model,
    caption_enabled: settings.caption_enabled ?? defaults.caption_enabled ?? false,
    caption_style: settings.caption_style || defaults.caption_style || "balloon",
    caption_position: settings.caption_position || defaults.caption_position || "bottom",
    caption_font_family: settings.caption_font_family || defaults.caption_font_family || "sans",
    caption_font_bold: settings.caption_font_bold ?? defaults.caption_font_bold ?? false,
    caption_font_size: settings.caption_font_size || defaults.caption_font_size || 0.032,
  };
  const button = $("#book-create");
  button.disabled = true;
  button.textContent = "Criando…";
  try {
    const session = await api("/api/sessions", {
      method: "POST",
      body: JSON.stringify(body),
    });
    window.location.href = `/books/${session.id}`;
  } catch (err) {
    errorBox.textContent = err.message || "Falha ao criar o livro.";
    errorBox.classList.remove("is-hidden");
    button.disabled = false;
    button.textContent = "Criar livro";
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

function importBackup() {
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
  xhr.upload.onload = () => busyProcessing("Importando livros, imagens e personagens…");
  xhr.onload = () => {
    if (xhr.status >= 200 && xhr.status < 300) {
      const result = xhr.response || {};
      busyProgress(100, "Importação concluída");
      const skipped = result.skipped
        ? ` · ${result.skipped} de teste ignorados`
        : "";
      $("#busy-sub").textContent =
        `${result.sessions || 0} livros · ${result.pages || 0} páginas${skipped}`;
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

async function checkServerVersion() {
  try {
    const meta = await api("/api/meta");
    const features = Array.isArray(meta.features) ? meta.features : [];
    $("#server-banner").classList.toggle(
      "is-hidden",
      features.includes("multi-book")
    );
  } catch (_) {
    $("#server-banner").classList.remove("is-hidden");
  }
}

async function deleteBook(id, title) {
  if (!window.confirm(`Excluir "${title}"? Páginas e imagens serão removidas.`)) {
    return;
  }
  try {
    await api(`/api/sessions/${id}`, { method: "DELETE" });
    state.sessions = state.sessions.filter((book) => book.id !== id);
    renderBooks();
    toast("Livro excluído.");
  } catch (err) {
    toast(err.message || "Falha ao excluir o livro", true);
  }
}

function bind() {
  $("#book-grid").addEventListener("click", (event) => {
    const button = event.target.closest("[data-delete]");
    if (!button) return;
    deleteBook(button.dataset.delete, button.dataset.title);
  });
  $("#new-book-toggle").addEventListener("click", () => {
    $("#new-book-form").classList.toggle("is-hidden");
    $("#new-book-error").classList.add("is-hidden");
  });
  $("#book-cancel").addEventListener("click", () => {
    $("#new-book-form").classList.add("is-hidden");
  });
  $("#new-book-form").addEventListener("submit", createBook);
  $("#import-btn").addEventListener("click", importBackup);
}

async function boot() {
  bind();
  await checkServerVersion();
  try {
    [state.defaults, state.settings, state.sessions] = await Promise.all([
      api("/api/defaults"),
      api("/api/settings").catch(() => null),
      api("/api/sessions"),
    ]);
  } catch (err) {
    $("#meta").innerHTML = `<span>Falha ao carregar (${escapeHtml(err.message)})</span>`;
    return;
  }
  const pages = (state.settings && state.settings.pages) || state.defaults.pages;
  $("#book-pages").value = Math.max(1, Math.min(200, pages));
  $("#book-title").placeholder = state.defaults.title || "Título do livro";
  renderBooks();
}

boot();
