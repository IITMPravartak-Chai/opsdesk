import test from "node:test";
import assert from "node:assert/strict";
import {
  FOCUSABLE_SELECTOR,
  bannerSemantics,
  getFocusable,
  isTopLayer,
  nextTrapTarget,
  pushLayer,
  restoreFocus,
} from "./uiPrimitives.js";

// Minimal stand-ins for DOM nodes: enough surface for the helpers, no DOM library needed.
const node = (name, { disabled = false, hidden = false } = {}) => ({
  name,
  disabled,
  closest: (sel) => (hidden && sel.includes("aria-hidden") ? {} : null),
});
const container = (children) => ({
  querySelectorAll: (sel) => (sel === FOCUSABLE_SELECTOR ? children : []),
  contains: (n) => children.includes(n),
});

test("FOCUSABLE_SELECTOR excludes disabled controls and aria-disabled links", () => {
  for (const part of ["button:not([disabled])", "input:not([disabled])", "select:not([disabled])", "textarea:not([disabled])"]) {
    assert.ok(FOCUSABLE_SELECTOR.includes(part), part);
  }
  assert.ok(FOCUSABLE_SELECTOR.includes("a[href]:not([aria-disabled='true'])"));
  assert.ok(FOCUSABLE_SELECTOR.includes("[tabindex]:not([tabindex='-1'])"));
});

test("getFocusable: drops disabled and aria-hidden nodes, keeps order", () => {
  const a = node("a"), b = node("b", { disabled: true }), c = node("c", { hidden: true }), d = node("d");
  assert.deepEqual(getFocusable(container([a, b, c, d])).map((n) => n.name), ["a", "d"]);
  assert.deepEqual(getFocusable(null), []);
});

test("nextTrapTarget: Tab on the last item wraps to the first", () => {
  const a = node("a"), b = node("b"), c = node("c");
  assert.equal(nextTrapTarget([a, b, c], c, false, container([a, b, c])), a);
});

test("nextTrapTarget: Shift+Tab on the first item wraps to the last", () => {
  const a = node("a"), b = node("b"), c = node("c");
  assert.equal(nextTrapTarget([a, b, c], a, true, container([a, b, c])), c);
});

test("nextTrapTarget: Tab between interior items is left to the browser", () => {
  const a = node("a"), b = node("b"), c = node("c");
  const box = container([a, b, c]);
  assert.equal(nextTrapTarget([a, b, c], a, false, box), null);
  assert.equal(nextTrapTarget([a, b, c], c, true, box), null);
});

test("nextTrapTarget: focus that escaped the dialog is pulled back in", () => {
  const a = node("a"), c = node("c"), outside = node("page-button");
  const box = container([a, c]);
  assert.equal(nextTrapTarget([a, c], outside, false, box), a);
  assert.equal(nextTrapTarget([a, c], outside, true, box), c);
  assert.equal(nextTrapTarget([a, c], null, false, box), a);
});

test("nextTrapTarget: a dialog with no controls keeps focus on itself", () => {
  const box = container([]);
  assert.equal(nextTrapTarget([], node("x"), false, box), box);
});

test("layer stack: only the innermost open dialog is on top", () => {
  const outer = {}, inner = {};
  const popOuter = pushLayer(outer);
  assert.equal(isTopLayer(outer), true);
  const popInner = pushLayer(inner);
  assert.equal(isTopLayer(outer), false);
  assert.equal(isTopLayer(inner), true);
  popInner();
  assert.equal(isTopLayer(outer), true);
  popOuter();
  assert.equal(isTopLayer(outer), false);
});

test("layer stack: closing out of order does not strand the remaining dialog", () => {
  const first = {}, second = {};
  const popFirst = pushLayer(first);
  const popSecond = pushLayer(second);
  popFirst();
  assert.equal(isTopLayer(second), true);
  popSecond();
  assert.equal(isTopLayer(second), false);
  popSecond(); // idempotent
});

test("restoreFocus: focuses a connected opener and skips detached or missing ones", () => {
  let focused = 0;
  const opener = { isConnected: true, focus: () => { focused += 1; } };
  assert.equal(restoreFocus(opener), true);
  assert.equal(focused, 1);
  assert.equal(restoreFocus({ isConnected: false, focus: () => { focused += 1; } }), false);
  assert.equal(focused, 1);
  assert.equal(restoreFocus(null), false);
  assert.equal(restoreFocus({ isConnected: true, focus: () => { throw new Error("gone"); } }), false);
});

test("bannerSemantics: errors interrupt, degraded and info notices are polite", () => {
  assert.deepEqual(bannerSemantics("error"), { role: "alert", className: "degraded-banner degraded-banner--error" });
  assert.equal(bannerSemantics("warning").role, "status");
  assert.equal(bannerSemantics("info").role, "status");
  assert.equal(bannerSemantics(undefined).className, "degraded-banner degraded-banner--warning");
  assert.equal(bannerSemantics("bogus").className, "degraded-banner degraded-banner--warning");
});
