import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { ROLE_LABELS, STRATEGY_LABELS } from "./adminHelpers.js";
import { PageState } from "./AdminCommon.jsx";
import { actionLabel, fmtWhen, pct } from "./adminDashboardHelpers.js";

/** Horizontal bars; each row links to the Administration page that holds the data. */
function Bars({ rows, to, labelOf }) {
  const max = Math.max(1, ...rows.map((r) => r.count));
  if (!rows.length) return <p className="muted small">Nothing yet.</p>;
  return rows.map((r, i) => (
    <a key={i} href={to} className="hbar hbar-link">
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{labelOf(r)}</span>
      <span className="track"><i style={{ width: `${(r.count / max) * 100}%`, background: "var(--accent)" }} /></span>
      <span className="num" style={{ textAlign: "right" }}>{r.count}</span>
    </a>
  ));
}

export default function AdminDashboard() {
  const [state, setState] = useState({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    setState({ status: "loading" });
    api("/admin/overview/")
      .then((data) => alive && setState({ status: "ready", data }))
      .catch((e) => alive && setState({ status: e.status === 403 ? "forbidden" : "error", message: `The overview couldn't be loaded${e.message ? `: ${e.message}` : "."}` }));
    return () => { alive = false; };
  }, [attempt]);

  const head = (
    <div className="page-head">
      <div>
        <h1>Dashboard</h1>
        <p className="muted">Users, teams, SLA, configuration and audit activity for this workspace.</p>
      </div>
      <button type="button" className="btn sm" onClick={() => setAttempt((n) => n + 1)}>Refresh</button>
    </div>
  );
  if (state.status !== "ready") {
    return (
      <div className="page">
        {head}
        <PageState state={state} noun="the overview" onRetry={() => setAttempt((n) => n + 1)} forbiddenText="Administration is only available to the super admin." />
      </div>
    );
  }

  const { users, teams, sla, config, audit } = state.data;
  const maxDay = Math.max(1, ...audit.series.map((d) => d.count));
  const inactiveChannels = config.find((c) => c.slug === "channels");
  const defaultPolicy = sla.policies.find((p) => p.is_default);

  return (
    <div className="page">
      {head}
      <div className="kpis">
        <a href="/settings/users" className="kpi kpi-link"><b>{users.active}</b><span>Active users</span></a>
        <a href="/settings/users" className={`kpi kpi-link ${users.deactivated ? "warn" : ""}`}><b>{users.deactivated}</b><span>Deactivated users</span></a>
        <a href="/settings/teams" className={`kpi kpi-link ${teams.without_members ? "warn" : ""}`}><b>{teams.total}</b><span>Teams ({teams.without_members} without members)</span></a>
        <a href="/settings/sla" className="kpi kpi-link"><b>{sla.policies.length}</b><span>SLA policies ({sla.business_hours} calendars)</span></a>
        <a href="/settings/config" className="kpi kpi-link"><b>{inactiveChannels ? `${inactiveChannels.active ?? inactiveChannels.total}/${inactiveChannels.total}` : "-"}</b><span>Channels on</span></a>
        <a href="/settings/audit-log" className="kpi kpi-link"><b>{audit.last_24h}</b><span>Audit events, 24 h</span></a>
      </div>

      <div className="dash-grid">
        <section className="card">
          <div className="card-h"><h2>Users by role</h2><a href="/settings/users" className="small">Open Users</a></div>
          <div className="card-b">
            <Bars rows={users.by_role} to="/settings/users" labelOf={(r) => ROLE_LABELS[r.role] || r.role} />
          </div>
        </section>

        <section className="card">
          <div className="card-h"><h2>Sign-in and security</h2></div>
          <div className="card-b">
            <table className="data">
              <tbody>
                <tr><td>Two-step sign-in on</td><td className="num">{users.two_step_enabled} of {users.active} ({pct(users.two_step_enabled, users.active)})</td></tr>
                <tr><td>Deactivated accounts</td><td className="num">{users.deactivated}</td></tr>
              </tbody>
            </table>
          </div>
        </section>

        <section className="card">
          <div className="card-h"><h2>Largest teams</h2><a href="/settings/teams" className="small">Open Teams</a></div>
          <div className="card-b">
            <Bars rows={teams.largest.map((t) => ({ ...t, count: t.members }))} to="/settings/teams"
              labelOf={(t) => `${t.name} (${STRATEGY_LABELS[t.strategy] || t.strategy})`} />
            {teams.without_members > 0 && <p className="small muted" style={{ marginTop: 8 }}>{teams.without_members} team{teams.without_members === 1 ? " has" : "s have"} no members, so nothing can be assigned to {teams.without_members === 1 ? "it" : "them"}.</p>}
          </div>
        </section>

        <section className="card">
          <div className="card-h"><h2>SLA policies</h2><a href="/settings/sla" className="small">Open SLA</a></div>
          <div className="card-b">
            {sla.policies.length === 0 ? <p className="muted small">No policies yet.</p> : (
              <table className="data">
                <thead><tr><th>Policy</th><th>Hours</th><th className="num">At risk</th><th className="num">Targets</th></tr></thead>
                <tbody>{sla.policies.map((p) => (
                  <tr key={p.id}>
                    <td><a href={`/settings/sla/${p.id}`}>{p.name}</a>{p.is_default ? " (default)" : ""}</td>
                    <td>{p.is_24x7 ? "24x7" : p.business_hours || "-"}</td>
                    <td className="num">{p.at_risk_percent}%</td>
                    <td className="num">{p.targets}</td>
                  </tr>
                ))}</tbody>
              </table>
            )}
            {!defaultPolicy && sla.policies.length > 0 && <p className="small muted" style={{ marginTop: 8 }}>No default policy is set.</p>}
          </div>
        </section>

        <section className="card">
          <div className="card-h"><h2>Configuration</h2><a href="/settings/config" className="small">Open Configuration</a></div>
          <div className="card-b">
            <table className="data">
              <thead><tr><th>Item</th><th className="num">Total</th><th className="num">Active</th></tr></thead>
              <tbody>{config.map((c) => (
                <tr key={c.slug}><td><a href={`/settings/config/${c.slug}`}>{c.label}</a></td><td className="num">{c.total}</td><td className="num">{c.active ?? "-"}</td></tr>
              ))}</tbody>
            </table>
          </div>
        </section>

        <section className="card">
          <div className="card-h">
            <h2>Audit activity, 14 days</h2>
            <span className="small muted">{audit.last_7d} in the last 7 days</span>
          </div>
          <div className="card-b">
            <div className="bars" role="img" aria-label={`${audit.last_24h} audit events in the last 24 hours`}>
              {audit.series.map((d) => (
                <div key={d.date} className="day" title={`${d.date}: ${d.count} events`}>
                  <i style={{ height: `${(d.count / maxDay) * 100}%`, background: "var(--accent)" }} />
                </div>
              ))}
            </div>
            <div className="row small muted" style={{ marginTop: 6 }}><span>{audit.series[0]?.date}</span><span className="spacer" /><span>today</span></div>
          </div>
        </section>

        <section className="card">
          <div className="card-h"><h2>Most frequent actions, 7 days</h2></div>
          <div className="card-b"><Bars rows={audit.top_actions} to="/settings/audit-log" labelOf={(r) => actionLabel(r.action)} /></div>
        </section>

        <section className="card">
          <div className="card-h"><h2>Recent activity</h2><a href="/settings/audit-log" className="small">Open Audit log</a></div>
          <div className="card-b">
            {audit.recent.length === 0 ? <p className="muted small">No events yet.</p> : (
              <table className="data">
                <tbody>{audit.recent.map((e) => (
                  <tr key={e.id}><td>{actionLabel(e.action)}</td><td>{e.actor || "System"}</td><td className="num">{fmtWhen(e.created_at)}</td></tr>
                ))}</tbody>
              </table>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
