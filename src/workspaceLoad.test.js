// Backend Stabilization W2: a workspace that can't load says so instead of loading forever.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { workspaceLoadError } from "./navModel.js";

test("workspaceLoadError: 403 explains access, other failures carry the server detail", () => {
  assert.match(workspaceLoadError({ status: 403, message: "You do not have permission to perform this action." }), /can't open the workspace on this server/);
  assert.equal(workspaceLoadError({ status: 503, message: "Service unavailable" }), "Your workspace couldn't be loaded: Service unavailable");
  assert.equal(workspaceLoadError(new TypeError("")), "Your workspace couldn't be loaded.");
  assert.equal(workspaceLoadError(undefined), "Your workspace couldn't be loaded.");
});

test("App.jsx surfaces a meta failure with retry and sign-out instead of swallowing it", () => {
  const src = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");
  assert.doesNotMatch(src, /api\("\/meta\/"\)\.then\(setMeta\)\.catch\(\(\) => \{\}\)/, "meta error is no longer swallowed");
  assert.match(src, /loadWorkspaceMeta\(api, \{ isCancelled: \(\) => !alive \}\)\.then\(\(m\) => alive && setMeta\(m\)\)/, "the load is bounded (timeout and one automatic retry)");
  assert.match(src, /\.catch\(\(e\) => alive && setMetaError\(workspaceLoadError\(e\)\)\)/);
  const at = src.indexOf("if (!meta && metaError)");
  assert.ok(at > 0 && at < src.indexOf('if (!meta) return <div className="placeholder" role="status">Loading your workspace...</div>;'),
    "error branch is checked before the loading placeholder");
  const branch = src.slice(at, src.indexOf("if (!meta) return", at));
  assert.match(branch, /role="alert"/);
  assert.match(branch, /setMetaAttempt\(\(n\) => n \+ 1\)/);
  assert.match(branch, /onClick=\{logout\}/);
  assert.match(src, /\}, \[workspace, auth\?\.user\?\.id, metaAttempt\]\);/, "retry re-runs the gated effect");
});
