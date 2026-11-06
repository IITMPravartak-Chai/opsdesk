// U6 Advanced Operations: helper and wiring tests.
import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import {
  alertGroupFrom, exportContentProblem, exportFileName, maskInboundUrl, nonAlertEntries, routingRows,
} from "./opsHelpers.js";
import { classifyNodes, graphSummary, layoutGraphNodes, truncationNotice } from "./cmdbHelpers.js";

const src = (f) => readFileSync(new URL(f, import.meta.url), "utf8");

// ---- E4 alert grouping
test("alertGroupFrom reads only the server's correlation keys; null for ordinary tickets", () => {
  assert.equal(alertGroupFrom({ contact: "x" }), null);
  assert.equal(alertGroupFrom(undefined), null);
  const g = alertGroupFrom({ alert_fingerprint: "disk-full", alert_occurrences: "3", first_alert_at: "a", last_alert_at: "b",
    alert_status: "firing", active_alerts: ["disk-full", 7] });
  assert.deepEqual(g, { fingerprint: "disk-full", occurrences: 3, firstAt: "a", lastAt: "b", status: "firing", active: ["disk-full", "7"] });
  assert.equal(alertGroupFrom({ alert_occurrences: "n/a" }).occurrences, null); // never invents a count
  assert.deepEqual(nonAlertEntries({ alert_status: "x", active_alerts: [], sender: "s" }), [["sender", "s"]]);
});

// ---- E5 integrations
test("maskInboundUrl hides the secret token segment only", () => {
  assert.equal(maskInboundUrl("https://ops.example/api/channels/inbound/AbC_123-xyz/"), "https://ops.example/api/channels/inbound/••••••••/");
  assert.equal(maskInboundUrl("https://ops.example/api/channels/inbound/AbC"), "https://ops.example/api/channels/inbound/••••••••");
  assert.ok(!maskInboundUrl("https://h/api/channels/inbound/secret-token/").includes("secret-token"));
  assert.equal(maskInboundUrl(""), "");
});

test("routingRows lists routing defaults only (no nested objects)", () => {
  assert.deepEqual(routingRows({ ticket_type: "incident", team: "Desk", impact: 3, nested: { a: 1 }, empty: "" }),
    [["Ticket type", "incident"], ["Team", "Desk"], ["Impact", "3"]]);
});

test("channel detail (the former Integrations page) masks the webhook URL and never renders the raw one", () => {
  const page = src("./Configuration.jsx");
  assert.match(page, /maskInboundUrl\(d\.webhook_url\)/);
  assert.doesNotMatch(page, /\{d\.webhook_url\}/, "raw URL is never rendered");
  assert.match(page, /clipboard\.writeText\(d\.webhook_url\)/, "copied only on an explicit action");
  assert.ok(!existsSync(new URL("./Integrations.jsx", import.meta.url)), "the separate Integrations page is gone");
});

test("Integrations is no longer a menu entry and its old path moves to the channels page", async () => {
  const { MOVED } = await import("../features.js");
  assert.equal(MOVED["/integrations"], "/settings/config/channels");
  assert.doesNotMatch(src("../App.jsx"), /href="\/integrations"/);
});

// ---- E6 exports
test("exportContentProblem accepts only the requested file type", () => {
  assert.equal(exportContentProblem("csv", "text/csv; charset=utf-8"), "");
  assert.equal(exportContentProblem("pdf", "application/pdf"), "");
  assert.match(exportContentProblem("csv", "application/json"), /did not return a report file/);
  assert.match(exportContentProblem("pdf", "text/html"), /did not return a report file/);
  assert.match(exportContentProblem("xlsx", "x"), /Unsupported/);
  assert.equal(exportFileName('attachment; filename="opsdesk_tickets_20261022_1200.csv"', "f.csv"), "opsdesk_tickets_20261022_1200.csv");
  assert.equal(exportFileName(null, "f.csv"), "f.csv");
});

test("Dashboard export checks the response type before saving a file", () => {
  const page = src("./Dashboard.jsx");
  const at = page.indexOf("exportContentProblem(format");
  assert.ok(at > 0 && at < page.indexOf("await res.blob()"), "type is checked before the body is saved");
});

