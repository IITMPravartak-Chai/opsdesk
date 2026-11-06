// U5 (UI-DEC-016): notifications, preferences and live-update resilience.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  LIVE_STATUS_TEXT, PREFERENCE_FIELDS, applyAllRead, applyRead, badgeText, notificationTarget,
  notificationsErrorMessage, parseStreamChunk, preferencesErrorMessage, reconnectDelay, routeMissing,
  shouldRetryStream, unreadLabel,
} from "./notificationsModel.js";

const err = (status, message = "boom") => Object.assign(new Error(message), { status });
const src = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8");

// ---- count presentation: a failed count never reads as zero ----

test("unreadLabel: unknown count is stated as unavailable, never as 0 unread", () => {
  assert.equal(unreadLabel(null), "Notifications (unread count unavailable)");
  assert.equal(unreadLabel(undefined), "Notifications (unread count unavailable)");
  assert.equal(unreadLabel(0), "Notifications (0 unread)");
  assert.equal(unreadLabel(4), "Notifications (4 unread)");
});

test("badgeText: no badge for unknown or zero; capped at 99+", () => {
  assert.equal(badgeText(null), null);
  assert.equal(badgeText(0), null);
  assert.equal(badgeText(7), "7");
  assert.equal(badgeText(250), "99+");
});

// ---- honest unavailable / error wording ----

test("routeMissing and messages: unrouted endpoints (BLK-004) are named, other failures quoted", () => {
  assert.deepEqual([404, 405, 501, 500, 403, undefined].map(routeMissing), [true, true, true, false, false, false]);
  assert.equal(notificationsErrorMessage(err(404)), "Notifications aren't available on this server yet.");
  assert.equal(notificationsErrorMessage(err(503, "down")), "Notifications couldn't be loaded: down.");
  assert.equal(preferencesErrorMessage(err(404)), "Notification settings aren't available on this server yet.");
  assert.equal(preferencesErrorMessage(err(500, "Server error")), "Settings couldn't be loaded: Server error.");
});

test("notificationTarget: navigates only to a positive integer ticket id", () => {
  assert.equal(notificationTarget({ ticket: 101 }), "/tickets/101");
  for (const n of [{}, { ticket: null }, { ticket: "101" }, { ticket: 0 }, null]) assert.equal(notificationTarget(n), null, JSON.stringify(n));
});

test("applyRead / applyAllRead: pure updates used only after server success", () => {
  const list = [{ id: 1, is_read: false }, { id: 2, is_read: false }];
  assert.deepEqual(applyRead(list, 2), [{ id: 1, is_read: false }, { id: 2, is_read: true }]);
  assert.deepEqual(applyAllRead(list).map((n) => n.is_read), [true, true]);
  assert.equal(list[1].is_read, false, "input not mutated");
});

test("PREFERENCE_FIELDS: exactly the existing contract's six fields", () => {
  assert.deepEqual(PREFERENCE_FIELDS.map((f) => f.key), [
    "in_app_enabled", "email_enabled", "notify_on_assignment", "notify_on_replies", "notify_on_approvals", "notify_on_sla",
  ]);
});

// ---- event stream: existing line format, bounded retry ----

test("parseStreamChunk: reads id/event lines and keeps the unfinished remainder", () => {
  const r = parseStreamChunk("event: connected\ndata: {}\n\nid: 41\nevent: notification\ndata: {\"id\":41}\n\nid: 4");
  assert.deepEqual(r.events, ["connected", "notification"]);
  assert.equal(r.lastEventId, 41);
  assert.equal(r.rest, "id: 4");
  const next = parseStreamChunk(r.rest + "2\nevent: notification\n");
  assert.equal(next.lastEventId, 42);
  assert.deepEqual(next.events, ["notification"]);
});

test("reconnectDelay: 3s doubling, capped at 60s", () => {
  assert.deepEqual([0, 1, 2, 3, 4, 5, 10].map(reconnectDelay), [3000, 6000, 12000, 24000, 48000, 60000, 60000]);
});

