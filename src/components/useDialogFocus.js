import { useEffect, useRef } from "react";
import { getFocusable, isTopLayer, nextTrapTarget, pushLayer, restoreFocus } from "./uiPrimitives.js";

/**
 * Focus management shared by Modal and the mobile More sheet (U2.2, UI-DEC-006).
 *
 *  - Moves focus into the dialog when it opens (first tabbable item, else the dialog itself).
 *  - Traps Tab / Shift+Tab and pulls back focus that escapes to the page behind.
 *  - Escape closes from anywhere in the document, but only for the topmost open dialog.
 *  - Restores focus to the opener on close or unmount, and leaves no listeners behind.
 *
 * The dialog element should carry tabIndex={-1} so it can hold focus when it has no controls.
 */
export default function useDialogFocus(ref, onClose, open = true) {
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!open) return undefined;
    const el = ref.current;
    const opener = document.activeElement;
    const id = {};
    const popLayer = pushLayer(id);
    const focusFirst = () => (getFocusable(el)[0] || el)?.focus();
    const raf = requestAnimationFrame(focusFirst);

    const onKey = (e) => {
      if (!isTopLayer(id)) return;
      if (e.key === "Escape") {
        e.preventDefault();
        onCloseRef.current?.();
        return;
      }
      if (e.key !== "Tab") return;
      const target = nextTrapTarget(getFocusable(el), document.activeElement, e.shiftKey, el);
      if (target) { e.preventDefault(); target.focus(); }
    };
    const onFocusIn = (e) => {
      if (isTopLayer(id) && el && !el.contains(e.target)) focusFirst();
    };

    document.addEventListener("keydown", onKey);
    document.addEventListener("focusin", onFocusIn);
    return () => {
      cancelAnimationFrame(raf);
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("focusin", onFocusIn);
      popLayer();
      restoreFocus(opener);
    };
  }, [open, ref]);
}
