// Attachment limits are checked before submit, and a refused upload is never silent.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DEFAULT_LIMITS, fileProblems, formatSize, limitsFrom, uploadErrorMessage } from "./attachmentHelpers.js";

const MB = 1024 * 1024;
const src = (f) => readFileSync(new URL(f, import.meta.url), "utf8");
const limits = { max_file_mb: 20, max_request_mb: 25, blocked_extensions: [".exe"] };

test("a 50 MB PDF is refused with its size and the limit", () => {
  const problems = fileProblems([{ name: "report.pdf", size: 50 * MB }], limits);
  assert.equal(problems.length, 1);
  assert.match(problems[0], /report\.pdf is 50 MB\. The largest file you can attach is 20 MB\./);
});

test("files within the limits pass, including one exactly at the limit", () => {
  assert.deepEqual(fileProblems([{ name: "a.pdf", size: 20 * MB }, { name: "b.png", size: 1024 }], limits), []);
  assert.deepEqual(fileProblems([], limits), []);
  assert.deepEqual(fileProblems(null, limits), []);
});

test("blocked types and an oversized total are refused", () => {
  assert.match(fileProblems([{ name: "Setup.EXE", size: 10 }], limits)[0], /files of type \.exe are not allowed/);
  const total = fileProblems([{ name: "a.pdf", size: 15 * MB }, { name: "b.pdf", size: 15 * MB }], limits);
  assert.match(total[0], /30 MB together\. You can attach up to 25 MB at a time\./);
});

test("limits come from the server's meta, with defaults for an older API", () => {
  assert.deepEqual(limitsFrom({}), DEFAULT_LIMITS);
  assert.equal(limitsFrom({ attachment_limits: { max_file_mb: 5 } }).max_file_mb, 5);
  assert.equal(limitsFrom({ attachment_limits: { max_file_mb: 5 } }).max_request_mb, 25);
});

test("a refused upload gets a readable message, never the proxy's HTML page", () => {
  assert.match(uploadErrorMessage({ status: 413, message: "<html><h1>413</h1></html>" }, limits), /too large.*20 MB/);
  assert.equal(uploadErrorMessage({ status: 502, message: "<html>Bad Gateway</html>" }), "The files could not be uploaded.");
  assert.equal(uploadErrorMessage({ status: 400, message: "No valid files uploaded." }), "No valid files uploaded.");
});

test("formatSize", () => {
  assert.equal(formatSize(512), "512 B");
  assert.equal(formatSize(2048), "2 KB");
  assert.equal(formatSize(1.5 * MB), "1.5 MB");
  assert.equal(formatSize(50 * MB), "50 MB");
});

test("the new-ticket form and the reply composer check files and do not swallow upload errors", () => {
  const form = src("./NewTicket.jsx");
  const composer = src("../components/Composer.jsx");
  for (const code of [form, composer]) assert.match(code, /fileProblems\(/);
  assert.doesNotMatch(form, /attachments\/`, \{ method: "POST", form \}\)\.catch\(\(\) => \{\}\)/);
  assert.match(form, /disabled=\{busy \|\| fileErrors\.length > 0\}/);
});
