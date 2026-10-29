import React, { useEffect, useState } from "react";
import { api, getAuth } from "../api.js";
import { useApp } from "../App.jsx";
import { isPrivilegedRole } from "../roles.js";
import { SOURCE_LABEL } from "../util.js";
import { idByName, ticketsLink } from "./dashboardLinks.js";
import { exportContentProblem, exportFileName } from "./opsHelpers.js";

const fmt = (v, unit = "") => (v === null || v === undefined ? "-" : `${v}${unit}`);
/** Hours value shown in minutes when under one hour. */
const fmtHours = (v) => (v === null || v === undefined ? "-" : v < 1 ? `${Math.round(v * 60)}m` : `${v}h`);

/** A link to another screen that behaves like a normal anchor (new-tab and copy-link work) but navigates inside the app. */
function Go({ to, className, title, children, ...rest }) {
  const { navigate } = useApp();
  if (!to) return <span className={className} {...rest}>{children}</span>;
  const onClick = (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    navigate(to);
  };
  return <a href={to} onClick={onClick} className={className} title={title} {...rest}>{children}</a>;
}

function HBars({ rows, colorOf = () => "var(--accent)", labelOf = (r) => r.name || "Unassigned", hrefOf = () => null }) {
  const max = Math.max(1, ...rows.map((r) => r.count));
  return rows.map((r, i) => {
    const to = hrefOf(r);
    return (
      <Go key={i} to={to} className={`hbar${to ? " hbar-link" : ""}`} title={to ? "Show these tickets" : undefined}>
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{labelOf(r)}</span>
        <span className="track"><i style={{ width: `${(r.count / max) * 100}%`, background: colorOf(r) }} /></span>
        <span className="num" style={{ textAlign: "right" }}>{r.count}</span>
      </Go>
    );
  });
}

