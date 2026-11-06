// Display helpers for the super admin dashboard. Pure functions so they can be unit tested.

/** "3 of 4" as a whole percentage; a dash when there is nothing to divide by. */
export const pct = (part, whole) => (whole ? `${Math.round((part / whole) * 100)}%` : "-");

/** "admin.categories.update" -> "Admin categories update". */
export function actionLabel(action) {
  const text = String(action || "").replace(/[._]+/g, " ").trim();
  return text ? text[0].toUpperCase() + text.slice(1) : "Unknown";
}

/** ISO timestamp as a short local date and time; empty input gives a dash. */
export function fmtWhen(iso) {
  const d = iso ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return "-";
  return d.toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}
