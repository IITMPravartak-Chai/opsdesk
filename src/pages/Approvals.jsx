import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import Modal from "../components/Modal.jsx";
import {
  canDecideApproval, formatApprovalBadge, filterApprovals,
  applyDecision, approvalsLoadErrorMessage, decisionErrorMessage, isStaleDecision, submitApprovalDecision,
} from "./approvalHelpers.js";
import { inFlightGuard } from "./workflowHelpers.js";

export default function Approvals() {
  const { user, navigate, refreshCounts } = useApp();
  const [approvals, setApprovals] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState("pending");
  const [feedback, setFeedback] = useState(null);

  // Decision Modal State
  const [decisionModal, setDecisionModal] = useState(null); // { item, type: "approved" | "rejected" }
  const [comment, setComment] = useState("");
  const [deciding, setDeciding] = useState(false);
  const [decideError, setDecideError] = useState(null);
  const [loaded, setLoaded] = useState(false); // counts are shown only for a list that actually loaded
  const decideGuard = useRef(inFlightGuard());

  // `silent` refreshes keep the current list on failure (used after a decision) and report the outcome.
  const loadApprovals = ({ silent = false } = {}) => {
    if (!silent) { setLoading(true); setError(null); }
    return api("/approvals/")
      .then((data) => {
        setApprovals(Array.isArray(data) ? data : data.results || []);
        setLoaded(true);
        setError(null);
        return true;
      })
      .catch((err) => {
        if (!silent) { setError(approvalsLoadErrorMessage(err)); setLoaded(false); } // BLK-002
        return false;
      })
      .finally(() => { if (!silent) setLoading(false); });
  };

  useEffect(() => {
    loadApprovals();
  }, []);

  const handleOpenDecision = (item, type) => {
    setDecisionModal({ item, type });
    setComment("");
    setDecideError(null);
  };

  const handleCloseDecision = () => {
    if (deciding) return;
    setDecisionModal(null);
    setComment("");
    setDecideError(null);
  };

  const handleSubmitDecision = async () => {
    if (!decisionModal || deciding) return;
    const { item, type } = decisionModal;

    setDeciding(true);
    setDecideError(null);

    // Same payload as before: {decision, comment}. One submission at a time.
    const r = await submitApprovalDecision(api, item.ticket.id, item.id, { decision: type, comment: comment.trim() }, { guard: decideGuard.current });
    if (r.skipped) return;
    setDeciding(false);
    if (!r.ok) {
      setDecideError(decisionErrorMessage(r.error));
      if (isStaleDecision(r.error)) loadApprovals({ silent: true }); // stale item: show the server's current state
      return;
    }

    // Confirmed by the server: mark it decided locally so it cannot be decided again before the refresh.
    setApprovals((prev) => applyDecision(prev, item.id, r.status));
    setDecisionModal(null);
    setComment("");
    refreshCounts();
    const label = `Request ${item.ticket.key || "#" + item.ticket.id} was ${r.status}.`;
    setFeedback(label);
    const refreshed = await loadApprovals({ silent: true });
    if (!refreshed) setFeedback(`${label} The list couldn't be refreshed, so it may be out of date.`);
  };

  const visibleApprovals = filterApprovals(approvals, filter);

  return (
    // width: 100% — with auto margins the page would otherwise size to the table's min-content (~632px)
    // and clip the filter buttons on phones; the table scrolls inside its own card instead.
    <div className="page" style={{ maxWidth: 1080, margin: "0 auto", width: "100%" }}>
      <div className="page-head">
        <div>
          <h1>Manager Approvals</h1>
          <div className="muted small">
            Review, authorize, or decline employee service and access requests.
          </div>
        </div>
        <div className="row" role="group" aria-label="Filter approvals" style={{ gap: 8 }}>
          <button
            type="button"
            className={`btn sm ${filter === "pending" ? "primary" : "ghost"}`}
            aria-pressed={filter === "pending"}
            onClick={() => setFilter("pending")}
          >
            {/* No count unless the list loaded: "Pending (0)" after a failure would claim nothing is pending */}
            Pending{loaded ? ` (${approvals.filter((a) => (a.status || a.decision || "pending") === "pending").length})` : ""}
          </button>
          <button
            type="button"
            className={`btn sm ${filter === "all" ? "primary" : "ghost"}`}
            aria-pressed={filter === "all"}
            onClick={() => setFilter("all")}
          >
            All History
          </button>
        </div>
      </div>

      {feedback && (
        <div className="notice" role="status" style={{ marginBottom: 14, background: "var(--ok-soft)", color: "var(--ok)" }}>
          {feedback}
        </div>
      )}

      {loading ? (
        <div className="placeholder" role="status">Loading pending approvals...</div>
      ) : error ? (
        <div className="notice bad stack" role="alert">
          <div>{error}</div>
          <div>
            <button type="button" className="btn sm" onClick={() => loadApprovals()}>
              Retry
            </button>
          </div>
        </div>
      ) : visibleApprovals.length === 0 ? (
        <div className="empty">
          <div>{filter === "pending" ? "No pending approvals requiring your action." : "No approval records found."}</div>
        </div>
      ) : (
        <div className="approvals-table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Request</th>
                <th>Service / Subject</th>
                <th>Requester</th>
                <th>Department</th>
                <th>Stage</th>
                <th>Status</th>
                <th style={{ textAlign: "right" }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {visibleApprovals.map((item) => {
                const badge = formatApprovalBadge(item);
                const canAct = canDecideApproval(user, item);
                const req = item.requester || item.ticket?.requester;
                const requesterId = req?.id;
                const currentUserId = user?.id ?? user?.pk;
                const isRequester =
                  requesterId && (requesterId === currentUserId || String(requesterId) === String(currentUserId));
                const deptName = (typeof req?.department === "object" ? req?.department?.name : req?.department) || null;

                return (
                  <tr key={item.id}>
                    <td>
                      <a
                        href={`/tickets/${item.ticket.id}`}
                        onClick={(e) => {
                          e.preventDefault();
                          navigate(`/tickets/${item.ticket.id}`);
                        }}
                        style={{ fontWeight: 600 }}
                      >
                        {item.ticket.key || `#${item.ticket.id}`}
                      </a>
                    </td>
                    <td>
                      <div style={{ fontWeight: 500 }}>{item.ticket.subject}</div>
                      <div className="small muted">
                        Requested on {new Date(item.created_at).toLocaleDateString()}
                      </div>
                    </td>
                    <td>{req?.name || "Unknown"}</td>
                    <td>
                      {deptName || (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td>
                      <span className="tag" style={{ textTransform: "capitalize" }}>
                        {item.approval_type ? item.approval_type.replace(/_/g, " ") : "Manager"}
                      </span>
                    </td>
                    <td>
                      <span className={badge.className}>{badge.label}</span>
                    </td>
                    <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                      {canAct ? (
                        <div className="row" style={{ justifyContent: "flex-end", gap: 6 }}>
                          <button
                            type="button"
                            className="btn primary sm"
                            aria-label={`Approve ${item.ticket.key || "#" + item.ticket.id}`}
                            onClick={() => handleOpenDecision(item, "approved")}
                          >
                            Approve
                          </button>
                          <button
                            type="button"
                            className="btn danger sm"
                            aria-label={`Reject ${item.ticket.key || "#" + item.ticket.id}`}
                            onClick={() => handleOpenDecision(item, "rejected")}
                          >
                            Reject
                          </button>
                        </div>
                      ) : isRequester ? (
                        <span className="small muted" title="Requester cannot approve own request (escalated to higher authority)">
                          Self-Request (Escalated)
                        </span>
                      ) : (
                        <span className="small muted">
                          {item.decision ? `Decided by ${item.approver?.name || "manager"}` : "Assigned"}
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Decision confirmation modal */}
      {decisionModal && (
        <Modal
          title={`${decisionModal.type === "approved" ? "Approve" : "Reject"} Request: ${
            decisionModal.item.ticket.key || "#" + decisionModal.item.ticket.id
          }`}
          onClose={handleCloseDecision}
          footer={
            <>
              <button
                type="button"
                className="btn ghost"
                onClick={handleCloseDecision}
                disabled={deciding}
              >
                Cancel
              </button>
              <button
                type="button"
                className={`btn ${decisionModal.type === "approved" ? "primary" : "danger"}`}
                onClick={handleSubmitDecision}
                disabled={deciding}
              >
                {deciding
                  ? "Recording decision..."
                  : decisionModal.type === "approved"
                  ? "Confirm Approval"
                  : "Confirm Rejection"}
              </button>
            </>
          }
        >
          <div className="stack" style={{ gap: 14 }}>
            <div>
              <b>Subject:</b> {decisionModal.item.ticket.subject}
            </div>
            <div>
              {/* No invented department: shown only when the record has one */}
              <b>Requester:</b> {decisionModal.item.ticket.requester?.name || decisionModal.item.requester?.name || "Unknown"}
              {decisionModal.item.ticket.requester?.department?.name ? ` (${decisionModal.item.ticket.requester.department.name})` : ""}
            </div>

            {decisionModal.type === "rejected" && (
              <div className="callout" style={{ padding: "8px 12px" }}>
                {/* Source-verified: the decide endpoint records the decision on the approval and in the ticket's activity. */}
                <b>Rejection:</b> Your decision and comment are recorded on this approval and in the ticket's activity.
              </div>
            )}

            {decideError && <div className="notice bad" role="alert">{decideError}</div>}

            <label className="field">
              <span>Decision Comment / Feedback</span>
              <textarea
                className="input"
                rows={3}
                placeholder={
                  decisionModal.type === "approved"
                    ? "Optional approval comments (e.g., approved for Q3 budget)..."
                    : "Please state the reason for rejecting this request..."
                }
                value={comment}
                onChange={(e) => setComment(e.target.value)}
              />
            </label>
          </div>
        </Modal>
      )}
    </div>
  );
}
