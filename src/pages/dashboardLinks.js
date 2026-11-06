/** Where a Dashboard figure leads: the ticket list, narrowed to the tickets that figure counts.
 * `view` is a queue (open, sla, done...); the other keys are extra filters the list understands.
 * `all` lists tickets in every state (for figures that also count resolved and closed ones). */
export function ticketsLink(view, filters = {}) {
  const p = new URLSearchParams({ view });
  for (const [k, v] of Object.entries(filters)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  return `/tickets?${p}`;
}

/** The extra list filters carried in a /tickets URL, as API query parameters. */
export const LIST_FILTER_KEYS = ["team", "source", "sla_status", "ordering"];

export function urlListFilters(search) {
  const out = {};
  for (const k of LIST_FILTER_KEYS) {
    const v = search.get(k);
    if (v) out[k] = v;
  }
  return out;
}

/** Queue parameters with the URL filters applied. With `all=1` the "open" and "state" limits of the queue are dropped. */
export function applyListFilters(viewParams, search) {
  const out = { ...viewParams, ...urlListFilters(search) };
  if (search.get("all") === "1") { delete out.open; delete out.state_category; }
  return out;
}

export const idByName = (rows, name) => (rows || []).find((r) => r.name === name)?.id;
