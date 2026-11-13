// Screens changed after the manual QA round: customer ticket views, the survey, and the AI panel and reply box.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const src = (f) => readFileSync(new URL(f, import.meta.url), "utf8");

test("customers get My tickets and Company tickets, keyed to the counts the server returns", () => {
  const app = src("../App.jsx");
  assert.match(app, /key: "mine", label: "My tickets", params: \{ mine: 1, open: 1/);
  assert.match(app, /key: "company", label: "Company tickets", params: \{ mine: 0, open: 1/);
  assert.match(app, /key: "done", label: "Resolved"/);
});

test("the survey lets the stars change but takes written feedback once", () => {
  const page = src("./Csat.jsx");
  assert.match(page, /info\.has_comment \? \(/, "the feedback box goes away once feedback was given");
  assert.match(page, /if \(d\.score\) setScore\(d\.score\)/, "the saved stars are shown again");
  assert.match(page, /ratingButtonLabel\(\{ busy, sent \}\)/);
  assert.match(page, /<output className="muted"/);
  assert.match(page, /\{savedNote\(info, score\)\} You can change your star rating below/);
  assert.match(page, /if \(r\.ok\) \{\s*saved\(await r\.json\(\)\.catch\(\(\) => null\)\);\s*\} else \{\s*setError\(/);
});
