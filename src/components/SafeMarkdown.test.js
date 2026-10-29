import test from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import SafeMarkdown, { safeUrlTransform, safeImageUrlTransform } from "./SafeMarkdown.js";

function render(content) {
  return renderToStaticMarkup(React.createElement(SafeMarkdown, { content }));
}

test("safeUrlTransform validates protocols correctly", () => {
  // Allowed safe protocols
  assert.equal(safeUrlTransform("https://opsdesk.dev/docs"), "https://opsdesk.dev/docs");
  assert.equal(safeUrlTransform("http://localhost:8000/api"), "http://localhost:8000/api");
  assert.equal(safeUrlTransform("mailto:help@opsdesk.dev"), "mailto:help@opsdesk.dev");
  assert.equal(safeUrlTransform("tel:+1234567890"), "tel:+1234567890");

  // Allowed relative paths & anchors
  assert.equal(safeUrlTransform("/kb/42"), "/kb/42");
  assert.equal(safeUrlTransform("#symptoms"), "#symptoms");
  assert.equal(safeUrlTransform("./relative-doc"), "./relative-doc");
  assert.equal(safeUrlTransform("../parent-doc"), "../parent-doc");

  // Blocked protocol-relative and backslash-based URLs
  assert.equal(safeUrlTransform("//evil.example/path"), "");
  assert.equal(safeUrlTransform("   //evil.example/path  "), "");
  assert.equal(safeUrlTransform("//localhost:8000"), "");
  assert.equal(safeUrlTransform("\\\\evil.example/path"), "");
  assert.equal(safeUrlTransform("   \\\\evil.example/path   "), "");
  assert.equal(safeUrlTransform("\\evil.example/path"), "");
  assert.equal(safeUrlTransform("   \\evil.example/path   "), "");
  assert.equal(safeUrlTransform("/\\\\evil.example/path"), "");
  assert.equal(safeUrlTransform("   /\\\\evil.example/path   "), "");
  assert.equal(safeUrlTransform("/\\evil.example/path"), "");
  assert.equal(safeUrlTransform("   /\\evil.example/path   "), "");
  assert.equal(safeUrlTransform("\\/evil.example/path"), "");
  assert.equal(safeUrlTransform("   \\/evil.example/path   "), "");
  assert.equal(safeUrlTransform("%5Cevil.example/path"), "");
  assert.equal(safeUrlTransform("/%5Cevil.example/path"), "");
  assert.equal(safeUrlTransform("/%5C%5Cevil.example/path"), "");
  assert.equal(safeUrlTransform("%5C%5Cevil.example/path"), "");
  assert.equal(safeUrlTransform("/%2fevil.example/path"), "");

  // Blocked dangerous protocols
  assert.equal(safeUrlTransform("javascript:alert(1)"), "");
  assert.equal(safeUrlTransform("javascript:/*--></title></style>*/<script>"), "");
  assert.equal(safeUrlTransform("JAVASCRIPT:alert('xss')"), "");
  assert.equal(safeUrlTransform("   javascript:alert(1)  "), "");
  assert.equal(safeUrlTransform("vbscript:msgbox(1)"), "");
  assert.equal(safeUrlTransform("data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg=="), "");
  assert.equal(safeUrlTransform("file:///etc/passwd"), "");
  assert.equal(safeUrlTransform("blob:https://example.com/uuid"), "");

  // Blocked malformed and ambiguous strings
  assert.equal(safeUrlTransform("arbitrary_ambiguous_string"), "");
  assert.equal(safeUrlTransform("foo:bar"), "");
});

