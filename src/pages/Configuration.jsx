import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { FEATURES } from "../features.js";
import { isPrivilegedRole, isTopRole } from "../roles.js";
import Modal from "../components/Modal.jsx";
import { BackLink, ConfirmDialog, PageState, SettingsNav } from "./AdminCommon.jsx";
import { loadError } from "./adminHelpers.js";
import { maskInboundUrl, routingRows } from "./opsHelpers.js";
import {
  CHILD_RESOURCES, SECTIONS, apiPath, buildPayload, cellText, configPath, configRoute, configWriteError, fieldErrors, visibleConfigGroups,
  initialValues, matrixCells, matrixLookup,
} from "./configHelpers.js";

const FORBIDDEN = "Configuration is available to managers and team leads only.";

/** Configuration console: workflows, ticket types, priorities, rules, accounts, channels, CMDB and more.
 * The server checks every change (role, tenant, validation) and records it in the audit log. */
export default function Configuration() {
  const { user, loc } = useApp();
  const route = configRoute(loc.path);
  const allowed = isPrivilegedRole(user);
  const [schema, setSchema] = useState({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!allowed) return;
    api("/admin/config/").then((data) => setSchema({ status: "ready", data })).catch((e) => setSchema(loadError(e, "configuration")));
  }, [allowed, attempt]);

  let body;
  if (!allowed) body = <PageState state={{ status: "forbidden" }} forbiddenText={FORBIDDEN} />;
  else if (schema.status !== "ready") body = <PageState state={schema} noun="configuration" onRetry={() => setAttempt((n) => n + 1)} forbiddenText={FORBIDDEN} />;
  else if (!route) body = <PageState state={{ status: "missing", message: "This configuration page doesn't exist." }} />;
  else if (!route.section) body = <Home />;
  else if (route.section === "priority-matrix") body = <PriorityMatrix />;
  else if (route.section === "inbound-messages") body = <InboundMessages />;
  else if (route.section === "channels" && route.id) body = <ChannelDetail schema={schema.data} id={route.id} />;
  else if (route.id) body = <Detail schema={schema.data} section={route.section} id={route.id} />;
  else body = (<><BackLink to={configPath()} label="All configuration" />
    <ResourceTable schema={schema.data} slug={route.section} noun={SECTIONS[route.section].noun} columns={SECTIONS[route.section].columns} /></>);

  const meta = route?.section ? SECTIONS[route.section] : null;
  return (
    <div className="page admin-page">
      <div className="page-head">
        <div>
          <h1>{meta ? meta.label : "Configuration"}</h1>
          <p className="muted admin-sub">{meta ? meta.blurb : "How your service desk behaves. Every change is checked and recorded in the audit log."}</p>
        </div>
      </div>
      <SettingsNav current="config" />
      {route?.section === "channels" && <OauthReturnNotice />}
      {body}
    </div>
  );
}

function Home() {
  const { navigate } = useApp();
  return (
    <div className="stack">
      {visibleConfigGroups(FEATURES).map((g) => (
        <section key={g.key} aria-labelledby={`cfg-${g.key}`}>
          <h2 className="admin-h2" id={`cfg-${g.key}`}>{g.label}</h2>
          <div className="catalog-grid">
            {g.sections.map((s) => (
              <a key={s} className="catalog-card" href={configPath(s)} onClick={(e) => { e.preventDefault(); navigate(configPath(s)); }}>
                <div className="catalog-card-b"><div className="catalog-card-title">{SECTIONS[s].label}</div><div className="catalog-card-desc">{SECTIONS[s].blurb}</div></div>
              </a>
            ))}
          </div>
        </section>
      ))}
      <section aria-labelledby="cfg-other">
        <h2 className="admin-h2" id="cfg-other">Elsewhere in Administration</h2>
        <p className="small muted">Users and teams, SLA policies and business hours, and the audit log have their own tabs above.</p>
      </section>
    </div>
  );
}

