/**
 * Popover Select — progressively enhances native <select> elements into
 * Pwrite-styled floating popover dropdowns with keyboard nav & GSAP motion.
 */

const instances = new WeakMap();
let activePopover = null;

function closeActive() {
  if (!activePopover) return;
  const { wrap, menu, trigger } = activePopover;
  wrap.classList.remove("is-open");
  trigger.setAttribute("aria-expanded", "false");
  menu.classList.add("is-hidden");
  activePopover = null;
}

document.addEventListener("click", (ev) => {
  if (activePopover && !activePopover.wrap.contains(ev.target)) {
    closeActive();
  }
});

document.addEventListener("keydown", (ev) => {
  if (!activePopover) return;
  if (ev.key === "Escape") {
    ev.preventDefault();
    const { trigger } = activePopover;
    closeActive();
    trigger.focus();
  }
});

function buildMenu(select, wrap, trigger, menu) {
  const options = Array.from(select.options);
  const selected = options.find((o) => o.value === select.value) || options[0];

  const labelEl = trigger.querySelector(".pselect-label");
  labelEl.textContent = selected ? selected.textContent : "Selecionar…";
  trigger.disabled = select.disabled;

  menu.innerHTML = "";
  for (const opt of options) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "pselect-option" + (opt.value === select.value ? " is-selected" : "");
    item.setAttribute("role", "option");
    item.setAttribute("aria-selected", opt.value === select.value ? "true" : "false");
    if (opt.disabled) item.disabled = true;

    const badge = opt.dataset.badge || "";
    item.innerHTML = `
      <span class="pselect-opt-main">
        <span class="pselect-opt-text"></span>
        ${badge ? `<span class="pselect-opt-badge"></span>` : ""}
      </span>
      <svg class="pselect-check" viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2">
        <path d="M4.5 10.5l3.5 3.5 7.5-8" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
    `;
    item.querySelector(".pselect-opt-text").textContent = opt.textContent;
    if (badge) item.querySelector(".pselect-opt-badge").textContent = badge;

    item.addEventListener("click", () => {
      if (select.value !== opt.value) {
        select.value = opt.value;
        select.dispatchEvent(new Event("change", { bubbles: true }));
      }
      buildMenu(select, wrap, trigger, menu);
      closeActive();
      trigger.focus();
    });
    menu.appendChild(item);
  }
}

function positionMenu(trigger, menu) {
  const rect = trigger.getBoundingClientRect();
  const spaceBelow = window.innerHeight - rect.bottom;
  const openUp = spaceBelow < 240 && rect.top > spaceBelow;
  menu.classList.toggle("is-up", openUp);
}

export function enhanceSelect(select) {
  if (!select || select.tagName !== "SELECT") return;
  const existing = instances.get(select);
  if (existing) {
    buildMenu(select, existing.wrap, existing.trigger, existing.menu);
    return;
  }

  select.classList.add("pselect-native");
  select.tabIndex = -1;

  const wrap = document.createElement("div");
  wrap.className = "pselect";
  select.parentNode.insertBefore(wrap, select);
  wrap.appendChild(select);

  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.className = "pselect-trigger";
  trigger.setAttribute("aria-haspopup", "listbox");
  trigger.setAttribute("aria-expanded", "false");
  trigger.innerHTML = `
    <span class="pselect-label"></span>
    <svg class="pselect-chevron" viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2">
      <path d="M5.5 7.5L10 12l4.5-4.5" stroke-linecap="round" stroke-linejoin="round" />
    </svg>
  `;

  const menu = document.createElement("div");
  menu.className = "pselect-menu is-hidden";
  menu.setAttribute("role", "listbox");

  wrap.appendChild(trigger);
  wrap.appendChild(menu);

  trigger.addEventListener("click", (ev) => {
    ev.stopPropagation();
    const isSame = activePopover && activePopover.wrap === wrap;
    closeActive();
    if (isSame) return;

    buildMenu(select, wrap, trigger, menu);
    positionMenu(trigger, menu);
    menu.classList.remove("is-hidden");
    wrap.classList.add("is-open");
    trigger.setAttribute("aria-expanded", "true");
    activePopover = { wrap, menu, trigger };

    if (typeof window.gsap !== "undefined") {
      const mm = window.gsap.matchMedia();
      mm.add("(prefers-reduced-motion: no-preference)", () => {
        window.gsap.fromTo(
          menu,
          { y: menu.classList.contains("is-up") ? 6 : -6, autoAlpha: 0, scale: 0.98 },
          { y: 0, autoAlpha: 1, scale: 1, duration: 0.16, ease: "power2.out", clearProps: "transform,opacity,visibility" }
        );
      });
    }
  });

  const record = { wrap, trigger, menu };
  instances.set(select, record);
  buildMenu(select, wrap, trigger, menu);
}

export function syncAllPopoverSelects(root = document) {
  root.querySelectorAll("select").forEach((sel) => enhanceSelect(sel));
}