test("safeImageUrlTransform restricts image sources strictly", () => {
  // Allowed image sources
  assert.equal(safeImageUrlTransform("https://example.com/logo.png"), "https://example.com/logo.png");
  assert.equal(safeImageUrlTransform("http://localhost:8000/static/pic.jpg"), "http://localhost:8000/static/pic.jpg");
  assert.equal(safeImageUrlTransform("/static/img.png"), "/static/img.png");
  assert.equal(safeImageUrlTransform("./assets/icon.svg"), "./assets/icon.svg");
  assert.equal(safeImageUrlTransform("../images/diagram.png"), "../images/diagram.png");

  // Rejected protocol-relative and backslash-based image sources
  assert.equal(safeImageUrlTransform("//evil.example/pic.png"), "");
  assert.equal(safeImageUrlTransform("   //evil.example/pic.png   "), "");
  assert.equal(safeImageUrlTransform("\\\\evil.example/pic.png"), "");
  assert.equal(safeImageUrlTransform("   \\\\evil.example/pic.png   "), "");
  assert.equal(safeImageUrlTransform("\\evil.example/pic.png"), "");
  assert.equal(safeImageUrlTransform("   \\evil.example/pic.png   "), "");
  assert.equal(safeImageUrlTransform("/\\\\evil.example/pic.png"), "");
  assert.equal(safeImageUrlTransform("   /\\\\evil.example/pic.png   "), "");
  assert.equal(safeImageUrlTransform("/\\evil.example/pic.png"), "");
  assert.equal(safeImageUrlTransform("   /\\evil.example/pic.png   "), "");
  assert.equal(safeImageUrlTransform("\\/evil.example/pic.png"), "");
  assert.equal(safeImageUrlTransform("   \\/evil.example/pic.png   "), "");
  assert.equal(safeImageUrlTransform("%5Cevil.example/pic.png"), "");
  assert.equal(safeImageUrlTransform("/%5Cevil.example/pic.png"), "");
  assert.equal(safeImageUrlTransform("/%5C%5Cevil.example/pic.png"), "");

  // Rejected unapproved schemes
  assert.equal(safeImageUrlTransform("mailto:user@domain.com"), "");
  assert.equal(safeImageUrlTransform("tel:+1234567890"), "");
  assert.equal(safeImageUrlTransform("javascript:alert(1)"), "");
  assert.equal(safeImageUrlTransform("vbscript:msgbox(1)"), "");
  assert.equal(safeImageUrlTransform("data:image/svg+xml,..."), "");
  assert.equal(safeImageUrlTransform("file:///C:/secret.png"), "");
  assert.equal(safeImageUrlTransform("blob:https://example.com/uuid"), "");

  // Rejected malformed or ambiguous strings
  assert.equal(safeImageUrlTransform("arbitrary_ambiguous_string"), "");
  assert.equal(safeImageUrlTransform("foo:bar"), "");
});

test("Formatting: headings and paragraphs render correctly", () => {
  const md = "# Heading 1\n\n## Heading 2\n\n### Heading 3\n\nParagraph text here.";
  const html = render(md);

  assert.match(html, /<h1>Heading 1<\/h1>/);
  assert.match(html, /<h2>Heading 2<\/h2>/);
  assert.match(html, /<h3>Heading 3<\/h3>/);
  assert.match(html, /<p>Paragraph text here\.<\/p>/);
});

test("Formatting: lists, emphasis, and code render correctly", () => {
  const md = "- Item A\n- Item B\n\n1. First\n2. Second\n\n**Bold** and *Italic*\n\nInline `code snippet`";
  const html = render(md);

  assert.match(html, /<ul>.*<li>Item A<\/li>.*<li>Item B<\/li>.*<\/ul>/s);
  assert.match(html, /<ol>.*<li>First<\/li>.*<li>Second<\/li>.*<\/ol>/s);
  assert.match(html, /<strong>Bold<\/strong>/);
  assert.match(html, /<em>Italic<\/em>/);
  assert.match(html, /<code>code snippet<\/code>/);
});

test("Formatting: fenced code blocks render with container and classes", () => {
  const md = "```bash\nkubectl get pods -n ingress-nginx\n```";
  const html = render(md);

  assert.match(html, /<pre class="kb-code-block"><code class="language-bash">kubectl get pods -n ingress-nginx\n<\/code><\/pre>/);
});

test("Formatting: GFM tables render with responsive wrapper", () => {
  const md = "| State | Meaning |\n|---|---|\n| Open | Active ticket |\n| Closed | Resolved fix |";
  const html = render(md);

  assert.match(html, /<div class="kb-table-wrap"><table class="kb-table">/);
  assert.match(html, /<th>State<\/th>/);
  assert.match(html, /<th>Meaning<\/th>/);
  assert.match(html, /<td>Open<\/td>/);
  assert.match(html, /<td>Active ticket<\/td>/);
});

test("Formatting: safe links and external link safety attributes", () => {
  const md = "[External Docs](https://docs.opsdesk.dev) and [Internal Article](/kb/99)";
  const html = render(md);

  assert.match(
    html,
    /<a href="https:\/\/docs\.opsdesk\.dev" target="_blank" rel="noopener noreferrer">External Docs<\/a>/
  );
  assert.match(html, /<a href="\/kb\/99">Internal Article<\/a>/);
  // Internal link should not have target="_blank"
  assert.doesNotMatch(html, /href="\/kb\/99"[^>]*target="_blank"/);
});

test("Formatting: empty, null, and malformed markdown do not crash", () => {
  assert.equal(render(""), "");
  assert.equal(render(null), "");
  assert.equal(render(undefined), "");

  // Malformed markdown structures
  const malformed = "### Broken table\n| col1 | col2\n|---\n[unclosed link(http://example.com\n```\nunclosed code";
  assert.doesNotThrow(() => {
    const html = render(malformed);
    assert.ok(html.includes("Broken table"));
  });
});

test("Security: script tags are escaped, not executed as DOM elements", () => {
  const md = "Hello <script>alert('pwned')</script> world";
  const html = render(md);

  assert.doesNotMatch(html, /<script\b/i);
  assert.ok(html.includes("&lt;script&gt;alert("));
  assert.ok(html.includes("&lt;/script&gt;"));
});

