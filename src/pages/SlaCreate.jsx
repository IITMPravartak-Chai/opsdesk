import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import Modal from "../components/Modal.jsx";
import { ConfirmDialog } from "./AdminCommon.jsx";
import { DAYS, checkSchedule, checkTargets, scheduleRows, toSchedule, toTargets } from "./adminHelpers.js";
import { configWriteError, fieldErrors } from "./configHelpers.js";
import { TimeZoneOptions } from "./Users.jsx";

/** Create business hours (managers). The server applies the same schedule rules as editing. */
export function NewHoursDialog({ onClose, onCreated }) {
  const [form, setForm] = useState({ name: "", timezone: "UTC", is_24x7: false, rows: scheduleRows({}), holidays: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const setRange = (day, i, k) => (e) => setForm((f) => ({ ...f, rows: f.rows.map((r) => (r.day === day ? { ...r, ranges: r.ranges.map((x, j) => (j === i ? { ...x, [k]: e.target.value } : x)) } : r)) }));
  const addRange = (day) => setForm((f) => ({ ...f, rows: f.rows.map((r) => (r.day === day ? { ...r, ranges: [...r.ranges, { start: "09:00", end: "18:00" }] } : r)) }));
  const dropRange = (day, i) => setForm((f) => ({ ...f, rows: f.rows.map((r) => (r.day === day ? { ...r, ranges: r.ranges.filter((_, j) => j !== i) } : r)) }));
  const submit = async (e) => {
    e.preventDefault();
    const problem = !form.name.trim() ? "Enter a name." : checkSchedule(form.rows, form.is_24x7);
    if (problem) { setError(problem); return; }
    setBusy(true);
    setError("");
    try {
      const holidays = form.holidays.split(/[\s,]+/).filter(Boolean);
      onCreated(await api("/admin/config/business-hours/", { method: "POST", body: { name: form.name.trim(), timezone: form.timezone.trim() || "UTC", is_24x7: form.is_24x7, schedule: form.is_24x7 ? {} : toSchedule(form.rows), holidays } }));
    } catch (err) { setError(configWriteError(err, "business hours")); setBusy(false); }
  };
  return (
    <Modal title="New business hours" onClose={busy ? () => {} : onClose} footer={(
      <><button type="button" className="btn" onClick={onClose} disabled={busy}>Cancel</button>
        <button type="submit" form="new-hours" className="btn primary" disabled={busy}>{busy ? "Creating…" : "Create"}</button></>
    )}>
      <form id="new-hours" className="stack" onSubmit={submit} noValidate>
        <label className="field"><span>Name</span><input className="input" value={form.name} maxLength={120} autoFocus onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
        <label className="field"><span>Time zone</span><input className="input" list="tz-options" value={form.timezone} onChange={(e) => setForm({ ...form, timezone: e.target.value })} /><TimeZoneOptions /></label>
        <label className="check"><input type="checkbox" checked={form.is_24x7} onChange={(e) => setForm({ ...form, is_24x7: e.target.checked })} /> <span>Open 24 hours, 7 days a week</span></label>
        {!form.is_24x7 && form.rows.map((r) => (
          <div key={r.day} className="field"><span>{DAYS[r.day]}</span>
            {r.ranges.map((x, i) => (
              <div key={i} className="row">
                <input className="input" aria-label={`${DAYS[r.day]} start`} value={x.start} onChange={setRange(r.day, i, "start")} size={6} />
                <span>to</span>
                <input className="input" aria-label={`${DAYS[r.day]} end`} value={x.end} onChange={setRange(r.day, i, "end")} size={6} />
                <button type="button" className="btn sm" onClick={() => dropRange(r.day, i)}>Remove</button>
              </div>
            ))}
            <div><button type="button" className="btn sm" onClick={() => addRange(r.day)}>Add period</button></div>
          </div>
        ))}
        <label className="field"><span>Holidays (YYYY-MM-DD, separated by spaces or commas)</span><textarea className="input" rows={2} value={form.holidays} onChange={(e) => setForm({ ...form, holidays: e.target.value })} /></label>
        {error && <div className="notice bad" role="alert">{error}</div>}
      </form>
    </Modal>
  );
}

/** Create an SLA policy with its targets (managers). */
export function NewPolicyDialog({ onClose, onCreated }) {
  const [form, setForm] = useState({ name: "", is_default: false, at_risk_percent: "75", business_hours_id: "", ticket_type_ids: [], targets: [] });
  const [opts, setOpts] = useState({ types: [], hours: [] });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    Promise.all([api("/admin/config/options/TicketType/"), api("/admin/config/options/BusinessHours/"), api("/admin/config/options/Priority/")]).then(([types, hours, prios]) => {
      setOpts({ types, hours });
      setForm((f) => ({ ...f, targets: prios.map((p) => ({ priority_id: p.id, label: p.label, first_response_minutes: "", resolution_minutes: "" })) }));
    }).catch(() => setError("The choices couldn't be loaded. Close this dialog and try again."));
  }, []);
  const setTarget = (i, k) => (e) => setForm((f) => ({ ...f, targets: f.targets.map((t, j) => (j === i ? { ...t, [k]: e.target.value } : t)) }));
  const toggleType = (id) => setForm((f) => ({ ...f, ticket_type_ids: f.ticket_type_ids.includes(id) ? f.ticket_type_ids.filter((x) => x !== id) : [...f.ticket_type_ids, id] }));
  const submit = async (e) => {
    e.preventDefault();
    const pct = Number(form.at_risk_percent);
    const problem = !form.name.trim() ? "Enter a policy name." : !Number.isInteger(pct) || pct < 1 || pct > 100 ? "The at-risk threshold must be a whole number from 1 to 100." : checkTargets(form.targets);
    if (problem) { setError(problem); return; }
    setBusy(true);
    setError("");
    try {
      onCreated(await api("/admin/config/sla-policies/", { method: "POST", body: {
        name: form.name.trim(), is_default: form.is_default, at_risk_percent: pct, business_hours_id: form.business_hours_id ? Number(form.business_hours_id) : null,
        ticket_type_ids: form.ticket_type_ids, targets: toTargets(form.targets) } }));
    } catch (err) { setError(configWriteError(err, "policy")); setBusy(false); }
  };
  return (
    <Modal title="New SLA policy" onClose={busy ? () => {} : onClose} footer={(
      <><button type="button" className="btn" onClick={onClose} disabled={busy}>Cancel</button>
        <button type="submit" form="new-policy" className="btn primary" disabled={busy}>{busy ? "Creating…" : "Create"}</button></>
    )}>
      <form id="new-policy" className="stack" onSubmit={submit} noValidate>
        <label className="field"><span>Name</span><input className="input" value={form.name} maxLength={120} autoFocus onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
        <label className="field"><span>Business hours</span>
          <select className="input" value={form.business_hours_id} onChange={(e) => setForm({ ...form, business_hours_id: e.target.value })}>
            <option value="">24x7 (none)</option>{opts.hours.map((h) => <option key={h.id} value={h.id}>{h.label}</option>)}</select></label>
        <label className="field"><span>At risk once this % of the window is used</span><input className="input" type="number" min={1} max={100} value={form.at_risk_percent} onChange={(e) => setForm({ ...form, at_risk_percent: e.target.value })} /></label>
        <fieldset className="field"><legend>Applies to ticket types (none selected = all)</legend>
          {opts.types.map((t) => <label key={t.id} className="check"><input type="checkbox" checked={form.ticket_type_ids.includes(t.id)} onChange={() => toggleType(t.id)} /> <span>{t.label}</span></label>)}</fieldset>
        <label className="check"><input type="checkbox" checked={form.is_default} onChange={(e) => setForm({ ...form, is_default: e.target.checked })} /> <span>Default policy for the selected types</span></label>
        <fieldset className="field"><legend>Targets in minutes (leave both empty to skip a priority)</legend>
          {form.targets.map((t, i) => (
            <div key={t.priority_id} className="row">
              <span style={{ minWidth: "9rem" }}>{t.label}</span>
              <input className="input" type="number" min={1} aria-label={`${t.label} first response minutes`} placeholder="First response" value={t.first_response_minutes} onChange={setTarget(i, "first_response_minutes")} />
              <input className="input" type="number" min={1} aria-label={`${t.label} resolution minutes`} placeholder="Resolution" value={t.resolution_minutes} onChange={setTarget(i, "resolution_minutes")} />
            </div>
          ))}</fieldset>
        {error && <div className="notice bad" role="alert">{error}</div>}
      </form>
    </Modal>
  );
}

/** Delete a policy or calendar nothing uses. The server refuses (409 with counts) while anything refers to it. */
export function DeleteSlaDialog({ kind, id, name, onClose, onDeleted }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const noun = kind === "policy" ? "SLA policy" : "business hours";
  const run = async () => {
    setBusy(true);
    setError("");
    try { await api(`/admin/config/${kind === "policy" ? "sla-policies" : "business-hours"}/${id}/`, { method: "DELETE" }); onDeleted(); }
    catch (e) { setError(configWriteError(e, noun)); setBusy(false); }
  };
  return (
    <ConfirmDialog title={`Delete ${noun}?`} confirmLabel="Delete" danger busy={busy} onConfirm={run} onClose={onClose}>
      <p>“{name}” will be deleted. This works only while nothing uses it.</p>
      {error && <div className="notice bad" role="alert">{error}</div>}
    </ConfirmDialog>
  );
}
