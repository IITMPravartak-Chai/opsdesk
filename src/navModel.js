// Navigation model shared by the desktop/tablet rail, the B1 mobile tabbar and the More sheet
// (U2.3 / U2.4; UI-DEC-002, UI-DEC-005, UI-DEC-011). Pure functions so they can be unit tested.
// Role facts mirror the backend User model: STAFF_ROLES = super_admin/manager/team_lead/employee (is_agent), customer = external.

import { FEATURES } from "./features.js";
import { isAdminRole } from "./roles.js";
import { settingsTabs } from "./pages/adminHelpers.js";

// Public, unauthenticated routes (UI-DEC-010). The CSAT survey must not trigger workspace requests
// even when a staff session already exists in the same browser.
const CSAT_ROUTE = /^\/csat\/([\w-]+)/;
export const csatToken = (path) => path.match(CSAT_ROUTE)?.[1] || null;
export const isPublicRoute = (path) => csatToken(path) !== null;

/** Whether the authenticated workspace (meta, ticket counts) should be loaded for this route. */
export const loadsWorkspaceData = (auth, path) => Boolean(auth) && !isPublicRoute(path);

/** Message shown when the workspace metadata can't be loaded, instead of an endless placeholder
 * (Backend Stabilization W2). A 403 means this account may not open the workspace here. */
export const workspaceLoadError = (err) =>
  err?.timedOut
    ? "Your workspace is taking longer than expected to load. Check your connection, then try again."
    : err?.status === 403
    ? "Your account can't open the workspace on this server. Sign out, or ask your administrator for access."
    : `Your workspace couldn't be loaded${err?.message ? `: ${err.message}` : "."}`;

export const sectionOf = (path) =>
  path === "/" || path.startsWith("/tickets") ? "tickets" : path.slice(1).split("/")[0];

/** Order of the staff queues in the left rail. Display only: the list in App.jsx keeps "Assigned to me" first, which is
 * the queue that opens by default. */
export const RAIL_VIEW_ORDER = ["open", "unassigned", "sla", "major", "mine", "done"];
export const railViews = (views) => [...views].sort((a, b) => RAIL_VIEW_ORDER.indexOf(a.key) - RAIL_VIEW_ORDER.indexOf(b.key));

/** Every staff role opens on the Dashboard and sees the Workspace group above the queues. Customers have no
 * dashboard (the server refuses it), so they keep their request list. */
export const landsOnDashboard = (user) => Boolean(user?.is_agent) && !isAdministrationOnly(user);
export const homePath = (user) => (isAdministrationOnly(user) ? ADMIN_HOME : landsOnDashboard(user) ? "/dashboard" : "/");

/** The super admin only runs the site: Administration and its own profile, no tickets, dashboard or knowledge base. The
 * server still authorises every request; this keeps the interface to what the role is for. */
export const isAdministrationOnly = (user) => isAdminRole(user);
export const ADMIN_HOME = "/settings/overview"; // the super admin's dashboard
export const adminOnlyPathAllowed = (path) => path === "/profile" || path === "/settings" || path.startsWith("/settings/");

/** Screens housed in the mobile More sheet; the More tab is highlighted while on any of them. */
export const MORE_SECTIONS = ["dashboard", "approvals", "cmdb", "settings", "profile"];
export const isMoreSection = (section) => MORE_SECTIONS.includes(section);

/** Rail primary action: staff open a new ticket, customers raise a request. */
export function primaryAction(user) {
  return { to: "/new", label: user.is_agent ? "New ticket" : "Raise a request" };
}

/** B1 destination 2: "New" (/new) for customers and staff. */
export function createTab(user) {
  return { to: "/new", label: "New", name: user.is_agent ? "New ticket" : "Raise a request" };
}
export const isCreateSection = (section) => section === "new" || section === "catalog";

/** Secondary destinations in the More sheet, in display order. */
export function moreItems(user, features = FEATURES) {
  if (isAdministrationOnly(user)) {
    return [{ key: "overview", to: ADMIN_HOME, label: "Dashboard" }, ...settingsTabs(user).map(({ key, to, label }) => ({ key, to, label })), { key: "profile", to: "/profile", label: "My profile" }];
  }
  const internal = user.role !== "customer";
  const dashboard = user.is_agent && { key: "dashboard", to: "/dashboard", label: "Dashboard" };
  return [
    landsOnDashboard(user) && dashboard,
    // Staff reach the catalog from the rail on wider screens; keep it reachable on mobile too.
    features.catalog && user.is_agent && { key: "catalog", to: "/catalog", label: "Service catalog" },
    !landsOnDashboard(user) && dashboard,
    features.approvals && internal && { key: "approvals", to: "/approvals", label: "Approvals" },
    features.cmdb && user.is_agent && { key: "cmdb", to: "/cmdb", label: "CMDB Explorer" },
    // U7: Administration (users and teams for managers and team leads; SLA and the audit log for managers). The server
    // enforces each rule on every request.
    canConfigure(user) && { key: "settings", to: "/settings/users", label: "Administration" },
    { key: "profile", to: "/profile", label: "My profile" },
  ].filter(Boolean);
}

export const canConfigure = (user) => isAdminRole(user);

// Client-side customer route guard (UI-DEC-005). A UX aid that avoids dead-end screens;
// it is NOT a security boundary — the backend remains the authority for every request.
const CUSTOMER_BLOCKED = {
  "/catalog": "The service catalog is not available for your account type.",
  "/approvals": "Approvals are not available for your account type.",
};
export const GUARD_REDIRECT = "/tickets";

/** Notice text when `path` is a page customers are steered away from, else null. */
export function customerGuard(path, user) {
  if (!user || user.role !== "customer") return null;
  return CUSTOMER_BLOCKED[path] || null;
}
