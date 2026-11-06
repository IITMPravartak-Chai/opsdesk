import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api, getAuth } from "../api.js";
import { inFlightGuard, latestOnly } from "../pages/workflowHelpers.js";
import { parseStreamChunk, reconnectDelay, shouldRetryStream } from "./notificationsModel.js";

const POLL_MS = 30000; // unchanged from the pre-U5 bell

const FeedContext = createContext(null);
export const useNotificationFeed = () => useContext(FeedContext);

/**
 * One notification feed per signed-in shell (U5, UI-DEC-016): unread count polling plus the
 * existing event stream. Every bell or entry point reads from here, so there is one poll and one
 * stream connection no matter how many views show the count or how often the panel opens.
 *
 *  - `unreadCount` is null until a count has loaded; a failure keeps the last known value and sets
 *    `countError`, so a failed request never reads as "0 unread".
 *  - Polls never overlap, and an older response cannot overwrite a newer one.
 *  - The stream reads the current token on every attempt, backs off 3s→60s, and stops retrying
 *    when the route is not served (404/405/501). Everything is torn down on unmount (e.g. logout).
 *  - The stream is closed when the page is hidden (pagehide) and reopened if it is shown again
 *    (pageshow). Without this, pages kept in the browser's back/forward cache after a reload or
 *    navigation keep their stream open, and after six of them the browser's per-host connection
 *    limit stalls every new request (pre-U8 Workstream C).
 */
export function NotificationsProvider({ children }) {
  const [unreadCount, setUnreadCount] = useState(null);
  const [countError, setCountError] = useState(null);
  const [liveStatus, setLiveStatus] = useState("idle");
  const listeners = useRef(new Set());
  const pollGuard = useRef(inFlightGuard());
  const gate = useRef(latestOnly());
  const refreshQueued = useRef(false);

  // Requests never overlap, but none is dropped: a refresh asked for mid-flight (e.g. by a live
  // "notification" event) runs once after the current one, so the count can't stay stale.
  const refreshCount = useCallback(async () => {
    if (!pollGuard.current.enter()) { refreshQueued.current = true; return; }
    const token = gate.current.next();
    try {
      const res = await api("/notifications/unread_count/");
      if (!gate.current.isCurrent(token)) return;
      setUnreadCount(Number.isFinite(res?.unread_count) ? res.unread_count : 0);
      setCountError(null);
    } catch (e) {
      if (gate.current.isCurrent(token)) setCountError(e);
    } finally {
      pollGuard.current.exit();
      if (refreshQueued.current) { refreshQueued.current = false; refreshCount(); }
    }
  }, []);

  /** Apply a server-confirmed change to the count (e.g. after mark-read succeeded). */
  const setConfirmedCount = useCallback((next) => {
    gate.current.next(); // any in-flight poll result is now older than this confirmation
    setUnreadCount((c) => (typeof next === "function" ? next(c) : next));
  }, []);

  const subscribe = useCallback((fn) => {
    listeners.current.add(fn);
    return () => listeners.current.delete(fn);
  }, []);

  useEffect(() => {
    refreshCount();
    const poll = setInterval(refreshCount, POLL_MS);

    let active = true;
    let abort = null;
    let timer = null;
    let attempt = 0;
    let lastEventId = 0;
    let hidden = false;

    const connect = async () => {
      const token = getAuth()?.access; // current token, not the one captured at mount
      if (!active || hidden || !token) return;
      setLiveStatus(attempt === 0 ? "connecting" : "reconnecting");
      abort = new AbortController();
      let retry = true;
      try {
        const url = lastEventId ? `/api/events/stream/?last_event_id=${lastEventId}` : "/api/events/stream/";
        const resp = await fetch(url, { headers: { Authorization: `Bearer ${token}`, Accept: "text/event-stream" }, signal: abort.signal });
        if (!resp.ok || !resp.body) {
          retry = shouldRetryStream(resp.status);
          throw new Error(`stream ${resp.status}`);
        }
        attempt = 0;
        setLiveStatus("connected");
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (active) {
          const { value, done } = await reader.read();
          if (done) break;
          const parsed = parseStreamChunk(buffer + decoder.decode(value, { stream: true }));
          buffer = parsed.rest;
          if (parsed.lastEventId !== null) lastEventId = parsed.lastEventId;
          if (parsed.events.includes("notification")) {
            refreshCount();
            listeners.current.forEach((fn) => fn());
          }
        }
      } catch (err) {
        if (err.name === "AbortError") return;
      }
      if (!active) return;
      if (!retry) { setLiveStatus("unavailable"); return; }
      setLiveStatus("reconnecting");
      timer = setTimeout(connect, reconnectDelay(attempt));
      attempt += 1;
    };

    // Release the connection while the page is hidden or cached; resume (replaying from lastEventId) when shown.
    const onPageHide = () => {
      hidden = true;
      if (timer) { clearTimeout(timer); timer = null; }
      if (abort) abort.abort();
    };
    const onPageShow = (e) => {
      if (!hidden && !e.persisted) return;
      hidden = false;
      attempt = 0;
      refreshCount();
      connect();
    };

    if (typeof window !== "undefined" && window.fetch && window.ReadableStream) {
      window.addEventListener("pagehide", onPageHide);
      window.addEventListener("pageshow", onPageShow);
      connect();
    } else setLiveStatus("unavailable");

    return () => {
      active = false;
      clearInterval(poll);
      if (timer) clearTimeout(timer);
      if (abort) abort.abort();
      if (typeof window !== "undefined") {
        window.removeEventListener("pagehide", onPageHide);
        window.removeEventListener("pageshow", onPageShow);
      }
    };
  }, [refreshCount]);

  const value = useMemo(
    () => ({ unreadCount, countError, liveStatus, refreshCount, setConfirmedCount, subscribe }),
    [unreadCount, countError, liveStatus, refreshCount, setConfirmedCount, subscribe],
  );
  return React.createElement(FeedContext.Provider, { value }, children);
}