test("Security: HTML event-handlers and raw img tags are neutralized", () => {
  const md = 'Test <img src="x" onerror="alert(document.domain)"> payload';
  const html = render(md);

  assert.doesNotMatch(html, /<img\b[^>]*onerror/i);
  assert.match(html, /&lt;img src=&quot;x&quot; onerror=&quot;alert\(document\.domain\)&quot;&gt;/);
});

test("Security: dangerous javascript: and vbscript: links are neutralized to non-executable spans", () => {
  const md = "[Malicious](javascript:alert(document.cookie)) and [VBScript](vbscript:msgbox(1))";
  const html = render(md);

  assert.doesNotMatch(html, /href="javascript:/i);
  assert.doesNotMatch(html, /href="vbscript:/i);
  assert.match(html, /<span class="kb-unsafe-link">Malicious<\/span>/);
  assert.match(html, /<span class="kb-unsafe-link">VBScript<\/span>/);
});

test("Security: malicious markdown image payloads are blocked", () => {
  const md = "![Exploit](javascript:alert(1))";
  const html = render(md);

  assert.doesNotMatch(html, /src="javascript:/i);
});

test("Security: raw iframes, embeds, and objects are neutralized", () => {
  const md = '<iframe src="javascript:alert(1)"></iframe><object data="test"></object>';
  const html = render(md);

  assert.doesNotMatch(html, /<iframe\b/i);
  assert.doesNotMatch(html, /<object\b/i);
});

test("Security: protocol-relative and backslash-based links cannot render as navigable anchors", () => {
  const md = [
    "[A](//evil.example/path)",
    "[B](\\\\evil.example/path)",
    "[C](/\\\\evil.example/path)",
    "[D](   /\\\\evil.example/path   )",
    "[E](/\\evil.example/path)",
    "[F](\\\\/evil.example/path)"
  ].join(" ");
  const html = render(md);

  assert.doesNotMatch(html, /<a\b/i);
  assert.match(html, /<span class="kb-unsafe-link">A<\/span>/);
  assert.match(html, /<span class="kb-unsafe-link">B<\/span>/);
  assert.match(html, /<span class="kb-unsafe-link">C<\/span>/);
  assert.match(html, /<span class="kb-unsafe-link">D<\/span>/);
  assert.match(html, /<span class="kb-unsafe-link">E<\/span>/);
  assert.match(html, /<span class="kb-unsafe-link">F<\/span>/);
});

test("Security: image renderer rejects protocol-relative, backslash-based, unapproved schemes, and malformed strings", () => {
  const md = [
    "![PR](//evil.example/pic.png)",
    "![BS](\\\\evil.example/pic.png)",
    "![PBS](/\\\\evil.example/pic.png)",
    "![Padded](  /\\\\evil.example/pic.png  )",
    "![Mail](mailto:user@domain.com)",
    "![Tel](tel:+1234567890)",
    "![JS](javascript:alert(1))",
    "![VBS](vbscript:msgbox(1))",
    "![Data](data:image/svg+xml,...)",
    "![File](file:///C:/secret.png)",
    "![Blob](blob:https://example.com/uuid)",
    "![Ambiguous](arbitrary_ambiguous_string)"
  ].join(" ");
  const html = render(md);

  // None of the invalid image sources should produce an <img> tag
  assert.doesNotMatch(html, /<img\b/i);
});

test("Formatting: mailto and tel links remain permitted for anchor tags", () => {
  const md = "[Send Email](mailto:support@opsdesk.dev) and [Call Us](tel:+18005550199)";
  const html = render(md);

  assert.match(html, /<a href="mailto:support@opsdesk\.dev">Send Email<\/a>/);
  assert.match(html, /<a href="tel:\+18005550199">Call Us<\/a>/);
});

test("Formatting: valid images render with lazy loading across supported schemes and relative paths", () => {
  const md = [
    "![HTTPS](https://example.com/logo.png)",
    "![HTTP](http://localhost:8000/static/pic.jpg)",
    "![Root](/static/icon.svg)",
    "![Dot](./assets/icon.svg)",
    "![Parent](../images/diagram.png)"
  ].join(" ");
  const html = render(md);

  assert.match(html, /<img src="https:\/\/example\.com\/logo\.png" alt="HTTPS" loading="lazy"\/>/);
  assert.match(html, /<img src="http:\/\/localhost:8000\/static\/pic\.jpg" alt="HTTP" loading="lazy"\/>/);
  assert.match(html, /<img src="\/static\/icon\.svg" alt="Root" loading="lazy"\/>/);
  assert.match(html, /<img src="\.\/assets\/icon\.svg" alt="Dot" loading="lazy"\/>/);
  assert.match(html, /<img src="\.\.\/images\/diagram\.png" alt="Parent" loading="lazy"\/>/);
});
