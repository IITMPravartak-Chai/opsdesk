// Configuration console: which sections exist, how paths map to
// them, and how form values become API payloads. Pure functions so they can be unit tested.
// The server validates every write; these helpers only shape input and explain refusals.

export const CONFIG_GROUPS = [
  { key: "service", label: "Service desk", sections: ["workflows", "ticket-types", "priorities", "priority-matrix", "categories", "rules"] },
  { key: "people", label: "Customers and content", sections: ["accounts", "contact-identities", "catalog-items", "canned-responses"] },
  { key: "integrations", label: "Integrations", sections: ["channels", "inbound-messages"] },
  { key: "cmdb", label: "Configuration management", sections: ["services", "items"] },
];

/** Configuration groups without the sections of switched-off features (the section definitions below stay). */
export function visibleConfigGroups(features) {
  const off = new Set([...(features.catalog ? [] : ["catalog-items"]), ...(features.cmdb ? [] : ["services", "items"])]);
  return CONFIG_GROUPS.map((g) => ({ ...g, sections: g.sections.filter((s) => !off.has(s)) })).filter((g) => g.sections.length > 0);
}

// columns: [key, label]; fk columns read "<name>_label" from the row.
export const SECTIONS = {
  workflows: { label: "Workflows", blurb: "Statuses, transitions and who may perform them.", noun: "workflow",
    columns: [["name", "Name"], ["state_count", "Statuses"], ["transition_count", "Transitions"]], detail: true },
  "ticket-types": { label: "Ticket types", blurb: "Incident, request, problem, change… with their workflow, number prefix and custom fields.", noun: "ticket type",
    columns: [["name", "Name"], ["key", "Key"], ["prefix", "Prefix"], ["workflow_label", "Workflow"], ["is_active", "Active"]], detail: true },
  priorities: { label: "Priorities", blurb: "P1–P4 names, levels and colours.", noun: "priority",
    columns: [["key", "Key"], ["name", "Name"], ["level", "Level"], ["color", "Colour"]] },
  "priority-matrix": { label: "Priority matrix", blurb: "Which priority an impact × urgency combination gets.", special: true },
  categories: { label: "Categories", blurb: "Ticket categories and the team they route to.", noun: "category",
    columns: [["path", "Category"], ["ticket_type_label", "Ticket type"], ["default_team_label", "Default team"]] },
  rules: { label: "Automation rules", blurb: "When an event happens and conditions match, run actions.", noun: "rule",
    columns: [["order", "Order"], ["name", "Name"], ["event", "Event"], ["is_active", "Active"], ["run_count", "Runs"]] },
  accounts: { label: "Accounts", blurb: "Customer organisations, their tier, e-mail domains, SLA policy and hours.", noun: "account",
    columns: [["name", "Name"], ["code", "Code"], ["tier", "Tier"], ["user_count", "Users"], ["is_active", "Active"]] },
  "contact-identities": { label: "Contact identities", blurb: "E-mail addresses and phone numbers mapped to a user.", noun: "identity",
    columns: [["kind", "Kind"], ["value", "Value"], ["user_label", "User"]] },
  "catalog-items": { label: "Service catalog", blurb: "Offerings employees can request, with their form and approval.", noun: "catalog item",
    columns: [["name", "Name"], ["ticket_type_label", "Ticket type"], ["requires_approval", "Approval"], ["is_active", "Active"]] },
  "canned-responses": { label: "Canned responses", blurb: "Reusable replies with placeholders.", noun: "canned response",
    columns: [["title", "Title"], ["ticket_type_label", "Ticket type"], ["owner_label", "Owner"]] },
  channels: { label: "Channels", blurb: "E-mail, IMAP, WhatsApp and monitoring webhooks, with their credentials and routing.", noun: "channel",
    columns: [["name", "Name"], ["kind", "Kind"], ["is_active", "Active"], ["oauth_status", "OAuth"]], detail: true },
  "inbound-messages": { label: "Inbound messages", blurb: "The delivery outbox: see what failed and requeue it.", special: true },
  services: { label: "Services", blurb: "Business and technical services.", noun: "service",
    columns: [["name", "Name"], ["account_label", "Account"], ["owner_team_label", "Owner team"], ["criticality", "Criticality"], ["ci_count", "Items"], ["is_active", "Active"]] },
  items: { label: "Configuration items", blurb: "Applications, AI models, pipelines and platforms, with what they depend on.", noun: "configuration item",
    columns: [["name", "Name"], ["type_label", "Type"], ["environment", "Env"], ["status", "Status"], ["service_label", "Service"]] },
};

