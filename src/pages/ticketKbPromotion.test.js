import test from "node:test";
import assert from "node:assert/strict";
import { canPromoteTicketToKb, createTicketKbDraft } from "./ticketKbPromotion.js";

test("canPromoteTicketToKb: returns true for agents on resolved tickets", () => {
  const user = { is_agent: true };
  const ticket = { state: { category: "resolved" } };
  assert.equal(canPromoteTicketToKb(user, ticket), true);
});

test("canPromoteTicketToKb: returns true for agents on closed tickets", () => {
  const user = { is_agent: true };
  const ticket = { state: { category: "closed" } };
  assert.equal(canPromoteTicketToKb(user, ticket), true);
});

test("canPromoteTicketToKb: returns false for agents on non-terminal ticket states", () => {
  const user = { is_agent: true };
  assert.equal(canPromoteTicketToKb(user, { state: { category: "new" } }), false);
  assert.equal(canPromoteTicketToKb(user, { state: { category: "in_progress" } }), false);
  assert.equal(canPromoteTicketToKb(user, { state: { category: "pending" } }), false);
  assert.equal(canPromoteTicketToKb(user, { state: { category: "cancelled" } }), false);
});

test("canPromoteTicketToKb: returns false for customers/non-agents regardless of ticket state", () => {
  const customer = { is_agent: false };
  assert.equal(canPromoteTicketToKb(customer, { state: { category: "resolved" } }), false);
  assert.equal(canPromoteTicketToKb(customer, { state: { category: "closed" } }), false);
  assert.equal(canPromoteTicketToKb(null, { state: { category: "resolved" } }), false);
});

test("createTicketKbDraft: successful template draft creation dispatches API, shows feedback, and navigates", async () => {
  const apiCalls = [];
  const fakeApi = async (path, options) => {
    apiCalls.push({ path, options });
    return {
      id: "art-101",
      title: "VPN Error 809 fix",
      body: "## Symptoms\nVPN error 809\n\n## Resolution\nReset adapter",
      status: "draft",
      generation_method: "template",
    };
  };

  const busyStates = [];
  let feedback = null;
  let navigated = null;
  let error = null;

  const res = await createTicketKbDraft(fakeApi, "tk-123", {
    isBusy: false,
    setBusy: (b) => busyStates.push(b),
    onFeedback: (msg) => { feedback = msg; },
    onNavigate: (path) => { navigated = path; },
    onError: (err) => { error = err; },
  });

  assert.equal(res.success, true);
  assert.equal(apiCalls.length, 1);
  assert.equal(apiCalls[0].path, "/kb/articles/from_ticket/");
  assert.deepEqual(apiCalls[0].options.body, { ticket_id: "tk-123" });
  assert.equal(feedback, "Draft created from template. Opening...");
  assert.equal(navigated, "/kb/art-101");
  assert.equal(error, null);
  assert.deepEqual(busyStates, [true, false]);
});

test("createTicketKbDraft: handles AI draft generation feedback correctly", async () => {
  const fakeApi = async () => ({
    id: "art-202",
    generation_method: "ai",
  });

  let feedback = null;
  let navigated = null;

  await createTicketKbDraft(fakeApi, "tk-123", {
    onFeedback: (msg) => { feedback = msg; },
    onNavigate: (path) => { navigated = path; },
  });

  assert.equal(feedback, "AI draft generated. Opening...");
  assert.equal(navigated, "/kb/art-202");
});

test("createTicketKbDraft: handles duplicate draft response gracefully", async () => {
  const fakeApi = async () => ({
    id: "art-303",
    duplicate: true,
  });

  let feedback = null;
  let navigated = null;

  await createTicketKbDraft(fakeApi, "tk-123", {
    onFeedback: (msg) => { feedback = msg; },
    onNavigate: (path) => { navigated = path; },
  });

  assert.equal(feedback, "Draft already exists for this ticket. Opening...");
  assert.equal(navigated, "/kb/art-303");
});

test("createTicketKbDraft: duplicate-click prevention skips request when isBusy is true", async () => {
  let callCount = 0;
  const fakeApi = async () => {
    callCount++;
    return { id: "art-999" };
  };

  const res = await createTicketKbDraft(fakeApi, "tk-123", { isBusy: true });
  assert.equal(res.skipped, true);
  assert.equal(callCount, 0);
});

test("createTicketKbDraft: handles backend error without unhandled exception", async () => {
  const fakeApi = async () => {
    throw new Error("Only resolved or closed tickets can be converted into Knowledge Base articles.");
  };

  let error = null;
  const res = await createTicketKbDraft(fakeApi, "tk-456", {
    onError: (err) => { error = err; },
  });

  assert.equal(res.success, false);
  assert.equal(error, "Only resolved or closed tickets can be converted into Knowledge Base articles.");
});

test("createTicketKbDraft: request body does not leak internal or sensitive notes", async () => {
  const sentBodies = [];
  const fakeApi = async (path, options) => {
    sentBodies.push(options.body);
    return { id: "art-777" };
  };

  await createTicketKbDraft(fakeApi, "tk-999", {});
  assert.deepEqual(sentBodies, [{ ticket_id: "tk-999" }]);
  // Client only transmits the ticket_id reference, ensuring private messages/notes are not client-serialized
  assert.equal(Object.keys(sentBodies[0]).length, 1);
});
