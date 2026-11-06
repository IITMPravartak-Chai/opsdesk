import React, { useEffect, useState, useMemo } from "react";
import { api } from "../api.js";
import { cx } from "../util.js";
import { RELATION_LABELS, TYPE_COLORS, graphSummary, layoutGraphNodes, truncationNotice } from "./cmdbHelpers.js";

const PAGE = 100; // the list endpoints are paginated (max page size 100)
const listOf = (res) => (res && Array.isArray(res.results) ? res.results : Array.isArray(res) ? res : []);
const totalOf = (res, list) => (res && Number.isFinite(res.count) ? res.count : list.length);

export default function CmdbExplorer() {
  const [ciList, setCiList] = useState([]);
  const [serviceList, setServiceList] = useState([]);
  const [catalogue, setCatalogue] = useState({ status: "loading", error: "", ciTotal: 0, srvTotal: 0 });
  const [impactError, setImpactError] = useState("");
  const [impactAttempt, setImpactAttempt] = useState(0);
  const [selectedType, setSelectedType] = useState("ci"); // "ci" or "service"
  const [selectedId, setSelectedId] = useState("");
  const [depth, setDepth] = useState(2);
  const [direction, setDirection] = useState("both");
  const [viewMode, setViewMode] = useState("graph"); // "graph" or "table"
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [graphData, setGraphData] = useState(null);
  const [activeNode, setActiveNode] = useState(null);
  const [nodeImpact, setNodeImpact] = useState(null);
  const [impactLoading, setImpactLoading] = useState(false);

  // Load initial CIs and Services list
  useEffect(() => {
    let alive = true;
    Promise.all([api(`/cmdb/items/?page_size=${PAGE}`), api(`/cmdb/services/?page_size=${PAGE}`)])
      .then(([ciRes, srvRes]) => {
        if (!alive) return;
        const cis = listOf(ciRes), srvs = listOf(srvRes);
        setCiList(cis);
        setServiceList(srvs);
        setCatalogue({ status: "ready", error: "", ciTotal: totalOf(ciRes, cis), srvTotal: totalOf(srvRes, srvs) });
        if (cis.length > 0) {
          setSelectedId(String(cis[0].id));
        } else if (srvs.length > 0) {
          setSelectedType("service");
          setSelectedId(String(srvs[0].id));
        }
      })
      .catch((err) => {
        if (alive) setCatalogue({ status: "error", error: err.message || "The CMDB catalogue couldn't be loaded.", ciTotal: 0, srvTotal: 0 });
      });
    return () => {
      alive = false;
    };
  }, []);

  // Fetch Graph whenever selection or filters change
  const fetchGraph = () => {
    if (!selectedId) return;
    setLoading(true);
    setError(null);
    const endpoint =
      selectedType === "service"
        ? `/cmdb/services/${selectedId}/graph/?depth=${depth}&direction=${direction}`
        : `/cmdb/items/${selectedId}/graph/?depth=${depth}&direction=${direction}`;

    api(endpoint)
      .then((data) => {
        setGraphData(data);
        const root = data.nodes?.find((n) => String(n.id) === String(data.root_id)) || data.nodes?.[0];
        setActiveNode(root || null);
        setLoading(false);
      })
      .catch((err) => {
        // Never leave the previous item's graph on screen next to this item's error.
        setGraphData(null);
        setActiveNode(null);
        setError(err.message || "The dependency graph couldn't be loaded.");
        setLoading(false);
      });
  };

  useEffect(() => {
    fetchGraph();
  }, [selectedType, selectedId, depth, direction]);

  // Fetch node impact when activeNode changes
  useEffect(() => {
    setImpactError("");
    if (!activeNode || String(activeNode.id).startsWith("srv-")) {
      setNodeImpact(null);
      return;
    }
    let alive = true;
    setImpactLoading(true);
    api(`/cmdb/items/${activeNode.id}/impact/`)
      .then((imp) => {
        if (!alive) return;
        setNodeImpact(imp);
        setImpactLoading(false);
      })
      .catch((err) => {
        if (!alive) return;
        // A failure is not "no impact": say so instead of showing an empty result.
        setNodeImpact(null);
        setImpactError(err.message || "Impact couldn't be calculated.");
        setImpactLoading(false);
      });
    return () => { alive = false; };
  }, [activeNode?.id, impactAttempt]);

  const summary = graphSummary(graphData);
  const notices = [truncationNotice("configuration items", ciList.length, catalogue.ciTotal),
    truncationNotice("services", serviceList.length, catalogue.srvTotal)].filter(Boolean);
  const nodeLabel = (n) => `${n.name}, ${n.type_label || n.ci_type || "item"}, ${RELATION_LABELS[n.relation] || "Connected"}`;

  const positionedNodes = useMemo(() => {
    if (!graphData || !graphData.nodes) return [];
    return layoutGraphNodes(graphData.nodes, graphData.links || [], graphData.root_id, 760, 480);
  }, [graphData]);

  const nodeMap = useMemo(() => {
    const map = {};
    positionedNodes.forEach((n) => {
      map[String(n.id)] = n;
    });
    return map;
  }, [positionedNodes]);

  return (
    <div className="page cmdb-page" style={{ padding: "20px 24px", maxWidth: 1200, margin: "0 auto" }}>
      <header className="page-header" style={{ marginBottom: 20 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 12 }}>
          <div>
            <h1 style={{ margin: 0, fontSize: 24, fontWeight: 700 }}>CMDB & Dependency Explorer</h1>
            <p className="muted" style={{ margin: "4px 0 0", fontSize: 13.5 }}>
              Visual relationship explorer for business services, configuration items, and infrastructure dependencies.
            </p>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button
              type="button"
              className={cx("btn sm", viewMode === "graph" ? "primary" : "ghost")}
              onClick={() => setViewMode("graph")}
              aria-pressed={viewMode === "graph"}
            >
              Visual Graph
            </button>
            <button
              type="button"
              className={cx("btn sm", viewMode === "table" ? "primary" : "ghost")}
              onClick={() => setViewMode("table")}
              aria-pressed={viewMode === "table"}
            >
              Accessible Table
            </button>
          </div>
        </div>
      </header>

      {/* Control bar */}
      <div
        className="card control-bar"
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 16,
          alignItems: "center",
          padding: "12px 16px",
          marginBottom: 20,
          background: "var(--card-bg, #fff)",
          border: "1px solid var(--border-color, #e2e8f0)",
          borderRadius: 8,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <label htmlFor="cmdb-entity-type" style={{ fontSize: 13, fontWeight: 600 }}>Target:</label>
          <select
            id="cmdb-entity-type"
            value={selectedType}
            onChange={(e) => {
              const next = e.target.value;
              setSelectedType(next);
              if (next === "ci" && ciList.length > 0) setSelectedId(String(ciList[0].id));
              if (next === "service" && serviceList.length > 0) setSelectedId(String(serviceList[0].id));
            }}
            style={{ padding: "4px 8px", borderRadius: 4, border: "1px solid #cbd5e1", fontSize: 13 }}
          >
            <option value="ci">Configuration Item (CI)</option>
            <option value="service">Business Service</option>
          </select>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8, flex: 1, minWidth: 220 }}>
          <label htmlFor="cmdb-root-select" style={{ fontSize: 13, fontWeight: 600 }}>Item:</label>
          <select
            id="cmdb-root-select"
            value={selectedId}
            onChange={(e) => setSelectedId(e.target.value)}
            style={{ padding: "4px 8px", borderRadius: 4, border: "1px solid #cbd5e1", fontSize: 13, width: "100%" }}
          >
            {selectedType === "ci"
              ? ciList.map((ci) => (
                  <option key={ci.id} value={ci.id}>
                    {ci.name} ({ci.type_label || ci.ci_type} - {ci.environment})
                  </option>
                ))
              : serviceList.map((srv) => (
                  <option key={srv.id} value={srv.id}>
                    {srv.name} (Criticality: {srv.criticality})
                  </option>
                ))}
          </select>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <label htmlFor="cmdb-direction-select" style={{ fontSize: 13, fontWeight: 600 }}>Direction:</label>
          <select
            id="cmdb-direction-select"
            value={direction}
            onChange={(e) => setDirection(e.target.value)}
            style={{ padding: "4px 8px", borderRadius: 4, border: "1px solid #cbd5e1", fontSize: 13 }}
          >
            <option value="both">Both (Dependencies & Dependants)</option>
            <option value="upstream">Upstream Dependencies Only</option>
            <option value="downstream">Downstream Dependants Only</option>
          </select>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <label htmlFor="cmdb-depth-select" style={{ fontSize: 13, fontWeight: 600 }}>Depth:</label>
          <select
            id="cmdb-depth-select"
            value={depth}
            onChange={(e) => setDepth(Number(e.target.value))}
            style={{ padding: "4px 8px", borderRadius: 4, border: "1px solid #cbd5e1", fontSize: 13 }}
          >
            <option value={1}>1 (Direct)</option>
            <option value={2}>2 (Transitive)</option>
            <option value={3}>3 (Deep)</option>
            <option value={4}>4 (Extended)</option>
            <option value={5}>5 (Maximum)</option>
          </select>
        </div>

        <button type="button" className="btn ghost sm" onClick={fetchGraph} disabled={loading}>
          {loading ? "Loading..." : "Refresh"}
        </button>
      </div>

      {catalogue.status === "error" && (
        <div className="notice bad" role="alert" style={{ marginBottom: 20 }}>
          The CMDB catalogue couldn't be loaded: {catalogue.error}
        </div>
      )}
      {catalogue.status === "loading" && <div className="small muted" role="status" style={{ marginBottom: 12 }}>Loading the CMDB catalogue…</div>}
      {catalogue.status === "ready" && ciList.length === 0 && serviceList.length === 0 && (
        <div className="notice" role="status" style={{ marginBottom: 20 }}>No configuration items or services are recorded yet.</div>
      )}
      {notices.length > 0 && <div className="small muted" role="note" style={{ marginBottom: 12 }}>{notices.join(" ")}</div>}

      {error && (
        <div className="notice bad" role="alert" style={{ marginBottom: 20, display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
          <span><strong>The dependency graph couldn't be loaded:</strong> {error}</span>
          <button type="button" className="btn sm" onClick={fetchGraph}>Retry</button>
        </div>
      )}

      {loading && !graphData && (
        <div role="status" style={{ padding: 40, textAlign: "center", color: "#64748b" }}>
          Tracing dependency paths across configuration items...
        </div>
      )}
      {!loading && summary && <div className="small muted" role="status" style={{ marginBottom: 12 }}>{summary}</div>}

      {/* Main content grid */}
      <div className="cmdb-grid">
        {/* Left Side: Graph or Table View */}
        <div
          className="card graph-canvas-card"
          style={{
            background: "#ffffff",
            border: "1px solid #e2e8f0",
            borderRadius: 8,
            overflow: "hidden",
            minHeight: 500,
          }}
        >
          {viewMode === "graph" ? (
            <div style={{ position: "relative", width: "100%", background: "#f8fafc" }}>
              <svg width="100%" height="500" viewBox="0 0 760 480" preserveAspectRatio="xMidYMid meet" role="group"
                aria-label="Dependency graph. Each item is a button; the Accessible Table view lists the same items.">
                <title>Dependency graph</title>
                <defs>
                  <marker
                    id="arrow-upstream"
                    viewBox="0 0 10 10"
                    refX="22"
                    refY="5"
                    markerWidth="6"
                    markerHeight="6"
                    orient="auto-start-reverse"
                  >
                    <path d="M 0 0 L 10 5 L 0 10 z" fill="#3b82f6" />
                  </marker>
                  <marker
                    id="arrow-downstream"
                    viewBox="0 0 10 10"
                    refX="22"
                    refY="5"
                    markerWidth="6"
                    markerHeight="6"
                    orient="auto-start-reverse"
                  >
                    <path d="M 0 0 L 10 5 L 0 10 z" fill="#f59e0b" />
                  </marker>
                </defs>

                {/* Draw links */}
                {graphData?.links?.map((link, idx) => {
                  const src = nodeMap[String(link.source)];
                  const tgt = nodeMap[String(link.target)];
                  if (!src || !tgt) return null;
                  const isUpstream = link.direction === "upstream";
                  return (
                    <g key={`link-${idx}`}>
                      <line
                        x1={src.x}
                        y1={src.y}
                        x2={tgt.x}
                        y2={tgt.y}
                        stroke={isUpstream ? "#93c5fd" : "#fcd34d"}
                        strokeWidth="2"
                        strokeDasharray={link.relation === "provides" ? "4,4" : "none"}
                        markerEnd={isUpstream ? "url(#arrow-upstream)" : "url(#arrow-downstream)"}
                      />
                    </g>
                  );
                })}

                {/* Draw nodes */}
                {positionedNodes.map((node) => {
                  const isSelected = activeNode && String(activeNode.id) === String(node.id);
                  const isRoot = String(node.id) === String(graphData?.root_id);
                  const color = TYPE_COLORS[node.ci_type || node.type] || "#64748b";

                  return (
                    <g
                      key={`node-${node.id}`}
                      className="cmdb-node"
                      transform={`translate(${node.x}, ${node.y})`}
                      onClick={() => setActiveNode(node)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          setActiveNode(node);
                        }
                      }}
                      role="button"
                      tabIndex={0}
                      aria-label={nodeLabel(node)}
                      aria-pressed={Boolean(isSelected)}
                      style={{ cursor: "pointer" }}
                    >
                      <circle
                        r={isRoot ? 26 : 20}
                        fill={color}
                        stroke={isSelected ? "#0f172a" : isRoot ? "#38bdf8" : "#ffffff"}
                        strokeWidth={isSelected ? 3 : 2}
                        filter="drop-shadow(0 2px 4px rgba(0,0,0,0.15))"
                      />
                      {isRoot && (
                        <circle r="32" fill="none" stroke={color} strokeWidth="1.5" strokeDasharray="3,3" />
                      )}
                      <text
                        y={isRoot ? 42 : 34}
                        textAnchor="middle"
                        fill="#1e293b"
                        fontSize={isRoot ? "12.5" : "11"}
                        fontWeight={isRoot ? "700" : "500"}
                      >
                        {node.name.length > 22 ? `${node.name.slice(0, 20)}...` : node.name}
                      </text>
                      <text
                        y={isRoot ? 54 : 46}
                        textAnchor="middle"
                        fill="#64748b"
                        fontSize="9.5"
                      >
                        {node.type_label || node.ci_type}
                      </text>
                    </g>
                  );
                })}
              </svg>

              {/* Legend below the graph: as an overlay it hid nodes laid out near the bottom edge */}
              <div
                style={{
                  margin: "0 12px 12px",
                  width: "fit-content",
                  maxWidth: "calc(100% - 24px)",
                  background: "rgba(255,255,255,0.92)",
                  padding: "8px 12px",
                  borderRadius: 6,
                  border: "1px solid #e2e8f0",
                  fontSize: 11,
                  display: "flex",
                  gap: 12,
                  flexWrap: "wrap",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#2563eb", display: "inline-block" }} /> Application
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#7c3aed", display: "inline-block" }} /> Database
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#d97706", display: "inline-block" }} /> Infrastructure
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#059669", display: "inline-block" }} /> AI Model
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#e11d48", display: "inline-block" }} /> Service
                </div>
              </div>
            </div>
          ) : (
            <div style={{ padding: 16, overflowX: "auto" }}>
              <table className="table" style={{ width: "100%", fontSize: 13 }}>
                <thead>
                  <tr style={{ textAlign: "left", borderBottom: "2px solid #e2e8f0" }}>
                    <th style={{ padding: "8px 12px" }}>Item Name</th>
                    <th style={{ padding: "8px 12px" }}>Type</th>
                    <th style={{ padding: "8px 12px" }}>Environment</th>
                    <th style={{ padding: "8px 12px" }}>Service</th>
                    <th style={{ padding: "8px 12px" }}>Relation to Root</th>
                    <th style={{ padding: "8px 12px" }}>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {positionedNodes.map((n) => {
                    const isRoot = String(n.id) === String(graphData?.root_id);
                    return (
                      <tr
                        key={n.id}
                        style={{
                          borderBottom: "1px solid #f1f5f9",
                          background: activeNode && String(activeNode.id) === String(n.id) ? "#f8fafc" : "transparent",
                        }}
                      >
                        <td style={{ padding: "8px 12px", fontWeight: isRoot ? 700 : 500 }}>
                          {n.name} {isRoot && <span className="badge hot" style={{ marginLeft: 6 }}>Root</span>}
                        </td>
                        <td style={{ padding: "8px 12px" }}>{n.type_label || n.ci_type}</td>
                        <td style={{ padding: "8px 12px" }}>{n.environment || "—"}</td>
                        <td style={{ padding: "8px 12px" }}>{n.service?.name || "—"}</td>
                        <td style={{ padding: "8px 12px" }}>{RELATION_LABELS[n.relation] || RELATION_LABELS.connected}</td>
                        <td style={{ padding: "8px 12px", whiteSpace: "nowrap" }}>
                          <button type="button" className="btn ghost sm" onClick={() => setActiveNode(n)}
                            aria-label={`Show details and impact for ${n.name}`}>
                            Details
                          </button>
                          {!isRoot && !String(n.id).startsWith("srv-") && (
                            <button
                              type="button"
                              className="btn ghost sm"
                              aria-label={`Explore dependencies of ${n.name}`}
                              onClick={() => {
                                setSelectedType("ci");
                                setSelectedId(String(n.id));
                              }}
                            >
                              Focus
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Right Side: Active Node Details & Impact Panel */}
        <div
          className="card node-details-panel"
          style={{
            background: "#ffffff",
            border: "1px solid #e2e8f0",
            borderRadius: 8,
            padding: 16,
          }}
        >
          {activeNode ? (
            <div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
                <div>
                  <span
                    className="badge"
                    style={{
                      background: TYPE_COLORS[activeNode.ci_type || activeNode.type] || "#64748b",
                      color: "#fff",
                      fontSize: 11,
                      padding: "2px 8px",
                      borderRadius: 12,
                    }}
                  >
                    {activeNode.type_label || activeNode.ci_type || "Entity"}
                  </span>
                  <h3 style={{ margin: "8px 0 2px", fontSize: 16, fontWeight: 700 }}>{activeNode.name}</h3>
                  <div className="small muted">ID: {activeNode.id}</div>
                </div>
              </div>

              <div style={{ fontSize: 12.5, lineHeight: 1.6, borderTop: "1px solid #f1f5f9", paddingTop: 10 }}>
                {activeNode.environment && (
                  <div>
                    <span className="muted">Environment:</span> <strong>{activeNode.environment}</strong>
                  </div>
                )}
                {activeNode.tech_stack && (
                  <div>
                    <span className="muted">Tech stack:</span> <span>{activeNode.tech_stack}</span>
                  </div>
                )}
                {activeNode.service && (
                  <div>
                    <span className="muted">Service:</span> <strong>{activeNode.service.name}</strong>
                  </div>
                )}
                {activeNode.owner_team && (
                  <div>
                    <span className="muted">Owner team:</span> <span>{activeNode.owner_team}</span>
                  </div>
                )}
                {activeNode.status && (
                  <div>
                    <span className="muted">Status:</span> <span>{activeNode.status}</span>
                  </div>
                )}
              </div>

              {/* Pivot / Focus button */}
              {!String(activeNode.id).startsWith("srv-") && String(activeNode.id) !== String(selectedId) && (
                <button
                  type="button"
                  className="btn primary sm"
                  style={{ width: "100%", marginTop: 14 }}
                  onClick={() => {
                    setSelectedType("ci");
                    setSelectedId(String(activeNode.id));
                  }}
                >
                  Make Focus Root
                </button>
              )}

              {/* Downstream Impact & Open Tickets */}
              <div style={{ marginTop: 20, borderTop: "1px solid #e2e8f0", paddingTop: 14 }}>
                <h4 style={{ margin: "0 0 8px", fontSize: 13, fontWeight: 700 }}>Impacted Downstream Services</h4>
                {impactLoading ? (
                  <div className="small muted" role="status">Calculating impact...</div>
                ) : impactError ? (
                  <div className="notice bad" role="alert" style={{ fontSize: 12.5 }}>
                    Impact couldn't be calculated: {impactError}{" "}
                    <button type="button" className="btn ghost sm" onClick={() => setImpactAttempt((n) => n + 1)}>Retry</button>
                  </div>
                ) : nodeImpact ? (
                  <div>
                    <div style={{ fontSize: 12, marginBottom: 8 }}>
                      <strong>{nodeImpact.impacted?.length || 0}</strong> transitive dependent CI(s)
                    </div>
                    {nodeImpact.impacted && nodeImpact.impacted.length > 0 && (
                      <ul style={{ margin: 0, paddingLeft: 18, fontSize: 11.5, color: "#475569" }}>
                        {nodeImpact.impacted.slice(0, 5).map((ci) => (
                          <li key={ci.id}>
                            {ci.name} ({ci.type_label})
                          </li>
                        ))}
                        {nodeImpact.impacted.length > 5 && <li>...and {nodeImpact.impacted.length - 5} more</li>}
                      </ul>
                    )}

                    <h4 style={{ margin: "14px 0 6px", fontSize: 13, fontWeight: 700 }}>Open Incidents on CI</h4>
                    {nodeImpact.open_tickets && nodeImpact.open_tickets.length > 0 ? (
                      <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
                        {nodeImpact.open_tickets.map((t) => (
                          <li key={t.id}>
                            <a href={`/tickets/${t.id}`} style={{ fontWeight: 600, color: "#2563eb" }}>
                              {t.number}
                            </a>
                            : {t.subject}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <div className="small muted">No active open incidents.</div>
                    )}
                  </div>
                ) : (
                  <div className="small muted">
                    {String(activeNode.id).startsWith("srv-")
                      ? "Impact is calculated per configuration item; select one of this service's items."
                      : "Select a CI node to inspect impact."}
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div style={{ color: "#64748b", fontSize: 13, textAlign: "center", padding: 24 }}>
              Click any node in the graph to view properties, dependencies, and impacted tickets.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