export const CHILD_RESOURCES = {
  workflows: [{ slug: "states", label: "Statuses", noun: "status", columns: [["order", "Order"], ["name", "Name"], ["key", "Key"], ["category", "Category"], ["is_initial", "Initial"], ["pauses_sla", "Pauses SLA"]] },
    { slug: "transitions", label: "Transitions", noun: "transition", columns: [["name", "Button"], ["from_state_label", "From"], ["to_state_label", "To"], ["requires_comment", "Comment"], ["requires_approval", "Approval"]] }],
  "ticket-types": [{ slug: "custom-fields", label: "Custom fields", noun: "custom field", columns: [["order", "Order"], ["label", "Label"], ["key", "Key"], ["field_type", "Type"], ["required", "Required"], ["visible_to_customer", "Customer sees"]] }],
};

/** "/settings/config/workflows/3" → { section: "workflows", id: 3 }; "/settings/config" → { section: null }. null if not a config path. */
export function configRoute(path) {
  const m = /^\/settings\/config(?:\/([a-z-]+)(?:\/(\d+))?)?\/?$/.exec(path || "");
  if (!m) return null;
  if (m[1] && !SECTIONS[m[1]]) return null;
  if (m[2] && !SECTIONS[m[1]]?.detail) return null;
  return { section: m[1] || null, id: m[2] ? Number(m[2]) : null };
}

export const configPath = (section, id) => `/settings/config${section ? `/${section}` : ""}${id ? `/${id}` : ""}`;
export const apiPath = (slug, parent) => `/admin/config/${parent ? `${parent.slug}/${parent.id}/` : ""}${slug}/`;

const LIST_KINDS = new Set(["str_list", "domain_list"]);
const JSON_KINDS = new Set(["json_list", "json_dict"]);

export const prettyJson = (v) => JSON.stringify(v ?? null, null, 2);

/** Text shown in an editor for a stored value. */
export function toInput(spec, value) {
  if (LIST_KINDS.has(spec.kind)) return Array.isArray(value) ? value.join(spec.choices ? "," : "\n") : "";
  if (JSON_KINDS.has(spec.kind)) return value === undefined || value === null ? (spec.kind === "json_list" ? "[]" : "{}") : prettyJson(value);
  if (spec.kind === "bool") return Boolean(value);
  if (spec.kind === "id_list") return Array.isArray(value) ? value : [];
  if (value === null || value === undefined) return spec.kind === "fk" ? "" : "";
  return value;
}

/** Initial editor values for a resource: the row's values when editing, empty/defaults when creating. */
export function initialValues(fields, row) {
  const out = {};
  for (const [name, spec] of Object.entries(fields)) {
    if (spec.read_only) continue;
    out[name] = toInput(spec, row ? row[name] : undefined);
    if (!row && spec.kind === "bool") out[name] = name === "is_active" || name === "customer_can_create" || name === "visible_to_customer" || name === "requires_approval" || name === "email_enabled";
    if (!row && spec.kind === "choice" && spec.choices?.length && !spec.nullable) out[name] = spec.choices[0];
  }
  return out;
}

const splitList = (text, spec) => String(text || "").split(spec.choices ? "," : /[\n,]/).map((s) => s.trim()).filter(Boolean);

/**
 * API payload from editor values. Returns { body, errors } where errors is a field→message map for input that can't be
 * sent at all (bad JSON, non-numeric number). Server-side validation covers everything else.
 * When editing, only changed fields are sent (so unchanged secrets/masked values are never re-sent needlessly).
 */
