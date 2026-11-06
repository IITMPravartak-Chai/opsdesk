import test from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
  BULK_LIMIT, PAGE_SIZE, QueueLoadMore, buildTicketQueryParams, loadTicketPages, selectAllLoaded, toggleSelected,
} from "./queuePagination.js";

// =============================================================================
// 1. Query Parameter & API Contract Tests
// =============================================================================

test("buildTicketQueryParams: omits page parameter on page 1 (default)", () => {
  const params = buildTicketQueryParams({
    viewParams: { open: "1", ordering: "due", assignee: "me" },
    page: 1,
  });
  assert.equal(params.get("open"), "1");
  assert.equal(params.get("ordering"), "due");
  assert.equal(params.get("assignee"), "me");
  assert.equal(params.has("page"), false);
});

test("buildTicketQueryParams: passes page parameter when page > 1", () => {
  const params = buildTicketQueryParams({
    viewParams: { open: "1", ordering: "updated" },
    page: 2,
  });
  assert.equal(params.get("open"), "1");
  assert.equal(params.get("ordering"), "updated");
  assert.equal(params.get("page"), "2");
});

test("buildTicketQueryParams: preserves search query, filters, and ordering across pages", () => {
  const params = buildTicketQueryParams({
    viewParams: { state_category: "open,in_progress", ordering: "due" },
    q: "database connection",
    account: "42",
    priority: "p1",
    page: 3,
  });
  assert.equal(params.get("state_category"), "open,in_progress");
  assert.equal(params.get("ordering"), "due");
  assert.equal(params.get("q"), "database connection");
  assert.equal(params.get("account"), "42");
  assert.equal(params.get("priority"), "p1");
  assert.equal(params.get("page"), "3");
});

test("buildTicketQueryParams: trims search query and omits empty filters", () => {
  const params = buildTicketQueryParams({
    viewParams: { open: "1" },
    q: "   ",
    account: "",
    priority: "",
    page: 1,
  });
  assert.equal(params.has("q"), false);
  assert.equal(params.has("account"), false);
  assert.equal(params.has("priority"), false);
  assert.equal(params.has("page"), false);
});

// =============================================================================
// 2. Loading: one scrolling list, fetched in pages and appended
// =============================================================================

const makeTickets = (n) => Array.from({ length: n }, (_, i) => ({ id: i + 1, number: `INC-${i + 1}` }));
const serverFor = (all) => async (p) => {
  const start = (p - 1) * PAGE_SIZE;
  if (start >= all.length && p > 1) { const e = new Error("Not found."); e.status = 404; throw e; }
  return { count: all.length, next: start + PAGE_SIZE < all.length ? `/api/tickets/?page=${p + 1}` : null, results: all.slice(start, start + PAGE_SIZE) };
};

test("loadTicketPages: page 1 only, then 'load more' appends page 2 and 3 with no gaps or repeats (125 tickets)", async () => {
  const all = makeTickets(125);
  const fetchPage = serverFor(all);
  const one = await loadTicketPages(fetchPage, 1);
  assert.equal(one.results.length, 50);
  assert.equal(one.count, 125);
  const two = await loadTicketPages(fetchPage, 2);
  assert.deepEqual(two.results.map((t) => t.id), all.slice(0, 100).map((t) => t.id));
  const three = await loadTicketPages(fetchPage, 3);
  assert.deepEqual(three.results.map((t) => t.id), all.map((t) => t.id));
  assert.equal(new Set(three.results.map((t) => t.id)).size, 125);
});

test("loadTicketPages: stops at the last page even if more were asked for; a short list is one request", async () => {
  let calls = 0;
  const base = serverFor(makeTickets(9));
  const out = await loadTicketPages(async (p) => { calls += 1; return base(p); }, 5);
  assert.equal(out.results.length, 9);
  assert.equal(calls, 1);
});

test("loadTicketPages: a ticket that moved between two requests is not listed twice", async () => {
  const a = [{ id: 1 }, { id: 2 }], b = [{ id: 2 }, { id: 3 }];
  const out = await loadTicketPages(async (p) => (p === 1 ? { count: 3, next: "x", results: a } : { count: 3, next: null, results: b }), 2);
  assert.deepEqual(out.results.map((t) => t.id), [1, 2, 3]);
});

test("loadTicketPages: a failing page surfaces its error (the caller decides how to recover)", async () => {
  // the list shrank after page 1 said there was a page 2
  const shrunk = async (p) => {
    if (p === 1) return { count: 60, next: "/api/tickets/?page=2", results: makeTickets(50) };
    const e = new Error("Not found."); e.status = 404; throw e;
  };
  await assert.rejects(() => loadTicketPages(shrunk, 2), (e) => e.status === 404);
});

// =============================================================================
// 3. Footer: nothing when everything is loaded, a clear 'load more' otherwise
// =============================================================================

const footer = (props) => renderToStaticMarkup(React.createElement(QueueLoadMore, { shown: 50, total: 125, loading: false, onLoadMore: () => {}, ...props }));

