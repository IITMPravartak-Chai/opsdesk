// AI assistance on the ticket page: summary, triage section and draft reply.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  TEMPLATE_DRAFT_NOTE, TEMPLATE_SUMMARY_NOTE, draftFrom, draftNote, noSuggestionText, recordDraftUse, storedSummary,
  summaryButtonLabel, summaryFrom, summaryNote,
} from "./aiAssistHelpers.js";

const src = (f) => readFileSync(new URL(f, import.meta.url), "utf8");

test("a stored summary is shown without a note; a fresh one says where it came from", () => {
  assert.deepEqual(storedSummary({ ai: { summary: "Disk full on build server." } }), { text: "Disk full on build server.", method: "" });
  assert.deepEqual(storedSummary({}), { text: "", method: "" });
  assert.deepEqual(summaryFrom({ summary: "Problem: disk full", method: "template" }), { text: "Problem: disk full", method: "template" });
  assert.deepEqual(summaryFrom({ summary: "Disk is full." }), { text: "Disk is full.", method: "" }, "an older server sends no method");
});

test("only a template summary gets the 'not written by an AI model' note", () => {
  assert.equal(summaryNote({ text: "Problem: disk full", method: "template" }), TEMPLATE_SUMMARY_NOTE);
  assert.equal(summaryNote({ text: "The disk is full.", method: "ai" }), "");
  assert.equal(summaryNote({ text: "Stored earlier", method: "" }), "");
  assert.equal(summaryNote({ text: "", method: "template" }), "", "no summary, no note");
  assert.match(TEMPLATE_SUMMARY_NOTE, /No AI model is configured, so this was not written by one\./);
});

test("the summary button says what pressing it will do", () => {
  assert.equal(summaryButtonLabel({ busy: "summary", summary: { text: "" } }), "Summarising...");
  assert.equal(summaryButtonLabel({ busy: "", summary: { text: "" } }), "Summarise");
  assert.equal(summaryButtonLabel({ busy: "", summary: { text: "Disk full." } }), "Refresh");
  assert.equal(summaryButtonLabel({ busy: "apply", summary: { text: "Disk full." } }), "Refresh", "only summarising shows the working text");
});

test("the triage section is never empty: it says why there is nothing to apply", () => {
  assert.match(noSuggestionText({ possible_duplicates: [] }), /^No suggestion yet\. Suggestions appear once there are similar tickets/);
  assert.match(noSuggestionText(undefined), /^No suggestion yet/);
  assert.match(noSuggestionText({ basis: "similar_tickets" }), /^No change suggested: the category, team, impact and urgency already match/);
});

test("a draft remembers its text, its sources and where it came from", () => {
  assert.deepEqual(draftFrom({ draft: "Hi Sam, ...", sources: ["INC-000004"], method: "template" }),
    { text: "Hi Sam, ...", sources: ["INC-000004"], method: "template" });
  assert.deepEqual(draftFrom({ draft: "Hi" }), { text: "Hi", sources: [], method: "" });
  assert.equal(draftNote({ text: "Hi", sources: [], method: "template" }), TEMPLATE_DRAFT_NOTE);
  assert.equal(draftNote({ text: "Hi", sources: [], method: "ai" }), "");
  assert.equal(draftNote(null), "", "no draft in the box, no note");
  assert.match(TEMPLATE_DRAFT_NOTE, /^Template draft: no AI model is configured/);
});

test("a draft counts as accepted only when a public reply is sent, and says whether it was edited", () => {
  const posts = [];
  const api = (path, options) => { posts.push({ path, ...options }); return Promise.resolve({}); };
  const draft = { text: "Hi Sam,\nrestart the client.", sources: ["Reset VPN"], method: "ai" };
  recordDraftUse(api, 12, draft, { kind: "public", body: "  Hi Sam,\nrestart the client.\n" });
  recordDraftUse(api, 12, draft, { kind: "public", body: "Hi Sam, please restart the client today." });
  assert.deepEqual(posts.map((p) => [p.path, p.method, p.body.ticket_id, p.body.feature, p.body.accepted]),
    [["/ai/feedback/", "POST", 12, "draft", true], ["/ai/feedback/", "POST", 12, "draft", true]]);
  assert.deepEqual(posts.map((p) => p.body.suggestion), [{ sources: ["Reset VPN"], edited: false }, { sources: ["Reset VPN"], edited: true }]);
});

test("nothing is recorded for an internal note or when no draft was used", () => {
  const api = () => { throw new Error("must not be called"); };
  recordDraftUse(api, 12, { text: "Hi", sources: [] }, { kind: "internal", body: "Hi" });
  recordDraftUse(api, 12, null, { kind: "public", body: "Typed by hand" });
});

test("failing to record never disturbs the reply that was sent", async () => {
  const api = () => Promise.reject(new Error("feedback endpoint down"));
  recordDraftUse(api, 12, { text: "Hi", sources: [] }, { kind: "public", body: "Hi" }); // must not throw or leave a rejection behind
  await new Promise((resolve) => setImmediate(resolve));
});

test("the ticket page and the reply box use these helpers", () => {
  const page = src("./TicketDetail.jsx");
  assert.match(page, /useState\(storedSummary\(t\)\)/);
  assert.match(page, /setSummary\(summaryFrom\(await api\(`\/tickets\/\$\{t\.id\}\/ai_summary\/`, \{ method: "POST" \}\)\)\)/);
  assert.match(page, /sugg\.length === 0 && \(/);
  assert.match(page, /\{noSuggestionText\(triage\)\}/);
  assert.match(page, /\{summaryNote\(summary\) && <p/);
  const box = src("../components/Composer.jsx");
  const draft = box.slice(box.indexOf("const draft = async"), box.indexOf("const applyFormatting"));
  assert.doesNotMatch(draft, /\/ai\/feedback\//, "generating a draft records nothing");
  assert.match(draft, /setAiDraft\(draftFrom\(r\)\)/);
  const send = box.slice(box.indexOf("const send = async"), box.indexOf("return (", box.indexOf("const send = async")));
  assert.match(send, /recordDraftUse\(api, t\.id, aiDraft, \{ kind, body \}\);\s*setAiDraft\(null\);/);
  assert.match(box, /\{draftNote\(aiDraft\) && <div/);
});
