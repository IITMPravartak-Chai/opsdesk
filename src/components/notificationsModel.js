// Notification and live-update helpers (U5, UI-DEC-016). Framework-free so `node --test` covers them.
// They encode the existing contract only: GET/POST paths, payloads and the SSE line format are unchanged.

/** The server has no route for this request (BLK-004/005 on the served gateway), so nothing ran. */
export const routeMissing = (status) => [404, 405, 501].includes(status);

/** Unread count is `null` until it has actually loaded — a failed request must never read as zero. */
export function unreadLabel(count) {
  if (count === null || count === undefined) return "Notifications (unread count unavailable)";
  return `Notifications (${count} unread)`;
}

export function badgeText(count) {
  if (!Number.isFinite(count) || count <= 0) return null;
  return count > 99 ? "99+" : String(count);
}

export function notificationsErrorMessage(err) {
  if (routeMissing(err?.status)) return "Notifications aren't available on this server yet.";
  return `Notifications couldn't be loaded: ${err?.message || "request failed"}.`;
}

export function preferencesErrorMessage(err) {
  if (routeMissing(err?.status)) return "Notification settings aren't available on this server yet.";
  return `Settings couldn't be loaded: ${err?.message || "request failed"}.`;
}

/** Destination for a notification, only when it names a ticket the existing route can open. */
export function notificationTarget(n) {
  const id = n?.ticket;
  return Number.isInteger(id) && id > 0 ? `/tickets/${id}` : null;
}

export const applyRead = (list, id) => (list || []).map((n) => (n.id === id ? { ...n, is_read: true } : n));
export const applyAllRead = (list) => (list || []).map((n) => ({ ...n, is_read: true }));

/** Preference fields of the existing contract (NotificationPreferenceSerializer), in display order. */
export const PREFERENCE_FIELDS = [
  { key: "in_app_enabled", label: "In-app notifications" },
  { key: "email_enabled", label: "Email notifications" },
  { key: "notify_on_assignment", label: "Ticket assignment", dependsOnInApp: true },
  { key: "notify_on_replies", label: "Replies and status changes", dependsOnInApp: true },
  { key: "notify_on_approvals", label: "Approvals", dependsOnInApp: true },
  { key: "notify_on_sla", label: "SLA risk and breach", dependsOnInApp: true },
];

// ---- event stream (existing format: "id: N" / "event: notification|connected" / "data: …" lines) ----

/**
 * Consume complete lines from a buffer. Returns the last event id seen, the event names in order,
 * and the unfinished remainder to carry into the next read.
 */
export function parseStreamChunk(buffer) {
  const lines = buffer.split("\n");
  const rest = lines.pop();
  const events = [];
  let lastEventId = null;
  for (const raw of lines) {
    const line = raw.trim();
    if (line.startsWith("id:")) {
      const id = parseInt(line.slice(3).trim(), 10);
      if (!Number.isNaN(id)) lastEventId = id;
    } else if (line.startsWith("event:")) {
      events.push(line.slice(6).trim());
    }
  }
  return { lastEventId, events, rest };
}

/** Reconnect delay: 3s, 6s, 12s … capped at 60s, so a dead stream is retried quietly. */
export const reconnectDelay = (attempt) => Math.min(60000, 3000 * 2 ** Math.max(0, attempt));

/** Whether a failed stream attempt should be retried at all. Unrouted endpoints are not retried. */
export const shouldRetryStream = (status) => !routeMissing(status);

export const LIVE_STATUS_TEXT = {
  idle: "",
  connecting: "Connecting to live updates…",
  connected: "Live updates on.",
  reconnecting: "Live updates interrupted. Reconnecting…",
  unavailable: "Live updates aren't available. The unread count refreshes every 30 seconds.",
};
