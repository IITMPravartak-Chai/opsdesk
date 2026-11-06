import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import {
  DAYS, canManageSla, checkSchedule, checkTargets, formatMinutes, ifMatch, impactLines, loadError, scheduleRows,
  settingsRoute, toSchedule, toTargets, writeError,
} from "./adminHelpers.js";
import { BackLink, ConfirmDialog, PageState, SettingsNav, WriteResult } from "./AdminCommon.jsx";
import { TimeZoneOptions } from "./Users.jsx";
import { DeleteSlaDialog, NewHoursDialog, NewPolicyDialog } from "./SlaCreate.jsx";

const FORBIDDEN = "SLA configuration is available to managers only.";

/** SLA configuration (U7-A3). Saving changes configuration only: existing due dates never move, and a change that the
 * SLA monitor would apply to active tickets is confirmed with the server's own impact count first. */
export default function SlaConfig() {
  const { user, loc } = useApp();
  const route = settingsRoute(loc.path);
  let body;
  if (!canManageSla(user)) body = <PageState state={{ status: "forbidden" }} forbiddenText={FORBIDDEN} />;
  else if (route?.id && route.kind === "hours") body = <HoursDetail id={route.id} />;
  else if (route?.id) body = <PolicyDetail id={route.id} />;
  else body = <SlaOverview />;
  return (
    <div className="page admin-page">
      <div className="page-head">
        <div>
          <h1>SLA configuration</h1>
          <p className="muted admin-sub">Response and resolution targets, and the business hours they're counted in.</p>
        </div>
      </div>
      <SettingsNav current="sla" />
      {body}
    </div>
  );
}

const hoursSummary = (bh) => {
  if (bh.is_24x7) return "24x7";
  const days = DAYS.filter((_, i) => (bh.schedule?.[String(i)] || []).length).map((d) => d.slice(0, 3));
  return days.length ? days.join(", ") : "No working periods";
};

