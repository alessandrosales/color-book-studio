/**
 * Visual typography reference presets mapped to the 6 book categories.
 * Shared by the library creator (home.js) and studio stage 1 (app.js).
 */

export const FONT_PRESETS = [
  {
    id: "cozy",
    name: "Bold & Easy",
    meta: "Legenda, Sans negrito",
    imageUrl: "/static/font-presets/cozy.png",
    caption: {
      enabled: true,
      style: "caption",
      position: "bottom",
      font_family: "sans",
      bold: true,
      font_size_ratio: 0.032,
    },
  },
  {
    id: "kids",
    name: "Infantil",
    meta: "Balão, Sans aberta",
    imageUrl: "/static/font-presets/kids.png",
    caption: {
      enabled: true,
      style: "balloon",
      position: "bottom",
      font_family: "sans",
      bold: false,
      font_size_ratio: 0.032,
    },
  },
  {
    id: "manga",
    name: "Mangá",
    meta: "Balão, caixa alta",
    imageUrl: "/static/font-presets/manga.png",
    caption: {
      enabled: true,
      style: "balloon",
      position: "bottom",
      font_family: "sans",
      bold: false,
      font_size_ratio: 0.032,
    },
  },
  {
    id: "manhwa",
    name: "Manhwa",
    meta: "Legenda, Serif suave",
    imageUrl: "/static/font-presets/manhwa.png",
    caption: {
      enabled: true,
      style: "caption",
      position: "bottom",
      font_family: "serif",
      bold: false,
      font_size_ratio: 0.032,
    },
  },
  {
    id: "kawaii",
    name: "Kawaii",
    meta: "Legenda, arredondada",
    imageUrl: "/static/font-presets/kawaii.png",
    caption: {
      enabled: true,
      style: "caption",
      position: "bottom",
      font_family: "sans",
      bold: true,
      font_size_ratio: 0.032,
    },
  },
  {
    id: "adult",
    name: "Adulto",
    meta: "Legenda, Serif editorial",
    imageUrl: "/static/font-presets/adult.png",
    caption: {
      enabled: true,
      style: "caption",
      position: "bottom",
      font_family: "serif",
      bold: false,
      font_size_ratio: 0.024,
    },
  },
];

export function getFontPreset(categoryId) {
  return FONT_PRESETS.find((p) => p.id === categoryId) || null;
}

export async function fetchFontPresetFile(preset) {
  const res = await fetch(preset.imageUrl);
  if (!res.ok) throw new Error("Não foi possível carregar a amostra de fonte.");
  const blob = await res.blob();
  return new File([blob], `font-preset-${preset.id}.png`, { type: "image/png" });
}

export function renderFontPresetPicker({ root, selectedId, onSelect }) {
  if (!root) return;
  root.innerHTML = "";
  root.classList.add("font-preset-grid");
  for (const preset of FONT_PRESETS) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "font-preset-btn" + (preset.id === selectedId ? " is-selected" : "");
    btn.setAttribute("aria-pressed", preset.id === selectedId ? "true" : "false");
    btn.innerHTML = `
      <span class="font-preset-thumb">
        <img src="${preset.imageUrl}" alt="Amostra tipográfica ${preset.name}" loading="lazy" />
      </span>
      <span class="font-preset-label">
        <strong>${preset.name}</strong>
        <span>${preset.meta}</span>
      </span>
    `;
    btn.addEventListener("click", () => onSelect(preset));
    root.appendChild(btn);
  }
}
