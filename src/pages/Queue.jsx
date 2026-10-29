import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { FEATURES } from "../features.js";
import { isPrivilegedRole, isTopRole } from "../roles.js";
import { applyListFilters } from "./dashboardLinks.js";
import { PriorityMark, StateChip } from "../components/Chips.jsx";
import { Fuse } from "../components/Fuse.jsx";
import Modal from "../components/Modal.jsx";
import { cx, relTime, SOURCE_LABEL } from "../util.js";
import TicketDetail from "./TicketDetail.jsx";
import {
  BULK_LIMIT, QueueLoadMore, buildTicketQueryParams, loadTicketPages, selectAllLoaded, toggleSelected,
} from "./queuePagination.js";
import { bulkErrorMessage, bulkResultMessage, inFlightGuard, queueEmptyMessage, savedQueueErrorMessage, teamEmployees } from "./workflowHelpers.js";

export default function Queue({ ticketId }) {
  const { user, meta, loc, navigate, views, refreshCounts } = useApp();
  const viewKey = loc.search.get("view") || views[0].key;
  const view = views.find((v) => v.key === viewKey) || views[0];
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [account, setAccount] = useState(loc.search.get("account") || "");
  const [priority, setPriority] = useState(loc.search.get("priority") || "");
  const viewParams = applyListFilters(view.params, loc.search); // the queue plus any filters a Dashboard link put in the URL
  const viewParamsKey = JSON.stringify(viewParams);
  const urlFiltered = ["team", "source", "sla_status", "all"].some((k) => loc.search.get(k));
  const [page, setPage] = useState(1); // how many batches of tickets are loaded (the list scrolls; "Load more" adds one)
  const [data, setData] = useState(null);
  const [totalCount, setTotalCount] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const reqIdRef = useRef(0);

  // Phase 3: Selection, Bulk Actions, Saved Queues, and Keyboard Navigation
  const [selectedIds, setSelectedIds] = useState([]);
  const [savedQueues, setSavedQueues] = useState([]);
  const [activeSavedQueueId, setActiveSavedQueueId] = useState(null);
  const [bulkModal, setBulkModal] = useState(null); // 'assign' | 'priority' | null
  const [saveQueueModalOpen, setSaveQueueModalOpen] = useState(false);
  const [newQueueName, setNewQueueName] = useState("");
  const [newQueueShared, setNewQueueShared] = useState(false);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkFeedback, setBulkFeedback] = useState(null);
  // Saved queues (BLK-003): failures are shown, never swallowed; the endpoint may be unrouted.
  const [savedQueuesError, setSavedQueuesError] = useState("");
  const [saveQueueError, setSaveQueueError] = useState("");
  const [saveQueueBusy, setSaveQueueBusy] = useState(false);
  const saveQueueGuard = useRef(inFlightGuard());

  // Bulk modal state fields
  const [bulkAssignee, setBulkAssignee] = useState("");
  const [bulkTeam, setBulkTeam] = useState("");
  const [bulkPriority, setBulkPriority] = useState("P2");

  // Synchronously reset page & data during render when view changes so no stale request fires
  const [prevViewKey, setPrevViewKey] = useState(view.key);
  if (prevViewKey !== view.key) {
    setPrevViewKey(view.key);
    setPage(1);
    setData(null);
    setTotalCount(null);
    setSelectedIds([]);
  }

  // Debounce search query so typing is smooth and does not fire request per keystroke
  useEffect(() => {
    const t = setTimeout(() => {
      setDebouncedQ(q);
      setPage(1);
      setSelectedIds([]);
    }, q ? 250 : 0);
    return () => clearTimeout(t);
  }, [q]);

  const savedQueuesOn = FEATURES.savedQueues && user.is_agent;
  const loadSavedQueues = useCallback(async () => {
    if (!savedQueuesOn) return;
    try {
      const res = await api("/saved-queues/");
      setSavedQueues(res.results || res || []);
      setSavedQueuesError("");
    } catch (e) {
      setSavedQueuesError(savedQueueErrorMessage("load", e));
    }
  }, [savedQueuesOn]);

  useEffect(() => {
    loadSavedQueues();
  }, [loadSavedQueues]);

  const load = useCallback(async () => {
    const reqId = ++reqIdRef.current;
    setLoading(true);
    try {
      const res = await loadTicketPages(
        (p) => api(`/tickets/?${buildTicketQueryParams({ viewParams, q: debouncedQ, account, priority, page: p })}`),
        page,
      );
      if (reqId !== reqIdRef.current) return;
      setData(res);
      setTotalCount(res.count);
      setError("");
    } catch (e) {
      if (reqId !== reqIdRef.current) return;
      if (e.status === 404 && page > 1) {
        setPage(1);
      } else {
        setError(e.message);
      }
    } finally {
      if (reqId === reqIdRef.current) {
        setLoading(false);
      }
    }
  }, [viewParamsKey, debouncedQ, account, priority, page]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    load();
    const id = setInterval(load, 30000);
    return () => clearInterval(id);
  }, [load]);

  const loadMore = () => {
    if (loading || results.length >= countVal) return;
    setPage((p) => p + 1);
  };

  const handleAccountChange = (e) => {
    setAccount(e.target.value);
    setPage(1);
    setData(null);
    setSelectedIds([]);
  };

  const handlePriorityChange = (k) => {
    setPriority(k);
    setPage(1);
    setData(null);
    setSelectedIds([]);
  };

  const open = (id) => navigate(`/tickets/${id}?view=${view.key}`);
  const onChanged = () => { load(); refreshCounts(); };

  const countVal = totalCount ?? (data?.count || 0);
  const results = data?.results || [];

  // Multi-select helpers
  const allVisibleSelected = results.length > 0 && selectedIds.length >= Math.min(results.length, BULK_LIMIT) && results.slice(0, BULK_LIMIT).every((t) => selectedIds.includes(t.id));
  const limitNotice = (limited) => {
    if (!limited) return;
    setBulkFeedback(`You can select up to ${BULK_LIMIT} tickets at once.`);
    setTimeout(() => setBulkFeedback(null), 6000);
  };

  const toggleSelectAllVisible = () => {
    if (allVisibleSelected) {
      setSelectedIds([]);
    } else {
      const all = selectAllLoaded(results);
      setSelectedIds(all.ids);
      limitNotice(all.limited);
    }
  };

  const selectTicket = (id) => {
    const next = toggleSelected(selectedIds, id);
    setSelectedIds(next.ids);
    limitNotice(next.limited);
  };

  const toggleSelectTicket = (id, e) => {
    e.stopPropagation();
    selectTicket(id);
  };

  // Bulk action submission
  const executeBulkAction = async (actionPayload) => {
    setBulkBusy(true);
    try {
      const res = await api("/tickets/bulk/", {
        method: "POST",
        body: {
          ticket_ids: selectedIds,
          ...actionPayload,
        },
      });
      setBulkFeedback(bulkResultMessage(res));
      setSelectedIds([]);
      setBulkModal(null);
      load();
      refreshCounts();
      setTimeout(() => setBulkFeedback(null), res.failed > 0 ? 15000 : 6000);
    } catch (e) {
      setBulkFeedback(bulkErrorMessage(e));
    } finally {
      setBulkBusy(false);
    }
  };

  // Saved queue apply & delete
  const applySavedQueue = (sq) => {
    setActiveSavedQueueId(sq.id);
    const p = sq.params || {};
    if (p.priority !== undefined) setPriority(p.priority);
    if (p.account !== undefined) setAccount(p.account);
    if (p.q !== undefined) setQ(p.q);
    setPage(1);
  };

  const deleteSavedQueue = async (sqId, e) => {
    e.stopPropagation();
    try {
      await api(`/saved-queues/${sqId}/`, { method: "DELETE" });
      setSavedQueues((prev) => prev.filter((q) => q.id !== sqId));
      if (activeSavedQueueId === sqId) setActiveSavedQueueId(null);
      setSavedQueuesError("");
    } catch (err) {
      setSavedQueuesError(savedQueueErrorMessage("delete", err));
    }
  };

  const saveCurrentQueue = async () => {
    if (!newQueueName.trim()) return;
    if (!saveQueueGuard.current.enter()) return;
    setSaveQueueBusy(true);
    setSaveQueueError("");
    try {
      const params = {};
      if (priority) params.priority = priority;
      if (account) params.account = account;
      if (debouncedQ) params.q = debouncedQ;
      if (view.params?.ordering) params.ordering = view.params.ordering;

      const res = await api("/saved-queues/", {
        method: "POST",
        body: {
          name: newQueueName.trim(),
          params,
          is_shared: newQueueShared,
        },
      });
      setSavedQueues((prev) => [...prev, res]);
      setSaveQueueModalOpen(false);
      setNewQueueName("");
      setNewQueueShared(false);
      setActiveSavedQueueId(res.id);
    } catch (err) {
      setSaveQueueError(savedQueueErrorMessage("save", err)); // dialog stays open; nothing is shown as saved
    } finally {
      saveQueueGuard.current.exit();
      setSaveQueueBusy(false);
    }
  };

  return (
    <div className={cx("queue-layout", ticketId && "has-detail")}>
      <section className="queue" aria-label="Ticket list">
        <div className="queue-head">
          <div className="row">
            {/* With a ticket open, the ticket title is the page heading (post-U8, U8-04: one h1 per page). */}
            {ticketId ? <h2 style={{ fontSize: 17 }}>{view.label}</h2> : <h1 style={{ fontSize: 17 }}>{view.label}</h1>}
            <span className="spacer" />
            {savedQueuesOn && (
              <button
                type="button"
                className="btn ghost sm"
                style={{ fontSize: 12, padding: "2px 8px" }}
                onClick={() => { setSaveQueueError(""); setSaveQueueModalOpen(true); }}
              >
                + Save Queue
              </button>
            )}
            {totalCount !== null && (
              <span className="muted small">
                {totalCount} ticket{totalCount === 1 ? "" : "s"}
              </span>
            )}
          </div>

          {urlFiltered && (
            <div className="row small" role="status" style={{ gap: 8, flexWrap: "wrap" }}>
              <span className="muted">Filtered:</span>
              {loc.search.get("all") === "1" && <span className="chip">all states</span>}
              {loc.search.get("sla_status") && <span className="chip">SLA {loc.search.get("sla_status").replace(/_/g, " ").replace(",", " or ")}</span>}
              {loc.search.get("team") && <span className="chip">team: {meta.teams.find((t) => String(t.id) === loc.search.get("team"))?.name || loc.search.get("team")}</span>}
              {loc.search.get("source") && <span className="chip">channel: {SOURCE_LABEL[loc.search.get("source")] || loc.search.get("source")}</span>}
              <a href={`/tickets?view=${view.key}`} onClick={(e) => { e.preventDefault(); navigate(`/tickets?view=${view.key}`); }}>Clear</a>
            </div>
          )}

          {/* Saved Queues Pills Bar */}
          {savedQueuesOn && savedQueuesError && (
            <div className="notice small" role="status" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
              <span>{savedQueuesError}</span>
              <button type="button" className="btn sm" onClick={loadSavedQueues}>Retry</button>
            </div>
          )}
          {savedQueuesOn && savedQueues.length > 0 && (
            <div className="saved-queues-bar">
              <span className="muted small" style={{ marginRight: 4 }}>Saved:</span>
              {savedQueues.map((sq) => (
                <div key={sq.id} className={cx("saved-queue-pill", activeSavedQueueId === sq.id && "active")}>
                  {/* A native button (not an ARIA-role div) so Enter/Space apply it, with the delete control as a sibling */}
                  <button type="button" className="pill-apply" onClick={() => applySavedQueue(sq)} aria-pressed={activeSavedQueueId === sq.id}>
                    {sq.is_shared ? "👥 " : ""}{sq.name}
                  </button>
                  {((sq.is_shared && isPrivilegedRole(user)) ||
                    (!sq.is_shared && (!sq.owner || sq.owner.id === user.id || isTopRole(user)))) && (
                    <button
                      type="button"
                      className="pill-del"
                      title="Delete saved queue"
                      aria-label={`Delete saved queue ${sq.name}`}
                      onClick={(e) => deleteSavedQueue(sq.id, e)}
                    >
                      ×
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}

          <input
            className="input"
            type="search"
            placeholder={user.is_agent ? "Search subject, description or INC-000123" : "Search your requests"}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            aria-label="Search tickets"
          />

          {user.is_agent && (
            <div className="row" style={{ alignItems: "center" }}>
              <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12.5, cursor: "pointer" }}>
                <input
                  type="checkbox"
                  className="trow-checkbox"
                  checked={allVisibleSelected}
                  onChange={toggleSelectAllVisible}
                  aria-label="Select all loaded tickets"
                />
                <span className="muted">Select all</span>
              </label>

              <select
                className="input"
                style={{ flex: 1, minHeight: 32 }}
                value={account}
                onChange={handleAccountChange}
                aria-label="Account"
              >
                <option value="">All accounts</option>
                {meta.accounts.map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </select>

              <div className="seg" role="group" aria-label="Priority">
                {["", ...meta.priorities.map((p) => p.key)].map((k) => (
                  <button
                    key={k || "all"}
                    type="button"
                    className={priority === k ? "on" : ""}
                    onClick={() => handlePriorityChange(k)}
                  >
                    {k || "All"}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Floating Bulk Action Bar */}
        {user.is_agent && selectedIds.length > 0 && (
          <div className="bulk-bar" role="toolbar" aria-label="Bulk actions">
            <span className="bulk-count">
              ✓ {selectedIds.length} ticket{selectedIds.length === 1 ? "" : "s"} selected
            </span>
            <button
              type="button"
              className="btn-bulk"
              onClick={() => setBulkModal("assign")}
            >
              Assign
            </button>
            <button
              type="button"
              className="btn-bulk"
              onClick={() => setBulkModal("priority")}
            >
              Set Priority
            </button>
            <button
              type="button"
              className="btn-bulk clear"
              onClick={() => setSelectedIds([])}
            >
              Clear
            </button>
          </div>
        )}

        {bulkFeedback && (
          <div className="notice" role="status" style={{ margin: "8px 12px" }}>
            {bulkFeedback}
          </div>
        )}

        <div className="queue-list">
          {error && (
            <div className="notice bad" role="alert" style={{ margin: 16, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
              <span>{error}</span>
              <button type="button" className="btn sm" onClick={() => load()}>Retry</button>
            </div>
          )}
          {!data && !error && <div className="empty" role="status">Loading tickets...</div>}
          {data && results.length === 0 && (
            <div className="empty">
              <b>Nothing here</b>
              <span>{queueEmptyMessage({ search: debouncedQ, filtered: Boolean(account || priority), isAgent: user.is_agent })}</span>
              {!user.is_agent && <button type="button" className="btn primary sm" onClick={() => navigate("/new")}>Raise a request</button>}
            </div>
          )}
          {results.map((t) => {
            const isSelected = selectedIds.includes(t.id);
            return (
              <button
                key={t.id}
                type="button"
                className={cx("trow", t.id === ticketId && "active")}
                onClick={() => open(t.id)}
              >
                <div className="trow-top">
                  {user.is_agent && (
                    <input
                      type="checkbox"
                      className="trow-checkbox"
                      checked={isSelected}
                      onClick={(e) => toggleSelectTicket(t.id, e)}
                      onChange={() => {}}
                      aria-label={`Select ticket ${t.number}`}
                    />
                  )}
                  <PriorityMark p={t.priority} />
                  <span>{t.number}</span>
                  {t.is_major_incident && <span className="chip major">Major</span>}
                  <span className="spacer" />
                  <span>{relTime(t.updated_at)}</span>
                </div>
                <div className="trow-subject">{t.subject}</div>
                <div className="trow-meta">
                  <StateChip state={t.state} />
                  {user.is_agent && t.account && <span>{t.account.name}</span>}
                  <span>{SOURCE_LABEL[t.source]}</span>
                  {user.is_agent && <span>{t.assignee ? t.assignee.name : "Unassigned"}</span>}
                </div>
                <RowFuse t={t} />
              </button>
            );
          })}
        </div>

        <QueueLoadMore shown={results.length} total={totalCount} loading={loading} onLoadMore={loadMore} />
      </section>

      {ticketId && (
        <section className="detail-pane" aria-label="Ticket detail">
          <TicketDetail key={ticketId} id={ticketId} onChanged={onChanged} onBack={() => navigate(`/tickets?view=${view.key}`)} />
        </section>
      )}

      {/* Bulk Assign Modal */}
      {bulkModal === "assign" && (
        <Modal
          title={`Bulk Assign (${selectedIds.length} tickets)`}
          onClose={() => setBulkModal(null)}
          footer={
            <>
              <button type="button" className="btn ghost sm" onClick={() => setBulkModal(null)}>Cancel</button>
              <button
                type="button"
                className="btn primary sm"
                disabled={bulkBusy || !bulkTeam}
                onClick={() =>
                  executeBulkAction({
                    action: "assign",
                    assignee_id: bulkAssignee ? Number(bulkAssignee) : null,
                    team_id: bulkTeam ? Number(bulkTeam) : null,
                  })
                }
              >
                {bulkBusy ? "Assigning..." : "Apply Assignment"}
              </button>
            </>
          }
        >
          <div className="stack" style={{ gap: 14 }}>
            <p className="small muted">Choose a team first, then optionally one of its employees.</p>
            <label className="field">
              <span>Team</span>
              <select className="input" value={bulkTeam} onChange={(e) => { setBulkTeam(e.target.value); setBulkAssignee(""); }}>
                <option value="">Select a team</option>
                {meta.teams?.map((tm) => (
                  <option key={tm.id} value={tm.id}>{tm.name}</option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Employee</span>
              <select className="input" value={bulkAssignee} disabled={!bulkTeam} onChange={(e) => setBulkAssignee(e.target.value)}>
                <option value="">{bulkTeam ? "Any (team only)" : "Select a team first"}</option>
                {teamEmployees(meta.teams, meta.agents, bulkTeam).map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </select>
            </label>
          </div>
        </Modal>
      )}

      {/* Bulk Set Priority Modal */}
      {bulkModal === "priority" && (
        <Modal
          title={`Bulk Set Priority (${selectedIds.length} tickets)`}
          onClose={() => setBulkModal(null)}
          footer={
            <>
              <button type="button" className="btn ghost sm" onClick={() => setBulkModal(null)}>Cancel</button>
              <button
                type="button"
                className="btn primary sm"
                disabled={bulkBusy}
                onClick={() =>
                  executeBulkAction({
                    action: "set_priority",
                    priority_key: bulkPriority,
                  })
                }
              >
                {bulkBusy ? "Updating..." : "Set Priority"}
              </button>
            </>
          }
        >
          <div className="stack" style={{ gap: 14 }}>
            <p className="small muted">Change priority for all selected tickets.</p>
            <label className="field">
              <span>Priority Level</span>
              <select className="input" value={bulkPriority} onChange={(e) => setBulkPriority(e.target.value)}>
                {meta.priorities?.map((p) => (
                  <option key={p.key} value={p.key}>{p.name} ({p.key})</option>
                ))}
              </select>
            </label>
          </div>
        </Modal>
      )}

      {/* Save Current Queue Modal */}
      {saveQueueModalOpen && (
        <Modal
          title="Save Queue Filter"
          onClose={() => setSaveQueueModalOpen(false)}
          footer={
            <>
              <button type="button" className="btn ghost sm" onClick={() => setSaveQueueModalOpen(false)}>Cancel</button>
              <button
                type="button"
                className="btn primary sm"
                disabled={!newQueueName.trim() || saveQueueBusy}
                onClick={saveCurrentQueue}
              >
                {saveQueueBusy ? "Saving…" : "Save Queue"}
              </button>
            </>
          }
        >
          <div className="stack" style={{ gap: 14 }}>
            <p className="small muted">
              Save your current search and filter settings to quickly access them anytime.
            </p>
            <label className="field">
              <span>Queue Name</span>
              <input
                className="input"
                type="text"
                placeholder="e.g. High Priority Incidents, Database Tickets"
                value={newQueueName}
                onChange={(e) => setNewQueueName(e.target.value)}
              />
            </label>
            {isPrivilegedRole(user) && (
              <label className="toggle">
                <input
                  type="checkbox"
                  checked={newQueueShared}
                  onChange={(e) => setNewQueueShared(e.target.checked)}
                />
                <span>Share with entire team (Tenant-wide)</span>
              </label>
            )}
            {saveQueueError && <div className="error" role="alert">{saveQueueError}</div>}
          </div>
        </Modal>
      )}
    </div>
  );
}

function RowFuse({ t }) {
  if (!t.resolution_due_at) return null;
  return (
    <div style={{ marginTop: 6 }}>
      <Fuse
        label="SLA"
        start={t.created_at}
        due={t.resolution_due_at}
        done={t.resolved_at}
        status={t.sla_status}
        paused={t.sla_status === "paused"}
      />
    </div>
  );
}
