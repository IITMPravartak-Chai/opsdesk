export function relTime(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  const abs = Math.abs(s);
  const fmt = abs < 60 ? "just now" : abs < 3600 ? `${Math.round(abs / 60)}m` : abs < 86400 ? `${Math.round(abs / 3600)}h` : `${Math.round(abs / 86400)}d`;
  if (fmt === "just now") return fmt;
  return s >= 0 ? `${fmt} ago` : `in ${fmt}`;
}

export function duration(ms) {
  const m = Math.round(Math.abs(ms) / 60000);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ${m % 60 ? `${m % 60}m` : ""}`.trim();
  return `${Math.round(h / 24)}d`;
}

export const dateTime = (iso) => iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "-";

export const SLA_LABEL = { on_track: "On track", at_risk: "At risk", breached: "Breached", paused: "Paused", met: "Met", none: "No SLA" };

export const SOURCE_LABEL = { portal: "Portal", mobile: "Mobile", email: "Email", whatsapp: "WhatsApp", webhook: "Alert", phone: "Phone", api: "API", other: "Other" };

export function initials(name = "") {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]?.toUpperCase()).join("");
}

export function cx(...parts) { return parts.filter(Boolean).join(" "); }
