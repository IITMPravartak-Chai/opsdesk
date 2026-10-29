import test from "node:test";
import assert from "node:assert/strict";
import {
  apiPath, buildPayload, cellText, configPath, configRoute, configWriteError, fieldErrors, initialValues, matrixCells, matrixLookup,
  toInput, usageSentence,
} from "./configHelpers.js";

test("configRoute accepts known sections and details only where they exist", () => {
  assert.deepEqual(configRoute("/settings/config"), { section: null, id: null });
  assert.deepEqual(configRoute("/settings/config/channels"), { section: "channels", id: null });
  assert.deepEqual(configRoute("/settings/config/workflows/4/"), { section: "workflows", id: 4 });
  assert.deepEqual(configRoute("/settings/config/ticket-types/2"), { section: "ticket-types", id: 2 });
  assert.deepEqual(configRoute("/settings/config/channels/3"), { section: "channels", id: 3 }); // channel detail (was the Integrations page)
  for (const p of ["/settings/config/nope", "/settings/config/priorities/3", "/settings/configx", "/settings/users", "/admin/", "/settings/config/workflows/x"]) {
    assert.equal(configRoute(p), null, p);
  }
});

test("paths", () => {
  assert.equal(configPath(null), "/settings/config");
  assert.equal(configPath("workflows", 3), "/settings/config/workflows/3");
  assert.equal(apiPath("priorities"), "/admin/config/priorities/");
  assert.equal(apiPath("states", { slug: "workflows", id: 7 }), "/admin/config/workflows/7/states/");
});

const FIELDS = {
  name: { kind: "str", required: true }, level: { kind: "int", required: true }, is_active: { kind: "bool" },
  parent_id: { kind: "fk", nullable: true, ref: "Category" }, roles: { kind: "str_list", choices: ["agent", "admin"] },
  domains: { kind: "domain_list" }, config: { kind: "json_dict" }, actions: { kind: "json_list" }, ids: { kind: "id_list", ref: "Team" },
  kind: { kind: "choice", choices: ["a", "b"] }, sla: { kind: "fk", nullable: true, admin_only: true }, count: { kind: "int", read_only: true },
};

test("initialValues for create and edit", () => {
  const c = initialValues(FIELDS);
  assert.equal(c.is_active, true);
  assert.equal(c.kind, "a");
  assert.equal(c.config, "{}");
  assert.equal(c.actions, "[]");
  assert.ok(!("count" in c));
  const e = initialValues(FIELDS, { name: "X", level: 2, is_active: false, parent_id: null, roles: ["agent", "admin"], domains: ["a.com", "b.com"], config: { k: 1 }, ids: [3] });
  assert.equal(e.roles, "agent,admin");
  assert.equal(e.domains, "a.com\nb.com");
  assert.equal(e.parent_id, "");
  assert.deepEqual(e.ids, [3]);
  assert.match(e.config, /"k": 1/);
});

test("buildPayload converts types and reports unsendable input", () => {
  const { body, errors } = buildPayload(FIELDS, { name: "A", level: "3", is_active: true, parent_id: "5", roles: "agent, admin", domains: "x.com\ny.com,x.com",
    config: '{"a":1}', actions: "[]", ids: ["1", "2"], kind: "b", sla: "" });
  assert.deepEqual(errors, {});
  assert.deepEqual(body, { name: "A", level: 3, is_active: true, parent_id: 5, roles: ["agent", "admin"], domains: ["x.com", "y.com", "x.com"], config: { a: 1 },
    actions: [], ids: [1, 2], kind: "b", sla: null });
  const bad = buildPayload(FIELDS, { name: "A", level: "x", config: "{oops", actions: "{}" });
  assert.deepEqual(Object.keys(bad.errors).sort(), ["actions", "config", "level"]);
  assert.equal(buildPayload(FIELDS, { name: "A", level: "1", config: "[]" }).errors.config, "Enter a JSON object, e.g. { … }.");
});

test("buildPayload on edit sends only changed fields and skips admin-only for non-admins", () => {
  const original = { name: "A", level: 2, is_active: true, parent_id: null, config: { a: 1 } };
  const values = { name: "A", level: "2", is_active: true, parent_id: "", config: '{\n "a": 1\n}', sla: "9" };
  assert.deepEqual(buildPayload(FIELDS, values, { original, isAdmin: true }).body, { sla: 9 });
  assert.deepEqual(buildPayload(FIELDS, values, { original, isAdmin: false }).body, {});
  assert.deepEqual(buildPayload(FIELDS, { ...values, name: "B" }, { original, isAdmin: false }).body, { name: "B" });
});

test("create omits optional blanks but keeps required values", () => {
  const { body } = buildPayload({ name: { kind: "str", required: true }, note: { kind: "str" } }, { name: "N", note: "" });
  assert.deepEqual(body, { name: "N" });
});

test("fieldErrors and messages", () => {
  assert.deepEqual(fieldErrors({ data: { errors: { name: "Required" } } }), { name: "Required" });
  assert.deepEqual(fieldErrors({}), {});
  assert.equal(usageSentence({ tickets: 3, catalog_items: 1, categories: 0 }), "It is still used by 3 tickets and 1 catalog items.");
  assert.equal(usageSentence({ users: 2 }), "It is still used by 2 users.");
  assert.equal(usageSentence({}), "");
  const conflict = { status: 409, data: { usage: { tickets: 2 } }, message: "x" };
  assert.match(configWriteError(conflict, "status"), /can't be deleted.*2 tickets/);
  assert.equal(configWriteError({ status: 400, data: { errors: { name: "bad" } } }), "Fix the highlighted fields.");
  assert.match(configWriteError(new Error("offline")), /Check your connection/);
  assert.match(configWriteError({ status: 404 }, "rule"), /no longer exists/);
});

test("cellText and matrix helpers", () => {
  assert.equal(cellText({ a: true }, "a"), "Yes");
  assert.equal(cellText({ a: null }, "a"), "—");
  assert.equal(cellText({ a: ["x", "y"] }, "a"), "x, y");
  assert.equal(cellText({ a: 0 }, "a"), "0");
  const cells = [{ impact: 1, urgency: 2, priority_id: 5 }];
  assert.deepEqual(matrixCells(matrixLookup(cells)), cells);
  assert.equal(toInput({ kind: "str_list" }, undefined), "");
});

test("visibleConfigGroups hides the sections of switched-off features and keeps the rest", async () => {
  const { visibleConfigGroups, CONFIG_GROUPS } = await import("./configHelpers.js");
  const on = visibleConfigGroups({ catalog: true, approvals: true, cmdb: true });
  assert.deepEqual(on, CONFIG_GROUPS);
  const off = visibleConfigGroups({ catalog: false, approvals: false, cmdb: false });
  const flat = off.flatMap((g) => g.sections);
  assert.ok(!flat.includes("catalog-items") && !flat.includes("services") && !flat.includes("items"));
  assert.ok(flat.includes("workflows") && flat.includes("accounts") && flat.includes("channels"));
  assert.ok(!off.some((g) => g.key === "cmdb"));
});
