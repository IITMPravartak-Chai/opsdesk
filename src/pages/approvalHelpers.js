/**
 * Helper utilities for Manager Approvals and governance checks.
 */
import { endpointUnavailable } from "./workflowHelpers.js";
import { isTopRole } from "../roles.js";

export function canDecideApproval(user, approval) {
  if (!user || !approval) return false;

  // Requester cannot approve their own request under any circumstances
  const requesterId = approval.requester?.id || approval.ticket?.requester?.id || approval.ticket?.requester_id;
  const currentUserId = user.id ?? user.pk;
  if (requesterId && (requesterId === currentUserId || String(requesterId) === String(currentUserId))) {
    return false;
  }

  // Already decided approvals cannot be re-decided
  const status = approval.status || approval.decision || "pending";
  if (status !== "pending") {
    return false;
  }

  // User must be the designated approver or a tenant manager
  const approverId = approval.approver?.id || approval.approver_id;
  const isApprover = approverId && (approverId === currentUserId || String(approverId) === String(currentUserId));
  const isAdmin = isTopRole(user);

  return Boolean(isApprover || isAdmin);
}

export function formatApprovalBadge(approval) {
  if (!approval) return { label: "Unknown", color: "var(--ink-3)" };
  const d = approval.status || approval.decision || "pending";
  if (d === "approved") return { label: "Approved", color: "var(--ok)", className: "chip ok" };
  if (d === "rejected") return { label: "Rejected", color: "var(--bad)", className: "chip bad" };
  return { label: "Pending Approval", color: "var(--warn)", className: "chip warn" };
}

export function filterApprovals(approvals, filter = "pending") {
  if (!Array.isArray(approvals)) return [];
  if (filter === "all") return approvals;
  return approvals.filter((a) => (a.status || a.decision || "pending") === filter);
}

// ---- U4 (UI-DEC-015): decision submission and honest states. No API contract change. ----

export function approvalsLoadErrorMessage(err) {
  if (endpointUnavailable(err)) {
    return "The approvals list isn't available on this server yet. Approvals assigned to you can still be decided from the request's ticket page.";
  }
  return `Approvals couldn't be loaded: ${err?.message || "request failed"}.`;
}

/** The server says this approval is no longer decidable as shown (already decided, gone, or conflicting). */
export const isStaleDecision = (err) => [400, 404, 409].includes(err?.status);

export function decisionErrorMessage(err) {
  const detail = err?.message || "request failed";
  if (err?.status === 403) return `Your decision wasn't recorded: ${detail}`;
  if (isStaleDecision(err)) return `Your decision wasn't recorded: ${detail} The latest state has been reloaded.`;
  return `Your decision may not have been recorded (${detail}). Refresh before trying again.`;
}

/**
 * POST a decision once. `body` is passed through unchanged so each caller keeps its existing
 * payload ({decision, comment} from the approvals page, {decision} from the ticket panel).
 */
export async function submitApprovalDecision(apiClient, ticketId, approvalId, body, { guard } = {}) {
  if (guard && !guard.enter()) return { skipped: true };
  try {
    const res = await apiClient(`/tickets/${ticketId}/approvals/${approvalId}/decide/`, { method: "POST", body });
    return { ok: true, status: res?.status || body.decision };
  } catch (error) {
    return { ok: false, error };
  } finally {
    guard?.exit();
  }
}

/** Apply a server-confirmed decision locally so the item cannot be decided again before a refresh. */
export function applyDecision(approvals, approvalId, status) {
  return (approvals || []).map((a) => (a.id === approvalId ? { ...a, status, decision: status } : a));
}

/** Status-only approval summary for the requester's own ticket (no approver names or comments). */
export function requesterApprovalStatus(approvals) {
  if (!Array.isArray(approvals) || approvals.length === 0) return null;
  const st = approvals.map((a) => a.status);
  if (st.includes("rejected")) return "Rejected";
  if (st.includes("pending")) return "Awaiting approval";
  if (st.every((s) => s === "approved")) return "Approved";
  return null;
}
