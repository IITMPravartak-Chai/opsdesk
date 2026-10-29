import React from "react";
import { SLA_LABEL } from "../util.js";

export const StateChip = ({ state }) => (
  <span className="chip" style={{ color: state.color }}><span className="dot" />{<span style={{ color: "var(--ink)" }}>{state.name}</span>}</span>
);

export const PriorityMark = ({ p }) => p ? <span className="prio" style={{ color: p.color }}>{p.key}</span> : <span className="prio muted">-</span>;

const SLA_COLORS = { on_track: "var(--ok)", at_risk: "var(--warn)", breached: "var(--bad)", paused: "var(--pause)", met: "var(--ok)", none: "var(--ink-3)" };
export const SlaChip = ({ status }) => (
  <span className="chip" style={{ color: SLA_COLORS[status] }}><span className="dot" /><span>{SLA_LABEL[status] || status}</span></span>
);
