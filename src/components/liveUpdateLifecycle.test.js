// Pre-U8 Workstream C: the live-update stream must not outlive its page. Pages kept in the browser's back/forward
// cache kept their stream open; six of them exhausted the per-host connection limit and stalled the app.
// The browser behaviour itself is verified with a real browser (ui_pre_u8_readiness_2026-10-24/evidence/leak/).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("./useNotificationFeed.js", import.meta.url), "utf8");

test("the stream is closed on pagehide and reopened only when a hidden page is shown again", () => {
  assert.match(src, /window\.addEventListener\("pagehide", onPageHide\)/);
  assert.match(src, /window\.addEventListener\("pageshow", onPageShow\)/);
  const hide = src.slice(src.indexOf("const onPageHide"), src.indexOf("const onPageShow"));
  assert.match(hide, /hidden = true/);
  assert.match(hide, /abort\.abort\(\)/);
  assert.match(hide, /clearTimeout\(timer\)/, "a pending reconnect must not reopen the stream behind a hidden page");
  const show = src.slice(src.indexOf("const onPageShow"), src.indexOf("if (typeof window"));
  assert.match(show, /if \(!hidden && !e\.persisted\) return;/, "the initial pageshow must not open a second stream");
  assert.match(show, /connect\(\)/);
});

test("connect refuses to run while the page is hidden, and listeners are removed on unmount", () => {
  assert.match(src, /if \(!active \|\| hidden \|\| !token\) return;/);
  assert.match(src, /window\.removeEventListener\("pagehide", onPageHide\)/);
  assert.match(src, /window\.removeEventListener\("pageshow", onPageShow\)/);
});

test("an aborted stream is not retried (no reconnect loop after pagehide or unmount)", () => {
  assert.match(src, /if \(err\.name === "AbortError"\) return;/);
});
