import test from "node:test";
import assert from "node:assert/strict";
import { actionLabel, fmtWhen, pct } from "./adminDashboardHelpers.js";

test("pct: whole percentages, a dash when there is nothing to divide by", () => {
  assert.equal(pct(3, 4), "75%");
  assert.equal(pct(0, 5), "0%");
  assert.equal(pct(1, 3), "33%");
  assert.equal(pct(2, 0), "-");
});

test("actionLabel: readable audit action names", () => {
  assert.equal(actionLabel("admin.categories.update"), "Admin categories update");
  assert.equal(actionLabel("user_login"), "User login");
  assert.equal(actionLabel(""), "Unknown");
  assert.equal(actionLabel(null), "Unknown");
});

test("fmtWhen: dash for missing or invalid timestamps", () => {
  assert.equal(fmtWhen(null), "-");
  assert.equal(fmtWhen("not a date"), "-");
  assert.notEqual(fmtWhen("2026-10-28T10:30:00Z"), "-");
});
