import test from "node:test";
import assert from "node:assert/strict";
import { PRIVILEGED_ROLES, STAFF_ROLES, TOP_ROLES, isPrivilegedRole, isTopRole } from "./roles.js";
import { ROLE_LABELS, ROLE_OPTIONS, canManageSla, canManageUsers, settingsTabs } from "./pages/adminHelpers.js";
import { canReadAuditLog } from "./pages/auditLogHelpers.js";
import { canConfigure } from "./navModel.js";

const u = (role) => ({ role });

test("role groups: a super admin is top level, like a manager", () => {
  assert.deepEqual(TOP_ROLES, ["super_admin", "manager"]);
  assert.deepEqual(PRIVILEGED_ROLES, ["super_admin", "manager", "team_lead"]);
  assert.deepEqual(STAFF_ROLES, ["super_admin", "manager", "team_lead", "employee"]);
  assert.equal(isTopRole(u("super_admin")), true);
  assert.equal(isTopRole(u("manager")), true);
  for (const r of ["team_lead", "employee", "customer", undefined]) assert.equal(isTopRole(u(r)), false, String(r));
  assert.equal(isPrivilegedRole(u("team_lead")), true);
  assert.equal(isPrivilegedRole(u("employee")), false);
  assert.equal(isTopRole(null), false);
});

test("Administration belongs to the super admin alone", () => {
  for (const check of [canManageUsers, canManageSla, canReadAuditLog, canConfigure]) {
    assert.equal(check(u("super_admin")), true, check.name);
    for (const r of ["manager", "team_lead", "employee", "customer"]) assert.equal(check(u(r)), false, `${check.name} ${r}`);
  }
  assert.deepEqual(settingsTabs(u("super_admin")).map((t) => t.key), ["users", "teams", "sla", "config", "audit-log"]);
  assert.deepEqual(settingsTabs(u("manager")), []);
});

test("a team lead still has no SLA or audit log; roles are labelled and selectable", () => {
  assert.equal(canManageSla(u("team_lead")), false);
  assert.equal(canReadAuditLog(u("team_lead")), false);
  assert.equal(ROLE_LABELS.super_admin, "Super admin");
  assert.ok(ROLE_OPTIONS.includes("super_admin"));
});

test("the super admin is a single, locked account: never offered as a role to assign", async () => {
  const { ASSIGNABLE_ROLES, ROLE_OPTIONS, isSuperAdmin } = await import("./pages/adminHelpers.js");
  assert.ok(ROLE_OPTIONS.includes("super_admin"));      // still filterable and displayable
  assert.ok(!ASSIGNABLE_ROLES.includes("super_admin")); // but never assignable
  assert.deepEqual(ASSIGNABLE_ROLES, ["manager", "team_lead", "employee", "customer"]);
  assert.equal(isSuperAdmin({ role: "super_admin" }), true);
  assert.equal(isSuperAdmin({ role: "manager" }), false);
});
