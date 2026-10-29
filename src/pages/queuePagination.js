import React from "react";

export const PAGE_SIZE = 50;
/** The server refuses more tickets than this in one bulk action. */
export const BULK_LIMIT = 100;

export function buildTicketQueryParams({ viewParams = {}, q = "", account = "", priority = "", page = 1 } = {}) {
  const params = new URLSearchParams({ ...viewParams });
  if (q && q.trim()) params.set("q", q.trim());
  if (account) params.set("account", account);
  if (priority) params.set("priority", priority);
  if (page > 1) params.set("page", String(page));
  return params;
}

/** The list is one scrolling column: the server still sends it in pages of PAGE_SIZE, so "load more" appends the next
 * page. This fetches pages 1..upTo and merges them in order, without repeats (a ticket updated between two requests can
 * move to a later page and would otherwise appear twice). `next` null means there are no more pages. */
export async function loadTicketPages(fetchPage, upTo) {
  const seen = new Set();
  const results = [];
  let last = null;
  for (let p = 1; p <= Math.max(1, upTo); p++) {
    const part = await fetchPage(p);
    last = part;
    for (const t of part.results || []) {
      if (!seen.has(t.id)) { seen.add(t.id); results.push(t); }
    }
    if (!part.next) break;
  }
  return { ...last, results };
}

/** Footer of the list: how many are shown, and a button for the next batch. Nothing is rendered once every ticket is
 * loaded (the header already says how many there are). */
export function QueueLoadMore({ shown, total, loading, onLoadMore }) {
  if (total === null || total <= 0 || shown >= total) return null;
  return React.createElement(
    "div",
    { className: "queue-foot", role: "navigation", "aria-label": "Load more tickets" },
    React.createElement("span", { className: "muted small", role: "status" }, `Showing ${shown} of ${total}`),
    React.createElement(
      "button",
      { type: "button", className: "btn sm", disabled: Boolean(loading), onClick: onLoadMore },
      loading ? "Loading…" : "Load more",
    ),
  );
}

/** Add or remove one ticket from a selection, never beyond the bulk limit. */
export function toggleSelected(prev, id, limit = BULK_LIMIT) {
  if (prev.includes(id)) return { ids: prev.filter((x) => x !== id), limited: false };
  if (prev.length >= limit) return { ids: prev, limited: true };
  return { ids: [...prev, id], limited: false };
}

/** "Select all": every loaded ticket, up to the bulk limit. */
export function selectAllLoaded(results, limit = BULK_LIMIT) {
  const ids = results.map((t) => t.id);
  return { ids: ids.slice(0, limit), limited: ids.length > limit };
}
