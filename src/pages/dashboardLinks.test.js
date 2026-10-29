import test from "node:test";
import assert from "node:assert/strict";
import { applyListFilters, idByName, ticketsLink, urlListFilters } from "./dashboardLinks.js";

test("ticketsLink keeps the view and adds only the filters that are set", () => {
  assert.equal(ticketsLink("open"), "/tickets?view=open");
  assert.equal(ticketsLink("sla", { sla_status: "breached", account: 3, team: "", source: undefined }), "/tickets?view=sla&sla_status=breached&account=3");
});

test("urlListFilters picks up only the filters the list understands", () => {
  const s = new URLSearchParams("view=open&team=4&source=email&bogus=1&sla_status=breached");
  assert.deepEqual(urlListFilters(s), { team: "4", source: "email", sla_status: "breached" });
});

test("a URL sla_status replaces the queue's own, and all=1 drops the open and state limits", () => {
  const sla = { sla_status: "at_risk,breached", open: 1, ordering: "due" };
  assert.deepEqual(applyListFilters(sla, new URLSearchParams("sla_status=breached")), { sla_status: "breached", open: 1, ordering: "due" });
  assert.deepEqual(applyListFilters(sla, new URLSearchParams("all=1&source=email")), { sla_status: "at_risk,breached", ordering: "due", source: "email" });
  assert.deepEqual(applyListFilters({ state_category: "resolved,closed" }, new URLSearchParams("all=1")), {});
});

test("idByName finds a row by name", () => {
  assert.equal(idByName([{ id: 1, name: "A" }, { id: 2, name: "B" }], "B"), 2);
  assert.equal(idByName([], "B"), undefined);
});
