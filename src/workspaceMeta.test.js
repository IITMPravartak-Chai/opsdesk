// Post-U8 (U8-03): the workspace load is bounded: it times out, retries a timeout at most once automatically, never retries a
// real failure, and ends in an error state rather than an endless placeholder.
import test from "node:test";
import assert from "node:assert/strict";
import { loadWorkspaceMeta, WorkspaceTimeout, WORKSPACE_AUTO_RETRIES, WORKSPACE_TIMEOUT_MS } from "./workspaceMeta.js";
import { workspaceLoadError } from "./navModel.js";

// An API stub that never answers until aborted (like a hung request), or answers after `delay` ms.
const hang = (calls) => (path, { signal }) => new Promise((_, reject) => {
  calls.push(path);
  signal.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
});
const fast = { timeoutMs: 30 };

test("defaults are bounded: 15 s per attempt, one automatic retry", () => {
  assert.equal(WORKSPACE_TIMEOUT_MS, 15000);
  assert.equal(WORKSPACE_AUTO_RETRIES, 1);
});

test("a normal load returns the metadata after one request", async () => {
  const calls = [];
  const meta = await loadWorkspaceMeta(async (p) => { calls.push(p); return { ok: 1 }; }, fast);
  assert.deepEqual(meta, { ok: 1 });
  assert.deepEqual(calls, ["/meta/"]);
});

test("a hung first attempt is aborted and the single automatic retry can succeed", async () => {
  let n = 0;
  const calls = [];
  const apiFn = (p, o) => (++n === 1 ? hang(calls)(p, o) : Promise.resolve({ retried: true }));
  const meta = await loadWorkspaceMeta(apiFn, fast);
  assert.deepEqual(meta, { retried: true });
  assert.equal(n, 2);
});

test("retry exhaustion ends in WorkspaceTimeout after exactly 1 + retries attempts (no retry storm)", async () => {
  const calls = [];
  await assert.rejects(loadWorkspaceMeta(hang(calls), fast), (e) => e instanceof WorkspaceTimeout && e.timedOut === true);
  assert.equal(calls.length, 1 + WORKSPACE_AUTO_RETRIES);
  const calls2 = [];
  await assert.rejects(loadWorkspaceMeta(hang(calls2), { ...fast, autoRetries: 0 }), WorkspaceTimeout);
  assert.equal(calls2.length, 1);
});

test("a real failure (HTTP error, network error) is reported at once and not retried", async () => {
  for (const err of [Object.assign(new Error("Forbidden"), { status: 403 }), Object.assign(new Error("Session expired"), { status: 401 }), new TypeError("Failed to fetch")]) {
    let n = 0;
    await assert.rejects(loadWorkspaceMeta(async () => { n += 1; throw err; }, fast), (e) => e === err);
    assert.equal(n, 1);
  }
});

test("a cancelled view stops retrying and surfaces the abort", async () => {
  const calls = [];
  await assert.rejects(loadWorkspaceMeta(hang(calls), { ...fast, isCancelled: () => true }), (e) => e.name === "AbortError");
  assert.equal(calls.length, 1);
});

test("each attempt gets its own signal and the timer is cleared on success", async () => {
  const signals = [];
  await loadWorkspaceMeta(async (_p, { signal }) => { signals.push(signal); return {}; }, { timeoutMs: 20 });
  await new Promise((r) => setTimeout(r, 60));
  assert.equal(signals[0].aborted, false, "the timeout must not fire after a successful load");
});

test("the timeout message is distinct, plain and actionable; other messages are unchanged", () => {
  assert.match(workspaceLoadError(new WorkspaceTimeout()), /taking longer than expected.*try again/i);
  assert.match(workspaceLoadError({ status: 403 }), /can't open the workspace/);
  assert.match(workspaceLoadError({ status: 500, message: "boom" }), /couldn't be loaded: boom/);
});
