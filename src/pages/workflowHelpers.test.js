import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  bulkErrorMessage,
  endpointUnavailable,
  inFlightGuard,
  latestOnly,
  normalizeInteractions,
  queueEmptyMessage,
  runTicketTransition,
  savedQueueErrorMessage,
  submitArticleVote,
} from "./workflowHelpers.js";

const err = (status, message = "boom") => Object.assign(new Error(message), { status });
const deferred = () => { let resolve, reject; const p = new Promise((res, rej) => { resolve = res; reject = rej; }); return { p, resolve, reject }; };
const src = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8");

// ---- ordering and guards ----

test("latestOnly: only the newest request token is current", () => {
  const g = latestOnly();
  const first = g.next();
  const second = g.next();
  assert.equal(g.isCurrent(first), false);
  assert.equal(g.isCurrent(second), true);
});

test("latestOnly: a slower, older search response cannot replace newer results", async () => {
  const g = latestOnly();
  const shown = [];
  const slow = deferred(), fast = deferred();
  const run = (p) => { const token = g.next(); return p.then((v) => { if (g.isCurrent(token)) shown.push(v); }); };
  const a = run(slow.p); // "pri" typed first
  const b = run(fast.p); // "printer" typed second
  fast.resolve("printer results");
  slow.resolve("pri results");
  await Promise.all([a, b]);
  assert.deepEqual(shown, ["printer results"]);
});

test("inFlightGuard: rejects re-entry until exit", () => {
  const g = inFlightGuard();
  assert.equal(g.enter(), true);
  assert.equal(g.busy, true);
  assert.equal(g.enter(), false);
  g.exit();
  assert.equal(g.enter(), true);
});

// ---- honest blocked/error wording ----

test("endpointUnavailable: 404/405/501 mean the route is not served", () => {
  for (const s of [404, 405, 501]) assert.equal(endpointUnavailable(err(s)), true, String(s));
  for (const s of [400, 401, 403, 500, 502, 503, undefined]) assert.equal(endpointUnavailable(err(s)), false, String(s));
});

