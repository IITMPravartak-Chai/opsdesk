// U6 Advanced Operations: pure helpers (unit-tested in opsHelpers.test.js).

// Keys the monitoring/webhook ingest writes to correlate alerts into one ticket
// (backend apps/channels/router.py). Nothing here is computed client-side.
export const ALERT_KEYS = ["alert_fingerprint", "alert_occurrences", "first_alert_at", "last_alert_at", "alert_status", "active_alerts"];

/** The server-side alert group stored on a ticket, or null when the ticket isn't alert-correlated. */
export function alertGroupFrom(customFields) {
  const cf = customFields || {};
  if (cf.alert_fingerprint === undefined && cf.alert_occurrences === undefined) return null;
  const occurrences = Number.parseInt(cf.alert_occurrences, 10);
  const active = Array.isArray(cf.active_alerts) ? cf.active_alerts.map(String) : [];
  return {
    fingerprint: cf.alert_fingerprint ? String(cf.alert_fingerprint) : null,
    occurrences: Number.isFinite(occurrences) ? occurrences : null,
    firstAt: cf.first_alert_at || null,
    lastAt: cf.last_alert_at || null,
    status: cf.alert_status ? String(cf.alert_status) : null,
    active,
  };
}

/** Custom-field entries other than the alert keys (those are shown in the alert-group card). */
export const nonAlertEntries = (customFields) => Object.entries(customFields || {}).filter(([k]) => !ALERT_KEYS.includes(k));

// ---- Integrations (channel inventory) ----

/** Show where an inbound webhook URL points without revealing its secret path token. */
export function maskInboundUrl(url) {
  if (!url) return "";
  return String(url).replace(/(\/inbound\/)[^/]+(\/?)$/, "$1••••••••$2");
}

const ROUTING_LABELS = {
  ticket_type: "Ticket type", account_code: "Account", team: "Team", impact: "Impact", urgency: "Urgency",
  thread_window_hours: "Reply threading window (hours)",
};

/** Routing defaults from channel.settings, labelled; unknown keys are listed as-is. Values only, never config. */
export function routingRows(settings) {
  return Object.entries(settings || {})
    .filter(([, v]) => v !== null && v !== undefined && v !== "" && typeof v !== "object")
    .map(([k, v]) => [ROUTING_LABELS[k] || k, String(v)]);
}

// ---- Report exports ----

export const EXPORT_TYPES = { csv: "text/csv", pdf: "application/pdf" };

/** A server export is only a success if the server sent the file type that was asked for. */
export function exportContentProblem(format, contentType) {
  const want = EXPORT_TYPES[format];
  if (!want) return "Unsupported export format.";
  return String(contentType || "").toLowerCase().startsWith(want) ? "" : "The server did not return a report file. Nothing was downloaded.";
}

/** File name from Content-Disposition, falling back to a local name. */
export function exportFileName(disposition, fallback) {
  const m = /filename="?([^";]+)"?/i.exec(disposition || "");
  return m ? m[1] : fallback;
}
