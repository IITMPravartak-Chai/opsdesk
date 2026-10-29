import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  GUARD_REDIRECT,
  MORE_SECTIONS,
  canConfigure,
  createTab,
  csatToken,
  customerGuard,
  isCreateSection,
  isMoreSection,
  isPublicRoute,
  loadsWorkspaceData,
  homePath,
  isAdministrationOnly,
  adminOnlyPathAllowed,
  railViews,
  RAIL_VIEW_ORDER,
  landsOnDashboard,
  moreItems,
  primaryAction,
  sectionOf,
} from "./navModel.js";
import { ALL_ON, FEATURES, isDisabledPath } from "./features.js";

// Role shapes as returned by /auth/me/ (is_agent = super_admin/manager/team_lead/employee on the backend).
const customer = { role: "customer", is_agent: false };
const employee = { role: "employee", is_agent: true };
const teamLead = { role: "team_lead", is_agent: true };
const manager = { role: "manager", is_agent: true };
const keys = (user, features = ALL_ON) => moreItems(user, features).map((i) => i.key);

test("sectionOf: tickets for /, /tickets and ticket detail; first segment otherwise", () => {
  assert.equal(sectionOf("/"), "tickets");
  assert.equal(sectionOf("/tickets"), "tickets");
  assert.equal(sectionOf("/tickets/42"), "tickets");
  assert.equal(sectionOf("/kb/7"), "kb");
  assert.equal(sectionOf("/cmdb/ci/3"), "cmdb");
  assert.equal(sectionOf("/new"), "new");
});

test("More is active on every screen it houses, including nested routes", () => {
  assert.deepEqual(MORE_SECTIONS, ["dashboard", "approvals", "cmdb", "settings", "profile"]);
  for (const p of ["/dashboard", "/approvals", "/cmdb", "/cmdb/ci/9", "/profile"]) {
    assert.equal(isMoreSection(sectionOf(p)), true, p);
  }
  for (const p of ["/tickets", "/kb", "/new", "/catalog"]) {
    assert.equal(isMoreSection(sectionOf(p)), false, p);
  }
});

test("createTab (B1 destination 2): New -> /new for everyone", () => {
  assert.deepEqual(createTab(customer), { to: "/new", label: "New", name: "Raise a request" });
  for (const staff of [employee, teamLead, manager]) {
    assert.deepEqual(createTab(staff), { to: "/new", label: "New", name: "New ticket" });
  }
  assert.equal(isCreateSection("new"), true);
  assert.equal(isCreateSection("catalog"), true);
  assert.equal(isCreateSection("kb"), false);
});

test("primaryAction keeps the existing rail destinations per role", () => {
  assert.deepEqual(primaryAction(customer), { to: "/new", label: "Raise a request" });
  assert.deepEqual(primaryAction(employee), { to: "/new", label: "New ticket" });
});

test("moreItems: staff get catalog, health, approvals, CMDB and profile, but not /new", () => {
  assert.deepEqual(keys(employee), ["dashboard", "catalog", "approvals", "cmdb", "profile"]);
  // Administration is only for the super admin.
  assert.deepEqual(keys(teamLead), ["dashboard", "catalog", "approvals", "cmdb", "profile"]);
  assert.deepEqual(keys(manager), ["dashboard", "catalog", "approvals", "cmdb", "profile"]);
  // The super admin is Administration-only: no ticket-side destinations.
  assert.deepEqual(keys({ role: "super_admin", is_agent: true }), ["overview", "users", "teams", "sla", "config", "audit-log", "profile"]);
});

test("moreItems: customers only get their profile", () => {
  assert.deepEqual(keys(customer), ["profile"]);
});

test("every More destination is a route App.jsx renders", () => {
  const routed = ["/new", "/catalog", "/dashboard", "/approvals", "/cmdb", "/settings/audit-log", "/settings/users", "/profile"];
  for (const user of [customer, employee, teamLead, manager]) {
    for (const item of moreItems(user, ALL_ON)) assert.ok(routed.includes(item.to), `${user.role} ${item.to}`);
  }
});

test("canConfigure: only the super admin sees Administration", () => {
  assert.equal(canConfigure({ role: "super_admin", is_agent: true }), true);
  for (const u of [manager, teamLead, employee, customer]) assert.equal(canConfigure(u), false);
});

test("customerGuard: customers are steered off /catalog and /approvals only", () => {
  assert.match(customerGuard("/catalog", customer), /service catalog/i);
  assert.match(customerGuard("/approvals", customer), /approvals/i);
  for (const p of ["/tickets", "/new", "/kb", "/profile", "/catalog/x"]) assert.equal(customerGuard(p, customer), null, p);
  assert.equal(GUARD_REDIRECT, "/tickets");
});

test("customerGuard: never applies to internal roles or a signed-out visitor", () => {
  for (const u of [employee, teamLead, manager]) {
    assert.equal(customerGuard("/catalog", u), null);
    assert.equal(customerGuard("/approvals", u), null);
  }
  assert.equal(customerGuard("/catalog", null), null);
  assert.equal(customerGuard("/catalog", undefined), null);
});

// CSAT network isolation (UI-DEC-010): a staff session in the same browser must not cause
// /api/meta/ or /api/tickets/counts/ to be requested while the public survey is open.
const staffSession = { access: "token", user: employee };

test("csatToken / isPublicRoute: match the public survey route only", () => {
  assert.equal(csatToken("/csat/abc-123"), "abc-123");
  assert.equal(csatToken("/csat/abc/extra"), "abc");
  for (const p of ["/csat", "/csat/", "/tickets", "/x/csat/abc", "/"]) {
    assert.equal(csatToken(p), null, p);
    assert.equal(isPublicRoute(p), false, p);
  }
  assert.equal(isPublicRoute("/csat/abc-123"), true);
});

