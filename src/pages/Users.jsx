import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { isTopRole } from "../roles.js";
import { lastPage, rangeLabel } from "./auditLogHelpers.js";
import {
  ASSIGNABLE_ROLES, ROLE_OPTIONS, buildUserQuery, isSuperAdmin, canManageUsers, ifMatch, loadError, parseSkills, roleLabel, settingsRoute, userChanges,
  generatePassword, newUserPayload, newUserProblem, userDraft, writeError,
} from "./adminHelpers.js";
import Modal from "../components/Modal.jsx";
import { BackLink, ConfirmDialog, PageState, SettingsNav, WriteResult } from "./AdminCommon.jsx";

const FORBIDDEN = "User management is available to managers and team leads only.";

/** Users (U7-A2). The server decides every permission; this page shows only what it says the caller may change. */
export default function Users() {
  const { user, loc } = useApp();
  const route = settingsRoute(loc.path);
  const allowed = canManageUsers(user);
  return (
    <div className="page admin-page">
      <div className="page-head">
        <div>
          <h1>Users</h1>
          <p className="muted admin-sub">People in this workspace, their roles and whether they can sign in.</p>
        </div>
      </div>
      <SettingsNav current="users" />
      {!allowed
        ? <PageState state={{ status: "forbidden" }} forbiddenText={FORBIDDEN} />
        : route?.id ? <UserDetail id={route.id} /> : <UserList />}
    </div>
  );
}

