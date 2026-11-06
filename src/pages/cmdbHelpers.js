export const TYPE_COLORS = {
  service: "#e11d48",
  application: "#2563eb",
  database: "#7c3aed",
  infrastructure: "#d97706",
  ai_model: "#059669",
  llm_app: "#0d9488",
  data_pipeline: "#0284c7",
  integration: "#4f46e5",
  other: "#64748b",
};

/**
 * Relation of every non-root node to the graph root, from the API's links (U6 E1):
 * "upstream" (the root depends on it, transitively), "downstream" (it depends on the root),
 * "component" (a CI a service root provides) or "connected". Decided per node from its own links;
 * the previous version classified every non-upstream node as downstream if any link qualified.
 */
export function classifyNodes(nodes, links, rootId) {
  const up = new Set(), down = new Set(), comp = new Set();
  for (const l of links || []) {
    if (l.relation === "provides") comp.add(String(l.target));
    else if (l.direction === "upstream") up.add(String(l.target));
    else if (l.direction === "downstream") down.add(String(l.source));
  }
  const out = {};
  for (const n of nodes || []) {
    const id = String(n.id);
    out[id] = id === String(rootId) ? "root" : up.has(id) ? "upstream" : down.has(id) ? "downstream" : comp.has(id) ? "component" : "connected";
  }
  return out;
}

export const RELATION_LABELS = {
  root: "Selected item",
  upstream: "Dependency (the selected item relies on it)",
  downstream: "Dependant (relies on the selected item)",
  component: "Component of the service",
  connected: "Connected",
};

/** Plain-language summary of a loaded graph; distinguishes "no dependencies" from a failure. */
export function graphSummary(graph) {
  if (!graph || !Array.isArray(graph.nodes)) return "";
  const others = graph.nodes.length - 1;
  const scope = `within ${graph.depth} hop${graph.depth === 1 ? "" : "s"}, ${graph.direction === "both" ? "both directions" : graph.direction}`;
  return others <= 0 ? `No dependencies or dependants are recorded for this item (${scope}).` : `${others} related item${others === 1 ? "" : "s"} (${scope}).`;
}

/** Notice when a paginated list was not loaded completely, so a short dropdown isn't mistaken for the whole CMDB. */
export function truncationNotice(label, loaded, total) {
  return Number.isFinite(total) && total > loaded ? `Showing the first ${loaded} of ${total} ${label}.` : "";
}

export function layoutGraphNodes(nodes, links, rootId, width = 760, height = 480) {
  if (!nodes || nodes.length === 0) return [];
  const cx = width / 2;
  const cy = height / 2;

  if (nodes.length === 1) {
    return [{ ...nodes[0], x: cx, y: cy, isRoot: true, relation: "root" }];
  }

  // Identify root node
  const root = nodes.find((n) => String(n.id) === String(rootId)) || nodes[0];
  const others = nodes.filter((n) => String(n.id) !== String(root.id));
  const relation = classifyNodes(nodes, links, root.id);

  const upstream = [];
  const downstream = [];
  const neutral = [];

  others.forEach((n) => {
    const r = relation[String(n.id)];
    if (r === "upstream") upstream.push({ ...n, relation: r });
    else if (r === "downstream" || r === "component") downstream.push({ ...n, relation: r });
    else neutral.push({ ...n, relation: r });
  });

  const positioned = [{ ...root, x: cx, y: cy, isRoot: true, relation: "root" }];

  // Position upstream on the left
  const upCount = upstream.length;
  upstream.forEach((n, idx) => {
    const ySpread = height * 0.75;
    const y = upCount === 1 ? cy : cy - ySpread / 2 + (idx / (upCount - 1)) * ySpread;
    positioned.push({ ...n, x: cx - 220, y, isUpstream: true });
  });

  // Position downstream on the right
  const downCount = downstream.length;
  downstream.forEach((n, idx) => {
    const ySpread = height * 0.75;
    const y = downCount === 1 ? cy : cy - ySpread / 2 + (idx / (downCount - 1)) * ySpread;
    positioned.push({ ...n, x: cx + 220, y, isDownstream: true });
  });

  // Neutral or others around the perimeter
  const remCount = neutral.length;
  neutral.forEach((n, idx) => {
    const angle = (idx / (remCount || 1)) * 2 * Math.PI;
    positioned.push({
      ...n,
      x: cx + 180 * Math.cos(angle),
      y: cy + 140 * Math.sin(angle),
    });
  });

  return positioned;
}
