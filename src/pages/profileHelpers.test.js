// Post-U6 Fix D: two-step sign-in on the Profile page, and the sign-in form's code prompt.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { isSixDigitCode, mfaErrorMessage } from "./profileHelpers.js";

const src = (f) => readFileSync(new URL(f, import.meta.url), "utf8");

test("mfaErrorMessage gives an actionable message per status and never claims success", () => {
  assert.match(mfaErrorMessage({ status: 401 }, "start"), /session has ended/);
  assert.match(mfaErrorMessage({ status: 403 }, "start"), /isn't available for your account/);
  assert.match(mfaErrorMessage({ status: 409 }, "start"), /already on/);
  assert.match(mfaErrorMessage({ status: 400 }, "confirm"), /didn't match/);
  assert.match(mfaErrorMessage({ status: 429 }, "confirm"), /Too many attempts/);
  assert.match(mfaErrorMessage({ status: 500 }, "confirm"), /still off/);
  assert.match(mfaErrorMessage({ status: 502 }, "start"), /still off/);
  assert.match(mfaErrorMessage(new TypeError("Failed to fetch"), "start"), /Check your connection/);
  for (const s of [400, 401, 403, 409, 429, 500, undefined]) {
    for (const phase of ["start", "confirm"]) assert.doesNotMatch(mfaErrorMessage({ status: s }, phase), /is on\b|enabled/i);
  }
});

test("isSixDigitCode accepts exactly six digits", () => {
  assert.ok(isSixDigitCode("123456"));
  assert.ok(isSixDigitCode(" 123456 "));
  for (const bad of ["", "12345", "1234567", "12a456", null]) assert.ok(!isSixDigitCode(bad));
});

test("Profile handles start and confirm failures and only reports success on a confirmed response", () => {
  const page = src("./Profile.jsx");
  const start = page.slice(page.indexOf("const start = async"), page.indexOf("const confirm = async"));
  assert.match(start, /try \{[\s\S]*catch \(e\)[\s\S]*mfaErrorMessage\(e, "start"\)/, "start() reports failures");
  const confirm = page.slice(page.indexOf("const confirm = async"), page.indexOf("const cancel ="));
  assert.match(confirm, /if \(!res\?\.mfa_enabled\) throw/, "success needs the server's confirmation");
  assert.ok(confirm.indexOf("mfa_enabled: true") > confirm.indexOf("if (!res?.mfa_enabled)"));
  assert.match(page, /role="alert">\{msg\.text\}/);
  assert.match(page, /role="status">\{msg\.text\}/);
  assert.match(page, /disabled=\{!isSixDigitCode\(code\) \|\| busy !== ""\}/);
});

test("sign-in form asks for the code when the server says mfa_required", () => {
  const login = src("./Login.jsx");
  assert.match(login, /err\.data\?\.mfa_required\) setNeedOtp\(true\)/);
});