function SlaOverview() {
  const { navigate } = useApp();
  const [policies, setPolicies] = useState({ status: "loading" });
  const [hours, setHours] = useState({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    api("/admin/sla-policies/").then((data) => setPolicies({ status: "ready", data })).catch((e) => setPolicies(loadError(e, "SLA policies")));
    api("/admin/business-hours/").then((data) => setHours({ status: "ready", data })).catch((e) => setHours(loadError(e, "business hours")));
  }, [attempt]);
  const go = (to) => (e) => { e.preventDefault(); navigate(to); };
  const retry = () => setAttempt((n) => n + 1);
  const [creating, setCreating] = useState(null); // "policy" | "hours"

  return (
    <>
      <section className="notice admin-note" role="note">
        Changes here never move due dates already set on tickets. Target changes apply to new tickets, and to an existing
        ticket only when its priority or account changes. At-risk thresholds and business hours are read live, so the next
        SLA check (about once a minute) can change ticket statuses and notify assignees; you'll see how many before saving.
      </section>

      <div className="row admin-toolbar">
        <button type="button" className="btn primary sm" onClick={() => setCreating("policy")}>New policy</button>
        <button type="button" className="btn sm" onClick={() => setCreating("hours")}>New business hours</button>
      </div>
      {creating === "policy" && <NewPolicyDialog onClose={() => setCreating(null)} onCreated={(p) => navigate(`/settings/sla/${p.id}`)} />}
      {creating === "hours" && <NewHoursDialog onClose={() => setCreating(null)} onCreated={(b) => navigate(`/settings/sla/hours/${b.id}`)} />}

      <h2 className="admin-h2">Policies</h2>
      <PageState state={policies} noun="SLA policies" onRetry={retry} forbiddenText={FORBIDDEN} />
      {policies.data?.default_conflicts?.length > 0 && (
        <div className="callout" role="alert">
          <strong>More than one default policy applies to the same tickets.</strong>
          {policies.data.default_conflicts.map((c) => (
            <div key={c.scope}>{c.scope}: {c.policies.map((p) => p.name).join(" and ")}. New tickets get one of them arbitrarily; remove the default flag from all but one.</div>
          ))}
        </div>
      )}
      {policies.status === "ready" && policies.data.results.length === 0 && <div className="empty" role="status">No SLA policies exist, so tickets aren't timed.</div>}
      {policies.data?.results?.length > 0 && (
        <div className="admin-table-wrap">
          <table className="data">
            <caption className="sr-only">SLA policies</caption>
            <thead><tr><th scope="col">Policy</th><th scope="col">Applies to</th><th scope="col">Business hours</th><th scope="col">At risk at</th><th scope="col">Active tickets</th></tr></thead>
            <tbody>{policies.data.results.map((p) => (
              <tr key={p.id}>
                <td><a href={`/settings/sla/${p.id}`} onClick={go(`/settings/sla/${p.id}`)}>{p.name}</a>{p.is_default && <> <span className="chip ok">Default</span></>}</td>
                <td>{p.ticket_types.length ? p.ticket_types.map((t) => t.name).join(", ") : "All ticket types"}</td>
                <td>{p.business_hours ? p.business_hours.name : "24x7 (none set)"}</td>
                <td>{p.at_risk_percent}%</td>
                <td>{p.active_ticket_count}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}

      <h2 className="admin-h2">Business hours</h2>
      <PageState state={hours} noun="business hours" onRetry={retry} forbiddenText={FORBIDDEN} />
      {hours.status === "ready" && hours.data.results.length === 0 && <div className="empty" role="status">No business hours exist; policies without them run 24x7.</div>}
      {hours.data?.results?.length > 0 && (
        <div className="admin-table-wrap">
          <table className="data">
            <caption className="sr-only">Business hours</caption>
            <thead><tr><th scope="col">Calendar</th><th scope="col">Days</th><th scope="col">Time zone</th><th scope="col">Used by</th><th scope="col">Active tickets</th></tr></thead>
            <tbody>{hours.data.results.map((b) => (
              <tr key={b.id}>
                <td><a href={`/settings/sla/hours/${b.id}`} onClick={go(`/settings/sla/hours/${b.id}`)}>{b.name}</a>{!b.valid && <> <span className="chip warn">Needs attention</span></>}</td>
                <td>{hoursSummary(b)}</td>
                <td>{b.timezone}</td>
                <td>{[b.policies.length && `${b.policies.length} polic${b.policies.length === 1 ? "y" : "ies"}`, b.account_count && `${b.account_count} account${b.account_count === 1 ? "" : "s"}`].filter(Boolean).join(", ") || "Nothing"}</td>
                <td>{b.active_ticket_count}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </>
  );
}

/** Save with the impact confirmation the server may ask for (409 with `impact`). */
function useImpactSave(noun) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [pending, setPending] = useState(null); // { impact, run }
  const save = async (send, onSaved) => {
    setBusy(true);
    setResult(null);
    try {
      onSaved(await send(false));
      setResult({ ok: true, message: "Changes saved." });
    } catch (e) {
      if (e.status === 409 && e.data?.impact) setPending({ impact: e.data.impact, run: () => send(true), onSaved });
      else setResult({ ok: false, message: writeError(e, noun), stale: e.status === 412 });
    } finally {
      setBusy(false);
    }
  };
  const confirm = async () => {
    setBusy(true);
    try {
      pending.onSaved(await pending.run());
      setResult({ ok: true, message: "Changes saved." });
    } catch (e) {
      setResult({ ok: false, message: writeError(e, noun), stale: e.status === 412 });
    } finally {
      setBusy(false);
      setPending(null);
    }
  };
  return { busy, result, setResult, pending, setPending, save, confirm };
}

function ImpactDialog({ pending, busy, noun, onConfirm, onClose }) {
  if (!pending) return null;
  return (
    <ConfirmDialog title="Apply this change to active tickets?" confirmLabel="Apply change" busy={busy} onConfirm={onConfirm} onClose={onClose}>
      <ul className="admin-impact">{impactLines(pending.impact, noun).map((l) => <li key={l}>{l}</li>)}</ul>
      <p className="small muted">Consider saving outside busy hours if many tickets will be marked at risk.</p>
    </ConfirmDialog>
  );
}

function PolicyDetail({ id }) {
  const [state, setState] = useState({ status: "loading" });
  const [form, setForm] = useState(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const s = useImpactSave("SLA policy");
  const [deleting, setDeleting] = useState(false);
  const { navigate } = useApp();

  const adopt = (data) => {
    setState({ status: "ready", data });
    setForm({
      name: data.name, is_default: data.is_default, at_risk_percent: String(data.at_risk_percent),
      business_hours_id: data.business_hours?.id ?? null, ticket_type_ids: data.ticket_types.map((t) => t.id),
      targets: data.targets.map((t) => ({ priority_id: t.priority.id, label: `${t.priority.key} ${t.priority.name}`,
        first_response_minutes: t.first_response_minutes ?? "", resolution_minutes: t.resolution_minutes ?? "" })),
    });
  };
  const load = () => api(`/admin/sla-policies/${id}/`).then(adopt).catch((e) => setState(loadError(e, "SLA policy")));
  useEffect(() => { setState({ status: "loading" }); load(); }, [id, attempt]); // eslint-disable-line react-hooks/exhaustive-deps

  if (state.status !== "ready") {
    return (<><BackLink to="/settings/sla" label="SLA configuration" /><PageState state={state} noun="SLA policy" onRetry={() => setAttempt((n) => n + 1)} forbiddenText={FORBIDDEN} /></>);
  }
  const p = state.data;
  const original = {
    name: p.name, is_default: p.is_default, at_risk_percent: p.at_risk_percent, business_hours_id: p.business_hours?.id ?? null,
    ticket_type_ids: p.ticket_types.map((t) => t.id).sort((a, b) => a - b),
    targets: JSON.stringify(toTargets(p.targets.map((t) => ({ priority_id: t.priority.id, first_response_minutes: t.first_response_minutes ?? "", resolution_minutes: t.resolution_minutes ?? "" })))),
  };
  const body = {};
  if (form.name.trim() !== original.name) body.name = form.name.trim();
  if (form.is_default !== original.is_default) body.is_default = form.is_default;
  if (Number(form.at_risk_percent) !== original.at_risk_percent) body.at_risk_percent = Number(form.at_risk_percent);
  if (form.business_hours_id !== original.business_hours_id) body.business_hours_id = form.business_hours_id;
  const types = [...form.ticket_type_ids].sort((a, b) => a - b);
  if (JSON.stringify(types) !== JSON.stringify(original.ticket_type_ids)) body.ticket_type_ids = types;
  if (JSON.stringify(toTargets(form.targets)) !== original.targets) body.targets = toTargets(form.targets);
  const dirty = Object.keys(body).length > 0;

  const submit = (e) => {
    e.preventDefault();
    const pct = Number(form.at_risk_percent);
    const problem = !form.name.trim() ? "Enter a policy name."
      : !Number.isInteger(pct) || pct < 1 || pct > 100 ? "The at-risk threshold must be a whole number from 1 to 100."
        : checkTargets(form.targets);
    setError(problem);
    if (problem) return;
    s.save((confirmImpact) => api(`/admin/sla-policies/${id}/`, { method: "PATCH", body: confirmImpact ? { ...body, confirm_impact: true } : body, headers: ifMatch(p.version) }), adopt);
  };
  const setTarget = (i, k) => (e) => setForm((f) => ({ ...f, targets: f.targets.map((t, j) => (j === i ? { ...t, [k]: e.target.value } : t)) }));
  const toggleType = (tid) => setForm((f) => ({ ...f, ticket_type_ids: f.ticket_type_ids.includes(tid) ? f.ticket_type_ids.filter((x) => x !== tid) : [...f.ticket_type_ids, tid] }));

  return (
    <>
      <BackLink to="/settings/sla" label="SLA configuration" />
      {deleting && <DeleteSlaDialog kind="policy" id={id} name={p.name} onClose={() => setDeleting(false)} onDeleted={() => navigate("/settings/sla")} />}
      {p.default_conflicts.length > 0 && (
        <div className="callout" role="alert">
          This policy and {p.default_conflicts[0].policies.filter((x) => x.id !== p.id).map((x) => x.name).join(", ")} are both defaults for {p.default_conflicts[0].scope.toLowerCase()}, so new tickets get one of them arbitrarily. Turn off "Default" on all but one.
        </div>
      )}
      <WriteResult result={s.result} onReload={() => { s.setResult(null); load(); }} />
      <form className="card card-b admin-form" onSubmit={submit} aria-labelledby="policy-title">
        <h2 id="policy-title" className="admin-h2">{p.name}</h2>
        <p className="small muted">{p.active_ticket_count} active and {p.paused_ticket_count} paused ticket{p.paused_ticket_count === 1 ? "" : "s"} use this policy; {p.account_count} account{p.account_count === 1 ? "" : "s"} name it.</p>
        <div className="admin-grid">
          <label className="field"><span>Name</span><input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} maxLength={120} required /></label>
          <label className="field"><span>At risk at (% of the target used)</span>
            <input className="input" type="number" min={1} max={100} step={1} value={form.at_risk_percent} onChange={(e) => setForm({ ...form, at_risk_percent: e.target.value })} required /></label>
          <label className="field"><span>Business hours</span>
            <select className="input" value={form.business_hours_id ?? ""} onChange={(e) => setForm({ ...form, business_hours_id: e.target.value ? Number(e.target.value) : null })}>
              <option value="">None (24x7)</option>
              {p.options.business_hours.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
            </select></label>
          <label className="toggle admin-wide">
            <input type="checkbox" checked={form.is_default} onChange={(e) => setForm({ ...form, is_default: e.target.checked })} />
            Default policy for tickets that don't get one from their account
          </label>
        </div>
        <fieldset className="admin-fieldset">
          <legend>Ticket types</legend>
          <div className="admin-checks">
            {p.options.ticket_types.map((t) => (
              <label key={t.id} className="toggle"><input type="checkbox" checked={form.ticket_type_ids.includes(t.id)} onChange={() => toggleType(t.id)} />{t.name}</label>
            ))}
          </div>
          <p className="small muted">None selected means all ticket types.</p>
        </fieldset>
        <fieldset className="admin-fieldset">
          <legend>Targets (working minutes)</legend>
          <div className="admin-table-wrap">
            <table className="data admin-targets">
              <caption className="sr-only">Targets by priority</caption>
              <thead><tr><th scope="col">Priority</th><th scope="col">First response</th><th scope="col">Resolution</th></tr></thead>
              <tbody>{form.targets.map((t, i) => (
                <tr key={t.priority_id}>
                  <th scope="row">{t.label}</th>
                  <td><input className="input" type="number" min={1} step={1} inputMode="numeric" value={t.first_response_minutes} onChange={setTarget(i, "first_response_minutes")} aria-label={`${t.label} first response, minutes`} />
                    <div className="small muted">{formatMinutes(t.first_response_minutes === "" ? null : t.first_response_minutes)}</div></td>
                  <td><input className="input" type="number" min={1} step={1} inputMode="numeric" value={t.resolution_minutes} onChange={setTarget(i, "resolution_minutes")} aria-label={`${t.label} resolution, minutes`} />
                    <div className="small muted">{formatMinutes(t.resolution_minutes === "" ? null : t.resolution_minutes)}</div></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
          <p className="small muted">Leave both empty to stop timing that priority under this policy.</p>
        </fieldset>
        {error && <div className="notice bad" role="alert">{error}</div>}
        <div className="row admin-actions">
          <button type="submit" className="btn primary sm" disabled={!dirty || s.busy}>{s.busy && !s.pending ? "Saving…" : "Save changes"}</button>
          {dirty && <button type="button" className="btn ghost sm" disabled={s.busy} onClick={() => { adopt(p); setError(""); }}>Discard</button>}
          <button type="button" className="btn danger sm" disabled={s.busy} onClick={() => setDeleting(true)}>Delete policy</button>
        </div>
      </form>
      <ImpactDialog pending={s.pending} busy={s.busy} noun="policy" onConfirm={s.confirm} onClose={() => s.setPending(null)} />
    </>
  );
}

function HoursDetail({ id }) {
  const [state, setState] = useState({ status: "loading" });
  const [form, setForm] = useState(null);
  const [holiday, setHoliday] = useState("");
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const s = useImpactSave("business hours");
  const [deleting, setDeleting] = useState(false);
  const { navigate } = useApp();

  const adopt = (data) => {
    setState({ status: "ready", data });
    setForm({ name: data.name, timezone: data.timezone, is_24x7: data.is_24x7, rows: scheduleRows(data.schedule), holidays: [...data.holidays] });
  };
  const load = () => api(`/admin/business-hours/${id}/`).then(adopt).catch((e) => setState(loadError(e, "business hours")));
  useEffect(() => { setState({ status: "loading" }); load(); }, [id, attempt]); // eslint-disable-line react-hooks/exhaustive-deps

  if (state.status !== "ready") {
    return (<><BackLink to="/settings/sla" label="SLA configuration" /><PageState state={state} noun="business hours" onRetry={() => setAttempt((n) => n + 1)} forbiddenText={FORBIDDEN} /></>);
  }
  const b = state.data;
  const body = {};
  if (form.name.trim() !== b.name) body.name = form.name.trim();
  if (form.timezone !== b.timezone) body.timezone = form.timezone;
  if (form.is_24x7 !== b.is_24x7) body.is_24x7 = form.is_24x7;
  const schedule = toSchedule(form.rows);
  if (JSON.stringify(schedule) !== JSON.stringify(toSchedule(scheduleRows(b.schedule)))) body.schedule = schedule;
  const holidays = [...new Set(form.holidays)].sort();
  if (JSON.stringify(holidays) !== JSON.stringify([...b.holidays].sort())) body.holidays = holidays;
  const dirty = Object.keys(body).length > 0;

  const setRange = (day, i, k) => (e) => setForm((f) => ({ ...f, rows: f.rows.map((r) => (r.day !== day ? r : { ...r, ranges: r.ranges.map((x, j) => (j === i ? { ...x, [k]: e.target.value } : x)) })) }));
  const addRange = (day) => setForm((f) => ({ ...f, rows: f.rows.map((r) => (r.day !== day ? r : { ...r, ranges: [...r.ranges, { start: "09:00", end: "18:00" }] })) }));
  const dropRange = (day, i) => setForm((f) => ({ ...f, rows: f.rows.map((r) => (r.day !== day ? r : { ...r, ranges: r.ranges.filter((_, j) => j !== i) })) }));
  const submit = (e) => {
    e.preventDefault();
    const problem = !form.name.trim() ? "Enter a name." : !form.timezone.trim() ? "Enter a time zone." : checkSchedule(form.rows, form.is_24x7);
    setError(problem);
    if (problem) return;
    s.save((confirmImpact) => api(`/admin/business-hours/${id}/`, { method: "PATCH", body: confirmImpact ? { ...body, confirm_impact: true } : body, headers: ifMatch(b.version) }), adopt);
  };

  return (
    <>
      <BackLink to="/settings/sla" label="SLA configuration" />
      {deleting && <DeleteSlaDialog kind="hours" id={id} name={b.name} onClose={() => setDeleting(false)} onDeleted={() => navigate("/settings/sla")} />}
      {!b.valid && <div className="callout" role="alert">These business hours have a problem the SLA engine can't work with (for example no working periods while not 24x7). Fix them below.</div>}
      <WriteResult result={s.result} onReload={() => { s.setResult(null); load(); }} />
      <form className="card card-b admin-form" onSubmit={submit} aria-labelledby="hours-title">
        <h2 id="hours-title" className="admin-h2">{b.name}</h2>
        <p className="small muted">Used by {b.policies.length ? b.policies.map((x) => x.name).join(", ") : "no policy"}{b.account_count ? ` and ${b.account_count} account${b.account_count === 1 ? "" : "s"}` : ""}; {b.active_ticket_count} active ticket{b.active_ticket_count === 1 ? "" : "s"} are timed with it.</p>
        <div className="admin-grid">
          <label className="field"><span>Name</span><input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} maxLength={120} required /></label>
          <label className="field"><span>Time zone</span><input className="input" value={form.timezone} onChange={(e) => setForm({ ...form, timezone: e.target.value })} list="tz-options" maxLength={64} required /></label>
          <label className="toggle admin-wide"><input type="checkbox" checked={form.is_24x7} onChange={(e) => setForm({ ...form, is_24x7: e.target.checked })} />Open 24 hours, 7 days (the weekly schedule and holidays are ignored)</label>
        </div>
        <fieldset className="admin-fieldset" disabled={form.is_24x7}>
          <legend>Weekly schedule</legend>
          <div className="admin-schedule">
            {form.rows.map((r) => (
              <div key={r.day} className="admin-day" role="group" aria-label={r.label}>
                <div className="admin-day-name">{r.label}</div>
                <div className="admin-day-ranges">
                  {r.ranges.length === 0 && <span className="small muted">Closed</span>}
                  {r.ranges.map((x, i) => (
                    <div key={i} className="row admin-range">
                      <input className="input" type="time" value={x.start} onChange={setRange(r.day, i, "start")} aria-label={`${r.label} period ${i + 1} start`} />
                      <span aria-hidden="true">to</span>
                      <input className="input" type="time" value={x.end} onChange={setRange(r.day, i, "end")} aria-label={`${r.label} period ${i + 1} end (00:00 means midnight)`} />
                      <button type="button" className="btn ghost sm" onClick={() => dropRange(r.day, i)} aria-label={`Remove ${r.label} period ${i + 1}`}>Remove</button>
                    </div>
                  ))}
                  {r.ranges.length < 6 && <button type="button" className="btn ghost sm" onClick={() => addRange(r.day)}>Add period</button>}
                </div>
              </div>
            ))}
          </div>
          <p className="small muted">Times are in the time zone above. An end of 00:00 means midnight.</p>
        </fieldset>
        <fieldset className="admin-fieldset" disabled={form.is_24x7}>
          <legend>Holidays</legend>
          {form.holidays.length === 0 ? <p className="small muted">No holidays.</p> : (
            <ul className="admin-holidays">{holidays.map((h) => (
              <li key={h}><span>{h}</span><button type="button" className="btn ghost sm" onClick={() => setForm({ ...form, holidays: form.holidays.filter((x) => x !== h) })} aria-label={`Remove holiday ${h}`}>Remove</button></li>
            ))}</ul>
          )}
          <div className="row admin-add">
            <label className="field"><span>Add a holiday</span><input className="input" type="date" value={holiday} onChange={(e) => setHoliday(e.target.value)} /></label>
            <button type="button" className="btn sm" disabled={!holiday} onClick={() => { setForm({ ...form, holidays: [...form.holidays, holiday] }); setHoliday(""); }}>Add</button>
          </div>
        </fieldset>
        {error && <div className="notice bad" role="alert">{error}</div>}
        <div className="row admin-actions">
          <button type="submit" className="btn primary sm" disabled={!dirty || s.busy}>{s.busy && !s.pending ? "Saving…" : "Save changes"}</button>
          {dirty && <button type="button" className="btn ghost sm" disabled={s.busy} onClick={() => { adopt(b); setError(""); }}>Discard</button>}
          <button type="button" className="btn danger sm" disabled={s.busy} onClick={() => setDeleting(true)}>Delete business hours</button>
        </div>
      </form>
      <ImpactDialog pending={s.pending} busy={s.busy} noun="calendar" onConfirm={s.confirm} onClose={() => s.setPending(null)} />
      <TimeZoneOptions />
    </>
  );
}