test("shouldRetryStream: an unrouted stream (BLK-005) is not retried; transient failures are", () => {
  assert.equal(shouldRetryStream(404), false);
  assert.equal(shouldRetryStream(501), false);
  assert.equal(shouldRetryStream(503), true);
  assert.equal(shouldRetryStream(401), true); // the next attempt reads the current (refreshed) token
  assert.equal(shouldRetryStream(undefined), true); // network error
});

test("LIVE_STATUS_TEXT: unavailable state states the polling fallback that actually exists", () => {
  assert.match(LIVE_STATUS_TEXT.unavailable, /refreshes every 30 seconds/);
});

// ---- wiring (node --test cannot render JSX; check the source) ----

test("Feed: count starts unknown, polls are single-flight and latest-only, interval unchanged (30s)", () => {
  const s = src("./useNotificationFeed.js");
  assert.match(s, /const \[unreadCount, setUnreadCount\] = useState\(null\);/);
  assert.match(s, /const POLL_MS = 30000;/);
  // Overlapping requests are coalesced, not dropped: a refresh asked for mid-flight (e.g. by a live
  // event) runs once afterwards. Found by the browser check — dropping it left the count stale.
  assert.match(s, /if \(!pollGuard\.current\.enter\(\)\) \{ refreshQueued\.current = true; return; \}/);
  assert.match(s, /if \(refreshQueued\.current\) \{ refreshQueued\.current = false; refreshCount\(\); \}/);
  assert.match(s, /if \(gate\.current\.isCurrent\(token\)\) setCountError\(e\);/);
  assert.doesNotMatch(s, /catch \{\s*\/\/ Ignore/);
});

test("Feed: stream uses the current token each attempt, bounded backoff, stops on unrouted, cleans up", () => {
  const s = src("./useNotificationFeed.js");
  const connect = s.slice(s.indexOf("const connect = async"), s.indexOf("if (typeof window"));
  assert.match(connect, /const token = getAuth\(\)\?\.access;/, "token read per attempt");
  assert.match(connect, /retry = shouldRetryStream\(resp\.status\);/);
  assert.match(connect, /if \(!retry\) \{ setLiveStatus\("unavailable"\); return; \}/);
  assert.match(connect, /setTimeout\(connect, reconnectDelay\(attempt\)\)/);
  assert.match(connect, /Authorization: `Bearer \$\{token\}`/, "token stays in a header, not the URL");
  assert.doesNotMatch(connect, /[?&](token|access)=/);
  assert.match(s, /active = false;\s*clearInterval\(poll\);\s*if \(timer\) clearTimeout\(timer\);\s*if \(abort\) abort\.abort\(\);/);
  assert.match(s, /\}, \[refreshCount\]\);/, "stream effect does not re-run when a panel opens or closes");
});

test("Bell: no fake empty list, reads/settings applied only after success, single-flight, keyboard items", () => {
  const s = src("./NotificationsBell.jsx");
  assert.match(s, /list\.status === "ready" && list\.items\.length === 0 && <div className="notif-state">No notifications yet\.<\/div>/);
  const mark = s.slice(s.indexOf("const markAsRead = async"), s.indexOf("const markAllRead"));
  assert.ok(mark.indexOf("await api(`/notifications/${n.id}/read/`") < mark.indexOf("applyRead("), "read applied after the POST");
  assert.match(mark, /if \(n\.is_read \|\| !guard\.current\.enter\(\)\) return false;/);
  const pref = s.slice(s.indexOf("const updatePref = async"), s.indexOf("const count = unreadCount"));
  assert.ok(pref.indexOf('api("/notifications/preferences/", { method: "PATCH", body: { [key]: val } })') < pref.indexOf("setPrefs("), "no optimistic preference change");
  assert.match(pref, /setSavedNote\("Saved\."\)/);
  assert.doesNotMatch(s, /role="button"/, "items are native buttons");
  assert.match(s, /aria-expanded=\{isOpen\}/);
  assert.match(s, /aria-label=\{label\}/);
});

test("App: one shared feed wraps the shell, and phones reach notifications from the More sheet", () => {
  const s = src("../App.jsx");
  assert.match(s, /<NotificationsProvider>/);
  assert.match(s, /<NotificationsBell navigate=\{navigate\} variant="sheet" onNavigate=\{onClose\} \/>/);
});
