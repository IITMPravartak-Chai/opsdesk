// UI feature switches. A switched-off feature disappears from the interface (menus, command palette, routes and the
// matching Configuration sections) but nothing is deleted: its pages, helpers, API routes and data all stay in place.
// To bring a feature back, set its flag to true and rebuild the web image.
export const FEATURES = {
  catalog: false,   // Service catalog
  approvals: false, // Approvals queue (and the approval requests on a ticket)
  cmdb: false,      // CMDB Explorer (and the Configuration management section)
  savedQueues: false, // "Save Queue" on the ticket list (the saved-queues API and data stay)
};

export const ALL_ON = { catalog: true, approvals: true, cmdb: true, savedQueues: true };

const PATH_FEATURES = [[/^\/catalog(\/|$)/, "catalog"], [/^\/approvals(\/|$)/, "approvals"], [/^\/cmdb(\/|$)/, "cmdb"]];

/** The feature a path belongs to, or null. */
export const featureOfPath = (path) => PATH_FEATURES.find(([re]) => re.test(path))?.[1] || null;

/** Pages that moved: the old path now opens the new one (the query string is kept). */
export const MOVED = { "/integrations": "/settings/config/channels" };

/** True when `path` belongs to a switched-off feature: such a route is never rendered. */
export const isDisabledPath = (path, features = FEATURES) => {
  const f = featureOfPath(path);
  return f !== null && !features[f];
};
