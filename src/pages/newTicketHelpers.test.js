// Raising a ticket: what is checked, what is sent, and what happens to the files after the ticket exists.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createErrors, otherErrors, raiseTicket, submitLabel, ticketBody, ticketProblems, uploadAttachments } from "./newTicketHelpers.js";

const src = (f) => readFileSync(new URL(f, import.meta.url), "utf8");
const form = { subject: "  VPN down ", description: "Since 9", account: "4", ci: "", category: "7", impact: "", urgency: "2", team: "" };
const type = { id: 3, custom_fields: [{ key: "site", label: "Site", required: true }, { key: "note", label: "Note", required: false }] };

test("a summary and every required custom field are needed", () => {
  assert.deepEqual(ticketProblems({ subject: "  " }, type, {}), {
    subject: "Add a short summary of the problem or request.", site: "Site is required.",
  });
  assert.deepEqual(ticketProblems({ subject: "VPN" }, type, { site: "Chennai" }), {});
  assert.deepEqual(ticketProblems({ subject: "VPN" }, undefined, {}), {}, "no ticket type chosen yet");
});

test("the request body sends chosen references as numbers and leaves empty ones out", () => {
  assert.deepEqual(ticketBody({ form, type, custom: { site: "Chennai" }, mobile: false }), {
    ticket_type: 3, subject: "VPN down", description: "Since 9", custom_fields: { site: "Chennai" }, source: "portal",
    account: 4, category: 7, urgency: 2,
  });
  assert.equal(ticketBody({ form, type, custom: {}, mobile: true }).source, "mobile");
});

test("server field errors become one message per field, otherwise one form message", () => {
  assert.deepEqual(createErrors({ data: { subject: ["Too long.", "Trim it."], team: "Unknown team." } }),
    { subject: "Too long. Trim it.", team: "Unknown team." });
  assert.deepEqual(createErrors({ message: "Network down" }), { form: "Network down" });
  assert.deepEqual(createErrors({ data: "<html>502</html>", message: "Bad gateway" }), { form: "Bad gateway" });
});

test("the button says what will happen", () => {
  assert.equal(submitLabel({ busy: true, isAgent: true }), "Submitting...");
  assert.equal(submitLabel({ busy: false, isAgent: true }), "Create ticket");
  assert.equal(submitLabel({ busy: false, isAgent: false }), "Submit request");
});

test("errors for fields the form has no input for are listed separately", () => {
  const errors = { subject: "a", site: "b", form: "c", team: "d" };
  assert.deepEqual(otherErrors(errors, type), [["form", "c"], ["team", "d"]]);
  assert.deepEqual(otherErrors({ subject: "a" }, undefined), []);
});

test("the upload reports nothing when every file is stored, and the reason when it is not", async () => {
  const limits = { max_file_mb: 20 };
  const files = [new Blob(["a"]), new Blob(["b"])];
  let seen;
  const stored = async (path, options) => { seen = { path, count: options.form.getAll("files").length, method: options.method }; return { created: [1, 2], errors: [] }; };
  assert.equal(await uploadAttachments(stored, 9, files, limits), "");
  assert.deepEqual(seen, { path: "/tickets/9/attachments/", count: 2, method: "POST" });
  const partly = async () => ({ created: [1], errors: ["bad.exe: Files of type .exe are not allowed", "x.pdf: File exceeds 20 MB"] });
  assert.equal(await uploadAttachments(partly, 9, files, limits), "bad.exe: Files of type .exe are not allowed; x.pdf: File exceeds 20 MB");
  const tooBig = async () => { throw { status: 413, message: "<html>413</html>" }; };
  assert.match(await uploadAttachments(tooBig, 9, files, limits), /too large.*20 MB/);
});

test("raising a ticket creates it first and uploads its files second", async () => {
  const calls = [];
  const api = async (path, options) => {
    calls.push([path, options.method]);
    return path === "/tickets/" ? { id: 31, number: "INC-000031" } : { created: [1], errors: [] };
  };
  const limits = { max_file_mb: 20 };
  const done = await raiseTicket(api, { form, type, custom: { site: "Chennai" }, mobile: false, files: [new Blob(["a"])], limits });
  assert.deepEqual(done, { ticket: { id: 31, number: "INC-000031" }, uploadProblem: "" });
  assert.deepEqual(calls, [["/tickets/", "POST"], ["/tickets/31/attachments/", "POST"]]);
});

test("a ticket without files makes one request; a refused upload is reported with the ticket that now exists", async () => {
  const limits = { max_file_mb: 20 };
  const calls = [];
  const plain = async (path) => { calls.push(path); return { id: 32 }; };
  assert.deepEqual(await raiseTicket(plain, { form, type, custom: {}, mobile: true, files: [], limits }), { ticket: { id: 32 }, uploadProblem: "" });
  assert.deepEqual(calls, ["/tickets/"]);
  const refusesFiles = async (path) => {
    if (path === "/tickets/") return { id: 33, number: "INC-000033" };
    throw { status: 413, message: "<html>413</html>" };
  };
  const result = await raiseTicket(refusesFiles, { form, type, custom: {}, mobile: false, files: [new Blob(["a"])], limits });
  assert.equal(result.ticket.number, "INC-000033");
  assert.match(result.uploadProblem, /too large.*20 MB/);
});

test("a refused ticket throws, so nothing is uploaded and the form can show why", async () => {
  const refuse = async () => { throw { data: { subject: ["Too long."] }, message: "Too long." }; };
  await assert.rejects(() => raiseTicket(refuse, { form, type, custom: {}, mobile: false, files: [new Blob(["a"])], limits: {} }),
    (error) => createErrors(error).subject === "Too long.");
});

test("the new-ticket screen uses these helpers instead of repeating the logic", () => {
  const page = src("./NewTicket.jsx");
  for (const name of ["ticketProblems(", "raiseTicket(", "createErrors(", "submitLabel(", "otherErrors("]) {
    assert.ok(page.includes(name), name);
  }
  assert.doesNotMatch(page, /catch \(upErr\)/);
});