test("savedQueueErrorMessage: unrouted endpoint is explained; other failures name the action", () => {
  assert.match(savedQueueErrorMessage("load", err(404)), /aren't available on this server/);
  assert.match(savedQueueErrorMessage("save", err(500, "Server error")), /Couldn't save this queue: Server error/);
  assert.match(savedQueueErrorMessage("delete", err(403, "Forbidden")), /Couldn't delete this saved queue: Forbidden/);
});

test("bulkErrorMessage: claims 'no tickets were changed' only when the route was not served", () => {
  assert.match(bulkErrorMessage(err(404)), /No tickets were changed/);
  const other = bulkErrorMessage(err(500, "Server error"));
  assert.doesNotMatch(other, /No tickets were changed/);
  assert.match(other, /may not have been updated/);
});

test("queueEmptyMessage: search, then filters, then role-specific wording", () => {
  assert.equal(queueEmptyMessage({ search: "vpn", filtered: true, isAgent: true }), "No tickets match that search.");
  assert.equal(queueEmptyMessage({ search: "", filtered: true, isAgent: true }), "No tickets match these filters.");
  assert.equal(queueEmptyMessage({ search: "", filtered: false, isAgent: true }), "This queue is clear.");
  assert.equal(queueEmptyMessage({ search: "", filtered: false, isAgent: false }), "You have no requests in this list.");
});

// ---- interactions shape ----

test("normalizeInteractions: paginated newest-first is reversed; plain lists are kept as-is", () => {
  assert.deepEqual(normalizeInteractions({ count: 5, results: [{ id: 3 }, { id: 2 }] }), { items: [{ id: 2 }, { id: 3 }], total: 5 });
  assert.deepEqual(normalizeInteractions([{ id: 1 }, { id: 2 }]), { items: [{ id: 1 }, { id: 2 }], total: 2 });
  assert.deepEqual(normalizeInteractions(null), { items: [], total: 0 });
});

// ---- ticket transitions ----

test("runTicketTransition: posts the existing contract and refreshes the conversation", async () => {
  const calls = [];
  const api = async (path, opts) => { calls.push([path, opts]); return { id: 7, state: { name: "Resolved" } }; };
  let reloaded = 0;
  const r = await runTicketTransition(api, 7, 3, { comment: "done" }, { guard: inFlightGuard(), reloadMessages: async () => { reloaded += 1; } });
  assert.deepEqual(calls, [["/tickets/7/transition/", { method: "POST", body: { transition_id: 3, comment: "done" } }]]);
  assert.equal(r.ok, true);
  assert.equal(r.ticket.id, 7);
  assert.equal(r.refreshError, null);
  assert.equal(reloaded, 1);
});

test("runTicketTransition: a server failure is a failure and nothing is refreshed", async () => {
  let reloaded = 0;
  const r = await runTicketTransition(async () => { throw err(409, "Invalid transition"); }, 7, 3, {}, { guard: inFlightGuard(), reloadMessages: async () => { reloaded += 1; } });
  assert.equal(r.ok, false);
  assert.equal(r.error.message, "Invalid transition");
  assert.equal(reloaded, 0);
});

test("runTicketTransition: a refresh failure after success is NOT reported as a failed transition", async () => {
  const r = await runTicketTransition(async () => ({ id: 7 }), 7, 3, {}, { guard: inFlightGuard(), reloadMessages: async () => { throw err(503, "down"); } });
  assert.equal(r.ok, true);
  assert.equal(r.refreshError.message, "down");
});

test("runTicketTransition: a second click while the first is in flight sends nothing", async () => {
  const guard = inFlightGuard();
  const pending = deferred();
  let posts = 0;
  const api = () => { posts += 1; return pending.p; };
  const first = runTicketTransition(api, 7, 3, {}, { guard });
  const second = await runTicketTransition(api, 7, 3, {}, { guard });
  assert.deepEqual(second, { skipped: true });
  pending.resolve({ id: 7 });
  await first;
  assert.equal(posts, 1);
  assert.equal(guard.busy, false); // released afterwards
});

test("runTicketTransition: the guard is released after a failure so the user can retry", async () => {
  const guard = inFlightGuard();
  await runTicketTransition(async () => { throw err(500); }, 7, 3, {}, { guard });
  assert.equal(guard.busy, false);
});

// ---- article votes ----

test("submitArticleVote: existing contract, and 'thanks' only after success", async () => {
  const calls = [];
  let done = 0, error = null;
  const r = await submitArticleVote(async (p, o) => { calls.push([p, o]); return {}; }, 12, true, { guard: inFlightGuard(), onDone: () => { done += 1; }, onError: (m) => { error = m; } });
  assert.deepEqual(calls, [["/kb/articles/12/vote/", { method: "POST", body: { helpful: true } }]]);
  assert.equal(r.ok, true);
  assert.equal(done, 1);
  assert.equal(error, null);
});

test("submitArticleVote: failure reports an error and never claims success", async () => {
  let done = 0, error = null;
  const guard = inFlightGuard();
  const r = await submitArticleVote(async () => { throw err(503, "Service unavailable"); }, 12, false, { guard, onDone: () => { done += 1; }, onError: (m) => { error = m; } });
  assert.equal(r.ok, false);
  assert.equal(done, 0);
  assert.match(error, /wasn't recorded: Service unavailable/);
  assert.equal(guard.busy, false); // can retry
});

test("submitArticleVote: a duplicate submission while one is in flight is skipped", async () => {
  const guard = inFlightGuard();
  const pending = deferred();
  let posts = 0;
  const api = () => { posts += 1; return pending.p; };
  const first = submitArticleVote(api, 12, true, { guard });
  assert.deepEqual(await submitArticleVote(api, 12, true, { guard }), { skipped: true });
  pending.resolve({});
  await first;
  assert.equal(posts, 1);
});

// ---- wiring in the components (node --test cannot render JSX, so check the source) ----

test("Composer: Ctrl/Cmd+Enter and the Send button share one in-flight guard", () => {
  const s = src("../components/Composer.jsx");
  const send = s.slice(s.indexOf("const send = async"), s.indexOf("return (", s.indexOf("const send = async")));
  assert.match(send, /if \(!sendGuard\.current\.enter\(\)\) return;/);
  assert.ok(send.indexOf("sendGuard.current.enter()") < send.indexOf("api(`/tickets/${t.id}/interactions/`"), "guard precedes the POST");
  assert.match(send, /finally \{\s*sendGuard\.current\.exit\(\);/);
  assert.match(s, /\(e\.metaKey \|\| e\.ctrlKey\) && e\.key === "Enter"[\s\S]{0,80}send\(\);/);
  // Unchanged contract: multipart form with body, kind and files to the same endpoint.
  assert.match(send, /form\.append\("body", body\);\s*form\.append\("kind", kind\);/);
  assert.match(send, /api\(`\/tickets\/\$\{t\.id\}\/interactions\/`, \{ method: "POST", form \}\)/);
});

test("Knowledge: list responses and errors are both gated on the latest request", () => {
  const s = src("./Knowledge.jsx");
  assert.match(s, /\.then\(\(d\) => \{ if \(gate\.current\.isCurrent\(token\)\)/);
  assert.match(s, /\.catch\(\(e\) => \{ if \(gate\.current\.isCurrent\(token\)\)/);
  assert.match(s, /onClick=\{retry\}>Retry</);
  assert.match(s, /disabled=\{voting\}>Yes</);
  assert.match(s, /disabled=\{voting\}>No</);
});

test("Queue: saved-queue failures are surfaced, not swallowed or alert()ed", () => {
  const s = src("./Queue.jsx");
  assert.doesNotMatch(s, /\balert\(/);
  assert.doesNotMatch(s, /\/\/ Ignore/);
  assert.match(s, /setSavedQueuesError\(savedQueueErrorMessage\("load", e\)\)/);
  assert.match(s, /setBulkFeedback\(bulkErrorMessage\(e\)\)/);
  assert.doesNotMatch(s, /role="button"/, "saved-queue pill is a real button");
});

test("TicketDetail: transitions are guarded and the approvals panel keeps its API contract", () => {
  const s = src("./TicketDetail.jsx");
  assert.match(s, /runTicketTransition\(api, id, tr\.id, extra, \{ guard: transitionGuard\.current, reloadMessages \}\)/);
  assert.match(s, /onClick=\{\(\) => clickTransition\(tr\)\} disabled=\{transitionBusy\}/);
  // U3 kept the approvals panel untouched (UI-DEC-014). U4 (UI-DEC-015) authorizes changing it,
  // so this now pins the contract instead: same request payload, and decide sends exactly {decision}
  // through the single-flight helper (whose path/method are covered in approvalHelpers.test.js).
  assert.match(s, /api\(`\/tickets\/\$\{t\.id\}\/approvals\/`, \{ method: "POST", body: \{ approver_id: Number\(approver\) \} \}\)/);
  assert.match(s, /submitApprovalDecision\(api, t\.id, a\.id, \{ decision \}, \{ guard: guard\.current \}\)/);
});
