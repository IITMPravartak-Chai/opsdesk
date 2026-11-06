// U4 (UI-DEC-015) employee service workflows: catalog, request creation, approvals.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  buildCatalogSubmissionPayload,
  catalogEmptyMessage,
  catalogFieldIds,
  catalogLoadErrorMessage,
  catalogSubmitErrorMessage,
  requestCreatedPath,
} from "./catalogHelpers.js";
import {
  applyDecision,
  approvalsLoadErrorMessage,
  decisionErrorMessage,
  isStaleDecision,
  requesterApprovalStatus,
  submitApprovalDecision,
} from "./approvalHelpers.js";
import { inFlightGuard } from "./workflowHelpers.js";

const err = (status, message = "boom") => Object.assign(new Error(message), { status });
const deferred = () => { let resolve; const p = new Promise((r) => { resolve = r; }); return { p, resolve }; };
const src = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8");

// ---- U4.1 catalog states ----

test("catalogLoadErrorMessage: unrouted endpoint (BLK-001) is named and offers the standard-request path", () => {
  assert.match(catalogLoadErrorMessage(err(404)), /isn't available on this server yet\. You can still raise a standard request\./);
  assert.match(catalogLoadErrorMessage(err(500, "Server error")), /couldn't be loaded: Server error/);
});

test("catalogEmptyMessage: an empty catalog is distinguished from a search or category with no matches", () => {
  assert.equal(catalogEmptyMessage({ total: 0, search: "", category: "All" }), "No services are published in the catalog yet.");
  assert.equal(catalogEmptyMessage({ total: 0, search: "laptop", category: "All" }), "No services are published in the catalog yet.");
  assert.equal(catalogEmptyMessage({ total: 4, search: "laptop", category: "All" }), "No services match your search.");
  assert.equal(catalogEmptyMessage({ total: 4, search: "", category: "Hardware" }), "No services in this category.");
});

// ---- U4.2 request creation ----

test("catalogSubmitErrorMessage: only claims 'nothing was submitted' when that is known", () => {
  assert.match(catalogSubmitErrorMessage(err(404)), /Nothing was submitted/);
  assert.match(catalogSubmitErrorMessage(err(400, "Laptop model is required.")), /wasn't submitted: Laptop model is required\./);
  const unknown = catalogSubmitErrorMessage(err(502, "Bad gateway"));
  assert.doesNotMatch(unknown, /Nothing was submitted|wasn't submitted/);
  assert.match(unknown, /may not have been submitted .*Check My requests before trying again/);
  assert.match(catalogSubmitErrorMessage(new TypeError("Failed to fetch")), /may not have been submitted/);
});

test("requestCreatedPath: navigates only when the response identifies the created request", () => {
  assert.equal(requestCreatedPath({ id: 42, number: "SR-0042" }), "/tickets/42");
  for (const r of [{}, null, undefined, { id: "42" }, { id: 0 }, { id: -1 }, { number: "SR-1" }]) assert.equal(requestCreatedPath(r), null, JSON.stringify(r));
});

test("catalogFieldIds: stable, attribute-safe ids tying a field to its hint and error", () => {
  assert.deepEqual(catalogFieldIds("cost centre/id"), { input: "catalog-field-cost-centre-id", hint: "catalog-field-cost-centre-id-hint", error: "catalog-field-cost-centre-id-error" });
});

test("buildCatalogSubmissionPayload: payload shape unchanged ({fields, additional_notes})", () => {
  assert.deepEqual(buildCatalogSubmissionPayload({ id: 1 }, { model: "X" }, "  notes  "), { fields: { model: "X" }, additional_notes: "notes" });
});

// ---- U4.4 approvals ----

test("approvalsLoadErrorMessage: unrouted list (BLK-002) points to the ticket-page path, which is served", () => {
  assert.match(approvalsLoadErrorMessage(err(404)), /isn't available on this server yet\..*ticket page/);
  assert.match(approvalsLoadErrorMessage(err(503, "down")), /couldn't be loaded: down/);
});

test("decisionErrorMessage: forbidden, stale and unknown outcomes are worded differently", () => {
  assert.match(decisionErrorMessage(err(403, "Only the named approver can decide.")), /wasn't recorded: Only the named approver can decide\./);
  assert.match(decisionErrorMessage(err(400, "This approval has already been decided.")), /wasn't recorded: .*latest state has been reloaded/);
  assert.match(decisionErrorMessage(err(500, "Server error")), /may not have been recorded .*Refresh before trying again/);
  assert.deepEqual([400, 404, 409, 403, 500].map((s) => isStaleDecision(err(s))), [true, true, true, false, false]);
});

test("submitApprovalDecision: posts the caller's body unchanged to the existing decide endpoint", async () => {
  const calls = [];
  const api = async (p, o) => { calls.push([p, o]); return { status: "approved" }; };
  const page = await submitApprovalDecision(api, 7, 3, { decision: "approved", comment: "ok" }, { guard: inFlightGuard() });
  const panel = await submitApprovalDecision(api, 7, 4, { decision: "rejected" }, { guard: inFlightGuard() });
  assert.deepEqual(calls, [
    ["/tickets/7/approvals/3/decide/", { method: "POST", body: { decision: "approved", comment: "ok" } }],
    ["/tickets/7/approvals/4/decide/", { method: "POST", body: { decision: "rejected" } }],
  ]);
  assert.deepEqual(page, { ok: true, status: "approved" });
  assert.equal(panel.ok, true);
});

test("submitApprovalDecision: a second decision while one is in flight is not sent", async () => {
  const guard = inFlightGuard();
  const d = deferred();
  let posts = 0;
  const api = () => { posts += 1; return d.p; };
  const first = submitApprovalDecision(api, 7, 3, { decision: "approved" }, { guard });
  assert.deepEqual(await submitApprovalDecision(api, 7, 3, { decision: "rejected" }, { guard }), { skipped: true });
  d.resolve({ status: "approved" });
  await first;
  assert.equal(posts, 1);
  assert.equal(guard.busy, false);
});

test("submitApprovalDecision: failure is returned, never reported as success, and the guard is released", async () => {
  const guard = inFlightGuard();
  const r = await submitApprovalDecision(async () => { throw err(403, "Only the named approver can decide."); }, 7, 3, { decision: "approved" }, { guard });
  assert.equal(r.ok, false);
  assert.equal(r.error.status, 403);
  assert.equal(guard.busy, false);
});

test("applyDecision: marks only the confirmed item as decided", () => {
  const list = [{ id: 1, status: "pending" }, { id: 2, status: "pending" }];
  assert.deepEqual(applyDecision(list, 2, "rejected"), [{ id: 1, status: "pending" }, { id: 2, status: "rejected", decision: "rejected" }]);
  assert.deepEqual(list[1].status, "pending", "input not mutated");
});

test("requesterApprovalStatus: status-only summary for the requester", () => {
  assert.equal(requesterApprovalStatus([]), null);
  assert.equal(requesterApprovalStatus(undefined), null);
  assert.equal(requesterApprovalStatus([{ status: "pending" }]), "Awaiting approval");
  assert.equal(requesterApprovalStatus([{ status: "approved" }, { status: "pending" }]), "Awaiting approval");
  assert.equal(requesterApprovalStatus([{ status: "approved" }, { status: "rejected" }]), "Rejected");
  assert.equal(requesterApprovalStatus([{ status: "approved" }, { status: "approved" }]), "Approved");
});

// ---- wiring (node --test cannot render JSX) ----

test("Catalog: single-flight submit, success only after the server, field errors associated", () => {
  const s = src("./Catalog.jsx");
  assert.match(s, /if \(!submitGuard\.current\.enter\(\)\) return;/);
  assert.ok(s.indexOf("submitGuard.current.enter()") < s.indexOf("api(`/catalog/${activeItem.id}/request/`"), "guard precedes the POST");
  assert.match(s, /const path = requestCreatedPath\(res\);[\s\S]*if \(path\) navigate\(path\);/);
  assert.doesNotMatch(s, /navigate\(`\/tickets\/\$\{res\.id\}`\)/, "no navigation to an unverified id");
  assert.match(s, /setSubmitError\(catalogSubmitErrorMessage\(err\)\)/);
  assert.match(s, /"aria-invalid": fieldError \? true : undefined, "aria-describedby": describedBy/);
  assert.match(s, /<span className="error" id=\{ids\.error\}>/);
  assert.match(s, /aria-pressed=\{activeCategory === cat\}/);
});

test("Approvals page: no misleading counts, confirmed decisions applied locally, honest refresh outcome", () => {
  const s = src("./Approvals.jsx");
  assert.match(s, /Pending\{loaded \? ` \(\$\{/);
  assert.match(s, /submitApprovalDecision\(api, item\.ticket\.id, item\.id, \{ decision: type, comment: comment\.trim\(\) \}, \{ guard: decideGuard\.current \}\)/);
  assert.match(s, /setApprovals\(\(prev\) => applyDecision\(prev, item\.id, r\.status\)\)/);
  assert.match(s, /The list couldn't be refreshed, so it may be out of date\./);
  assert.doesNotMatch(s, /\|\| "General"/, "no invented department");
  assert.doesNotMatch(s, /will mark the ticket as rejected and notify the requester/, "no unverified side-effect claim");
});

test("TicketDetail approvals panel: failures surfaced and actions single-flight", () => {
  const s = src("./TicketDetail.jsx");
  const panel = s.slice(s.indexOf("function Approvals("), s.indexOf("function Links("));
  assert.match(panel, /setErr\(`Approval wasn't requested: \$\{e\.message\}`\)/);
  assert.match(panel, /setErr\(decisionErrorMessage\(r\.error\)\)/);
  assert.match(panel, /disabled=\{busy !== null\}/);
  assert.match(panel, /role="alert"/);
  assert.match(s, /requesterApprovalStatus\(t\.approvals\)/);
});