test("in-app link routing leaves download anchors and blob: URLs to the browser", () => {
  const app = src("../App.jsx");
  const handler = app.slice(app.indexOf("const onClick = (e) =>"), app.indexOf('document.addEventListener("click", onClick)'));
  assert.match(handler, /a\.hasAttribute\("download"\)/);
  assert.match(handler, /\^https\?:\$\/\.test\(url\.protocol\)/);
  assert.ok(handler.indexOf("hasAttribute(\"download\")") < handler.indexOf("e.preventDefault()"), "checked before routing takes the click");
});

// ---- E2 major incident
test("child-incident link uses the served ?q= search and refuses to report success without a match", () => {
  const page = src("./TicketDetail.jsx");
  assert.doesNotMatch(page, /\/tickets\/\?search=\$\{encodeURIComponent\(linkNumber/);
  assert.match(page, /\/tickets\/\?q=\$\{encodeURIComponent\(linkNumber\.trim\(\)\)\}/);
  assert.match(page, /No ticket numbered \$\{linkNumber\.trim\(\)\} was found\. Nothing was updated\./);
  const opener = page.slice(page.indexOf("if (!openCoordinate) {"), page.indexOf("setOpenCoordinate(!openCoordinate);"));
  assert.match(opener, /setCommanderId\(t\.incident_commander\?\.id \|\| ""\)/, "coordinator re-seeds the commander from the ticket");
  assert.match(opener, /setStatusVal\(t\.major_incident_status/);
  for (const id of ["mi-status", "mi-commander", "mi-note", "mi-link", "mi-pir"]) {
    assert.match(page, new RegExp(`htmlFor="${id}"`));
    assert.match(page, new RegExp(`id="${id}"`));
  }
});

// ---- E1 CMDB
test("classifyNodes decides each node's relation from its own links", () => {
  const nodes = [{ id: 1 }, { id: 2 }, { id: 3 }, { id: 4 }, { id: 5 }];
  const links = [
    { source: 1, target: 2, relation: "depends_on", direction: "upstream" },
    { source: 2, target: 3, relation: "depends_on", direction: "upstream" },
    { source: 4, target: 1, relation: "depends_on", direction: "downstream" },
  ];
  assert.deepEqual(classifyNodes(nodes, links, 1), { 1: "root", 2: "upstream", 3: "upstream", 4: "downstream", 5: "connected" });
  const svc = classifyNodes([{ id: "srv-1" }, { id: 9 }], [{ source: "srv-1", target: 9, relation: "provides", direction: "downstream" }], "srv-1");
  assert.equal(svc[9], "component");
  // the old layout put every non-upstream node on the downstream side when any link qualified
  const laid = layoutGraphNodes(nodes, links, 1);
  assert.equal(laid.find((n) => n.id === 5).relation, "connected");
});

test("graphSummary distinguishes 'no dependencies' from data; truncationNotice is explicit", () => {
  assert.match(graphSummary({ nodes: [{ id: 1 }], links: [], depth: 2, direction: "both" }), /^No dependencies or dependants are recorded/);
  assert.equal(graphSummary({ nodes: [{ id: 1 }, { id: 2 }], depth: 1, direction: "upstream" }), "1 related item (within 1 hop, upstream).");
  assert.equal(graphSummary(null), "");
  assert.equal(truncationNotice("services", 100, 250), "Showing the first 100 of 250 services.");
  assert.equal(truncationNotice("services", 3, 3), "");
});

test("CMDB explorer surfaces impact failures and clears stale graphs", () => {
  const page = src("./CmdbExplorer.jsx");
  assert.match(page, /Impact couldn't be calculated/);
  assert.match(page, /setGraphData\(null\);\s*\n\s*setActiveNode\(null\);/);
  assert.match(page, /role="button"\s*\n\s*tabIndex=\{0\}/);
  const legend = page.slice(page.indexOf("{/* Legend below the graph"), page.indexOf("Application\n"));
  assert.doesNotMatch(legend, /position: "absolute"/, "legend must not cover graph nodes");
});