// ------------------------------------------------------------------------------------------ list + edit
function ResourceTable({ schema, slug, noun, columns, parent }) {
  const { user, navigate } = useApp();
  const spec = schema.resources[slug];
  const path = apiPath(slug, parent);
  const [state, setState] = useState({ status: "loading" });
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [attempt, setAttempt] = useState(0);
  const [editing, setEditing] = useState(null); // null | "new" | row
  const [deleting, setDeleting] = useState(null);
  const [notice, setNotice] = useState("");
  const meta = SECTIONS[slug];

  useEffect(() => {
    let alive = true;
    const qs = new URLSearchParams({ page: String(page), page_size: "100" });
    if (q.trim()) qs.set("q", q.trim());
    api(`${path}?${qs}`).then((data) => alive && setState({ status: "ready", data })).catch((e) => alive && setState(loadError(e, noun)));
    return () => { alive = false; };
  }, [path, q, page, attempt, noun]);

  const reload = () => setAttempt((n) => n + 1);
  const rows = state.data?.results || [];
  const pages = state.data ? Math.max(1, Math.ceil(state.data.count / state.data.page_size)) : 1;

  return (
    <>
      <div className="row admin-toolbar">
        {spec.search && <input className="input" type="search" placeholder={`Search ${meta?.label?.toLowerCase() || noun + "s"}`} aria-label="Search" value={q}
          onChange={(e) => { setQ(e.target.value); setPage(1); }} />}
        {spec.can_create && <button type="button" className="btn primary sm" onClick={() => setEditing("new")}>New {noun}</button>}
      </div>
      {notice && <div className="notice admin-ok" role="status">{notice}</div>}
      <PageState state={state} noun={`${noun}s`} onRetry={reload} forbiddenText={FORBIDDEN} />
      {state.status === "ready" && rows.length === 0 && <div className="empty" role="status">{q ? "Nothing matches your search." : `No ${noun}s yet.`}</div>}
      {rows.length > 0 && (
        <div className="admin-table-wrap">
          <table className="data">
            <caption className="sr-only">{meta?.label || noun}</caption>
            <thead><tr>{columns.map(([k, label]) => <th key={k} scope="col">{label}</th>)}<th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.id}>
                {columns.map(([k], i) => (
                  <td key={k}>{i === 0
                    ? (meta?.detail && !parent
                      ? <a href={configPath(slug, r.id)} onClick={(e) => { e.preventDefault(); navigate(configPath(slug, r.id)); }}>{cellText(r, k)}</a>
                      : <button type="button" className="linklike" onClick={() => setEditing(r)}>{cellText(r, k)}</button>)
                    : cellText(r, k)}</td>
                ))}
                <td className="admin-actions">
                  {meta?.detail && !parent && <button type="button" className="btn sm" onClick={() => navigate(configPath(slug, r.id))}>View</button>}
                  <button type="button" className="btn sm" onClick={() => setEditing(r)}>Edit</button>
                  {spec.can_delete && <button type="button" className="btn sm danger" onClick={() => setDeleting(r)}>Delete</button>}
                </td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
      {pages > 1 && (
        <div className="row admin-pager">
          <button type="button" className="btn sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Previous</button>
          <span className="small muted">Page {page} of {pages}</span>
          <button type="button" className="btn sm" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>Next</button>
        </div>
      )}
      {editing && (
        <ResourceForm schema={schema} slug={slug} noun={noun} parent={parent} row={editing === "new" ? null : editing} isAdmin={isTopRole(user)}
          onClose={() => setEditing(null)} onSaved={(saved, created) => { setEditing(null); setNotice(`${noun[0].toUpperCase()}${noun.slice(1)} ${created ? "created" : "saved"}.`); reload(); if (created && meta?.detail && !parent) navigate(configPath(slug, saved.id)); }} />
      )}
      {deleting && <DeleteDialog path={`${path}${deleting.id}/`} noun={noun} label={deleting[columns[0][0]] ?? deleting.id} onClose={() => setDeleting(null)}
        onDeleted={() => { setDeleting(null); setNotice(`${noun[0].toUpperCase()}${noun.slice(1)} deleted.`); reload(); }} />}
    </>
  );
}

function DeleteDialog({ path, noun, label, onClose, onDeleted }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const run = async () => {
    setBusy(true);
    setError("");
    try { await api(path, { method: "DELETE" }); onDeleted(); } catch (e) { setError(configWriteError(e, noun)); setBusy(false); }
  };
  return (
    <ConfirmDialog title={`Delete ${noun}?`} confirmLabel="Delete" danger busy={busy} onConfirm={run} onClose={onClose}>
      <p>“{String(label)}” will be deleted. This can't be undone.</p>
      {error && <div className="notice bad" role="alert">{error}</div>}
    </ConfirmDialog>
  );
}

// ------------------------------------------------------------------------------------------ form
function ResourceForm({ schema, slug, noun, parent, row, isAdmin, onClose, onSaved }) {
  const spec = schema.resources[slug];
  const fields = spec.fields;
  const [values, setValues] = useState(() => initialValues(fields, row));
  const [errors, setErrors] = useState({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [options, setOptions] = useState({});

  // option lists for foreign keys / id lists (states are limited to the workflow being edited)
  useEffect(() => {
    const refs = [...new Set(Object.values(fields).filter((f) => f.ref).map((f) => f.ref))];
    refs.forEach((ref) => {
      const parentId = ref === "State" ? (parent?.id ?? row?.id) : undefined;
      if (ref === "State" && !parentId) { setOptions((o) => ({ ...o, State: [] })); return; }
      api(`/admin/config/options/${ref}/${parentId ? `?parent=${parentId}` : ""}`).then((list) => setOptions((o) => ({ ...o, [ref]: list }))).catch(() => setOptions((o) => ({ ...o, [ref]: [] })));
    });
  }, [fields, parent, row]);

  const set = (name) => (v) => setValues((s) => ({ ...s, [name]: v }));
  const submit = async (e) => {
    e.preventDefault();
    const { body, errors: local } = buildPayload(fields, values, { original: row, isAdmin });
    setErrors(local);
    if (Object.keys(local).length) { setError("Fix the highlighted fields."); return; }
    if (row && Object.keys(body).length === 0) { onClose(); return; }
    setBusy(true);
    setError("");
    try {
      const saved = await api(row ? `${apiPath(slug, parent)}${row.id}/` : apiPath(slug, parent), { method: row ? "PATCH" : "POST", body });
      onSaved(saved, !row);
    } catch (err) {
      setErrors(fieldErrors(err));
      setError(configWriteError(err, noun));
      setBusy(false);
    }
  };
  const formId = `cfg-form-${slug}`;
  return (
    <Modal title={row ? `Edit ${noun}` : `New ${noun}`} onClose={busy ? () => {} : onClose} footer={(
      <>
        <button type="button" className="btn" onClick={onClose} disabled={busy}>Cancel</button>
        <button type="submit" form={formId} className="btn primary" disabled={busy}>{busy ? "Saving…" : row ? "Save changes" : "Create"}</button>
      </>
    )}>
      <form id={formId} className="stack" onSubmit={submit} noValidate>
        {Object.entries(fields).filter(([, f]) => !f.read_only).map(([name, f]) => (
          <FieldInput key={name} name={name} spec={f} value={values[name]} onChange={set(name)} error={errors[name]}
            options={f.ref ? options[f.ref] : null} disabled={f.admin_only && !isAdmin} adapters={slug === "channels" ? schema.adapters : null}
            kindValue={values.kind} vocab={slug === "rules" ? schema : null} />
        ))}
        {error && <div className="notice bad" role="alert">{error}</div>}
      </form>
    </Modal>
  );
}

const humanize = (name) => name.replace(/_id$/, "").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

function FieldInput({ name, spec, value, onChange, error, options, disabled, adapters, kindValue, vocab }) {
  const id = `f-${name}`;
  const label = humanize(name);
  const hint = spec.help || (name === "config" && adapters?.[kindValue]?.config_help ? null : "");
  let control;
  if (spec.kind === "bool") {
    control = <input id={id} type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} disabled={disabled} />;
    return (
      <div className="field"><label className="check" htmlFor={id}>{control} <span>{label}</span></label>{hint && <span className="small muted">{hint}</span>}
        {error && <span className="field-error" role="alert">{error}</span>}</div>
    );
  }
  if (spec.kind === "text" || spec.kind === "json_list" || spec.kind === "json_dict") {
    const mono = spec.kind !== "text";
    control = <textarea id={id} className={`input${mono ? " mono" : ""}`} rows={mono ? 8 : 4} value={value ?? ""} onChange={(e) => onChange(e.target.value)} disabled={disabled} spellCheck={!mono} />;
  } else if (spec.kind === "choice" || (spec.kind === "str" && spec.choices)) {
    control = <select id={id} className="input" value={value ?? ""} onChange={(e) => onChange(e.target.value)} disabled={disabled}>{(spec.choices || []).map((c) => <option key={c} value={c}>{c}</option>)}</select>;
  } else if (spec.kind === "fk") {
    control = (
      <select id={id} className="input" value={value ?? ""} onChange={(e) => onChange(e.target.value)} disabled={disabled || options === null}>
        <option value="">{spec.nullable ? "None" : "Choose…"}</option>
        {(options || []).map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
      </select>
    );
  } else if (spec.kind === "id_list") {
    control = (
      <select id={id} className="input" multiple size={Math.min(6, Math.max(3, (options || []).length))} value={(value || []).map(String)} disabled={disabled}
        onChange={(e) => onChange([...e.target.selectedOptions].map((o) => o.value))}>
        {(options || []).map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
      </select>
    );
  } else if (spec.kind === "str_list" && spec.choices) {
    const selected = new Set(String(value || "").split(",").filter(Boolean));
    control = (
      <div className="chip-row" role="group" aria-label={label}>
        {spec.choices.map((c) => (
          <label key={c} className="check"><input type="checkbox" checked={selected.has(c)} disabled={disabled}
            onChange={(e) => { const next = new Set(selected); e.target.checked ? next.add(c) : next.delete(c); onChange([...next].join(",")); }} /> <span>{c}</span></label>
        ))}
      </div>
    );
  } else if (spec.kind === "str_list" || spec.kind === "domain_list") {
    control = <textarea id={id} className="input" rows={3} value={value ?? ""} onChange={(e) => onChange(e.target.value)} disabled={disabled} placeholder="One per line" />;
  } else {
    const type = spec.kind === "int" ? "number" : spec.kind === "email" ? "email" : spec.kind === "color" ? "text" : "text";
    control = <input id={id} className="input" type={type} value={value ?? ""} onChange={(e) => onChange(e.target.value)} disabled={disabled}
      maxLength={spec.max_len} min={spec.min} max={spec.max} placeholder={spec.kind === "color" ? "#1a2b3c" : undefined} />;
  }
  return (
    <div className="field">
      <label htmlFor={id}><span>{label}{spec.required ? " *" : ""}</span></label>
      {control}
      {hint && <span className="small muted">{hint}</span>}
      {disabled && spec.admin_only && <span className="small muted">Only managers can change this.</span>}
      {name === "kind" && adapters && <span className="small muted">{adapters[kindValue]?.label || "Available kinds: " + Object.keys(adapters).join(", ")}</span>}
      {name === "config" && adapters?.[kindValue]?.config_help && (
        <details className="small"><summary>Keys for {kindValue}</summary>
          <ul>{Object.entries(adapters[kindValue].config_help).map(([k, v]) => <li key={k}><code>{k}</code>: {v}</li>)}</ul></details>
      )}
      {vocab && (name === "conditions" || name === "actions") && (
        <details className="small"><summary>{name === "conditions" ? "Operators" : "Action types"}</summary>
          <p>{name === "conditions" ? <>Each condition: <code>{'{"field": "priority.key", "op": "eq", "value": "P1"}'}</code>. Operators: {vocab.data.condition_ops.join(", ")}.</>
            : <>Each action: <code>{'{"type": "add_tag", "value": "ai"}'}</code>. Types: {vocab.data.action_types.join(", ")}.</>}</p></details>
      )}
      {error && <span className="field-error" role="alert">{error}</span>}
    </div>
  );
}

// ------------------------------------------------------------------------------------------ detail (workflow / ticket type)
function Detail({ schema, section, id }) {
  const { user } = useApp();
  const [state, setState] = useState({ status: "loading" });
  const [editing, setEditing] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const meta = SECTIONS[section];
  useEffect(() => {
    setState({ status: "loading" });
    api(`${apiPath(section)}${id}/`).then((data) => setState({ status: "ready", data })).catch((e) => setState(loadError(e, meta.noun)));
  }, [section, id, attempt, meta.noun]);
  if (state.status !== "ready") return (<><BackLink to={configPath(section)} label={meta.label} /><PageState state={state} noun={meta.noun} onRetry={() => setAttempt((n) => n + 1)} forbiddenText={FORBIDDEN} /></>);
  const d = state.data;
  const summary = Object.entries(schema.resources[section].fields).filter(([, f]) => !f.read_only).map(([k]) => k);
  return (
    <>
      <BackLink to={configPath(section)} label={meta.label} />
      <section className="card card-b admin-summary">
        <div className="admin-summary-head">
          <h2>{d.name}</h2>
          <button type="button" className="btn sm" onClick={() => setEditing(true)}>Edit {meta.noun}</button>
        </div>
        <dl className="admin-facts">
          {summary.filter((k) => k !== "name").map((k) => <div key={k}><dt>{k.replace(/_id$/, "").replace(/_/g, " ")}</dt><dd>{cellText({ [k]: d[k.replace(/_id$/, "_label")] ?? d[k] }, k)}</dd></div>)}
        </dl>
      </section>
      {(CHILD_RESOURCES[section] || []).map((c) => (
        <section key={c.slug} aria-labelledby={`child-${c.slug}`}>
          <h2 className="admin-h2" id={`child-${c.slug}`}>{c.label}</h2>
          <ResourceTable schema={schema} slug={c.slug} noun={c.noun} columns={c.columns} parent={{ slug: section, id }} />
        </section>
      ))}
      {editing && <ResourceForm schema={schema} slug={section} noun={meta.noun} row={d} isAdmin={isTopRole(user)} onClose={() => setEditing(false)}
        onSaved={() => { setEditing(false); setAttempt((n) => n + 1); }} />}
    </>
  );
}

// ------------------------------------------------------------------------------------------ channel detail
/** The result of connecting a mailbox with OAuth: the provider sends the browser back to the channels page. */
function OauthReturnNotice() {
  const { loc } = useApp();
  const returned = loc.search.get("oauth");
  if (returned === "authorized") return <div className="notice admin-ok" role="status">Mailbox connected. Polling starts within two minutes.</div>;
  if (returned === "error") return <div className="notice bad" role="alert">The mailbox couldn't be connected. Check the client id and secret, then try again.</div>;
  return null;
}

/** One channel: its type, inbound webhook, routing and (for IMAP) mailbox sign-in. The webhook URL embeds a secret
 * token, so it is shown masked and copied only on an explicit action. The server reports no connection health, so
 * nothing here claims the channel is connected or working. */
function ChannelDetail({ schema, id }) {
  const { user } = useApp();
  const [state, setState] = useState({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [editing, setEditing] = useState(false);
  const [copied, setCopied] = useState("");
  const [oauth, setOauth] = useState({ busy: false, error: "" });
  const meta = SECTIONS.channels;
  useEffect(() => {
    setState({ status: "loading" });
    api(`${apiPath("channels")}${id}/`).then((data) => setState({ status: "ready", data })).catch((e) => setState(loadError(e, meta.noun)));
  }, [id, attempt, meta.noun]);
  const back = <BackLink to={configPath("channels")} label={meta.label} />;
  if (state.status !== "ready") return (<>{back}<PageState state={state} noun={meta.noun} onRetry={() => setAttempt((n) => n + 1)} forbiddenText={FORBIDDEN} /></>);
  const d = state.data;

  const copy = async () => {
    try { await navigator.clipboard.writeText(d.webhook_url); setCopied("Webhook URL copied. Treat it as a secret."); }
    catch { setCopied("Couldn't copy (clipboard unavailable)."); }
  };
  const connect = async (provider) => {
    setOauth({ busy: true, error: "" });
    try {
      const out = await api(`/channels/${d.id}/oauth-initiate/?provider=${provider}`);
      window.location.assign(out.authorization_url);
    } catch (e) {
      setOauth({ busy: false, error: e.message || "Couldn't start the connection." });
    }
  };

  return (
    <>
      {back}
      <section className="card integration-card" aria-label={`${d.name} channel`}>
        <div className="card-h">
          <h2 style={{ fontSize: 15 }}>{d.name}</h2>
          <span className="row" style={{ gap: 8 }}>
            <span className={d.is_active ? "chip" : "chip muted"}>{d.is_active ? "Enabled" : "Disabled"}</span>
            <button type="button" className="btn sm" onClick={() => setEditing(true)}>Edit {meta.noun}</button>
          </span>
        </div>
        <div className="card-b">
          <dl>
            <dt>Type</dt><dd>{d.kind}</dd>
            <dt>Inbound webhook</dt>
            <dd>
              <code>{maskInboundUrl(d.webhook_url)}</code>{" "}
              <button type="button" className="btn ghost sm" onClick={copy} aria-label={`Copy webhook URL for ${d.name}`}>Copy</button>
              {copied && <span className="small muted" role="status"> {copied}</span>}
            </dd>
            {routingRows(d.settings).map(([label, value]) => (
              <React.Fragment key={label}><dt>{label}</dt><dd>{value}</dd></React.Fragment>
            ))}
            {d.created_at && <><dt>Created</dt><dd>{new Date(d.created_at).toLocaleString()}</dd></>}
          </dl>
          {d.kind === "imap" && (
            <div className="row" style={{ marginTop: 8 }}>
              <span className="small muted">Mailbox sign-in: {d.oauth_status ? d.oauth_status.replace(/_/g, " ") : "password"}</span>
              <button type="button" className="btn sm" disabled={oauth.busy} onClick={() => connect("microsoft")}>Connect Microsoft 365</button>
              <button type="button" className="btn sm" disabled={oauth.busy} onClick={() => connect("google")}>Connect Google</button>
            </div>
          )}
          {oauth.error && <div className="notice bad" role="alert" style={{ marginTop: 8 }}>{oauth.error}</div>}
          <div className="small muted" style={{ marginTop: 8 }}>
            Connection health isn't reported by the server; "Enabled" means the channel accepts traffic, not that the provider was verified.
          </div>
        </div>
      </section>
      {editing && <ResourceForm schema={schema} slug="channels" noun={meta.noun} row={d} isAdmin={isTopRole(user)} onClose={() => setEditing(false)}
        onSaved={() => { setEditing(false); setAttempt((n) => n + 1); }} />}
    </>
  );
}

// ------------------------------------------------------------------------------------------ priority matrix
function PriorityMatrix() {
  const [state, setState] = useState({ status: "loading" });
  const [priorities, setPriorities] = useState([]);
  const [grid, setGrid] = useState({});
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    api("/admin/config/priority-matrix/").then((data) => { setState({ status: "ready", data }); setGrid(matrixLookup(data.cells)); }).catch((e) => setState(loadError(e, "priority matrix")));
    api("/admin/config/options/Priority/").then(setPriorities).catch(() => setPriorities([]));
  }, []);
  useEffect(load, [load]);
  if (state.status !== "ready") return (<><BackLink to={configPath()} label="All configuration" /><PageState state={state} noun="priority matrix" onRetry={load} forbiddenText={FORBIDDEN} /></>);
  const { impact, urgency } = state.data;
  const save = async () => {
    setBusy(true);
    setResult(null);
    try {
      const out = await api("/admin/config/priority-matrix/", { method: "PUT", body: { cells: matrixCells(grid) } });
      setGrid(matrixLookup(out.cells));
      setResult({ ok: true, message: "Priority matrix saved. New tickets and impact/urgency changes use it straight away." });
    } catch (e) { setResult({ ok: false, message: configWriteError(e, "matrix") }); }
    setBusy(false);
  };
  return (
    <>
      <BackLink to={configPath()} label="All configuration" />
      <div className="admin-table-wrap">
        <table className="data matrix">
          <caption className="sr-only">Priority by impact and urgency</caption>
          <thead><tr><th scope="col">Impact ↓ / Urgency →</th>{Object.entries(urgency).map(([u, label]) => <th key={u} scope="col">{label}</th>)}</tr></thead>
          <tbody>{Object.entries(impact).map(([i, il]) => (
            <tr key={i}><th scope="row">{il}</th>
              {Object.keys(urgency).map((u) => (
                <td key={u}>
                  <select className="input" aria-label={`${il}, ${urgency[u]}`} value={grid[`${i}:${u}`] ?? ""} onChange={(e) => setGrid((g) => ({ ...g, [`${i}:${u}`]: e.target.value }))}>
                    <option value="">—</option>
                    {priorities.map((p) => <option key={p.id} value={p.id}>{p.label.split(" - ")[0]}</option>)}
                  </select>
                </td>
              ))}</tr>
          ))}</tbody>
        </table>
      </div>
      <div className="row"><button type="button" className="btn primary" onClick={save} disabled={busy}>{busy ? "Saving…" : "Save matrix"}</button></div>
      {result && <div className={`notice ${result.ok ? "admin-ok" : "bad"}`} role={result.ok ? "status" : "alert"}>{result.message}</div>}
    </>
  );
}

// ------------------------------------------------------------------------------------------ inbound messages
function InboundMessages() {
  const [state, setState] = useState({ status: "loading" });
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [picked, setPicked] = useState(() => new Set());
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const qs = new URLSearchParams({ page: String(page), page_size: "50" });
    if (status) qs.set("status", status);
    api(`/admin/config/inbound-messages/?${qs}`).then((data) => setState({ status: "ready", data })).catch((e) => setState(loadError(e, "inbound messages")));
  }, [status, page, attempt]);
  const toggle = (id) => setPicked((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const reprocess = async () => {
    setBusy(true);
    setResult(null);
    try {
      const out = await api("/admin/config/inbound-messages/reprocess/", { method: "POST", body: { ids: [...picked] } });
      setResult({ ok: true, message: `Requeued ${out.requeued} message${out.requeued === 1 ? "" : "s"}.` });
      setPicked(new Set());
      setAttempt((n) => n + 1);
    } catch (e) { setResult({ ok: false, message: configWriteError(e, "message") }); }
    setBusy(false);
  };
  const rows = state.data?.results || [];
  const pages = state.data ? Math.max(1, Math.ceil(state.data.count / state.data.page_size)) : 1;
  return (
    <>
      <BackLink to={configPath()} label="All configuration" />
      <div className="row admin-toolbar">
        <label className="field inline"><span>Status</span>
          <select className="input" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); setPicked(new Set()); }}>
            <option value="">All</option>{["queued", "processed", "duplicate", "ignored", "failed"].map((s) => <option key={s} value={s}>{s}</option>)}
          </select></label>
        <button type="button" className="btn primary sm" disabled={busy || picked.size === 0} onClick={reprocess}>Reprocess selected ({picked.size})</button>
      </div>
      {result && <div className={`notice ${result.ok ? "admin-ok" : "bad"}`} role={result.ok ? "status" : "alert"}>{result.message}</div>}
      <PageState state={state} noun="inbound messages" onRetry={() => setAttempt((n) => n + 1)} forbiddenText={FORBIDDEN} />
      {state.status === "ready" && rows.length === 0 && <div className="empty" role="status">No messages.</div>}
      {rows.length > 0 && (
        <div className="admin-table-wrap">
          <table className="data">
            <caption className="sr-only">Inbound messages</caption>
            <thead><tr><th scope="col"><span className="sr-only">Select</span></th><th scope="col">Received</th><th scope="col">Channel</th><th scope="col">Status</th><th scope="col">Attempts</th><th scope="col">Error</th><th scope="col">Ticket</th></tr></thead>
            <tbody>{rows.map((m) => (
              <tr key={m.id}>
                <td><input type="checkbox" aria-label={`Select message ${m.id}`} checked={picked.has(m.id)} disabled={m.status === "processed"} onChange={() => toggle(m.id)} /></td>
                <td>{new Date(m.received_at).toLocaleString()}</td><td>{m.channel}</td>
                <td><span className={`chip ${m.status === "processed" ? "ok" : m.status === "failed" ? "bad" : "warn"}`}>{m.status}</span></td>
                <td>{m.attempts}</td><td>{m.error || "—"}</td>
                <td>{m.ticket_id ? <a href={`/tickets/${m.ticket_id}`}>#{m.ticket_id}</a> : "—"}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
      {pages > 1 && (
        <div className="row admin-pager">
          <button type="button" className="btn sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Previous</button>
          <span className="small muted">Page {page} of {pages}</span>
          <button type="button" className="btn sm" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>Next</button>
        </div>
      )}
    </>
  );
}
