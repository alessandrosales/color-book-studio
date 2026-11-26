/**
 * Style picker: category tabs plus a grid of the styles in the active category.
 * Shared by the library (new book drawer) and the studio (stage 1).
 */

const PAGE_SIZE_LABEL = {
  "square_8.5x8.5": "Quadrado 8.5x8.5",
  "square_8.25x8.25": "Quadrado 8.25x8.25",
  "letter_8.5x11": "Carta 8.5x11",
  "6x9": "6x9 in",
  "5x8": "5x8 in",
  "square_8x8": "Quadrado 8x8",
  "8x10": "8x10 in",
  a4: "A4",
  a5: "A5",
  b6_jp: "B6 japonês",
  "manga_5x7.5": "Mangá 5x7.5",
  "formatinho_13.5x19": "Formatinho 13,5x19",
  "americano_17x26": "Americano 17x26",
  br_21x28: "21x28 cm",
};

export function formatPageSize(code) {
  return PAGE_SIZE_LABEL[code] || String(code || "letter_8.5x11").replace("_", " ");
}

const activeByRoot = new WeakMap();

function buildCategories(presets, categories) {
  const known = new Map(categories.map((c) => [c.id, { ...c }]));
  for (const preset of presets) {
    if (!known.has(preset.category)) {
      known.set(preset.category, {
        id: preset.category,
        name: preset.category === "custom" ? "Personalizados" : preset.category,
        tagline: "",
      });
    }
  }
  return Array.from(known.values())
    .map((c) => ({ ...c, count: presets.filter((p) => p.category === c.id).length }))
    .filter((c) => c.count > 0);
}

export function renderStylePicker({ root, presets, categories = [], selectedId, onSelect }) {
  if (!root) return;
  const cats = buildCategories(presets, categories);
  if (!cats.length) {
    root.innerHTML = "";
    return;
  }

  const selected = presets.find((p) => p.id === selectedId);
  let active = activeByRoot.get(root);
  if (!active || !cats.some((c) => c.id === active)) {
    active = selected?.category && cats.some((c) => c.id === selected.category)
      ? selected.category
      : cats[0].id;
    activeByRoot.set(root, active);
  }

  const activeMeta = cats.find((c) => c.id === active);
  root.innerHTML = "";
  root.classList.add("style-picker");

  const tabs = document.createElement("div");
  tabs.className = "chip-row style-tabs";
  tabs.setAttribute("role", "tablist");
  for (const cat of cats) {
    const tab = document.createElement("button");
    tab.type = "button";
    tab.className = "chip" + (cat.id === active ? " is-active" : "");
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-selected", cat.id === active ? "true" : "false");
    tab.textContent = `${cat.name} (${cat.count})`;
    tab.addEventListener("click", () => {
      activeByRoot.set(root, cat.id);
      renderStylePicker({ root, presets, categories, selectedId, onSelect });
    });
    tabs.appendChild(tab);
  }
  root.appendChild(tabs);

  if (activeMeta?.tagline) {
    const hint = document.createElement("p");
    hint.className = "hint style-tab-hint";
    hint.textContent = activeMeta.tagline;
    root.appendChild(hint);
  }

  const grid = document.createElement("div");
  grid.className = "preset-grid";
  for (const preset of presets.filter((p) => p.category === active)) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "preset-card" + (preset.id === selectedId ? " is-selected" : "");
    if (preset.concept_prompt) card.title = preset.concept_prompt;
    card.innerHTML = `
      <div class="preset-card-top">
        <span class="preset-name"></span>
        <span class="preset-badge"></span>
      </div>
      <p class="preset-tagline"></p>
      <div class="preset-meta"><span></span></div>
    `;
    card.querySelector(".preset-name").textContent = preset.name;
    card.querySelector(".preset-badge").textContent = preset.badge || preset.category;
    card.querySelector(".preset-tagline").textContent = preset.tagline || preset.description;
    const thresholdLabels = { otsu: "Otsu", adaptive: "Adaptativo", fixed: "Limiar fixo" };
    const thresh = thresholdLabels[preset.processing?.threshold] || "Adaptativo";
    card.querySelector(".preset-meta span").textContent =
      `${formatPageSize(preset.page_size)}, ${thresh}`;
    card.addEventListener("click", () => {
      activeByRoot.set(root, preset.category);
      onSelect(preset);
    });
    grid.appendChild(card);
  }
  root.appendChild(grid);
}
