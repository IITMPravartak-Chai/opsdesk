// U7-A2/A3 administration pages: helper behaviour and page wiring.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  buildUserQuery, canManageSla, canManageTeams, canManageUsers, checkSchedule, checkTargets, formatMinutes, ifMatch,
  impactLines, loadError, parseSkills, roleLabel, scheduleRows, settingsHome, settingsRoute, settingsTabs, toSchedule,
  toTargets, userChanges, userDraft, writeError,
} from "./adminHelpers.js";

const src = (f) => readFileSync(new URL(f, import.meta.url), "utf8");
const role = (r) => ({ role: r });
const keys = (u) => settingsTabs(u).map((t) => t.key);

test("page access mirrors the server: Administration is the super admin's alone", () => {
  assert.deepEqual(keys(role("super_admin")), ["users", "teams", "sla", "config", "audit-log"]);
  for (const r of ["manager", "team_lead", "employee", "customer"]) {
    assert.deepEqual(keys(role(r)), []);
    assert.equal(settingsHome(role(r)), null);
    assert.equal(canManageUsers(role(r)) || canManageTeams(role(r)) || canManageSla(role(r)), false);
  }
  assert.equal(settingsHome(role("super_admin")), "/settings/users");
  assert.equal(canManageUsers(null), false);
});

test("settingsRoute parses list and detail paths and nothing else", () => {
  assert.deepEqual(settingsRoute("/settings/users"), { section: "users", kind: null, id: null });
  assert.deepEqual(settingsRoute("/settings/teams/12"), { section: "teams", kind: null, id: 12 });
  assert.deepEqual(settingsRoute("/settings/sla/hours/3"), { section: "sla", kind: "hours", id: 3 });
  assert.deepEqual(settingsRoute("/settings/sla/7/"), { section: "sla", kind: null, id: 7 });
  for (const p of ["/settings", "/settings/users/x", "/settings/users/1/2", "/admin/users", "/settings/teams/hours/1"]) {
    assert.equal(settingsRoute(p), null, p);
  }
});

test("ifMatch quotes the version and sends nothing without one", () => {
  assert.deepEqual(ifMatch("abc"), { "If-Match": '"abc"' });
  assert.deepEqual(ifMatch(null), {});
});

