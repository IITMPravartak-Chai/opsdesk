// Framework-free helpers behind the shared dialog and banner primitives (U2.2, UI-DEC-006/009).
// Kept free of React and JSX so the existing `node --test` runner can cover them directly.

export const FOCUSABLE_SELECTOR = [
  "a[href]:not([aria-disabled='true'])",
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

/** Tabbable descendants of `container`, excluding disabled, inert and aria-hidden ones. */
export function getFocusable(container) {
  if (!container) return [];
  return Array.from(container.querySelectorAll(FOCUSABLE_SELECTOR)).filter(
    (n) => !n.disabled && !n.closest?.("[inert],[aria-hidden='true']")
  );
}

/**
 * Where focus should go for a Tab press inside a focus trap, or null to let the browser move it.
 * Focus that has escaped the container is pulled back to the first (Tab) or last (Shift+Tab) item.
 */
export function nextTrapTarget(items, active, shiftKey, container) {
  if (!items.length) return container || null;
  const first = items[0];
  const last = items[items.length - 1];
  if (!container || !active || !container.contains(active)) return shiftKey ? last : first;
  if (shiftKey && active === first) return last;
  if (!shiftKey && active === last) return first;
  return null;
}

// Open dialogs, innermost last. Only the topmost one reacts to Escape, Tab and stray focus,
// so nested dialogs never fight over the keyboard.
const layers = [];

export function pushLayer(id) {
  layers.push(id);
  return () => {
    const i = layers.lastIndexOf(id);
    if (i >= 0) layers.splice(i, 1);
  };
}

export const isTopLayer = (id) => layers.length > 0 && layers[layers.length - 1] === id;

/** Return focus to the element that opened a dialog, if it is still in the document. */
export function restoreFocus(el) {
  if (!el || typeof el.focus !== "function" || el.isConnected === false) return false;
  try { el.focus(); return true; } catch { return false; }
}

/**
 * Live-region semantics for DegradedBanner. Errors interrupt (alert); degraded/info notices are
 * announced politely (status). Unknown levels fall back to the warning treatment.
 */
export function bannerSemantics(level) {
  if (level === "error") return { role: "alert", className: "degraded-banner degraded-banner--error" };
  if (level === "info") return { role: "status", className: "degraded-banner degraded-banner--info" };
  return { role: "status", className: "degraded-banner degraded-banner--warning" };
}
