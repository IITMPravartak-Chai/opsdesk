import { isAdminRole } from "../roles.js";
// Audit log page (U7-A1): query building, time display and change rendering. Pure functions so they can be tested.

/** Who may open the page. Mirrors the server rule (managers only); the server enforces it on every request. */
export const canReadAuditLog = (user) => isAdminRole(user);

/** What the log covers today. Kept next to the code that writes it, so it is honest about the gaps. */
export const COVERAGE = {
  recorded: [
    "Sign-ins",
    "Two-step sign-in being turned on",
    "Ticket field changes (including applied AI triage)",
    "Major incident declared, demoted or updated",
    "Changes made on the Administration pages: users, teams, SLA policies and business hours (U7)",
  ],
  notRecorded: [
    "Changes made in Configuration (the separate admin site), including users, teams, SLA and every other setting",
    "Ticket status changes, replies, notes, attachments, links and merges (see each ticket's activity instead). Some older status changes may appear as ticket.transition from an earlier interface",
    "Failed sign-in attempts and approvals",
  ],
};

export const PAGE_SIZE = 50;

/** Local calendar date (YYYY-MM-DD from <input type="date">) to the UTC instant of that local midnight. */
export function localDayStartIso(day, plusDays = 0) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day || "");
  if (!m) return null;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]) + plusDays);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

const OBJECT_TYPE = /^[A-Za-z][A-Za-z0-9_]{0,79}$/;
const OBJECT_ID = /^[A-Za-z0-9_-]{1,64}$/;

/** Validate the filter form. Returns { query, error } — never sends a value the server would reject. */
export function buildQuery(filters, page = 1) {
  const q = new URLSearchParams();
  const f = filters || {};
  if (f.action) q.set("action", f.action);
  if (f.actor) q.set("actor", String(f.actor.id));
  if (f.objectType) {
    if (!OBJECT_TYPE.test(f.objectType.trim())) return { query: null, error: "Target type can contain only letters, numbers and underscores." };
    q.set("object_type", f.objectType.trim());
  }
  if (f.objectId) {
    if (!OBJECT_ID.test(f.objectId.trim())) return { query: null, error: "Target ID can contain only letters, numbers, - and _." };
    q.set("object_id", f.objectId.trim());
  }
  const since = f.from ? localDayStartIso(f.from) : null;
  const until = f.to ? localDayStartIso(f.to, 1) : null; // "to" is inclusive: up to the end of that day
  if (f.from && !since) return { query: null, error: "From date isn't valid." };
  if (f.to && !until) return { query: null, error: "To date isn't valid." };
  if (since && until && until <= since) return { query: null, error: "To date must be on or after From date." };
  if (since && until && new Date(until) - new Date(since) > 366 * 864e5) return { query: null, error: "Choose a range of one year or less." };
  if (since) q.set("since", since);
  if (until) q.set("until", until);
  q.set("page", String(page));
  q.set("page_size", String(PAGE_SIZE));
  return { query: q.toString(), error: "" };
}

export const hasFilters = (f) => Boolean(f && (f.action || f.actor || f.objectType || f.objectId || f.from || f.to));

/** "23 Oct 2026, 11:42 IST" in the viewer's time zone; the exact UTC value goes in the title attribute. */
export function formatTimestamp(iso, locale) {
  const d = new Date(iso);
  if (!iso || Number.isNaN(d.getTime())) return { text: "Unknown time", title: "" };
  const text = new Intl.DateTimeFormat(locale, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZoneName: "short" }).format(d);
  return { text, title: `${d.toISOString().replace(".000Z", "Z")} (UTC)` };
}

export const viewerTimeZone = () => {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "your local time zone"; } catch { return "your local time zone"; }
};

const show = (v) => {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
};

/** Rows for the detail panel. A stored [old, new] pair becomes from/to; anything else is shown as a value. */
export function changeRows(changes) {
  if (!changes || typeof changes !== "object" || Array.isArray(changes)) return [];
  return Object.entries(changes).map(([field, v]) =>
    Array.isArray(v) && v.length === 2 ? { field, from: show(v[0]), to: show(v[1]) } : { field, from: null, to: show(v) });
}

const OBJECT_PAGES = { Ticket: "/tickets/", User: "/settings/users/", Team: "/settings/teams/", SLAPolicy: "/settings/sla/", BusinessHours: "/settings/sla/hours/" };

/** Link target for an event's object, only where a page for it exists. */
export const objectLink = (e) => (OBJECT_PAGES[e?.object_type] && /^\d+$/.test(e.object_id || "") ? `${OBJECT_PAGES[e.object_type]}${e.object_id}` : null);

/** "Showing 51–100 of 312". */
export function rangeLabel(page, count, size = PAGE_SIZE) {
  if (!count) return "No events";
  const start = (page - 1) * size + 1;
  return `Showing ${start}–${Math.min(page * size, count)} of ${count}`;
}

export const lastPage = (count, size = PAGE_SIZE) => Math.max(1, Math.ceil((count || 0) / size));
