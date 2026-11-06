import React from "react";
import { bannerSemantics } from "./uiPrimitives.js";

/**
 * DegradedBanner — Honest degraded/unavailable service notice (U2.2, UI-DEC-009).
 *
 * Used when a backend endpoint or integration is blocked (gateway deferred,
 * UI-DEC-001). Never fabricates data, operation counts, or success states.
 *
 * Props:
 *   title    — Short summary, e.g. "Service temporarily unavailable"
 *   detail   — Optional longer explanation, e.g. "The approval service is
 *              undergoing maintenance. Try again later or contact support."
 *   level    — "warning" (default) | "error" | "info"; "error" is announced as an alert,
 *              the others politely as a status
 *   action   — Optional { label, onClick } for a retry or contact link
 */
export default function DegradedBanner({ title, detail, level = "warning", action }) {
  const { role, className } = bannerSemantics(level);

  return (
    <div role={role} className={className}>
      <div className="degraded-banner__body">
        <strong className="degraded-banner__title">{title}</strong>
        {detail && <p className="degraded-banner__detail">{detail}</p>}
      </div>
      {action && (
        <button type="button" className="btn sm ghost" onClick={action.onClick}>
          {action.label}
        </button>
      )}
    </div>
  );
}
