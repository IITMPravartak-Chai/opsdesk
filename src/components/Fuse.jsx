import React from "react";
import { dateTime, duration } from "../util.js";

/** SLA fuse: burns from left to right as the deadline approaches. */
export function Fuse({ label, start, due, done, status, size, paused }) {
  if (!due) {
    return (
      <div className={`fuse none ${size || ""}`}>
        <div className="fuse-track" />
        <span className="fuse-label">{label ? `${label}: ` : ""}no target</span>
      </div>
    );
  }
  const s = new Date(start).getTime();
  const d = new Date(due).getTime();
  const end = done ? new Date(done).getTime() : Date.now();
  const frac = Math.max(0, Math.min(1, (end - s) / Math.max(1, d - s)));
  let cls = status;
  let text;
  if (done) {
    const late = end > d;
    cls = late ? "breached" : "met";
    text = late ? `missed by ${duration(end - d)}` : `met in ${duration(end - s)}`;
  } else if (paused) {
    cls = "paused";
    text = "clock paused";
  } else {
    if (!cls || cls === "on_track" || cls === "met" || cls === "none") cls = end > d ? "breached" : frac >= 0.75 ? "at_risk" : "on_track";
    text = end > d ? `breached ${duration(end - d)} ago` : `${duration(d - end)} left`;
  }
  return (
    <div className={`fuse ${cls} ${size || ""} ${done ? "done" : ""}`} title={`Due ${dateTime(due)}`}>
      <div className="fuse-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(frac * 100)}
        aria-label={`${label || "SLA"} ${text}`}>
        <div className="fuse-burn" style={{ width: `${Math.max(frac * 100, 2)}%` }} />
      </div>
      <span className="fuse-label">{label ? `${label} ` : ""}{text}</span>
    </div>
  );
}

/** Picks the SLA clock that matters next for a list row. */
export function RowFuse({ t }) {
  if (["resolved", "closed", "cancelled"].includes(t.state.category)) {
    return t.resolution_due_at
      ? <Fuse label="Resolve" start={t.created_at} due={t.resolution_due_at} done={t.resolved_at || t.updated_at} />
      : null;
  }
  if (!t.first_responded_at && t.response_due_at) {
    return <Fuse label="Respond" start={t.created_at} due={t.response_due_at} status={t.sla_status} />;
  }
  if (!t.resolution_due_at) return null;
  return <Fuse label="Resolve" start={t.created_at} due={t.resolution_due_at} status={t.sla_status} paused={t.sla_status === "paused"} />;
}
