import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const SAFE_PROTOCOLS = new Set(["http:", "https:", "mailto:", "tel:"]);
const SAFE_IMAGE_PROTOCOLS = new Set(["http:", "https:"]);
const DUMMY_BASE = "https://opsdesk-base.internal";

/**
 * Normalizes percent-encoded slashes and backslashes so that prefix,
 * authority, and relative-path inspections cannot be bypassed via encoding.
 */
function normalizeSlashes(str) {
  let res = str;
  for (let i = 0; i < 2; i++) {
    const next = res.replace(/%5c/gi, "\\").replace(/%2f/gi, "/");
    if (next === res) break;
    res = next;
  }
  return res;
}

/**
 * Validates and transforms a general Markdown link URL.
 * Permitted:
 * - Safe schemes: http:, https:, mailto:, tel:
 * - Safe relative paths: /path, ./path, ../path
 * - Safe fragment links: #section
 *
 * Rejected:
 * - Protocol-relative URLs: //evil.example/path
 * - Backslash-based protocol-relative variants: \\evil.example, /\evil.example, /\\evil.example, \/evil.example
 * - Encoded slash/backslash bypass attempts (%5C, %2F)
 * - Dangerous schemes: javascript:, vbscript:, data:, file:, blob:
 * - Malformed, control-character, or ambiguous URL forms
 */
export function safeUrlTransform(url) {
  if (!url || typeof url !== "string") return "";
  const trimmed = url.trim();
  if (!trimmed) return "";

  // Reject ASCII control characters
  if (/[\x00-\x1f\x7f]/.test(trimmed)) return "";

  const normalized = normalizeSlashes(trimmed);

  // Reject protocol-relative and backslash-based authority/path variants
  if (/^[\/\\\\]{2,}/.test(normalized) || normalized.startsWith("\\")) {
    return "";
  }

  // Fragment links
  if (trimmed.startsWith("#")) {
    return trimmed;
  }

  // Legitimate in-app relative paths: /path, ./path, ../path
  if (
    trimmed.startsWith("/") ||
    trimmed.startsWith("./") ||
    trimmed.startsWith("../")
  ) {
    if (normalized.includes("\\")) {
      return "";
    }
    try {
      const parsed = new URL(trimmed, DUMMY_BASE);
      if (parsed.origin !== DUMMY_BASE) {
        return "";
      }
      return trimmed;
    } catch {
      return "";
    }
  }

  // Absolute URLs (http, https, mailto, tel)
  try {
    const parsed = new URL(trimmed);
    const protocol = parsed.protocol.toLowerCase();
    if (SAFE_PROTOCOLS.has(protocol)) {
      if (
        normalized.startsWith(`${protocol}\\\\`) ||
        normalized.startsWith(`${protocol}\\/`) ||
        normalized.startsWith(`${protocol}/\\`) ||
        normalized.startsWith(`${protocol}\\`)
      ) {
        return "";
      }
      return trimmed;
    }
  } catch {
    return "";
  }

  return "";
}

/**
 * Validates image sources strictly:
 * - Permits absolute http: and https: URLs only.
 * - Permits explicitly recognized relative image paths: root-relative (/...),
 *   dot-relative (./...), and parent-relative (../...).
 * - Rejects protocol-relative URLs and all backslash-based variants (//..., \\..., /\..., /\\..., \/...).
 * - Rejects mailto:, tel:, javascript:, vbscript:, data:, file:, blob:, and unapproved schemes.
 * - Rejects arbitrary strings, malformed strings, or ambiguous URL forms (no unvalidated fallback).
 */
export function safeImageUrlTransform(url) {
  if (!url || typeof url !== "string") return "";
  const trimmed = url.trim();
  if (!trimmed) return "";

  // Reject ASCII control characters
  if (/[\x00-\x1f\x7f]/.test(trimmed)) return "";

  const normalized = normalizeSlashes(trimmed);

  // Reject protocol-relative and backslash-based authority/path variants
  if (/^[\/\\\\]{2,}/.test(normalized) || normalized.startsWith("\\")) {
    return "";
  }

  // Permit explicitly recognized relative image paths: /path, ./path, ../path
  if (
    trimmed.startsWith("/") ||
    trimmed.startsWith("./") ||
    trimmed.startsWith("../")
  ) {
    if (normalized.includes("\\")) {
      return "";
    }
    try {
      const parsed = new URL(trimmed, DUMMY_BASE);
      if (parsed.origin !== DUMMY_BASE) {
        return "";
      }
      return trimmed;
    } catch {
      return "";
    }
  }

  // Permit absolute HTTP(S) URLs only
  try {
    const parsed = new URL(trimmed);
    const protocol = parsed.protocol.toLowerCase();
    if (SAFE_IMAGE_PROTOCOLS.has(protocol)) {
      if (
        normalized.startsWith(`${protocol}\\\\`) ||
        normalized.startsWith(`${protocol}\\/`) ||
        normalized.startsWith(`${protocol}/\\`) ||
        normalized.startsWith(`${protocol}\\`)
      ) {
        return "";
      }
      return trimmed;
    }
  } catch {
    return "";
  }

  return "";
}

export const markdownComponents = {
  a: ({ href, children, node, ...props }) => {
    const safeHref = safeUrlTransform(href);
    if (!safeHref) {
      return React.createElement(
        "span",
        { className: "kb-unsafe-link" },
        children
      );
    }
    const isExternal =
      safeHref.startsWith("http://") || safeHref.startsWith("https://");
    return React.createElement(
      "a",
      {
        href: safeHref,
        ...(isExternal ? { target: "_blank", rel: "noopener noreferrer" } : {}),
        ...props,
      },
      children
    );
  },
  img: ({ src, alt, node, ...props }) => {
    const safeSrc = safeImageUrlTransform(src);
    if (!safeSrc) return null;
    return React.createElement("img", {
      src: safeSrc,
      alt: alt || "",
      loading: "lazy",
      ...props,
    });
  },
  table: ({ children, node, ...props }) =>
    React.createElement(
      "div",
      { className: "kb-table-wrap" },
      React.createElement("table", { className: "kb-table", ...props }, children)
    ),
  pre: ({ children, node, ...props }) =>
    React.createElement(
      "pre",
      { className: "kb-code-block", ...props },
      children
    ),
};

export default function SafeMarkdown({ content, className = "kb-body" }) {
  if (!content || typeof content !== "string") return null;

  return React.createElement(
    "div",
    { className },
    React.createElement(
      ReactMarkdown,
      {
        remarkPlugins: [remarkGfm],
        urlTransform: safeUrlTransform,
        components: markdownComponents,
      },
      content
    )
  );
}
