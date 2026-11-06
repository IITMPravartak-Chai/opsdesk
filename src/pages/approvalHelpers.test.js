import test from "node:test";
import assert from "node:assert/strict";
import {
  canDecideApproval,
  formatApprovalBadge,
  filterApprovals,
} from "./approvalHelpers.js";

test("canDecideApproval: returns false if requester attempts self-approval", () => {
  const user = { id: 10, role: "team_lead" };
  const approval = {
    id: 1,
    approver: { id: 10 },
    ticket: { requester: { id: 10 } },
    decision: "pending",
  };

  assert.equal(canDecideApproval(user, approval), false);
});

test("canDecideApproval: returns false if already decided", () => {
  const user = { id: 20, role: "team_lead" };
  const approval = {
    id: 2,
    approver: { id: 20 },
    ticket: { requester: { id: 10 } },
    decision: "approved",
  };

  assert.equal(canDecideApproval(user, approval), false);
});

test("canDecideApproval: returns true for designated approver on pending request", () => {
  const user = { id: 20, role: "team_lead" };
  const approval = {
    id: 3,
    approver: { id: 20 },
    ticket: { requester: { id: 10 } },
    decision: "pending",
  };

  assert.equal(canDecideApproval(user, approval), true);
});

test("canDecideApproval: returns true for tenant admin on pending request even if not direct approver", () => {
  const admin = { id: 99, role: "manager" };
  const approval = {
    id: 4,
    approver: { id: 20 },
    ticket: { requester: { id: 10 } },
    decision: "pending",
  };

  assert.equal(canDecideApproval(admin, approval), true);
});

test("canDecideApproval: returns false for admin if admin is the requester", () => {
  const admin = { id: 99, role: "manager" };
  const approval = {
    id: 5,
    approver: { id: 99 },
    ticket: { requester: { id: 99 } },
    decision: "pending",
  };

  assert.equal(canDecideApproval(admin, approval), false);
});

test("canDecideApproval: returns false for unrelated user", () => {
  const user = { id: 55, role: "employee" };
  const approval = {
    id: 6,
    approver: { id: 20 },
    ticket: { requester: { id: 10 } },
    decision: "pending",
  };

  assert.equal(canDecideApproval(user, approval), false);
});

test("formatApprovalBadge: formats approved, rejected, and pending states", () => {
  assert.equal(formatApprovalBadge({ decision: "approved" }).label, "Approved");
  assert.equal(formatApprovalBadge({ status: "approved" }).label, "Approved");
  assert.equal(formatApprovalBadge({ decision: "rejected" }).label, "Rejected");
  assert.equal(formatApprovalBadge({ status: "rejected" }).label, "Rejected");
  assert.equal(formatApprovalBadge({ decision: "pending" }).label, "Pending Approval");
  assert.equal(formatApprovalBadge({ status: "pending" }).label, "Pending Approval");
  assert.equal(formatApprovalBadge(null).label, "Unknown");
});

test("filterApprovals: filters pending vs all approvals correctly with status or decision", () => {
  const list = [
    { id: 1, status: "pending" },
    { id: 2, decision: "approved" },
    { id: 3, status: "rejected" },
    { id: 4, decision: "pending" },
  ];

  assert.equal(filterApprovals(list, "pending").length, 2);
  assert.equal(filterApprovals(list, "approved").length, 1);
  assert.equal(filterApprovals(list, "rejected").length, 1);
  assert.equal(filterApprovals(list, "all").length, 4);
});

test("canDecideApproval: handles top-level requester and status field", () => {
  const user = { id: 15, role: "employee" };
  const selfApproval = {
    id: 10,
    approver: { id: 15 },
    requester: { id: 15 },
    status: "pending",
  };
  assert.equal(canDecideApproval(user, selfApproval), false);

  const validApproval = {
    id: 11,
    approver: { id: 15 },
    requester: { id: 25 },
    status: "pending",
  };
  assert.equal(canDecideApproval(user, validApproval), true);
});
