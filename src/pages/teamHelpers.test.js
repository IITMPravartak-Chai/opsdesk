// The team form: only what changed is sent, and the save button says what it will do.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { emptyTeamForm, saveLabel, saveTeam, teamPatch, teamToForm } from "./teamHelpers.js";

const src = (f) => readFileSync(new URL(f, import.meta.url), "utf8");
const saved = { name: "Network", email: "net@x.org", assignment_strategy: "round_robin", team_lead: { id: 5 }, manager: null };

test("a new team starts manual, with no email, lead or manager", () => {
  assert.deepEqual(emptyTeamForm(), { name: "", email: "", assignment_strategy: "manual", team_lead_id: null, manager_id: null });
});

test("a saved team fills the form, with blanks for what it does not have", () => {
  assert.deepEqual(teamToForm(saved), { name: "Network", email: "net@x.org", assignment_strategy: "round_robin", team_lead_id: 5, manager_id: null });
  assert.deepEqual(teamToForm({ name: "Ops", assignment_strategy: "manual" }),
    { name: "Ops", email: "", assignment_strategy: "manual", team_lead_id: null, manager_id: null });
});

test("an untouched form sends nothing", () => {
  assert.deepEqual(teamPatch(teamToForm(saved), saved), {});
  assert.deepEqual(teamPatch({ ...teamToForm(saved), name: "  Network  " }, saved), {}, "spaces around the name are not a change");
});

test("each changed field is sent, and only that field", () => {
  const form = teamToForm(saved);
  assert.deepEqual(teamPatch({ ...form, name: "Networks" }, saved), { name: "Networks" });
  assert.deepEqual(teamPatch({ ...form, email: "" }, saved), { email: "" });
  assert.deepEqual(teamPatch({ ...form, assignment_strategy: "manual" }, saved), { assignment_strategy: "manual" });
  assert.deepEqual(teamPatch({ ...form, team_lead_id: null }, saved), { team_lead_id: null }, "removing the lead is a change");
  assert.deepEqual(teamPatch({ ...form, manager_id: 9 }, saved), { manager_id: 9 });
  assert.deepEqual(teamPatch({ name: "N", email: "e", assignment_strategy: "manual", team_lead_id: 1, manager_id: 2 }, saved),
    { name: "N", email: "e", assignment_strategy: "manual", team_lead_id: 1, manager_id: 2 });
});

test("the save button label", () => {
  assert.equal(saveLabel({ busy: true, editing: true }), "Saving…");
  assert.equal(saveLabel({ busy: false, editing: true }), "Save changes");
  assert.equal(saveLabel({ busy: false, editing: false }), "Create team");
});

test("a new team is created with a tidied name", async () => {
  let seen;
  const api = async (path, options) => { seen = { path, ...options }; return { id: 8, name: "Ops" }; };
  const created = await saveTeam(api, { team: undefined, form: { ...emptyTeamForm(), name: "  Ops  " }, saved: null });
  assert.deepEqual(created, { id: 8, name: "Ops" });
  assert.deepEqual(seen, { path: "/admin/teams/", method: "POST", body: { ...emptyTeamForm(), name: "Ops" } });
});

test("an edit sends only what changed, with the version it was based on", async () => {
  let seen;
  const api = async (path, options) => { seen = { path, ...options }; return { id: 8, name: "Networks" }; };
  const stored = { ...saved, version: "v7" };
  const result = await saveTeam(api, { team: { id: 8 }, form: { ...teamToForm(stored), name: "Networks" }, saved: stored });
  assert.deepEqual(result, { id: 8, name: "Networks" });
  assert.deepEqual(seen, { path: "/admin/teams/8/", method: "PATCH", body: { name: "Networks" }, headers: { "If-Match": '"v7"' } });
});

test("an untouched edit calls nothing and returns the saved team", async () => {
  const api = () => { throw new Error("must not be called"); };
  const stored = { ...saved, version: "v7" };
  assert.equal(await saveTeam(api, { team: { id: 8 }, form: teamToForm(stored), saved: stored }), stored);
});

test("the team form declares its props and uses the helpers", () => {
  const page = src("./Teams.jsx");
  assert.match(page, /TeamFormModal\.propTypes = \{/);
  assert.match(page, /onSaved\(await saveTeam\(api, \{ team, form, saved: detail \}\)\)/);
  assert.match(page, /saveLabel\(\{ busy, editing \}\)/);
});

test("a team with a manager but no lead or email is compared the same way", () => {
  const other = { name: "Ops", assignment_strategy: "manual", manager: { id: 2 } };
  assert.deepEqual(teamToForm(other), { name: "Ops", email: "", assignment_strategy: "manual", team_lead_id: null, manager_id: 2 });
  assert.deepEqual(teamPatch(teamToForm(other), other), {});
  assert.deepEqual(teamPatch({ ...teamToForm(other), email: "ops@x.org", team_lead_id: 4, manager_id: null }, other),
    { email: "ops@x.org", team_lead_id: 4, manager_id: null });
});
