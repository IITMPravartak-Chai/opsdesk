// Administration pages (U7-A2 Teams and Users, U7-A3 SLA configuration): who sees which page, the settings
// sub-navigation, honest error messages and form helpers. Pure functions so they can be unit tested.
// The server re-checks every rule on every request; these only avoid offering actions it would refuse.

import { STAFF_ROLES, isAdminRole } from "../roles.js";

export const ROLE_LABELS = { super_admin: "Super admin", manager: "Manager", team_lead: "Team lead", employee: "Employee", customer: "Customer" };
export const ROLE_OPTIONS = ["super_admin", "manager", "team_lead", "employee", "customer"];
/** Roles that may be given to someone: the single super admin account is never assigned or taken away here. */
export const ASSIGNABLE_ROLES = ROLE_OPTIONS.filter((r) => r !== "super_admin");
export const isSuperAdmin = (user) => user?.role === "super_admin";
export { STAFF_ROLES };
export const STRATEGY_LABELS = { manual: "Manual", round_robin: "Round robin", least_loaded: "Least loaded" };

export const canManageUsers = (user) => isAdminRole(user);
export const canManageTeams = canManageUsers;
export const canManageSla = (user) => isAdminRole(user);

/** Tabs across the Administration pages, in display order, for this user. */
export function settingsTabs(user) {
  return [
    canManageUsers(user) && { key: "users", to: "/settings/users", label: "Users" },
    canManageTeams(user) && { key: "teams", to: "/settings/teams", label: "Teams" },
    canManageSla(user) && { key: "sla", to: "/settings/sla", label: "SLA" },
    canManageUsers(user) && { key: "config", to: "/settings/config", label: "Configuration" },
    isAdminRole(user) && { key: "audit-log", to: "/settings/audit-log", label: "Audit log" },
  ].filter(Boolean);
}

/** Where "Administration" leads for this user, or null when they have no administration page. */
export const settingsHome = (user) => settingsTabs(user)[0]?.to || null;

/** "/settings/users/12" → { section: "users", kind: null, id: 12 }; "/settings/sla/hours/3" → kind "hours". */
export function settingsRoute(path) {
  const m = /^\/settings\/(users|teams|sla|audit-log)(?:\/(?:(hours)\/)?(\d+))?\/?$/.exec(path || "");
  if (!m || (m[2] && m[1] !== "sla")) return null;
  return { section: m[1], kind: m[2] || null, id: m[3] ? Number(m[3]) : null };
}

/** Request headers for a write against the version last loaded. */
export const ifMatch = (version) => (version ? { "If-Match": `"${version}"` } : {});

/** Message for a failed write. It never implies the change was saved. */
export function writeError(err, noun = "item") {
  if (!err || typeof err.status !== "number") return "Couldn't save. Check your connection and try again.";
  switch (err.status) {
    case 412: return `This ${noun} was changed by someone else. Reload to see the latest version, then make your change again.`;
    case 428: return `Reload the ${noun} and try again.`;
    case 401: return "Your session ended. Sign in again.";
    case 403: return err.message || "You don't have permission to make this change.";
    case 404: return `This ${noun} no longer exists or isn't in your workspace.`;
    case 400: case 409: return err.message || `The ${noun} couldn't be saved.`;
    case 422: return "Some values weren't accepted. Check the form and try again.";
    default: return `Couldn't save the ${noun} (error ${err.status}). Try again.`;
  }
}

/** Message for a failed load, distinguishing a permission refusal from an outage. */
export function loadError(err, noun) {
  if (err?.status === 403) return { status: "forbidden" };
  if (err?.status === 404) return { status: "missing", message: `This ${noun} doesn't exist or isn't in your workspace.` };
  return { status: "error", message: `The ${noun} couldn't be loaded.` };
}

export const roleLabel = (role) => ROLE_LABELS[role] || role || "—";

/** Query string for the user list. */
export function buildUserQuery({ q = "", role = "", status = "", page = 1, pageSize = 50 } = {}) {
  const p = new URLSearchParams();
  if (q.trim()) p.set("q", q.trim().slice(0, 100));
  if (ROLE_OPTIONS.includes(role)) p.set("role", role);
  if (status === "active") p.set("is_active", "true");
  if (status === "inactive") p.set("is_active", "false");
  p.set("page", String(page));
  p.set("page_size", String(pageSize));
  return p.toString();
}

/** Only the fields the form changed, limited to those the server says this caller may edit. */
export function userChanges(original, draft, editable) {
  const out = {};
  for (const f of editable || []) {
    if (!(f in draft)) continue;
    const a = original[f] ?? null;
    const b = draft[f] ?? null;
    if (JSON.stringify(a) !== JSON.stringify(b)) out[f] = b;
  }
  return out;
}

/** Form state from a user detail response (ids flattened to *_id, skills as text). */
export function userDraft(u) {
  return {
    first_name: u.first_name || "", last_name: u.last_name || "", job_title: u.job_title || "", phone: u.phone || "",
    timezone: u.timezone || "", department_id: u.department?.id ?? null, account_id: u.account?.id ?? null,
    manager_id: u.manager?.id ?? null, is_available: Boolean(u.is_available), skills: [...(u.skills || [])], role: u.role,
  };
}

export const parseSkills = (text) => {
  const seen = new Set();
  return (text || "").split(",").map((s) => s.trim()).filter((s) => s && !seen.has(s.toLowerCase()) && seen.add(s.toLowerCase()));
};

// ---------------------------------------------------------------- SLA
export const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

