import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");
// CSS with every @media block removed, i.e. what applies at every screen width
const baseCss = (() => {
  let out = "", depth = 0, i = 0;
  while (i < css.length) {
    if (depth === 0 && css.startsWith("@media", i)) { i = css.indexOf("{", i); depth = 1; i++; continue; }
    if (depth > 0) { if (css[i] === "{") depth++; else if (css[i] === "}") depth--; i++; continue; }
    out += css[i++];
  }
  return out;
})();

test("queue screens show one pane at a time at every width: list full-screen, ticket full-screen with the list hidden", () => {
  assert.match(baseCss, /\.queue-layout\s*\{[^}]*grid-template-columns:\s*1fr;/, "single column, no 460px side list");
  assert.match(baseCss, /\.queue-layout\.has-detail \.queue\s*\{\s*display:\s*none;/, "list hidden while a ticket is open");
  assert.match(baseCss, /\.queue-layout:not\(\.has-detail\) \.detail-pane\s*\{\s*display:\s*none;/, "no empty detail pane beside the list");
});

test("Queue renders the detail pane only for an open ticket, with Back returning to the same queue", () => {
  const page = readFileSync(new URL("./Queue.jsx", import.meta.url), "utf8");
  assert.match(page, /\{ticketId && \(\s*<section className="detail-pane"/);
  assert.match(page, /onBack=\{\(\) => navigate\(`\/tickets\?view=\$\{view\.key\}`\)\}/);
  assert.match(page, /navigate\(`\/tickets\/\$\{id\}\?view=\$\{view\.key\}`\)/, "opening a ticket keeps the queue in the URL");
});
