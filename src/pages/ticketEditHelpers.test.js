// Editing a ticket's subject and description: who sees the Edit button, what gets sent, and how the editor behaves.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  canEditTicket, initialTicketEdit, saveTicketEdit, submitTicketEdit, ticketEditChanges, ticketEditProblem, ticketEditReducer,
} from "./ticketEditHelpers.js";

const src = (f) => readFileSync(new URL(f, import.meta.url), "utf8");
const open = { id: 1, subject: "VPN down", description: "Since 9am", requester: { id: 7 }, state: { category: "open" } };
const customer = { id: 7, is_agent: false };
const event = () => ({ prevented: 0, preventDefault() { this.prevented += 1; } });
const recorder = () => { const calls = []; const fn = (...args) => { calls.push(args.length === 1 ? args[0] : args); }; fn.calls = calls; return fn; };

test("a customer edits their own open ticket, not a colleague's and not a closed one", () => {
  assert.equal(canEditTicket(customer, open), true);
  assert.equal(canEditTicket({ id: 8, is_agent: false }, open), false, "a colleague's ticket is read-only");
  assert.equal(canEditTicket(customer, { ...open, requester: null }), false, "a ticket with no requester is nobody's own");
  assert.equal(canEditTicket(customer, { ...open, state: { category: "closed" } }), false);
  assert.equal(canEditTicket(customer, { ...open, state: { category: "cancelled" } }), false);
  assert.equal(canEditTicket(customer, { ...open, state: { category: "resolved" } }), true, "the server allows resolved");
});

test("staff may edit any open ticket they can see, but not a closed one", () => {
  assert.equal(canEditTicket({ id: 1, is_agent: true }, open), true);
  assert.equal(canEditTicket({ id: 1, is_agent: true }, { ...open, state: { category: "closed" } }), false);
  assert.equal(canEditTicket({ id: 1, is_agent: true }, { ...open, state: { is_final: true, category: "done" } }), false);
  assert.equal(canEditTicket({ id: 1, is_agent: true }, { ...open, state: undefined }), true);
  assert.equal(canEditTicket(null, open), false);
  assert.equal(canEditTicket(customer, null), false);
});

test("an empty summary cannot be saved", () => {
  assert.match(ticketEditProblem({ subject: "   " }), /short summary/);
  assert.match(ticketEditProblem({}), /short summary/);
  assert.equal(ticketEditProblem({ subject: "Printer" }), "");
});

test("only changed fields are sent, and an untouched form sends nothing", () => {
  assert.deepEqual(ticketEditChanges(open, { subject: "VPN down", description: "Since 9am" }), {});
  assert.deepEqual(ticketEditChanges(open, { subject: "  VPN down  ", description: "Since 9am" }), {});
  assert.deepEqual(ticketEditChanges(open, { subject: "VPN still down", description: "Since 9am" }), { subject: "VPN still down" });
  assert.deepEqual(ticketEditChanges(open, { subject: "VPN down", description: "" }), { description: "" });
  assert.deepEqual(ticketEditChanges({ id: 2 }, { subject: undefined, description: undefined }), {}, "a ticket with no text and an empty form match");
});

// ---- the editor's state

test("the editor starts with the ticket's text", () => {
  assert.deepEqual(initialTicketEdit(open), { subject: "VPN down", description: "Since 9am", error: "", busy: false });
  assert.deepEqual(initialTicketEdit({ id: 2 }), { subject: "", description: "", error: "", busy: false });
});

test("typing clears the message; saving and failing set the busy flag and the message", () => {
  const start = { ...initialTicketEdit(open), error: "old" };
  const typed = ticketEditReducer(start, { type: "field", key: "subject", value: "VPN still down" });
  assert.deepEqual(typed, { subject: "VPN still down", description: "Since 9am", error: "", busy: false });
  const saving = ticketEditReducer(typed, { type: "saving" });
  assert.deepEqual([saving.busy, saving.error], [true, ""]);
  const failed = ticketEditReducer(saving, { type: "failed", message: "This ticket is closed." });
  assert.deepEqual([failed.busy, failed.error, failed.subject], [false, "This ticket is closed.", "VPN still down"]);
  assert.equal(ticketEditReducer(failed, { type: "unknown" }), failed);
});