export function buildPayload(fields, values, { original = null, isAdmin = true } = {}) {
  const body = {};
  const errors = {};
  for (const [name, spec] of Object.entries(fields)) {
    if (spec.read_only || !(name in values)) continue;
    if (spec.admin_only && !isAdmin) continue;
    const raw = values[name];
    let v;
    if (spec.kind === "bool") v = Boolean(raw);
    else if (spec.kind === "int") {
      if (raw === "" || raw === null || raw === undefined) { if (spec.nullable) v = null; else if (original) continue; else continue; }
      else if (!/^-?\d+$/.test(String(raw).trim())) { errors[name] = "Enter a whole number."; continue; }
      else v = Number(raw);
    } else if (spec.kind === "fk") v = raw === "" || raw === null || raw === undefined ? null : Number(raw);
    else if (spec.kind === "id_list") v = (raw || []).map(Number);
    else if (LIST_KINDS.has(spec.kind)) v = splitList(raw, spec);
    else if (JSON_KINDS.has(spec.kind)) {
      try { v = JSON.parse(raw || (spec.kind === "json_list" ? "[]" : "{}")); } catch { errors[name] = "This isn't valid JSON."; continue; }
      if (spec.kind === "json_list" && !Array.isArray(v)) { errors[name] = "Enter a JSON list, e.g. [ … ]."; continue; }
      if (spec.kind === "json_dict" && (typeof v !== "object" || v === null || Array.isArray(v))) { errors[name] = "Enter a JSON object, e.g. { … }."; continue; }
    } else v = raw;
    if (original) {
      const before = original[name];
      if (JSON.stringify(v) === JSON.stringify(before === undefined ? null : before)) continue;
    } else if (v === "" && !spec.required) {
      continue;
    }
    body[name] = v;
  }
  return { body, errors };
}

/** Field errors from a failed write: { field: message } (empty when the response had none). */
export function fieldErrors(err) {
  const e = err?.data?.errors;
  return e && typeof e === "object" ? e : {};
}

const USAGE = {
  tickets: "tickets", ticket_types: "ticket types", catalog_items: "catalog items", categories: "categories", canned_responses: "canned responses",
  sla_targets: "SLA targets", priority_matrix_cells: "priority matrix cells", sub_categories: "sub-categories", users: "users",
  services: "services", configuration_items: "configuration items", knowledge_articles: "knowledge articles", policies: "SLA policies",
  accounts: "accounts",
};

/** "still used by 3 tickets and 2 catalog items" from a 409 `usage` map. */
export function usageSentence(usage) {
  const parts = Object.entries(usage || {}).filter(([, n]) => n).map(([k, n]) => `${n} ${USAGE[k] || k.replace(/_/g, " ")}`);
  if (!parts.length) return "";
  return `It is still used by ${parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : parts[0]}.`;
}

/** Human message for a failed configuration write. */
export function configWriteError(err, noun = "item") {
  if (!err || typeof err.status !== "number") return "Couldn't save. Check your connection and try again.";
  if (err.status === 409 && err.data?.usage) return `This ${noun} can't be deleted. ${usageSentence(err.data.usage)} Remove or change those first.`;
  if (err.status === 409 && err.data?.impact) return err.message;
  if (err.status === 400 && Object.keys(fieldErrors(err)).length) return "Fix the highlighted fields.";
  if (err.status === 403) return err.message || "You don't have permission to make this change.";
  if (err.status === 404) return `This ${noun} no longer exists or isn't in your workspace.`;
  if (err.status === 401) return "Your session ended. Sign in again.";
  return err.message || `Couldn't save the ${noun} (error ${err.status}).`;
}

/** Cell text for a list column. */
export function cellText(row, key) {
  const v = row[key];
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (v === null || v === undefined || v === "") return "—";
  if (Array.isArray(v)) return v.join(", ");
  return String(v);
}

/** Matrix cells → lookup, and back. */
export const matrixLookup = (cells) => Object.fromEntries(cells.map((c) => [`${c.impact}:${c.urgency}`, c.priority_id]));
export function matrixCells(lookup) {
  const out = [];
  for (let i = 1; i <= 4; i += 1) for (let u = 1; u <= 4; u += 1) if (lookup[`${i}:${u}`]) out.push({ impact: i, urgency: u, priority_id: Number(lookup[`${i}:${u}`]) });
  return out;
}