function UserList() {
  const { user, navigate } = useApp();
  const [draft, setDraft] = useState({ q: "", role: "", status: "" });
  const [filters, setFilters] = useState(draft);
  const [page, setPage] = useState(1);
  const [state, setState] = useState({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    let alive = true;
    setState((s) => ({ ...s, status: s.data ? "refreshing" : "loading" }));
    api(`/admin/users/?${buildUserQuery({ ...filters, page })}`)
      .then((data) => alive && setState({ status: "ready", data }))
      .catch((e) => alive && setState(loadError(e, "user list")));
    return () => { alive = false; };
  }, [filters, page, attempt]);

  const set = (k) => (e) => setDraft((d) => ({ ...d, [k]: e.target.value }));
  const apply = (e) => { e.preventDefault(); setPage(1); setFilters(draft); };
  const filtered = Boolean(filters.q || filters.role || filters.status);
  const rows = state.data?.results || [];

  return (
    <>
      <div className="row" style={{ justifyContent: "flex-end", marginBottom: 8 }}>
        <button type="button" className="btn primary sm" onClick={() => setAdding(true)}>Add user</button>
      </div>
      {adding && <AddUserDialog onClose={() => setAdding(false)} onCreated={(u) => { setAdding(false); navigate(`/settings/users/${u.id}`); }} />}
      <form className="card card-b admin-filters" onSubmit={apply} aria-label="Filter users">
        <label className="field"><span>Search</span>
          <input className="input" value={draft.q} onChange={set("q")} placeholder="Name or email" maxLength={100} /></label>
        <label className="field"><span>Role</span>
          <select className="input" value={draft.role} onChange={set("role")}>
            <option value="">All roles</option>
            {ROLE_OPTIONS.map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}
          </select></label>
        <label className="field"><span>Status</span>
          <select className="input" value={draft.status} onChange={set("status")}>
            <option value="">Active and deactivated</option><option value="active">Active</option><option value="inactive">Deactivated</option>
          </select></label>
        <div className="row admin-filter-actions">
          <button type="submit" className="btn primary sm">Apply</button>
          {filtered && <button type="button" className="btn ghost sm" onClick={() => { const e = { q: "", role: "", status: "" }; setDraft(e); setFilters(e); setPage(1); }}>Clear</button>}
        </div>
      </form>

      <PageState state={state} noun="users" onRetry={() => setAttempt((n) => n + 1)} forbiddenText={FORBIDDEN} />
      {state.data && rows.length === 0 && (
        <div className="empty" role="status">{filtered ? "No users match these filters." : "There are no users in this workspace yet."}</div>
      )}
      {state.data && rows.length > 0 && (
        <>
          <div className="admin-table-wrap" aria-busy={state.status === "refreshing"}>
            <table className="data">
              <caption className="sr-only">Users</caption>
              <thead><tr><th scope="col">Name</th><th scope="col">Role</th><th scope="col">Status</th><th scope="col">Teams</th></tr></thead>
              <tbody>{rows.map((u) => (
                <tr key={u.id} className="row-link" style={{ cursor: "pointer" }}
                  onClick={(e) => { if (!e.target.closest("a, button") && !window.getSelection()?.toString()) navigate(`/settings/users/${u.id}`); }}>
                  <td>
                    <a href={`/settings/users/${u.id}`} onClick={(e) => { e.preventDefault(); navigate(`/settings/users/${u.id}`); }}>{u.name}</a>
                    <div className="small muted admin-email">{u.email}</div>
                  </td>
                  <td>{roleLabel(u.role)}</td>
                  <td>{u.is_active ? <span className="chip ok">Active</span> : <span className="chip bad">Deactivated</span>}</td>
                  <td>{u.teams.length ? u.teams.map((t) => t.name).join(", ") : <span className="muted">—</span>}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
          <nav className="row audit-pager" aria-label="User list pages">
            <span className="small muted" role="status">{rangeLabel(page, state.data.count).replace("events", "users")}</span>
            <button type="button" className="btn sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Previous</button>
            <span className="small">Page {page} of {lastPage(state.data.count)}</span>
            <button type="button" className="btn sm" disabled={page >= lastPage(state.data.count)} onClick={() => setPage((p) => p + 1)}>Next</button>
          </nav>
        </>
      )}
    </>
  );
}

/** Add user (super admin): the account is created with an initial password the admin passes on; there is no invitation email. */
function AddUserDialog({ onClose, onCreated }) {
  const [options, setOptions] = useState(null);
  const [teams, setTeams] = useState(null);
  const [form, setForm] = useState({ email: "", first_name: "", last_name: "", role: "employee", account_id: "", team_id: "", password: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [created, setCreated] = useState(null);

  useEffect(() => { api("/admin/user-options/").then(setOptions).catch(() => setOptions({ accounts: [] })); }, []);
  useEffect(() => { api("/admin/teams/").then((r) => setTeams(r.results || [])).catch(() => setTeams([])); }, []);
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const submit = async (e) => {
    e.preventDefault();
    const problem = newUserProblem(form);
    if (problem || busy) { setError(problem); return; }
    setBusy(true);
    setError("");
    try {
      setCreated(await api("/admin/users/", { method: "POST", body: newUserPayload(form) }));
    } catch (err) {
      setError(writeError(err, "user"));
    } finally {
      setBusy(false);
    }
  };

  if (created) {
    return (
      <Modal title="User added" onClose={() => onCreated(created)} footer={<button type="button" className="btn primary" onClick={() => onCreated(created)}>Open user</button>}>
        <p><strong>{created.name}</strong> ({roleLabel(created.role)}) can now sign in with <code>{created.email}</code>.</p>
        <p className="small muted">Share the password you set through a safe channel. It isn't shown again.</p>
      </Modal>
    );
  }
  return (
    <Modal title="Add user" onClose={busy ? () => {} : onClose} footer={(
      <>
        <button type="button" className="btn" onClick={onClose} disabled={busy}>Cancel</button>
        <button type="submit" form="add-user-form" className="btn primary" disabled={busy}>{busy ? "Adding…" : "Add user"}</button>
      </>
    )}>
      <form id="add-user-form" className="stack" onSubmit={submit} noValidate>
        <label className="field"><span>Email</span>
          <input className="input" type="email" value={form.email} onChange={set("email")} maxLength={254} autoComplete="off" autoFocus required /></label>
        <div className="row" style={{ gap: 8 }}>
          <label className="field" style={{ flex: 1 }}><span>First name</span>
            <input className="input" value={form.first_name} onChange={set("first_name")} maxLength={150} required /></label>
          <label className="field" style={{ flex: 1 }}><span>Last name</span>
            <input className="input" value={form.last_name} onChange={set("last_name")} maxLength={150} /></label>
        </div>
        <label className="field"><span>Role</span>
          <select className="input" value={form.role} onChange={set("role")}>
            {ASSIGNABLE_ROLES.map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}
          </select></label>
        {form.role === "employee" && (
          <label className="field"><span>Team</span>
            <select className="input" value={form.team_id} onChange={set("team_id")} disabled={!teams} required>
              <option value="">Choose a team</option>
              {(teams || []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
            {teams && teams.length === 0 && <span className="hint">There are no teams yet. Create one under Teams first.</span>}
          </label>
        )}
        {form.role === "customer" && (
          <label className="field"><span>Company</span>
            <select className="input" value={form.account_id} onChange={set("account_id")} disabled={!options} required>
              <option value="">Choose a company</option>
              {(options?.accounts || []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select></label>
        )}
        <label className="field"><span>Initial password</span>
          <div className="row" style={{ gap: 8 }}>
            <input className="input" style={{ flex: 1 }} value={form.password} onChange={set("password")} autoComplete="new-password" spellCheck={false} />
            <button type="button" className="btn sm" onClick={() => setForm((f) => ({ ...f, password: generatePassword() }))}>Generate</button>
          </div>
          <span className="hint">At least 12 characters, not a common password and not close to the name or email.</span>
        </label>
        {error && <div className="notice bad" role="alert">{error}</div>}
      </form>
    </Modal>
  );
}

function UserDetail({ id }) {
  const { user } = useApp();
  const [state, setState] = useState({ status: "loading" });
  const [options, setOptions] = useState(null);
  const [draft, setDraft] = useState(null);
  const [skillsText, setSkillsText] = useState("");
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(null); // "role" | "deactivate" | "reactivate"
  const [attempt, setAttempt] = useState(0);

  const load = (keepResult = false) => {
    setState((s) => ({ ...s, status: s.data ? "refreshing" : "loading" }));
    if (!keepResult) setResult(null);
    return api(`/admin/users/${id}/`).then((data) => {
      setState({ status: "ready", data });
      setDraft(userDraft(data));
      setSkillsText((data.skills || []).join(", "));
    }).catch((e) => setState(loadError(e, "user")));
  };
  useEffect(() => { load(); }, [id, attempt]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { api("/admin/user-options/").then(setOptions).catch(() => setOptions({ departments: [], accounts: [], managers: [] })); }, []);

  if (state.status !== "ready" && state.status !== "refreshing") {
    return (<><BackLink to="/settings/users" label="All users" /><PageState state={state} noun="user" onRetry={() => setAttempt((n) => n + 1)} forbiddenText={FORBIDDEN} /></>);
  }
  const u = state.data;
  const perms = u.permissions;
  const can = (f) => perms.editable_fields.includes(f);
  const current = { ...draft, skills: parseSkills(skillsText) };
  const changes = userChanges(userDraft(u), current, perms.editable_fields);
  const dirty = Object.keys(changes).length > 0;
  const set = (k, fn = (e) => e.target.value) => (e) => setDraft((d) => ({ ...d, [k]: fn(e) }));
  const idOrNull = (e) => (e.target.value ? Number(e.target.value) : null);
  // Employees report to a team lead and team leads to a manager; managers and customers report to nobody.
  const REPORTS_TO = { employee: "team_lead", team_lead: "manager" };
  const reportChoices = (options?.managers || []).filter((m) => m.id !== u.id && m.role === REPORTS_TO[draft.role]);
  const changeRole = (e) => {
    const role = e.target.value;
    setDraft((d) => (REPORTS_TO[role] === REPORTS_TO[d.role] ? { ...d, role } : { ...d, role, manager_id: null }));
  };

  const save = async () => {
    setBusy(true);
    setResult(null);
    try {
      const data = await api(`/admin/users/${id}/`, { method: "PATCH", body: changes, headers: ifMatch(u.version) });
      setState({ status: "ready", data });
      setDraft(userDraft(data));
      setSkillsText((data.skills || []).join(", "));
      setResult({ ok: true, message: "Changes saved." });
    } catch (e) {
      setResult({ ok: false, message: writeError(e, "user"), stale: e.status === 412 });
    } finally {
      setBusy(false);
      setConfirm(null);
    }
  };
  const setActive = async (active) => {
    setBusy(true);
    setResult(null);
    try {
      const data = await api(`/admin/users/${id}/${active ? "reactivate" : "deactivate"}/`, { method: "POST", headers: ifMatch(u.version) });
      setState({ status: "ready", data });
      setDraft(userDraft(data));
      setResult({ ok: true, message: active ? `${data.name} can sign in again. Automatic assignment stays off until you turn availability on.`
        : `${data.name} is deactivated and can no longer sign in.${data.open_assigned_count ? ` ${data.open_assigned_count} open ticket${data.open_assigned_count === 1 ? " is" : "s are"} still assigned to them; reassign ${data.open_assigned_count === 1 ? "it" : "them"} from the queue.` : ""}` });
    } catch (e) {
      setResult({ ok: false, message: writeError(e, "user"), stale: e.status === 412 });
    } finally {
      setBusy(false);
      setConfirm(null);
    }
  };
  const onSubmit = (e) => {
    e.preventDefault();
    if (!dirty || busy) return;
    if ("role" in changes) setConfirm("role");
    else save();
  };
  const readOnly = perms.editable_fields.length === 0;

  return (
    <>
      <BackLink to="/settings/users" label="All users" />
      <section className="card card-b admin-summary" aria-labelledby="user-title">
        <div className="admin-summary-head">
          <h2 id="user-title">{u.name}</h2>
          {u.is_active ? <span className="chip ok">Active</span> : <span className="chip bad">Deactivated</span>}
        </div>
        <dl className="admin-facts">
          <div><dt>Email</dt><dd>{u.email}</dd></div>
          <div><dt>Role</dt><dd>{roleLabel(u.role)}</dd></div>
          <div><dt>Two-step sign-in</dt><dd>{u.mfa_enabled ? "On" : "Off"}</dd></div>
          {u.role === "manager"
            ? <div><dt>Teams</dt><dd>{u.team_lead_count ?? 0}</dd></div>
            : <div><dt>Team</dt><dd>{u.teams.length ? u.teams.map((t) => t.name).join(", ") : "None"}</dd></div>}
        </dl>
        {isTopRole(user) && !isSuperAdmin(u) && <AccountSecurity target={u} onChanged={() => load(true)} />}
      </section>

      <WriteResult result={result} onReload={() => load(true).then(() => setResult(null))} />

      <form className="card card-b admin-form" onSubmit={onSubmit} aria-labelledby="user-edit-title">
        <h2 id="user-edit-title" className="admin-h2">Details</h2>
        {readOnly && isSuperAdmin(u) && <div className="notice" role="note">The super admin account is managed by the site operator only. No role can edit, deactivate or reset it here.</div>}
        {readOnly && !isSuperAdmin(u) && <div className="notice" role="note">You can view this user but not change them{user.role === "team_lead" ? ": team leads can change only employees and customers" : ""}.</div>}
        <div className="admin-grid">
          <label className="field"><span>First name</span><input className="input" value={draft.first_name} onChange={set("first_name")} disabled={!can("first_name")} maxLength={150} /></label>
          <label className="field"><span>Last name</span><input className="input" value={draft.last_name} onChange={set("last_name")} disabled={!can("last_name")} maxLength={150} /></label>
          <label className="field"><span>Phone</span><input className="input" type="tel" value={draft.phone} onChange={set("phone")} disabled={!can("phone")} maxLength={32} placeholder="+91 98450 12345" /></label>
          {((can("account_id") && draft.role === "customer") || u.account) && (
            <label className="field"><span>Account</span>
              <select className="input" value={draft.account_id ?? ""} onChange={set("account_id", idOrNull)} disabled={!can("account_id") || !options}>
                <option value="">None</option>
                {(options?.accounts || []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                {u.account && !(options?.accounts || []).some((a) => a.id === u.account.id) && <option value={u.account.id}>{u.account.name}</option>}
              </select></label>
          )}
          {REPORTS_TO[draft.role] && (
            <label className="field"><span>Reports to</span>
              <select className="input" value={draft.manager_id ?? ""} onChange={set("manager_id", idOrNull)} disabled={!can("manager_id") || !options}>
                <option value="">Nobody</option>
                {reportChoices.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
                {u.manager && !reportChoices.some((m) => m.id === u.manager.id) && <option value={u.manager.id}>{u.manager.name}</option>}
              </select>
              <span className="hint">{draft.role === "employee" ? "An employee reports to a team lead." : "A team lead reports to a manager."}</span></label>
          )}
          <label className="field"><span>Role</span>
            <select className="input" value={draft.role} onChange={changeRole} disabled={!perms.can_change_role}
              aria-describedby="role-hint">
              {(isSuperAdmin(u) ? ROLE_OPTIONS : ASSIGNABLE_ROLES).map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}
            </select>
            <span className="hint" id="role-hint">{perms.can_change_role ? "Managers and team leads get staff access to Configuration." : u.id === user.id ? "You can't change your own role." : "Only managers can change roles."}</span>
          </label>
          <label className="field admin-wide"><span>Skills</span>
            <input className="input" value={skillsText} onChange={(e) => setSkillsText(e.target.value)} disabled={!can("skills")} placeholder="Separate with commas" />
          </label>
          <label className="toggle admin-wide">
            <input type="checkbox" checked={draft.is_available} onChange={set("is_available", (e) => e.target.checked)} disabled={!can("is_available")} />
            Available for automatic assignment
          </label>
        </div>
        {!readOnly && (
          <div className="row admin-actions">
            <button type="submit" className="btn primary sm" disabled={!dirty || busy}>{busy && !confirm ? "Saving…" : "Save changes"}</button>
            {dirty && <button type="button" className="btn ghost sm" disabled={busy} onClick={() => { setDraft(userDraft(u)); setSkillsText((u.skills || []).join(", ")); }}>Discard</button>}
          </div>
        )}
      </form>

      {perms.can_deactivate && (
        <section className="card card-b admin-danger" aria-labelledby="access-title">
          <h2 id="access-title" className="admin-h2">Sign-in access</h2>
          {u.is_active ? (
            <>
              <p className="small">Deactivating stops sign-in and API access immediately and removes them from automatic assignment. Their history stays; nothing is deleted.</p>
              <button type="button" className="btn danger sm" onClick={() => setConfirm("deactivate")} disabled={busy}>Deactivate user</button>
            </>
          ) : (
            <>
              <p className="small">This user can't sign in. Reactivating restores access with their current role.</p>
              <button type="button" className="btn sm" onClick={() => setConfirm("reactivate")} disabled={busy}>Reactivate user</button>
            </>
          )}
        </section>
      )}

      {confirm === "role" && (
        <ConfirmDialog title="Change role?" confirmLabel="Change role" busy={busy} onConfirm={save} onClose={() => setConfirm(null)}>
          <p>{u.name} will change from <strong>{roleLabel(u.role)}</strong> to <strong>{roleLabel(changes.role)}</strong>. Their access changes on their next request.</p>
        </ConfirmDialog>
      )}
      {confirm === "deactivate" && (
        <ConfirmDialog title="Deactivate user?" confirmLabel="Deactivate" danger busy={busy} onConfirm={() => setActive(false)} onClose={() => setConfirm(null)}>
          <p>{u.name} won't be able to sign in, and existing sessions stop working on their next request.</p>
          {u.open_assigned_count > 0 && <p>{u.open_assigned_count} open ticket{u.open_assigned_count === 1 ? " is" : "s are"} assigned to them and will stay assigned until someone reassigns {u.open_assigned_count === 1 ? "it" : "them"}.</p>}
        </ConfirmDialog>
      )}
      {confirm === "reactivate" && (
        <ConfirmDialog title="Reactivate user?" confirmLabel="Reactivate" busy={busy} onConfirm={() => setActive(true)} onClose={() => setConfirm(null)}>
          <p>{u.name} will be able to sign in again as {roleLabel(u.role).toLowerCase()}.</p>
        </ConfirmDialog>
      )}
    </>
  );
}

export function TimeZoneOptions() {
  let zones = [];
  try { zones = Intl.supportedValuesOf ? Intl.supportedValuesOf("timeZone") : []; } catch { zones = []; }
  return <datalist id="tz-options">{zones.map((z) => <option key={z} value={z} />)}</datalist>;
}


/** Manager-only account recovery: set a new password, or turn off two-step sign-in when a device is lost.
 * Both are recorded in the audit log (never the password). */
function AccountSecurity({ target, onChanged }) {
  const [dialog, setDialog] = useState(null); // "password" | "mfa"
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const close = () => { if (!busy) { setDialog(null); setPassword(""); setError(""); } };
  const run = async (fn, message) => {
    setBusy(true);
    setError("");
    try { await fn(); setDone(message); setDialog(null); setPassword(""); onChanged?.(); } catch (e) { setError(e?.data?.errors?.join?.(" ") || e?.message || "Couldn't make this change."); }
    setBusy(false);
  };
  return (
    <div className="admin-security">
      <div className="row">
        <button type="button" className="btn sm" onClick={() => { setDone(""); setDialog("password"); }}>Set password…</button>
        {target.mfa_enabled && <button type="button" className="btn sm" onClick={() => { setDone(""); setDialog("mfa"); }}>Turn off two-step sign-in…</button>}
      </div>
      {done && <div className="notice admin-ok" role="status">{done}</div>}
      {dialog === "password" && (
        <ConfirmDialog title="Set a new password" confirmLabel="Set password" busy={busy} onClose={close}
          onConfirm={() => run(() => api(`/admin/users/${target.id}/password/`, { method: "POST", body: { password } }), "Password updated.")}>
          <label className="field"><span>New password for {target.name}</span>
            <input className="input" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} minLength={12} autoFocus /></label>
          <p className="small muted">At least 12 characters, not common, not similar to their name or email. Share it securely; they should change it after signing in.</p>
          {error && <div className="notice bad" role="alert">{error}</div>}
        </ConfirmDialog>
      )}
      {dialog === "mfa" && (
        <ConfirmDialog title="Turn off two-step sign-in?" confirmLabel="Turn off" danger busy={busy} onClose={close}
          onConfirm={() => run(() => api(`/admin/users/${target.id}/mfa/disable/`, { method: "POST" }), "Two-step sign-in turned off.")}>
          <p>{target.name} will sign in with their password only until they set up two-step sign-in again.</p>
          {error && <div className="notice bad" role="alert">{error}</div>}
        </ConfirmDialog>
      )}
    </div>
  );
}
