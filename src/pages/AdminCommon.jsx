import React from "react";
import { useApp } from "../App.jsx";
import Modal from "../components/Modal.jsx";

/** The Administration pages are listed in the side menu (the rail, or the More sheet on mobile), so the pages carry no
 * tab strip of their own. Kept as a component so each page's placement is unchanged. */
export function SettingsNav() {
  return null;
}

/** Loading, forbidden, missing and error states shared by the administration pages. Returns null when ready. */
export function PageState({ state, noun, onRetry, forbiddenText }) {
  const { navigate } = useApp();
  if (state.status === "loading") return <div className="muted" role="status">Loading {noun}…</div>;
  if (state.status === "forbidden") {
    return (
      <div className="empty" role="alert">
        <div>{forbiddenText}</div>
        <button type="button" className="btn sm" onClick={() => navigate("/tickets")}>Back to tickets</button>
      </div>
    );
  }
  if (state.status === "missing") return <div className="notice bad" role="alert">{state.message}</div>;
  if (state.status === "error") {
    return (
      <div className="notice bad admin-error" role="alert">
        <span>{state.message}</span>
        {onRetry && <button type="button" className="btn sm" onClick={onRetry}>Retry</button>}
      </div>
    );
  }
  return null;
}

/** Result line after a write: success only after the server confirmed it; errors stay until the next attempt. */
export function WriteResult({ result, onReload }) {
  if (!result) return null;
  if (result.ok) return <div className="notice admin-ok" role="status">{result.message}</div>;
  return (
    <div className="notice bad admin-error" role="alert">
      <span>{result.message}</span>
      {result.stale && onReload && <button type="button" className="btn sm" onClick={onReload}>Reload</button>}
    </div>
  );
}

/** Confirmation for a consequential change. The confirm button is disabled while the request runs. */
export function ConfirmDialog({ title, children, confirmLabel, danger, busy, onConfirm, onClose }) {
  return (
    <Modal title={title} onClose={busy ? () => {} : onClose} footer={(
      <>
        <button type="button" className="btn" onClick={onClose} disabled={busy}>Cancel</button>
        <button type="button" className={`btn ${danger ? "danger" : "primary"}`} onClick={onConfirm} disabled={busy}>
          {busy ? "Working…" : confirmLabel}
        </button>
      </>
    )}>
      {children}
    </Modal>
  );
}

export const BackLink = ({ to, label }) => {
  const { navigate } = useApp();
  return <a href={to} className="small admin-back" onClick={(e) => { e.preventDefault(); navigate(to); }}>← {label}</a>;
};
