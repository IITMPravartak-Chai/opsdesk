import React, { useCallback, useEffect, useId, useRef, useState } from "react";
import { api } from "../api.js";
import { relTime } from "../util.js";
import { inFlightGuard, latestOnly } from "../pages/workflowHelpers.js";
import { useNotificationFeed } from "./useNotificationFeed.js";
import {
  LIVE_STATUS_TEXT, PREFERENCE_FIELDS, applyAllRead, applyRead, badgeText, notificationTarget,
  notificationsErrorMessage, preferencesErrorMessage, routeMissing, unreadLabel,
} from "./notificationsModel.js";

/**
 * Notification centre (U5, UI-DEC-016). Count, polling and the live stream come from the shared
 * NotificationsProvider; this component owns the panel, list, read actions and settings.
 * Read state and settings change on screen only after the server confirms the request.
 *
 * variant "rail": bell button in the desktop/tablet rail. variant "sheet": full-width row in the
 * mobile More sheet. The panel is fixed-positioned so the narrow rail cannot clip it.
 */
export default function NotificationsBell({ navigate, variant = "rail", onNavigate }) {
  const feed = useNotificationFeed();
  const { unreadCount, countError, liveStatus, refreshCount, setConfirmedCount, subscribe } = feed;
  const [isOpen, setIsOpen] = useState(false);
  const [showPrefs, setShowPrefs] = useState(false);
  const [list, setList] = useState({ status: "idle", items: [], error: "" }); // idle | loading | ready | error | unavailable
  const [prefs, setPrefs] = useState({ status: "idle", values: null, error: "" });
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState(null); // notification id | "all" | preference key
  const [savedNote, setSavedNote] = useState("");
  const [retryPref, setRetryPref] = useState(null); // the edit that failed, so it can be re-applied
  const wrapRef = useRef(null);
  const buttonRef = useRef(null);
  const listGate = useRef(latestOnly());
  const guard = useRef(inFlightGuard());
  const panelId = useId();

  const fetchList = useCallback(async () => {
    const token = listGate.current.next();
    setList((l) => ({ ...l, status: l.items.length ? l.status : "loading", error: "" }));
    try {
      const res = await api("/notifications/");
      if (!listGate.current.isCurrent(token)) return;
      setList({ status: "ready", items: res?.results || [], error: "" });
      if (Number.isFinite(res?.unread_count)) setConfirmedCount(res.unread_count);
    } catch (e) {
      if (!listGate.current.isCurrent(token)) return;
      setList((l) => ({ ...l, status: routeMissing(e.status) ? "unavailable" : "error", error: notificationsErrorMessage(e) }));
    }
  }, [setConfirmedCount]);

  const fetchPrefs = useCallback(async () => {
    setPrefs((p) => ({ ...p, status: "loading", error: "" }));
    try {
      const res = await api("/notifications/preferences/");
      setPrefs({ status: "ready", values: res, error: "" });
    } catch (e) {
      setPrefs({ status: routeMissing(e.status) ? "unavailable" : "error", values: null, error: preferencesErrorMessage(e) });
    }
  }, []);

  useEffect(() => { if (isOpen) fetchList(); }, [isOpen, fetchList]);
  useEffect(() => { if (isOpen && showPrefs && prefs.status === "idle") fetchPrefs(); }, [isOpen, showPrefs, prefs.status, fetchPrefs]);
  // A live "notification" event refreshes an open list; the feed already refreshed the count.
  useEffect(() => (isOpen ? subscribe(fetchList) : undefined), [isOpen, subscribe, fetchList]);

  const close = useCallback((restoreFocus = true) => {
    setIsOpen(false);
    setShowPrefs(false);
    if (restoreFocus) buttonRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!isOpen) return undefined;
    const outside = (e) => { if (wrapRef.current && !wrapRef.current.contains(e.target)) close(false); };
    document.addEventListener("mousedown", outside);
    return () => document.removeEventListener("mousedown", outside);
  }, [isOpen, close]);

  // On the wrapper so it works from the bell button too. Only while open, so Escape still closes
  // a surrounding More sheet when the panel is shut (React's stopPropagation stops the native event
  // before it reaches the document-level dialog listener).
  const onKey = (e) => {
    if (!isOpen || e.key !== "Escape") return;
    e.stopPropagation();
    close();
  };

  const markAsRead = async (n) => {
    if (n.is_read || !guard.current.enter()) return false;
    setBusy(n.id);
    setActionError("");
    try {
      await api(`/notifications/${n.id}/read/`, { method: "POST" });
      setList((l) => ({ ...l, items: applyRead(l.items, n.id) }));
      setConfirmedCount((c) => (Number.isFinite(c) ? Math.max(0, c - 1) : c));
      return true;
    } catch (e) {
      setActionError(`Couldn't mark it as read: ${e.message}`);
      return false;
    } finally {
      guard.current.exit();
      setBusy(null);
    }
  };

  const markAllRead = async () => {
    if (!guard.current.enter()) return;
    setBusy("all");
    setActionError("");
    try {
      await api("/notifications/mark_all_read/", { method: "POST" });
      setList((l) => ({ ...l, items: applyAllRead(l.items) }));
      setConfirmedCount(0);
    } catch (e) {
      setActionError(`Couldn't mark all as read: ${e.message}`);
    } finally {
      guard.current.exit();
      setBusy(null);
    }
  };

  const openItem = async (n) => {
    await markAsRead(n); // a failed mark-read is reported but does not block navigation
    const to = notificationTarget(n);
    if (!to) return;
    close(false);
    onNavigate?.();
    navigate(to);
  };

  const updatePref = async (key, val) => {
    if (!guard.current.enter()) return;
    setBusy(key);
    setSavedNote("");
    setRetryPref(null);
    try {
      const res = await api("/notifications/preferences/", { method: "PATCH", body: { [key]: val } });
      setPrefs((p) => ({ ...p, values: res && typeof res === "object" ? { ...p.values, ...res } : { ...p.values, [key]: val } }));
      setSavedNote("Saved.");
    } catch (e) {
      // The control keeps the last saved value; the failed edit can be re-applied with one click.
      setRetryPref({ key, val, message: `Not saved: ${e.message}` });
    } finally {
      guard.current.exit();
      setBusy(null);
    }
  };

  const count = unreadCount;
  const badge = badgeText(count);
  const label = unreadLabel(count);
  const countNote = countError && count !== null ? "Unread count may be out of date." : null;

  const panel = isOpen && (
    <div className={`notif-panel notif-panel--${variant}`} id={panelId} role="region" aria-label="Notifications">
      <div className="notif-header">
        <h2 className="notif-title">{showPrefs ? "Notification settings" : "Notifications"}</h2>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          {/* Always rendered in list view (disabled until there is confirmed unread), so the header
              doesn't shift under the pointer when the list finishes loading. */}
          {!showPrefs && (
            <button type="button" className="btn ghost sm" onClick={markAllRead}
              disabled={busy !== null || list.status !== "ready" || !list.items.some((n) => !n.is_read)}>
              {busy === "all" ? "Marking…" : "Mark all read"}
            </button>
          )}
          <button type="button" className="btn ghost sm" onClick={() => setShowPrefs((p) => !p)} aria-pressed={showPrefs}>
            {showPrefs ? "Back" : "Settings"}
          </button>
          <button type="button" className="btn ghost sm" onClick={() => close()} aria-label="Close notifications">×</button>
        </div>
      </div>

      {showPrefs ? (
        <div className="notif-prefs">
          {prefs.status === "loading" && <div className="notif-state" role="status">Loading settings…</div>}
          {(prefs.status === "error" || prefs.status === "unavailable") && (
            <div className="notif-state" role="alert">
              {prefs.error}
              {prefs.status === "error" && <button type="button" className="btn sm" onClick={fetchPrefs}>Retry</button>}
            </div>
          )}
          {prefs.status === "ready" && prefs.values && PREFERENCE_FIELDS.filter((f) => f.key in prefs.values).map((f) => (
            <label key={f.key} className="toggle notif-pref">
              <span>{f.label}</span>
              <input
                type="checkbox"
                checked={Boolean(prefs.values[f.key])}
                disabled={busy !== null || (f.dependsOnInApp && !prefs.values.in_app_enabled)}
                onChange={(e) => updatePref(f.key, e.target.checked)}
              />
            </label>
          ))}
          {savedNote && <div className="small muted" role="status">{savedNote}</div>}
          {retryPref && (
            <div className="error" role="alert">
              {retryPref.message}{" "}
              <button type="button" className="btn ghost sm" onClick={() => updatePref(retryPref.key, retryPref.val)}>Try again</button>
            </div>
          )}
        </div>
      ) : (
        <div className="notif-list">
          {list.status === "loading" && <div className="notif-state" role="status">Loading notifications…</div>}
          {(list.status === "error" || list.status === "unavailable") && (
            <div className="notif-state" role="alert">
              {list.error}
              {list.status === "error" && <button type="button" className="btn sm" onClick={fetchList}>Retry</button>}
            </div>
          )}
          {list.status === "ready" && list.items.length === 0 && <div className="notif-state">No notifications yet.</div>}
          {list.items.map((n) => (
            <div key={n.id} className={`notif-item${n.is_read ? "" : " unread"}`}>
              <button type="button" className="notif-item-main" onClick={() => openItem(n)} disabled={busy !== null}>
                <span className="notif-item-top">
                  <span className="notif-item-title">
                    {!n.is_read && <span className="notif-unread-label">Unread</span>}
                    {n.title}
                  </span>
                  <span className="small muted">{relTime(n.created_at)}</span>
                </span>
                {n.body && <span className="notif-item-body">{n.body}</span>}
              </button>
              {!n.is_read && (
                <button type="button" className="btn ghost sm notif-mark" disabled={busy !== null} onClick={() => markAsRead(n)} aria-label={`Mark "${n.title}" as read`}>
                  {busy === n.id ? "Marking…" : "Mark read"}
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {actionError && <div className="error notif-action-error" role="alert">{actionError}</div>}
      <div className="notif-footer small muted" role="status">
        {LIVE_STATUS_TEXT[liveStatus]}
        {countNote ? ` ${countNote}` : ""}
      </div>
    </div>
  );

  return (
    <div className={`notif-wrap notif-wrap--${variant}`} ref={wrapRef} onKeyDown={onKey}>
      <button
        ref={buttonRef}
        type="button"
        className={variant === "sheet" ? "more-sheet-item notif-sheet-btn" : "notif-bell-btn"}
        onClick={() => (isOpen ? close(false) : (setIsOpen(true), refreshCount()))}
        aria-label={label}
        aria-expanded={isOpen}
        aria-controls={isOpen ? panelId : undefined}
        title="Notifications"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.73 21a2 2 0 0 1-3.46 0" />
        </svg>
        {variant === "sheet" && <span>Notifications</span>}
        {badge && <span className="notif-badge" aria-hidden="true">{badge}</span>}
      </button>
      {panel}
    </div>
  );
}
