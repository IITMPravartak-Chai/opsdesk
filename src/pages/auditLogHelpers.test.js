// U7-A1 audit log page: helper behaviour and page wiring.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  COVERAGE, PAGE_SIZE, buildQuery, canReadAuditLog, changeRows, formatTimestamp, hasFilters, lastPage, localDayStartIso,
  objectLink, rangeLabel,
} from "./auditLogHelpers.js";

const src = (f) => readFileSync(new URL(f, import.meta.url), "utf8");
const params = (q) => Object.fromEntries(new URLSearchParams(q));

test("only administrators may open the page (mirrors the server rule)", () => {
  assert.equal(canReadAuditLog({ role: "super_admin" }), true);
  for (const role of ["manager", "team_lead", "employee", "customer"]) assert.equal(canReadAuditLog({ role }), false);
  assert.equal(canReadAuditLog(null), false);
});

test("buildQuery sends only supported, valid filters with explicit UTC instants", () => {
  const { query, error } = buildQuery({ action: "ticket.update", actor: { id: 7, name: "A" }, objectType: "Ticket", objectId: "12", from: "2026-10-01", to: "2026-10-23" }, 2);
  assert.equal(error, "");
  const p = params(query);
  assert.deepEqual(Object.keys(p).sort(), ["action", "actor", "object_id", "object_type", "page", "page_size", "since", "until"]);
  assert.equal(p.actor, "7");
  assert.equal(p.page, "2");
  assert.equal(p.page_size, String(PAGE_SIZE));
  assert.match(p.since, /Z$/);
  assert.match(p.until, /Z$/);
  assert.equal(new Date(p.until) - new Date(p.since), new Date(2026, 9, 24) - new Date(2026, 9, 1)); // "to" day included
  assert.ok(!/tenant/.test(query));
});

test("buildQuery refuses values the server would reject, and never sends them", () => {
  for (const [f, msg] of [
    [{ objectType: "1x" }, /Target type/], [{ objectId: "../x" }, /Target ID/],
    [{ from: "2026-10-23", to: "2026-10-01" }, /on or after/], [{ from: "2024-01-01", to: "2026-10-23" }, /one year or less/],
  ]) {
    const r = buildQuery(f, 1);
    assert.equal(r.query, null);
    assert.match(r.error, msg);
  }
  assert.equal(localDayStartIso("not-a-date"), null);
});

test("hasFilters, paging labels and links", () => {
  assert.equal(hasFilters({ action: "", objectId: "" }), false);
  assert.equal(hasFilters({ objectId: "5" }), true);
  assert.equal(rangeLabel(1, 0), "No events");
  assert.equal(rangeLabel(2, 120), "Showing 51–100 of 120");
  assert.equal(rangeLabel(3, 120), "Showing 101–120 of 120");
  assert.equal(lastPage(0), 1);
  assert.equal(lastPage(101), 3);
  assert.equal(objectLink({ object_type: "Ticket", object_id: "42" }), "/tickets/42");
  assert.equal(objectLink({ object_type: "User", object_id: "42" }), "/settings/users/42");
  assert.equal(objectLink({ object_type: "BusinessHours", object_id: "3" }), "/settings/sla/hours/3");
  assert.equal(objectLink({ object_type: "Account", object_id: "42" }), null);
  assert.equal(objectLink({ object_type: "Ticket", object_id: "x/../1" }), null);
});

test("formatTimestamp shows local time and keeps UTC in the title; bad input is labelled, not invented", () => {
  const t = formatTimestamp("2026-10-23T06:02:11Z", "en-GB");
  assert.match(t.text, /2026/);
  assert.equal(t.title, "2026-10-23T06:02:11Z (UTC)");
  assert.deepEqual(formatTimestamp("nope"), { text: "Unknown time", title: "" });
});

test("changeRows renders stored [old, new] pairs and other values", () => {
  assert.deepEqual(changeRows({ state: ["New", "Open"], tags: [["a"], ["a", "b"]], note: "x", empty: [null, ""] }), [
    { field: "state", from: "New", to: "Open" },
    { field: "tags", from: '["a"]', to: '["a","b"]' },
    { field: "note", from: null, to: "x" },
    { field: "empty", from: "—", to: "—" },
  ]);
  assert.deepEqual(changeRows(null), []);
  assert.deepEqual(changeRows(["x"]), []);
});

test("coverage notice lists what is and isn't recorded, including configuration changes", () => {
  assert.ok(COVERAGE.recorded.length >= 3);
  assert.ok(COVERAGE.notRecorded.some((t) => /Configuration/.test(t)));
  const page = src("./AuditLog.jsx");
  assert.match(page, /This is not a complete record of administrative activity\./);
  assert.match(page, /IP addresses aren't shown/);
});

test("the page is read-only, calls the API only when allowed, and has every required state", () => {
  const page = src("./AuditLog.jsx");
  assert.doesNotMatch(page, /method:\s*"(POST|PUT|PATCH|DELETE)"/, "no write requests");
  assert.match(page, /useState\(\{ status: allowed \? "loading" : "forbidden" \}\)/, "non-administrators start in the forbidden state");
  assert.equal((page.match(/if \(!allowed\) return/g) || []).length, 2, "both requests are skipped when not allowed");
  for (const s of ["No events match these filters.", "No audit events have been recorded in this workspace yet.",
    "The audit log couldn't be loaded.", "The audit log is available to managers only.", "Loading audit events…"]) {
    assert.ok(page.includes(s), s);
  }
  assert.match(page, /e\.status === 403\) setState\(\{ status: "forbidden" \}\)/, "a server 403 shows the forbidden state");
  assert.doesNotMatch(page, /ip_address/, "no IP field is rendered");
  assert.match(page, /aria-expanded=\{isOpen\}/);
});

test("route and navigation wiring: /settings/audit-log (not under /admin, which no longer exists)", () => {
  const app = src("../App.jsx");
  assert.match(app, /p === "\/settings\/audit-log"\) page = <AuditLog \/>/);
  // U7: the rail now leads to Administration; its audit-log tab is offered to administrators only (settingsTabs, tested
  // in adminHelpers.test.js), and the page itself starts in the forbidden state for every other role.
  assert.match(app, /settingsHome\(user\) && <a href=\{settingsHome\(user\)\}/);
  assert.match(src("./adminHelpers.js"), /isAdminRole\(user\) && \{ key: "audit-log", to: "\/settings\/audit-log"/);
  assert.doesNotMatch(app, /href="\/admin\/audit/);
});
