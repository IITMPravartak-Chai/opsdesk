import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import Modal from "../components/Modal.jsx";
import { STAFF_ROLES, STRATEGY_LABELS, canManageTeams, ifMatch, loadError, roleLabel, settingsRoute, writeError } from "./adminHelpers.js";
import { BackLink, ConfirmDialog, PageState, SettingsNav, WriteResult } from "./AdminCommon.jsx";

const FORBIDDEN = "Team management is available to managers and team leads only.";
const USAGE_LABELS = {
  tickets: "tickets (any state)", categories: "categories that route to it", catalog_items: "catalog items",
  services: "services it owns", configuration_items: "configuration items it owns",
};

/** Teams (U7-A2): members must be active staff; the server enforces it and explains refusals. */
/** A pick-one list of the users who hold a role (team lead or manager). The saved choice stays listed even if that user
 * has since been deactivated or given another role, so the form never silently drops it. */
function LeaderSelect({ label, value, current, choices = [], disabled, onChange, empty }) {
  const listed = choices.some((c) => c.id === current?.id);
  return (
    <label className="field"><span>{label}</span>
      <select className="input" value={value ?? ""} disabled={disabled} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
        <option value="">None</option>
        {current && !listed && <option value={current.id}>{current.name}{current.is_active ? "" : " (deactivated)"}</option>}
        {choices.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
      </select>
      {!disabled && choices.length === 0 && !current && <span className="hint">{empty}. Add one under Users.</span>}
    </label>
  );
}

export default function Teams() {
  const { user, loc } = useApp();
  const route = settingsRoute(loc.path);
  return (
    <div className="page admin-page">
      <div className="page-head">
        <div>
          <h1>Teams</h1>
          <p className="muted admin-sub">Groups of staff that tickets are routed and automatically assigned to.</p>
        </div>
      </div>
      <SettingsNav current="teams" />
      {!canManageTeams(user)
        ? <PageState state={{ status: "forbidden" }} forbiddenText={FORBIDDEN} />
        : route?.id ? <TeamDetail id={route.id} /> : <TeamList />}
    </div>
  );
}

function TeamList() {
  const { navigate } = useApp();
  const [state, setState] = useState({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [creating, setCreating] = useState(false);
  const [notice] = useState(() => {
    try { const m = sessionStorage.getItem("opsdesk.admin.notice"); sessionStorage.removeItem("opsdesk.admin.notice"); return m; } catch { return null; }
  });

  useEffect(() => {
    let alive = true;
    api("/admin/teams/").then((data) => alive && setState({ status: "ready", data })).catch((e) => alive && setState(loadError(e, "team list")));
    return () => { alive = false; };
  }, [attempt]);

  const rows = state.data?.results || [];
  return (
    <>
      <div className="row admin-toolbar">
        <button type="button" className="btn primary sm" onClick={() => setCreating(true)}>New team</button>
      </div>
      {notice && <div className="notice admin-ok" role="status">{notice}</div>}
      <PageState state={state} noun="teams" onRetry={() => setAttempt((n) => n + 1)} forbiddenText={FORBIDDEN} />
      {state.status === "ready" && rows.length === 0 && <div className="empty" role="status">No teams yet. Create one to start routing tickets.</div>}
      {rows.length > 0 && (
        <div className="admin-table-wrap">
          <table className="data">
            <caption className="sr-only">Teams</caption>
            <thead><tr><th scope="col">Team</th><th scope="col">Team lead</th><th scope="col">Manager</th><th scope="col">Members</th><th scope="col">Assignment</th><th scope="col">Open tickets</th></tr></thead>
            <tbody>{rows.map((t) => (
              <tr key={t.id}>
                <td>
                  <a href={`/settings/teams/${t.id}`} onClick={(e) => { e.preventDefault(); navigate(`/settings/teams/${t.id}`); }}>{t.name}</a>
                  {t.duplicate_name && <> <span className="chip warn">Duplicate name</span></>}
                </td>
                <td>{t.team_lead?.name || <span className="muted">—</span>}</td>
                <td>{t.manager?.name || <span className="muted">—</span>}</td>
                <td>{t.member_count}{t.ineligible_member_count > 0 && <> <span className="chip warn">{t.ineligible_member_count} not eligible</span></>}</td>
                <td>{STRATEGY_LABELS[t.assignment_strategy] || t.assignment_strategy}</td>
                <td>{t.open_ticket_count}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
      {creating && <CreateTeam onClose={() => setCreating(false)} onCreated={(t) => navigate(`/settings/teams/${t.id}`)} />}
    </>
  );
}

function CreateTeam({ onClose, onCreated }) {
  const [form, setForm] = useState({ name: "", email: "", assignment_strategy: "manual" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const submit = async (e) => {
    e.preventDefault();
    if (!form.name.trim()) { setError("Enter a team name."); return; }
    setBusy(true);
    setError("");
    try {
      const t = await api("/admin/teams/", { method: "POST", body: { ...form, name: form.name.trim() } });
      onCreated(t);
    } catch (err) {
      setError(writeError(err, "team"));
      setBusy(false);
    }
  };
  return (
    <Modal title="New team" onClose={busy ? () => {} : onClose} footer={(
      <>
        <button type="button" className="btn" onClick={onClose} disabled={busy}>Cancel</button>
        <button type="submit" form="new-team" className="btn primary" disabled={busy}>{busy ? "Creating…" : "Create team"}</button>
      </>
    )}>
      <form id="new-team" className="stack" onSubmit={submit}>
        <label className="field"><span>Name</span><input className="input" value={form.name} onChange={set("name")} maxLength={120} required autoFocus /></label>
        <label className="field"><span>Email (optional)</span><input className="input" type="email" value={form.email} onChange={set("email")} maxLength={254} /></label>
        <label className="field"><span>Automatic assignment</span>
          <select className="input" value={form.assignment_strategy} onChange={set("assignment_strategy")}>
            {Object.entries(STRATEGY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select></label>
        <p className="small muted">Add members after the team is created.</p>
        {error && <div className="notice bad" role="alert">{error}</div>}
      </form>
    </Modal>
  );
}

function TeamDetail({ id }) {
  const { navigate } = useApp();
  const [state, setState] = useState({ status: "loading" });
  const [options, setOptions] = useState(null);
  const [form, setForm] = useState(null);
  const [members, setMembers] = useState([]);
  const [adding, setAdding] = useState("");
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [attempt, setAttempt] = useState(0);

  const adopt = (data) => {
    setState({ status: "ready", data });
    setForm({ name: data.name, email: data.email, assignment_strategy: data.assignment_strategy, team_lead_id: data.team_lead?.id ?? null, manager_id: data.manager?.id ?? null });
    setMembers(data.members.map((m) => m.id));
  };
  const load = () => api(`/admin/teams/${id}/`).then(adopt).catch((e) => setState(loadError(e, "team")));
  useEffect(() => { setState({ status: "loading" }); setResult(null); load(); }, [id, attempt]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { api("/admin/user-options/").then(setOptions).catch(() => setOptions({ staff: [] })); }, []);

  if (state.status !== "ready") {
    return (<><BackLink to="/settings/teams" label="All teams" /><PageState state={state} noun="team" onRetry={() => setAttempt((n) => n + 1)} forbiddenText={FORBIDDEN} /></>);
  }
  const t = state.data;
  const sameIds = (a, b) => JSON.stringify([...a].sort((x, y) => x - y)) === JSON.stringify([...b].sort((x, y) => x - y));
  const detailsDirty = form.name !== t.name || form.email !== t.email || form.assignment_strategy !== t.assignment_strategy
    || form.team_lead_id !== (t.team_lead?.id ?? null) || form.manager_id !== (t.manager?.id ?? null);
  const membersDirty = !sameIds(members, t.members.map((m) => m.id));
  const byId = Object.fromEntries(t.members.map((m) => [m.id, m]));
  const staffById = Object.fromEntries((options?.staff || []).map((s) => [s.id, s]));
  const ineligibleKept = members.filter((mid) => byId[mid] && !STAFF_ROLES.includes(byId[mid].role)); // the server refuses to keep them
  // Only employees are added as members; team leads and managers are named in their own fields above.
  const candidates = (options?.staff || []).filter((s) => s.role === "employee" && !members.includes(s.id));

  const write = async (fn, okMessage) => {
    setBusy(true);
    setResult(null);
    try {
      const data = await fn();
      adopt(data);
      setResult({ ok: true, message: okMessage });
    } catch (e) {
      setResult({ ok: false, message: writeError(e, "team"), stale: e.status === 412 });
    } finally {
      setBusy(false);
    }
  };
  const saveDetails = (e) => {
    e.preventDefault();
    const body = {};
    if (form.name !== t.name) body.name = form.name;
    if (form.email !== t.email) body.email = form.email;
    if (form.assignment_strategy !== t.assignment_strategy) body.assignment_strategy = form.assignment_strategy;
    if (form.team_lead_id !== (t.team_lead?.id ?? null)) body.team_lead_id = form.team_lead_id;
    if (form.manager_id !== (t.manager?.id ?? null)) body.manager_id = form.manager_id;
    write(() => api(`/admin/teams/${id}/`, { method: "PATCH", body, headers: ifMatch(t.version) }), "Team details saved.");
  };
  const saveMembers = () => write(() => api(`/admin/teams/${id}/members/`, { method: "PUT", body: { user_ids: members }, headers: ifMatch(t.version) }), "Members saved.");
  const remove = async () => {
    setBusy(true);
    try {
      await api(`/admin/teams/${id}/`, { method: "DELETE", headers: ifMatch(t.version) });
      try { sessionStorage.setItem("opsdesk.admin.notice", `Team "${t.name}" was deleted.`); } catch { /* storage unavailable */ }
      navigate("/settings/teams");
    } catch (e) {
      setConfirmDelete(false);
      setBusy(false);
      setResult({ ok: false, message: writeError(e, "team"), stale: e.status === 412 });
    }
  };

  return (
    <>
      <BackLink to="/settings/teams" label="All teams" />
      {t.duplicate_name && <div className="callout" role="note">Another team in this workspace has the same name. Rename one of them so people can tell them apart; nothing is merged automatically.</div>}
      <WriteResult result={result} onReload={() => { setResult(null); load(); }} />

      <form className="card card-b admin-form" onSubmit={saveDetails} aria-labelledby="team-details-title">
        <h2 id="team-details-title" className="admin-h2">Details</h2>
        <div className="admin-grid">
          <label className="field"><span>Name</span><input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} maxLength={120} required /></label>
          <label className="field"><span>Email</span><input className="input" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} maxLength={254} /></label>
          <label className="field"><span>Automatic assignment</span>
            <select className="input" value={form.assignment_strategy} onChange={(e) => setForm({ ...form, assignment_strategy: e.target.value })}>
              {Object.entries(STRATEGY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <span className="hint">Picks only active, available staff members.</span></label>
          <LeaderSelect label="Team lead" value={form.team_lead_id} current={t.team_lead} choices={options?.team_leads} disabled={!options}
            onChange={(v) => setForm({ ...form, team_lead_id: v })} empty="No team lead users yet" />
          <LeaderSelect label="Manager" value={form.manager_id} current={t.manager} choices={options?.team_managers} disabled={!options}
            onChange={(v) => setForm({ ...form, manager_id: v })} empty="No manager users yet" />
        </div>
        <div className="row admin-actions">
          <button type="submit" className="btn primary sm" disabled={!detailsDirty || busy}>Save details</button>
        </div>
      </form>

      <section className="card card-b admin-form" aria-labelledby="team-members-title">
        <h2 id="team-members-title" className="admin-h2">Members</h2>
        {t.members.some((m) => !m.eligible) && (
          <div className="callout" role="note">
            Some members are never assigned work. Accounts that aren't staff must be removed before member changes can be saved; deactivated staff may stay.
          </div>
        )}
        {members.length === 0 ? <p className="small muted" role="status">No members. Tickets routed here won't be assigned automatically.</p> : (
          <ul className="admin-members">
            {members.map((mid) => {
              const m = byId[mid] || { id: mid, name: staffById[mid]?.name || `User ${mid}`, role: staffById[mid]?.role, eligible: true, is_active: true, email: staffById[mid]?.email };
              return (
                <li key={mid}>
                  <div>
                    <div>{m.name} <span className="small muted">{roleLabel(m.role)}</span></div>
                    <div className="small muted admin-email">{m.email}</div>
                  </div>
                  <div className="row admin-member-actions">
                    {!byId[mid] && <span className="chip">To be added</span>}
                    {byId[mid] && !m.eligible && <span className="chip warn" title={m.ineligible_reason}>{m.ineligible_reason}</span>}
                    {byId[mid] && m.eligible && !m.is_available && <span className="chip">Unavailable</span>}
                    <button type="button" className="btn ghost sm" onClick={() => setMembers(members.filter((x) => x !== mid))} aria-label={`Remove ${m.name}`}>Remove</button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        <div className="row admin-add">
          <label className="field"><span>Add a member</span>
            <select className="input" value={adding} onChange={(e) => setAdding(e.target.value)} disabled={!options}>
              <option value="">Choose an employee…</option>
              {candidates.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select></label>
          <button type="button" className="btn sm" disabled={!adding} onClick={() => { setMembers([...members, Number(adding)]); setAdding(""); }}>Add</button>
        </div>
        {ineligibleKept.length > 0 && membersDirty && <div className="notice bad" role="alert">Remove the members marked as not staff before saving.</div>}
        <div className="row admin-actions">
          <button type="button" className="btn primary sm" disabled={!membersDirty || busy || ineligibleKept.length > 0} onClick={saveMembers}>Save members</button>
          {membersDirty && <button type="button" className="btn ghost sm" disabled={busy} onClick={() => setMembers(t.members.map((m) => m.id))}>Discard</button>}
        </div>
      </section>

      <section className="card card-b admin-danger" aria-labelledby="team-delete-title">
        <h2 id="team-delete-title" className="admin-h2">Delete team</h2>
        {t.can_delete ? (
          <>
            <p className="small">Nothing refers to this team, so it can be deleted. Its members keep their accounts.</p>
            <button type="button" className="btn danger sm" onClick={() => setConfirmDelete(true)} disabled={busy}>Delete team</button>
          </>
        ) : (
          <>
            <p className="small">This team is in use, so it can't be deleted. Teams can't be archived yet.</p>
            <ul className="small">{Object.entries(t.usage).filter(([k, n]) => n > 0 && k !== "open_tickets").map(([k, n]) => <li key={k}>{n} {USAGE_LABELS[k] || k}</li>)}</ul>
          </>
        )}
      </section>
      {confirmDelete && (
        <ConfirmDialog title="Delete team?" confirmLabel="Delete" danger busy={busy} onConfirm={remove} onClose={() => setConfirmDelete(false)}>
          <p>"{t.name}" will be removed. This can't be undone; the deletion is recorded in the audit log.</p>
        </ConfirmDialog>
      )}
    </>
  );
}