test("writeError never reads as success and explains conflicts", () => {
  assert.match(writeError({ status: 412 }, "team"), /changed by someone else.*Reload/);
  assert.match(writeError({ status: 409, message: "A team with this name already exists." }), /already exists/);
  assert.match(writeError({ status: 403, message: "Only administrators can change roles." }), /Only administrators/);
  assert.match(writeError({ status: 500 }, "user"), /Couldn't save the user/);
  assert.match(writeError(new TypeError("Failed to fetch")), /connection/);
  for (const s of [400, 401, 403, 404, 409, 412, 422, 428, 500]) {
    const m = writeError({ status: s });
    assert.ok(m && !/^(saved|success|done|updated)/i.test(m) && !/\bwas saved\b|\bhas been saved\b/i.test(m), `${s}: ${m}`);
  }
});

test("loadError separates forbidden, missing and failure", () => {
  assert.deepEqual(loadError({ status: 403 }, "user"), { status: "forbidden" });
  assert.equal(loadError({ status: 404 }, "user").status, "missing");
  assert.equal(loadError({ status: 503 }, "user").status, "error");
});

test("buildUserQuery sends only supported filters", () => {
  const q = Object.fromEntries(new URLSearchParams(buildUserQuery({ q: "  asha ", role: "employee", status: "inactive", page: 2 })));
  assert.deepEqual(q, { q: "asha", role: "employee", is_active: "false", page: "2", page_size: "50" });
  const none = Object.fromEntries(new URLSearchParams(buildUserQuery({ role: "root", status: "maybe" })));
  assert.deepEqual(none, { page: "1", page_size: "50" });
});

test("userChanges sends only edited fields the caller may change", () => {
  const original = userDraft({ first_name: "A", role: "employee", skills: ["VPN"], department: { id: 2 }, is_available: true });
  const draft = { ...original, first_name: "Asha", role: "team_lead", skills: ["VPN", "DNS"], department_id: 3 };
  assert.deepEqual(userChanges(original, draft, ["first_name", "skills", "department_id"]),
    { first_name: "Asha", skills: ["VPN", "DNS"], department_id: 3 });
  assert.deepEqual(userChanges(original, draft, ["first_name", "role", "skills", "department_id"]).role, "team_lead");
  assert.deepEqual(userChanges(original, original, ["first_name"]), {});
});

test("parseSkills splits, trims and de-duplicates", () => {
  assert.deepEqual(parseSkills(" VPN, dns ,vpn,, Network "), ["VPN", "dns", "Network"]);
  assert.deepEqual(parseSkills(""), []);
});

test("roleLabel", () => {
  assert.equal(roleLabel("manager"), "Manager");
  assert.equal(roleLabel("team_lead"), "Team lead");
  assert.equal(roleLabel(undefined), "—");
});

test("formatMinutes", () => {
  assert.equal(formatMinutes(45), "45 min");
  assert.equal(formatMinutes(240), "4 h");
  assert.equal(formatMinutes(1620), "1 d 3 h");
  assert.equal(formatMinutes(null), "—");
});

test("schedule rows round-trip and validate like the server", () => {
  const rows = scheduleRows({ 0: [["09:00", "18:00"]], 4: [["09:00", "12:00"], ["13:00", "17:00"]] });
  assert.equal(rows.length, 7);
  assert.deepEqual(toSchedule(rows), { 0: [["09:00", "18:00"]], 4: [["09:00", "12:00"], ["13:00", "17:00"]] });
  assert.equal(checkSchedule(rows, false), "");
  assert.match(checkSchedule(scheduleRows({}), false), /at least one working period/);
  assert.equal(checkSchedule(scheduleRows({}), true), "");
  assert.match(checkSchedule(scheduleRows({ 1: [["18:00", "09:00"]] }), false), /Tuesday: .*end after/);
  assert.match(checkSchedule(scheduleRows({ 2: [["09:00", "13:00"], ["12:00", "18:00"]] }), false), /Wednesday: .*overlap/);
  assert.match(checkSchedule(scheduleRows({ 3: [["9:00", "18:00"]] }), false), /HH:MM/);
  assert.equal(checkSchedule(scheduleRows({ 5: [["22:00", "00:00"]] }), false), "");
});

test("targets validate and serialise", () => {
  const rows = [{ label: "P1", priority_id: 1, first_response_minutes: "15", resolution_minutes: "240" },
    { label: "P2", priority_id: 2, first_response_minutes: "", resolution_minutes: "" }];
  assert.equal(checkTargets(rows), "");
  assert.deepEqual(toTargets(rows), [{ priority_id: 1, first_response_minutes: 15, resolution_minutes: 240 }]);
  assert.match(checkTargets([{ ...rows[0], first_response_minutes: "300" }]), /can't be longer/);
  assert.match(checkTargets([{ ...rows[0], resolution_minutes: "" }]), /both targets/);
  assert.match(checkTargets([{ ...rows[0], resolution_minutes: "1.5" }]), /whole minutes/);
});

test("impactLines states notifications and that due dates don't move", () => {
  const lines = impactLines({ active_tickets: 3, to_at_risk: 2, to_on_track: 0, paused_tickets: 1, sampled: false });
  assert.match(lines[0], /3 open tickets are being timed.*1 paused ticket/);
  assert.match(lines[1], /2 tickets will be marked at risk.*notified/);
  assert.match(lines.at(-1), /Due dates .* won't move/);
  const quiet = impactLines({ active_tickets: 1, to_at_risk: 0, to_on_track: 0, paused_tickets: 0 }, "calendar");
  assert.match(quiet[0], /1 open ticket is being timed against this calendar/);
  assert.match(quiet[1], /No ticket changes status straight away/);
});

test("pages use the shared API client, send If-Match on every write and never fake success", () => {
  for (const f of ["./Users.jsx", "./Teams.jsx", "./SlaConfig.jsx"]) {
    const s = src(f);
    assert.match(s, /from "\.\.\/api\.js"/, f);
    assert.doesNotMatch(s, /\bfetch\(/, f);
    for (const m of s.matchAll(/method: "(PATCH|PUT|DELETE|POST)"/g)) {
      const at = s.slice(m.index, m.index + 200);
      // Unversioned actions: creating a team or a user, and an administrator setting a password / turning off two-step sign-in
      // (audited one-shot operations with no editable state to conflict with).
      const around = s.slice(Math.max(0, m.index - 80), m.index + 30);
      assert.ok(/ifMatch\(/.test(at) || /\/admin\/(teams|users)\/", \{ method: "POST"/.test(around) || /\/(password|mfa\/disable)\/`, \{ method: "POST"/.test(around), `${f}: ${m[0]} without If-Match`);
    }
    assert.match(s, /role="status"/, f);
    assert.match(s, /<PageState /, f); // loading, forbidden, missing and error states (AdminCommon.jsx)
    assert.match(s, /<WriteResult /, f); // write outcome: success only after the server confirmed it
  }
  const common = src("./AdminCommon.jsx");
  assert.match(common, /role="alert"/);
  assert.match(common, /if \(result\.ok\) return <div className="notice admin-ok" role="status">/);
  assert.match(src("./Users.jsx"), /Add user/); // the super admin creates accounts here, with an initial password
});

test("newUserPayload trims and lowercases; newUserProblem names what is missing", async () => {
  const { newUserPayload, newUserProblem, generatePassword } = await import("./adminHelpers.js");
  const form = { email: "  New.User@Example.COM ", first_name: " Ana ", last_name: "", role: "customer", account_id: "3", password: "Long-enough-pw1" };
  assert.deepEqual(newUserPayload(form), { email: "new.user@example.com", first_name: "Ana", last_name: "", role: "customer", account_id: 3, team_id: null, password: "Long-enough-pw1" });
  const emp = { ...form, role: "employee", account_id: "", team_id: "7" };
  assert.equal(newUserPayload(emp).team_id, 7);
  assert.match(newUserProblem({ ...emp, team_id: "" }), /team/i); // an employee is created into a team
  assert.equal(newUserProblem(emp), "");
  assert.equal(newUserPayload({ ...form, team_id: "7" }).team_id, null); // other roles send none
  assert.equal(newUserPayload({ ...form, account_id: "" }).account_id, null);
  assert.equal(newUserPayload({ ...form, role: "employee" }).account_id, null); // staff never get a company
  assert.match(newUserProblem({ ...form, account_id: "" }), /company/i);
  assert.equal(newUserProblem({ ...form, role: "employee", account_id: "", team_id: "7" }), ""); // staff need no company
  assert.equal(newUserProblem(form), "");
  assert.match(newUserProblem({ ...form, email: "nope" }), /email/i);
  assert.match(newUserProblem({ ...form, first_name: " " }), /first name/i);
  assert.match(newUserProblem({ ...form, role: "super_admin" }), /role/i);
  assert.match(newUserProblem({ ...form, password: "short" }), /12/);
  const pw = generatePassword();
  assert.equal(pw.length, 16);
  assert.match(pw, /^[A-Za-z0-9]+$/);
});