test("QueueLoadMore: renders nothing for an unknown, empty or fully loaded list (no 'Page 1 of 1')", () => {
  assert.equal(footer({ total: null }), "");
  assert.equal(footer({ total: 0, shown: 0 }), "");
  assert.equal(footer({ total: 9, shown: 9 }), "");
  assert.doesNotMatch(footer({ total: 9, shown: 9 }), /Page|Previous|Next/);
});

test("QueueLoadMore: shows progress and a button; the button is disabled while loading", () => {
  const html = footer({});
  assert.match(html, /Showing 50 of 125/);
  assert.match(html, />Load more</);
  assert.doesNotMatch(html, /disabled/);
  const busy = footer({ loading: true });
  assert.match(busy, /disabled/);
  assert.match(busy, /Loading…/);
});

// =============================================================================
// 4. Selection rules
// =============================================================================

test("selection never exceeds the bulk limit; unticking always works", () => {
  const full = Array.from({ length: BULK_LIMIT }, (_, i) => i + 1);
  assert.deepEqual(toggleSelected([], 7), { ids: [7], limited: false });
  assert.deepEqual(toggleSelected([7, 8], 7), { ids: [8], limited: false });
  assert.deepEqual(toggleSelected(full, 999), { ids: full, limited: true });
  assert.deepEqual(toggleSelected(full, 3).limited, false);
  const all = selectAllLoaded(makeTickets(150));
  assert.equal(all.ids.length, BULK_LIMIT);
  assert.equal(all.limited, true);
  assert.deepEqual(selectAllLoaded(makeTickets(9)), { ids: makeTickets(9).map((t) => t.id), limited: false });
});


// =============================================================================
// 5. Bulk results explain failures; the ticket list screen's switched-off and removed features
// =============================================================================

test("bulkResultMessage: says how many changed and, for the rest, why", async () => {
  const { bulkResultMessage } = await import("./workflowHelpers.js");
  assert.equal(bulkResultMessage({ succeeded: 3, failed: 0, results: {} }), "Bulk update: 3 tickets updated.");
  assert.equal(bulkResultMessage({ succeeded: 1, failed: 0 }), "Bulk update: 1 ticket updated.");
  const msg = bulkResultMessage({
    succeeded: 1, failed: 2,
    results: { 1: { success: true }, 2: { success: false, error: "Ticket not found or access denied." }, 3: { success: false, error: "Ticket not found or access denied." } },
  });
  assert.equal(msg, "Bulk update: 1 ticket updated. 2 couldn't be updated: Ticket not found or access denied.");
  assert.match(bulkResultMessage({ succeeded: 0, failed: 4, results: { a: { success: false, error: "e1" }, b: { success: false, error: "e2" }, c: { success: false, error: "e3" }, d: { success: false, error: "e4" } } }), /e1; e2; e3; …$/);
  assert.equal(bulkResultMessage({ succeeded: 0, failed: 2, results: {} }), "Bulk update: 0 tickets updated. 2 couldn't be updated.");
});

test("ticket list: Save Queue is switched off, the Tag bulk action is gone, Assign needs a choice, no page buttons", async () => {
  const { readFileSync } = await import("node:fs");
  const { FEATURES } = await import("../features.js");
  const page = readFileSync(new URL("./Queue.jsx", import.meta.url), "utf8");
  assert.equal(FEATURES.savedQueues, false);
  assert.match(page, /const savedQueuesOn = FEATURES\.savedQueues && user\.is_agent;/);
  assert.doesNotMatch(page, /setBulkModal\("tag"\)|Bulk Tag|bulkTag/, "the Tag bulk action is removed from the screen");
  assert.match(page, /disabled=\{bulkBusy \|\| !bulkTeam\}/, "Apply Assignment needs a team");
  assert.doesNotMatch(page, /QueuePagination|Previous|handlePageChange/, "no page buttons");
});

test("teamEmployees: only the chosen team's members are listed; nothing before a team is chosen", async () => {
  const { teamEmployees } = await import("./workflowHelpers.js");
  const teams = [{ id: 1, name: "L1", member_ids: [3, 4, 2] }, { id: 2, name: "Cloud", member_ids: [5] }, { id: 3, name: "Empty", member_ids: [] }];
  const agents = [{ id: 1, name: "A" }, { id: 2, name: "B" }, { id: 3, name: "C" }, { id: 4, name: "D" }, { id: 5, name: "E" }];
  assert.deepEqual(teamEmployees(teams, agents, "1").map((a) => a.id), [2, 3, 4]);
  assert.deepEqual(teamEmployees(teams, agents, 2).map((a) => a.id), [5]);
  assert.deepEqual(teamEmployees(teams, agents, "3"), []);
  assert.deepEqual(teamEmployees(teams, agents, ""), []);
  assert.deepEqual(teamEmployees(undefined, undefined, "1"), []);
});

test("the ticket list has no J/K keyboard shortcuts", async () => {
  const { readFileSync } = await import("node:fs");
  const page = readFileSync(new URL("./Queue.jsx", import.meta.url), "utf8");
  assert.doesNotMatch(page, /keydown|focusedIdx|J\/K/, "no key handler, no focused-row state, no hint");
});
