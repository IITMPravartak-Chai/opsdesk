import React, { useId, useRef } from "react";
import useDialogFocus from "./useDialogFocus.js";

/**
 * Modal — accessible dialog primitive (U2.2, UI-DEC-006).
 *
 * Named by its title via aria-labelledby. Focus is trapped while open, Escape closes it from
 * anywhere on the page (topmost dialog only), and focus returns to the opener on close.
 * The scrim covers the page, so background controls cannot be clicked while it is open.
 *
 * Disabled-link note (UI-DEC-004): CSS pointer-events:none on a.disabled / a[aria-disabled]
 * does not stop keyboard activation; such links are also excluded from the focus trap.
 */
export default function Modal({ title, children, footer, onClose }) {
  const dialogRef = useRef(null);
  const titleId = useId();
  useDialogFocus(dialogRef, onClose);

  return (
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={dialogRef} className="modal" role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}>
        <div className="modal-h"><h2 id={titleId}>{title}</h2></div>
        <div className="modal-b">{children}</div>
        {footer && <div className="modal-f">{footer}</div>}
      </div>
    </div>
  );
}