export default function Dashboard() {
  const { meta, user } = useApp();
  const employeeView = !isPrivilegedRole(user); // employees see only their own tickets, so the account and team breakdowns are hidden
  const [days, setDays] = useState(30);
  const [account, setAccount] = useState("");
  const [exportType, setExportType] = useState("overview");
  const [exporting, setExporting] = useState(false);
  const [exportMsg, setExportMsg] = useState({ kind: "", text: "" });
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    setD(null);
    api(`/reports/overview/?days=${days}${account ? `&account=${account}` : ""}`).then(setD).catch((e) => setErr(e.message));
  }, [days, account]);

  const handleExport = async (format) => {
    if (exporting) return; // one export at a time
    setExporting(true);
    setExportMsg({ kind: "", text: "" });
    try {
      const token = getAuth()?.access;
      const url = `/api/reports/export/?report=${exportType}&format=${format}&days=${days}${account ? `&account=${account}` : ""}`;
      const res = await fetch(url, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail || `Export failed (${res.status})`);
      }
      // Only a file of the requested type counts as success (U6 E6): never save JSON or an error page
      // under a .csv/.pdf name.
      const problem = exportContentProblem(format, res.headers.get("content-type"));
      if (problem) throw new Error(problem);
      const blob = await res.blob();
      const name = exportFileName(res.headers.get("content-disposition"), `opsdesk_${exportType}_${days}d.${format}`);
      const blobUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = blobUrl;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(blobUrl);
      setExportMsg({ kind: "ok", text: `Downloaded ${name} (server export, last ${days} days${account ? ", filtered account" : ", all accounts"}).` });
    } catch (e) {
      setExportMsg({ kind: "bad", text: `Export failed: ${e.message}` });
    } finally {
      setExporting(false);
    }
  };

  const acct = account ? { account } : {};
  // The accounts table has names only; the ticket list filters by id.
  const accountLink = (name, extra) => {
    const id = idByName(meta.accounts, name);
    return id ? ticketsLink(extra.sla_status ? "sla" : "open", { account: id, all: 1, ...extra }) : null;
  };
  const max = d ? Math.max(1, ...d.series.map((s) => Math.max(s.created, s.resolved))) : 1;
  return (
    <div className="page">
      <div className="page-head">
        <h1>Dashboard</h1>
        <div className="row" style={{ flexWrap: "wrap", gap: "8px" }}>
          <select className="input" style={{ width: "auto" }} value={account} onChange={(e) => setAccount(e.target.value)} aria-label="Account">
            <option value="">All accounts</option>
            {meta.accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
          <div className="seg" role="group" aria-label="Period">
            {[7, 30, 90].map((n) => <button key={n} className={days === n ? "on" : ""} onClick={() => setDays(n)}>{n} days</button>)}
          </div>
          <div className="row" style={{ gap: "6px", alignItems: "center" }}>
            <select
              className="input"
              style={{ width: "auto", fontSize: "0.85rem" }}
              value={exportType}
              onChange={(e) => setExportType(e.target.value)}
              aria-label="Report Type"
            >
              <option value="overview">Overview Report</option>
              <option value="tickets">Tickets Report</option>
              <option value="incidents">Major Incidents</option>
              <option value="workload">Team Workload</option>
            </select>
            <button
              className="btn btn-sm"
              disabled={exporting}
              onClick={() => handleExport("csv")}
              title="Download CSV export"
            >
              {exporting ? "..." : "CSV"}
            </button>
            <button
              className="btn btn-sm"
              disabled={exporting}
              onClick={() => handleExport("pdf")}
              title="Download PDF export"
            >
              {exporting ? "..." : "PDF"}
            </button>
          </div>
        </div>
      </div>
      {exportMsg.kind === "bad" && <div className="notice bad" role="alert">{exportMsg.text}</div>}
      {exportMsg.kind === "ok" && <div className="small muted" role="status">{exportMsg.text}</div>}
      {err && <div className="notice bad" role="alert">{err}</div>}
      {!d && !err && <p className="muted">Crunching the numbers...</p>}
      {d && (
        <>
          <div className="kpis">
            <Go to={ticketsLink("open", acct)} className="kpi kpi-link" title="Show open tickets"><b>{d.open}</b><span>Open now</span></Go>
            <Go to={ticketsLink("sla", { ...acct, sla_status: "breached" })} className={`kpi kpi-link ${d.breached_open ? "bad" : ""}`} title="Show open breached tickets"><b>{d.breached_open}</b><span>Open and breached</span></Go>
            <Go to={ticketsLink("sla", { ...acct, sla_status: "at_risk" })} className={`kpi kpi-link ${d.at_risk ? "warn" : ""}`} title="Show at-risk tickets"><b>{d.at_risk}</b><span>At risk</span></Go>
            <Go to={ticketsLink("done", { ...acct, sla_status: "met" })} className="kpi kpi-link" title="Show resolved tickets that met their SLA"><b>{fmt(d.sla_compliance_pct, "%")}</b><span>SLA met (resolved)</span></Go>
            <div className="kpi"><b>{fmtHours(d.first_response_hours)}</b><span>Avg first response</span></div>
            <div className="kpi"><b>{fmt(d.mttr_hours, "h")}</b><span>Mean time to resolve</span></div>
            <div className="kpi"><b>{fmt(d.csat_avg)}</b><span>CSAT ({d.csat_responses} ratings)</span></div>
            <div className="kpi"><b>{fmt(d.ai_acceptance_pct, "%")}</b><span>AI suggestions accepted</span></div>
          </div>
          <div className="dash-grid">
            <section className="card">
              <div className="card-h"><h2>Created vs resolved</h2>
                <div className="legend">
                  <Go to={ticketsLink("open", { ...acct, all: 1, ordering: "created" })} title="Show tickets in every state, newest first"><i style={{ background: "var(--line-2)" }} />Created ({d.created})</Go>
                  <Go to={ticketsLink("done", acct)} title="Show resolved and closed tickets"><i style={{ background: "var(--accent)" }} />Resolved ({d.resolved})</Go>
                </div>
              </div>
              <div className="card-b">
                <div className="bars" role="img" aria-label={`Created ${d.created}, resolved ${d.resolved} in the last ${d.period_days} days`}>
                  {d.series.map((s) => (
                    <div key={s.date} className="day" title={`${s.date}: ${s.created} created, ${s.resolved} resolved`}>
                      <i style={{ height: `${(s.created / max) * 100}%`, background: "var(--line-2)" }} />
                      <i style={{ height: `${(s.resolved / max) * 100}%`, background: "var(--accent)" }} />
                    </div>
                  ))}
                </div>
                <div className="row small muted" style={{ marginTop: 6 }}><span>{d.series[0]?.date}</span><span className="spacer" /><span>today</span></div>
              </div>
            </section>
            <section className="card">
              <div className="card-h"><h2>Backlog age</h2></div>
              <div className="card-b"><HBars rows={d.aging} labelOf={(r) => r.bucket} hrefOf={() => ticketsLink("open", { ...acct, ordering: "oldest" })}
                colorOf={(r) => (r.bucket.includes("30") || r.bucket.includes("7-") ? "var(--warn)" : "var(--accent)")} /></div>
            </section>
            <section className="card">
              <div className="card-h"><h2>Intake by channel</h2></div>
              <div className="card-b"><HBars rows={d.by_source} labelOf={(r) => SOURCE_LABEL[r.source] || r.source} hrefOf={(r) => ticketsLink("open", { ...acct, source: r.source, all: 1 })} /></div>
            </section>
            <section className="card">
              <div className="card-h"><h2>Open by priority</h2></div>
              <div className="card-b"><HBars rows={d.by_priority} hrefOf={(r) => (r.key ? ticketsLink("open", { ...acct, priority: r.key }) : null)} labelOf={(r) => r.key || "None"} colorOf={(r) => r.color || "var(--ink-3)"} /></div>
            </section>
            {!employeeView && (
              <section className="card">
                <div className="card-h"><h2>Accounts this period</h2></div>
                <div className="card-b">
                  <table className="data">
                    <thead><tr><th>Account</th><th className="num">Tickets</th><th className="num">Breached</th></tr></thead>
                    <tbody>{d.by_account.map((a, i) => (
                      <tr key={i}>
                        <td><Go to={accountLink(a.name, {})}>{a.name || "No account"}</Go></td>
                        <td className="num"><Go to={accountLink(a.name, {})}>{a.count}</Go></td>
                        <td className="num" style={{ color: a.breached ? "var(--bad)" : undefined }}>
                          {a.breached ? <Go to={accountLink(a.name, { sla_status: "breached" })}>{a.breached}</Go> : a.breached}
                        </td></tr>
                    ))}</tbody>
                  </table>
                </div>
              </section>
            )}
            {!employeeView && (
              <section className="card">
                <div className="card-h"><h2>Open by team</h2></div>
                <div className="card-b"><HBars rows={d.by_team} hrefOf={(r) => { const id = idByName(meta.teams, r.name); return id ? ticketsLink("open", { ...acct, team: id }) : null; }} /></div>
              </section>
            )}
          </div>
        </>
      )}
    </div>
  );
}
