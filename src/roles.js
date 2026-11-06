// Role groups, mirroring the backend (db/models/core.py). A super admin has the access of a manager for now, so every
// check that means "manager-level" goes through these helpers; give super_admin extra powers by branching on its name.
export const TOP_ROLES = ["super_admin", "manager"]; // SLA, audit log, passwords, role changes, deactivation
export const PRIVILEGED_ROLES = ["super_admin", "manager", "team_lead"]; // all tickets, users, teams, configuration
export const STAFF_ROLES = ["super_admin", "manager", "team_lead", "employee"]; // is_agent on the backend

export const ADMIN_ROLES = ["super_admin"]; // the Administration area: users, teams, SLA, configuration, audit log
export const isAdminRole = (user) => ADMIN_ROLES.includes(user?.role);
export const isTopRole = (user) => TOP_ROLES.includes(user?.role);
export const isPrivilegedRole = (user) => PRIVILEGED_ROLES.includes(user?.role);
