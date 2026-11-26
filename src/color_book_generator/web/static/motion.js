/**
 * Shared GSAP motion helpers for Color Book Studio.
 * Honors prefers-reduced-motion and keeps transitions fast (160-280ms) for Operate mode.
 */

const REDUCED_QUERY = "(prefers-reduced-motion: reduce)";

export function prefersReducedMotion() {
  return typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia(REDUCED_QUERY).matches;
}

function getGsap() {
  if (prefersReducedMotion()) return null;
  return typeof window !== "undefined" && window.gsap ? window.gsap : null;
}

export function animateCardsIn(selector) {
  const gsap = getGsap();
  if (!gsap) return;
  const targets = document.querySelectorAll(selector);
  if (!targets.length) return;
  gsap.fromTo(
    targets,
    { y: 10, autoAlpha: 0 },
    {
      y: 0,
      autoAlpha: 1,
      duration: 0.28,
      stagger: 0.03,
      ease: "power2.out",
      overwrite: "auto",
      clearProps: "transform,opacity,visibility",
    }
  );
}

export function animateStageEnter(panel) {
  const gsap = getGsap();
  if (!gsap || !panel) return;
  gsap.fromTo(
    panel,
    { y: 10, autoAlpha: 0 },
    {
      y: 0,
      autoAlpha: 1,
      duration: 0.24,
      ease: "power3.out",
      overwrite: "auto",
      clearProps: "transform,opacity,visibility",
    }
  );
}

export function animateDrawerOpen(drawer) {
  const gsap = getGsap();
  if (!gsap || !drawer) return;
  gsap.fromTo(
    drawer,
    { y: -8, autoAlpha: 0 },
    {
      y: 0,
      autoAlpha: 1,
      duration: 0.24,
      ease: "power2.out",
      overwrite: "auto",
      clearProps: "transform,opacity,visibility",
    }
  );
}

export function animateModalOpen(backdrop) {
  const gsap = getGsap();
  if (!gsap || !backdrop) return;
  const card = backdrop.querySelector(".modal-card");
  gsap.fromTo(
    backdrop,
    { autoAlpha: 0 },
    { autoAlpha: 1, duration: 0.18, ease: "power2.out", overwrite: "auto", clearProps: "opacity,visibility" }
  );
  if (card) {
    gsap.fromTo(
      card,
      { y: 10, scale: 0.98, autoAlpha: 0 },
      {
        y: 0,
        scale: 1,
        autoAlpha: 1,
        duration: 0.22,
        ease: "power3.out",
        overwrite: "auto",
        clearProps: "transform,opacity,visibility",
      }
    );
  }
}

export function animatePopoverOpen(menu, isUp) {
  const gsap = getGsap();
  if (!gsap || !menu) return;
  gsap.fromTo(
    menu,
    { y: isUp ? 6 : -6, autoAlpha: 0, scale: 0.98 },
    {
      y: 0,
      autoAlpha: 1,
      scale: 1,
      duration: 0.16,
      ease: "power2.out",
      overwrite: "auto",
      clearProps: "transform,opacity,visibility",
    }
  );
}

export function animateLightboxOpen(box) {
  const gsap = getGsap();
  if (!gsap || !box) return;
  const img = box.querySelector("img");
  gsap.fromTo(
    box,
    { autoAlpha: 0 },
    { autoAlpha: 1, duration: 0.18, ease: "power2.out", overwrite: "auto", clearProps: "opacity,visibility" }
  );
  if (img) {
    gsap.fromTo(
      img,
      { scale: 0.97, autoAlpha: 0 },
      {
        scale: 1,
        autoAlpha: 1,
        duration: 0.22,
        ease: "power3.out",
        overwrite: "auto",
        clearProps: "transform,opacity,visibility",
      }
    );
  }
}