test("loadsWorkspaceData: off on /csat/:token even with a staff session", () => {
  assert.equal(loadsWorkspaceData(staffSession, "/csat/abc-123"), false);
  assert.equal(loadsWorkspaceData({ access: "t", user: customer }, "/csat/abc-123"), false);
  assert.equal(loadsWorkspaceData(null, "/csat/abc-123"), false);
});

test("loadsWorkspaceData: on for every authenticated workspace route, off when signed out", () => {
  for (const p of ["/", "/tickets", "/tickets/9", "/new", "/catalog", "/approvals", "/dashboard", "/cmdb", "/kb/3", "/profile"]) {
    assert.equal(loadsWorkspaceData(staffSession, p), true, p);
    assert.equal(loadsWorkspaceData(null, p), false, p);
  }
});

test("App.jsx gates the /meta/ and /tickets/counts/ effects on loadsWorkspaceData", () => {
  const src = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");
  assert.match(src, /const workspace = loadsWorkspaceData\(auth, loc\.path\);/);
  // Each request must sit inside an effect that bails out first when `workspace` is false,
  // and that effect must re-run when `workspace` changes.
  // (post-U8: the metadata request goes through the bounded loader, which calls api("/meta/", { signal }))
  for (const call of ['loadWorkspaceMeta(api', 'api("/tickets/counts/")']) {
    const at = src.indexOf(call);
    assert.ok(at > 0, `${call} present`);
    const effectStart = src.lastIndexOf("useEffect(() => {", at);
    const effectEnd = src.indexOf("}, [", at);
    const body = src.slice(effectStart, at);
    assert.match(body, /if \(!workspace\)/, `${call} is gated`);
    assert.match(src.slice(effectEnd, effectEnd + 40), /\[workspace,/, `${call} effect depends on workspace`);
  }
  // The survey branch uses the same route matcher as the gate.
  assert.match(src, /const csat = csatToken\(loc\.path\);/);
});

test("every staff role opens on the Dashboard, listed first and named Dashboard; customers are unchanged", () => {
  for (const u of [manager, teamLead, employee]) {
    assert.equal(landsOnDashboard(u), true, u.role);
    assert.equal(homePath(u), "/dashboard", u.role);
    assert.deepEqual(moreItems(u, ALL_ON)[0], { key: "dashboard", to: "/dashboard", label: "Dashboard" }, u.role);
  }
  assert.equal(landsOnDashboard(customer), false);
  assert.equal(homePath(customer), "/");
  assert.ok(!keys(customer).includes("dashboard"));
});

test("switched-off features (catalog, approvals, CMDB) are absent from the More sheet by default", () => {
  assert.deepEqual(FEATURES, { catalog: false, approvals: false, cmdb: false, savedQueues: false });
  for (const u of [manager, teamLead, employee]) {
    const k = keys(u, FEATURES);
    for (const gone of ["catalog", "approvals", "cmdb"]) assert.ok(!k.includes(gone), `${u.role} ${gone}`);
    assert.equal(k[0], "dashboard");
    assert.ok(k.includes("profile"));
  }
  assert.deepEqual(keys(manager, FEATURES), ["dashboard", "profile"]);
  assert.deepEqual(keys({ role: "super_admin", is_agent: true }, FEATURES), ["overview", "users", "teams", "sla", "config", "audit-log", "profile"]);
  assert.deepEqual(keys(employee, FEATURES), ["dashboard", "profile"]);
  // turning one back on restores just that entry
  assert.deepEqual(keys(employee, { ...FEATURES, cmdb: true }), ["dashboard", "cmdb", "profile"]);
});

test("routes of switched-off features are disabled, others are not", () => {
  for (const p of ["/catalog", "/catalog/3", "/approvals", "/cmdb", "/cmdb/ci/9"]) assert.equal(isDisabledPath(p), true, p);
  for (const p of ["/", "/dashboard", "/tickets", "/tickets/4", "/kb", "/settings/config", "/catalogue"]) assert.equal(isDisabledPath(p), false, p);
  assert.equal(isDisabledPath("/catalog", ALL_ON), false);
});

test("staff queues are listed in the rail as: All open, Unassigned, At risk or breached, Major incidents, Assigned to me, Resolved and closed", () => {
  const original = [{ key: "mine" }, { key: "unassigned" }, { key: "sla" }, { key: "major" }, { key: "open" }, { key: "done" }];
  assert.deepEqual(RAIL_VIEW_ORDER, ["open", "unassigned", "sla", "major", "mine", "done"]);
  assert.deepEqual(railViews(original).map((v) => v.key), ["open", "unassigned", "sla", "major", "mine", "done"]);
  assert.equal(original[0].key, "mine", "the source list (and so the default queue) is not reordered");
});

test("the super admin is Administration-only: opens on Administration, no dashboard, tickets or knowledge base", () => {
  const sa = { role: "super_admin", is_agent: true };
  assert.equal(isAdministrationOnly(sa), true);
  for (const u of [manager, teamLead, employee, customer]) assert.equal(isAdministrationOnly(u), false, u.role);
  assert.equal(landsOnDashboard(sa), false);
  assert.equal(homePath(sa), "/settings/overview");
  for (const p of ["/settings/users", "/settings/teams", "/settings/audit-log", "/settings/config/x", "/profile"]) assert.equal(adminOnlyPathAllowed(p), true, p);
  for (const p of ["/", "/tickets", "/tickets/9", "/new", "/dashboard", "/kb", "/kb/3", "/settingsx"]) assert.equal(adminOnlyPathAllowed(p), false, p);
});