// ---- submitting the editor

test("an empty summary is refused before anything is sent", async () => {
  const dispatch = recorder();
  const save = recorder();
  const e = event();
  await submitTicketEdit({ event: e, ticket: open, state: { ...initialTicketEdit(open), subject: " " }, dispatch, save, close: recorder() });
  assert.equal(e.prevented, 1);
  assert.deepEqual(dispatch.calls, [{ type: "failed", message: "Add a short summary of the problem or request." }]);
  assert.deepEqual(save.calls, []);
});

test("with nothing changed the editor closes and nothing is sent", async () => {
  const dispatch = recorder();
  const save = recorder();
  const close = recorder();
  await submitTicketEdit({ event: event(), ticket: open, state: initialTicketEdit(open), dispatch, save, close });
  assert.deepEqual([dispatch.calls, save.calls, close.calls.length], [[], [], 1]);
});

test("a change is saved; a refusal is shown in the editor", async () => {
  const state = { ...initialTicketEdit(open), subject: "VPN still down" };
  const ok = recorder();
  await submitTicketEdit({ event: event(), ticket: open, state, dispatch: ok, save: async (body) => { ok(body); return ""; }, close: recorder() });
  assert.deepEqual(ok.calls, [{ type: "saving" }, { subject: "VPN still down" }]);
  const refused = recorder();
  await submitTicketEdit({ event: event(), ticket: open, state, dispatch: refused, save: async () => "You can only edit tickets you raised.", close: recorder() });
  assert.deepEqual(refused.calls, [{ type: "saving" }, { type: "failed", message: "You can only edit tickets you raised." }]);
});

// ---- saving through the API

test("a saved edit refreshes the page with the server's copy, then closes the editor", async () => {
  const order = [];
  let seen;
  const api = async (path, options) => { seen = { path, ...options }; return { id: 1, subject: "VPN still down" }; };
  const message = await saveTicketEdit({
    api, id: 1, body: { subject: "VPN still down" },
    changed: async (ticket) => { order.push(["changed", ticket.subject]); }, done: () => order.push(["done"]),
  });
  assert.equal(message, "");
  assert.deepEqual(seen, { path: "/tickets/1/", method: "PATCH", body: { subject: "VPN still down" } });
  assert.deepEqual(order, [["changed", "VPN still down"], ["done"]]);
});

test("a refused edit returns the server's message and leaves the editor open", async () => {
  const done = recorder();
  const changed = recorder();
  const refuse = async () => { throw new Error("You can only edit tickets you raised."); };
  assert.equal(await saveTicketEdit({ api: refuse, id: 1, body: {}, changed, done }), "You can only edit tickets you raised.");
  const silent = async () => { throw {}; };
  assert.equal(await saveTicketEdit({ api: silent, id: 1, body: {}, changed, done }), "Your changes weren't saved.");
  assert.deepEqual([done.calls, changed.calls], [[], []]);
});

test("the ticket page offers the editor and wires it to the helpers", () => {
  const page = src("./TicketDetail.jsx");
  assert.match(page, /canEditTicket\(user, t\) && <button type="button" className="btn ghost sm" onClick=\{\(\) => setEditing\(true\)\}>Edit/);
  assert.match(page, /onSave=\{\(body\) => saveTicketEdit\(\{ api, id, body, changed, done: closeEditor \}\)\}/);
  assert.match(page, /submitTicketEdit\(\{ event, ticket: t, state, dispatch, save: onSave, close: onCancel \}\)/);
  assert.match(page, /useReducer\(ticketEditReducer, t, initialTicketEdit\)/);
});
