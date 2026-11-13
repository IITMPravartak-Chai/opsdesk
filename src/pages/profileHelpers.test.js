// Post-U6 Fix D: two-step sign-in on the Profile page, and the sign-in form's code prompt.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { isSixDigitCode, mfaErrorMessage, twoStepButtonLabel } from "./profileHelpers.js";

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

test("mfaErrorMessage speaks of the emailed code and of the change being made", () => {
  assert.match(mfaErrorMessage({ status: 400 }, "confirm"), /from your email/);
  assert.doesNotMatch(mfaErrorMessage({ status: 400 }, "confirm"), /authenticator/i);
  assert.match(mfaErrorMessage({ status: 500 }, "confirm", false), /still on/);
  assert.match(mfaErrorMessage({ status: 503 }, "start", false), /couldn't email a code.*still on/);
  assert.match(mfaErrorMessage({ status: 409 }, "start", false), /already off/);
  assert.match(mfaErrorMessage({ status: 401 }, "start", false), /turn off two-step sign-in/);
  for (const s of [400, 401, 403, 429, 500, undefined]) {
    for (const phase of ["start", "confirm"]) assert.doesNotMatch(mfaErrorMessage({ status: s }, phase, false), /is off\b|disabled/i);
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
  assert.match(start, /try \{[\s\S]*catch \(e\)[\s\S]*mfaErrorMessage\(e, "start", turnOn\)/, "start() reports failures");
  const confirm = page.slice(page.indexOf("const confirm = async"), page.indexOf("const cancel ="));
  assert.match(confirm, /if \(res\?\.mfa_enabled !== setup\.turnOn\) throw/, "success needs the server's confirmation");
  assert.ok(confirm.indexOf("mfa_enabled: setup.turnOn }") > confirm.indexOf("if (res?.mfa_enabled !== setup.turnOn)"));
  assert.match(page, /role="alert">\{msg\.text\}/);
  assert.match(page, /role="status">\{msg\.text\}/);
  assert.match(page, /disabled=\{!isSixDigitCode\(code\) \|\| busy !== ""\}/);
});

test("Profile turns two-step sign-in on and off with an emailed code, with no authenticator app", () => {
  const page = src("./Profile.jsx");
  assert.match(page, /on \? "\/auth\/mfa\/" : "\/auth\/mfa\/disable\/"/);
  assert.match(page, /twoStepButtonLabel\(\{ busy, turnOn \}\)/, "the button text comes from the helper, which offers both directions");
  assert.match(page, /We emailed a 6-digit code to \{setup\.sentTo\}/);
  assert.doesNotMatch(page, /authenticator|setup\.secret/i);
});

test("sign-in form asks for the emailed code when the server says mfa_required, and can ask for a new one", () => {
  const login = src("./Login.jsx");
  assert.match(login, /err\.data\?\.mfa_required\) setNeedOtp\(true\)/);
  assert.match(login, /err\.data\?\.otp_sent\) setInfo\(err\.message\)/, "the 'code sent' reply is a notice, not an error");
  assert.match(login, /Code from your email/);
  assert.match(login, /onClick=\{resend\}/);
  assert.doesNotMatch(login, /authenticator/i);
});

test("the two-step button says what pressing it will do", () => {
  assert.equal(twoStepButtonLabel({ busy: "start", turnOn: true }), "Sending code…");
  assert.equal(twoStepButtonLabel({ busy: "", turnOn: true }), "Turn on two-step sign-in");
  assert.equal(twoStepButtonLabel({ busy: "", turnOn: false }), "Turn off two-step sign-in");
  assert.equal(twoStepButtonLabel({ busy: "confirm", turnOn: false }), "Turn off two-step sign-in", "only starting shows the sending text");
});
