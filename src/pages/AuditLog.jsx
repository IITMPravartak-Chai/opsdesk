import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { SettingsNav } from "./AdminCommon.jsx";
import {
  COVERAGE, buildQuery, canReadAuditLog, changeRows, formatTimestamp, hasFilters, lastPage, objectLink, rangeLabel, viewerTimeZone,
} from "./auditLogHelpers.js";

const EMPTY_FILTERS = { action: "", actor: null, objectType: "", objectId: "", from: "", to: "" };

/** Read-only audit log (U7-A1). The server is the authority: it re-checks the manager role and the tenant on
 * every request; hiding this page from other roles is only a convenience. */
export default function AuditLog() {
  const { user, navigate } = useApp();
  const allowed = canReadAuditLog(user);
  const [draft, setDraft] = useState(EMPTY_FILTERS);
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [page, setPage] = useState(1);
  const [state, setState] = useState({ status: allowed ? "loading" : "forbidden" });
  const [actions, setActions] = useState([]);
  const [formError, setFormError] = useState("");
  const [open, setOpen] = useState(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!allowed) return;
    api("/admin/audit-logs/actions/").then((r) => setActions(r.actions || [])).catch(() => setActions([]));
  }, [allowed]);

  useEffect(() => {
    if (!allowed) return undefined;
    const { query, error } = buildQuery(filters, page);
    if (error) return undefined;
    let alive = true;
    setState((s) => ({ ...s, status: "loading" }));
    api(`/admin/audit-logs/?${query}`)
      .then((data) => alive && setState({ status: "ready", data }))
      .catch((e) => {
        if (!alive) return;
        if (e.status === 403) setState({ status: "forbidden" });
        else setState({ status: "error", message: e.status === 400 ? e.message : "The audit log couldn't be loaded." });
      });
    return () => { alive = false; };
  }, [allowed, filters, page, attempt]);

  const apply = (e) => {
    e.preventDefault();
    const { error } = buildQuery(draft, 1);
    setFormError(error);
    if (error) return;
    setOpen(null);
    setPage(1);
    setFilters(draft);
  };
  const clear = () => { setDraft(EMPTY_FILTERS); setFilters(EMPTY_FILTERS); setFormError(""); setPage(1); setOpen(null); };
  const byActor = (actor) => { const next = { ...filters, actor }; setDraft(next); setFilters(next); setPage(1); setOpen(null); };
  const set = (k) => (e) => setDraft((d) => ({ ...d, [k]: e.target.value }));

  const data = state.data;
  const rows = data?.results || [];
  const pages = lastPage(data?.count);

  return (
    <div className="page audit-page">
      <div className="page-head">
        <h1>Audit log</h1>
        <p className="muted" style={{ margin: "4px 0 0" }}>A read-only record of selected events in this workspace. Events can't be edited or removed here.</p>
      </div>
      <SettingsNav current="audit-log" />

      <section className="notice audit-coverage" role="note" aria-labelledby="audit-coverage-title">
        <strong id="audit-coverage-title">This is not a complete record of administrative activity.</strong>
        <div className="audit-coverage-cols">
          <div>
            <div className="small" style={{ fontWeight: 600 }}>Recorded</div>
            <ul>{COVERAGE.recorded.map((t) => <li key={t}>{t}</li>)}</ul>
          </div>
          <div>
            <div className="small" style={{ fontWeight: 600 }}>Not recorded yet</div>
            <ul>{COVERAGE.notRecorded.map((t) => <li key={t}>{t}</li>)}</ul>
          </div>
        </div>
        <div className="small">IP addresses aren't shown: the stored values can't be relied on. Times are in {viewerTimeZone()}; hover a time for UTC.</div>
      </section>

      {state.status === "forbidden" ? (
        <div className="empty" role="alert">
          <div>The audit log is available to managers only.</div>
          <button type="button" className="btn sm" onClick={() => navigate("/tickets")}>Back to tickets</button>
        </div>
      ) : (
        <>
          <form className="card card-b audit-filters" onSubmit={apply} aria-label="Filter audit events">
            <label className="field">
              <span>Event</span>
              <select className="input" value={draft.action} onChange={set("action")}>
                <option value="">All events</option>
                {actions.map((a) => <option key={a} value={a}>{a}</option>)}
              </select>
            </label>
            <label className="field">
              <span>Target type</span>
              <input className="input" value={draft.objectType} onChange={set("objectType")} placeholder="e.g. Ticket" maxLength={80} />
            </label>
            <label className="field">
              <span>Target ID</span>
              <input className="input" value={draft.objectId} onChange={set("objectId")} maxLength={64} />
            </label>
            <label className="field">
              <span>From</span>
              <input className="input" type="date" value={draft.from} onChange={set("from")} />
            </label>
            <label className="field">
              <span>To</span>
              <input className="input" type="date" value={draft.to} onChange={set("to")} />
            </label>
            <div className="row audit-filter-actions">
              <button type="submit" className="btn primary sm">Apply filters</button>
              {hasFilters(filters) || hasFilters(draft) ? <button type="button" className="btn ghost sm" onClick={clear}>Clear</button> : null}
            </div>
            {filters.actor && (
              <div className="row small" style={{ gridColumn: "1 / -1" }}>
                <span className="chip">Actor: {filters.actor.name}</span>
                <button type="button" className="btn ghost sm" onClick={() => byActor(null)} aria-label={`Remove actor filter ${filters.actor.name}`}>Remove</button>
              </div>
            )}
            {formError && <div className="notice bad" role="alert" style={{ gridColumn: "1 / -1" }}>{formError}</div>}
          </form>

          {state.status === "loading" && !data && <div className="muted" role="status">Loading audit events…</div>}
          {state.status === "error" && (
            <div className="notice bad" role="alert" style={{ display: "flex", gap: 12, alignItems: "center", justifyContent: "space-between" }}>
              <span>{state.message}</span>
              <button type="button" className="btn sm" onClick={() => setAttempt((n) => n + 1)}>Retry</button>
            </div>
          )}
          {state.status === "ready" && rows.length === 0 && (
            hasFilters(filters) ? (
              <div className="empty" role="status">
                <div>No events match these filters.</div>
                <button type="button" className="btn sm" onClick={clear}>Clear filters</button>
              </div>
            ) : (
              <div className="empty" role="status"><div>No audit events have been recorded in this workspace yet.</div></div>
            )
          )}
          {data && rows.length > 0 && (
            <>
              <div className="audit-table-wrap" aria-busy={state.status === "loading"}>
                <table className="data">
                  <caption className="sr-only">Audit events, newest first</caption>
                  <thead>
                    <tr><th scope="col">Time</th><th scope="col">Event</th><th scope="col">Actor</th><th scope="col">Target</th><th scope="col"><span className="sr-only">Details</span></th></tr>
                  </thead>
                  <tbody>
                    {rows.map((e) => {
                      const ts = formatTimestamp(e.created_at);
                      const link = objectLink(e);
                      const isOpen = open === e.id;
                      const detail = changeRows(e.changes);
                      return (
                        <React.Fragment key={e.id}>
                          <tr>
                            <td><time dateTime={e.created_at} title={ts.title}>{ts.text}</time></td>
                            <td><code>{e.action}</code></td>
                            <td>{e.actor
                              ? <button type="button" className="link-btn" onClick={() => byActor(e.actor)} title={`Show only events by ${e.actor.name}`}>{e.actor.name}</button>
                              : <span className="muted">System or removed user</span>}</td>
                            <td>{e.object_type ? (link
                              ? <a href={link} onClick={(ev) => { ev.preventDefault(); navigate(link); }}>{e.object_type} {e.object_id}</a>
                              : <span>{e.object_type} {e.object_id}</span>) : <span className="muted">—</span>}</td>
                            <td>
                              <button type="button" className="btn ghost sm" aria-expanded={isOpen} aria-controls={`audit-detail-${e.id}`}
                                onClick={() => setOpen(isOpen ? null : e.id)}>{isOpen ? "Hide" : "Details"}</button>
                            </td>
                          </tr>
                          {isOpen && (
                            <tr id={`audit-detail-${e.id}`} className="audit-detail">
                              <td colSpan={5}>
                                {detail.length === 0 ? <div className="small muted">No change details were recorded for this event.</div> : (
                                  <table className="data audit-changes">
                                    <caption className="sr-only">Recorded changes</caption>
                                    <thead><tr><th scope="col">Field</th><th scope="col">From</th><th scope="col">To</th></tr></thead>
                                    <tbody>{detail.map((d) => (
                                      <tr key={d.field}><td><code>{d.field}</code></td><td>{d.from === null ? <span className="muted">—</span> : d.from}</td><td>{d.to}</td></tr>
                                    ))}</tbody>
                                  </table>
                                )}
                                {e.changes_truncated && <div className="small muted" style={{ marginTop: 6 }}>Some values were shortened or hidden for safety.</div>}
                              </td>
                            </tr>
                          )}
                        </React.Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <nav className="row audit-pager" aria-label="Audit log pages">
                <span className="small muted" role="status">{rangeLabel(page, data.count)}</span>
                <button type="button" className="btn sm" disabled={page <= 1 || state.status === "loading"} onClick={() => { setOpen(null); setPage((p) => p - 1); }}>Previous</button>
                <span className="small">Page {page} of {pages}</span>
                <button type="button" className="btn sm" disabled={page >= pages || state.status === "loading"} onClick={() => { setOpen(null); setPage((p) => p + 1); }}>Next</button>
              </nav>
            </>
          )}
        </>
      )}
    </div>
  );
}