/** "1 d 3 h", "4 h", "45 min". */
export function formatMinutes(m) {
  if (m === null || m === undefined || m === "") return "—";
  const n = Number(m);
  if (!Number.isFinite(n)) return "—";
  const d = Math.floor(n / 1440);
  const h = Math.floor((n % 1440) / 60);
  const min = n % 60;
  const parts = [d && `${d} d`, h && `${h} h`, min && `${min} min`].filter(Boolean);
  return parts.length ? parts.join(" ") : "0 min";
}

/** Editable rows per weekday from a stored schedule. */
export function scheduleRows(schedule) {
  return DAYS.map((label, i) => ({
    day: i, label,
    ranges: ((schedule || {})[String(i)] || []).map(([start, end]) => ({ start, end })),
  }));
}

/** Stored schedule from the editable rows (days without periods are left out). */
export function toSchedule(rows) {
  const out = {};
  for (const r of rows) if (r.ranges.length) out[String(r.day)] = r.ranges.map((x) => [x.start, x.end]);
  return out;
}

const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;
const mins = (t, end = false) => { const v = Number(t.slice(0, 2)) * 60 + Number(t.slice(3)); return end && v === 0 ? 1440 : v; };

/** The same checks the server makes, so most mistakes are explained before saving. "" when valid. */
export function checkSchedule(rows, is24x7) {
  let total = 0;
  for (const r of rows) {
    const spans = [];
    for (const x of r.ranges) {
      if (!HHMM.test(x.start || "") || !HHMM.test(x.end || "")) return `${r.label}: enter times as HH:MM.`;
      const a = mins(x.start);
      const b = mins(x.end, true);
      if (a >= b) return `${r.label}: each working period must end after it starts.`;
      spans.push([a, b]);
      total += b - a;
    }
    spans.sort((p, q) => p[0] - q[0]);
    for (let i = 1; i < spans.length; i += 1) if (spans[i][0] < spans[i - 1][1]) return `${r.label}: working periods overlap.`;
  }
  if (!is24x7 && total === 0) return "Add at least one working period, or mark these hours as 24x7.";
  return "";
}

export function checkTargets(rows) {
  for (const r of rows) {
    const set = r.first_response_minutes !== "" && r.first_response_minutes !== null;
    const setR = r.resolution_minutes !== "" && r.resolution_minutes !== null;
    if (!set && !setR) continue;
    if (!set || !setR) return `${r.label}: enter both targets, or leave both empty.`;
    const a = Number(r.first_response_minutes);
    const b = Number(r.resolution_minutes);
    if (![a, b].every((v) => Number.isInteger(v) && v >= 1 && v <= 527040)) return `${r.label}: targets must be whole minutes between 1 and 527,040.`;
    if (a > b) return `${r.label}: first response can't be longer than resolution.`;
  }
  return "";
}

/** Target rows for the API (rows left empty are removed from the policy). */
export const toTargets = (rows) => rows
  .filter((r) => r.first_response_minutes !== "" && r.first_response_minutes !== null)
  .map((r) => ({ priority_id: r.priority_id, first_response_minutes: Number(r.first_response_minutes), resolution_minutes: Number(r.resolution_minutes) }));

const plural = (n, one, many) => `${n.toLocaleString()} ${n === 1 ? one : many}`;

/** Sentences for the confirmation dialog, from the server's impact preview. */
export function impactLines(impact, noun = "policy") {
  if (!impact) return [];
  const lines = [`${plural(impact.active_tickets, "open ticket is", "open tickets are")} being timed against this ${noun} now${impact.paused_tickets ? `, and ${plural(impact.paused_tickets, "paused ticket", "paused tickets")} will use it when resumed` : ""}.`];
  if (impact.to_at_risk) lines.push(`${plural(impact.to_at_risk, "ticket", "tickets")} will be marked at risk at the next SLA check (within about a minute), and the assignees will be notified.`);
  if (impact.to_on_track) lines.push(`${plural(impact.to_on_track, "at-risk ticket", "at-risk tickets")} will return to on track.`);
  if (!impact.to_at_risk && !impact.to_on_track) lines.push("No ticket changes status straight away; later checks use the new setting.");
  lines.push("Due dates already set on existing tickets won't move.");
  if (impact.sampled) lines.push("Status changes were counted from the first 5,000 tickets.");
  return lines;
}

/** Body for creating a user from the Add user form: trimmed text, the account as a number or null. */
export function newUserPayload(form) {
  return {
    email: String(form.email || "").trim().toLowerCase(),
    first_name: String(form.first_name || "").trim(),
    last_name: String(form.last_name || "").trim(),
    role: form.role,
    account_id: form.role === "customer" && form.account_id ? Number(form.account_id) : null, // only customers belong to a company
    team_id: form.role === "employee" && form.team_id ? Number(form.team_id) : null, // employees are created into a team
    password: form.password || "",
  };
}

/** What is missing from the Add user form, as one sentence, or "" when it can be sent. The server re-checks everything. */
export function newUserProblem(form) {
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(String(form.email || "").trim())) return "Enter a valid email address.";
  if (!String(form.first_name || "").trim()) return "Enter a first name.";
  if (!ASSIGNABLE_ROLES.includes(form.role)) return "Choose a role.";
  if (form.role === "employee" && !form.team_id) return "Choose the employee's team.";
  if (form.role === "customer" && !form.account_id) return "Choose the customer's company.";
  if (String(form.password || "").length < 12) return "The password needs at least 12 characters.";
  return "";
}

/** A random 16-character password (letters and digits, no look-alikes) that meets the password policy. */
export function generatePassword(random = (n) => crypto.getRandomValues(new Uint32Array(n))) {
  const chars = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
  return Array.from(random(16), (n) => chars[n % chars.length]).join("");
}
