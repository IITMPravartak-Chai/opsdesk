import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { fileProblems, limitsFrom, uploadErrorMessage } from "./attachmentHelpers.js";

export default function NewTicket() {
  const { user, meta, navigate, refreshCounts } = useApp();
  const [typeId, setTypeId] = useState(meta.ticket_types[0]?.id || "");
  const type = meta.ticket_types.find((t) => t.id === Number(typeId));
  const [f, setF] = useState({ subject: "", description: "", account: "", ci: "", category: "", impact: "", urgency: "", team: "" });
  const [custom, setCustom] = useState({});
  const [files, setFiles] = useState([]);
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const [hints, setHints] = useState({ tickets: [], articles: [] });
  // Set when the ticket was saved but its files were not: { ticket, message }.
  const [uploadIssue, setUploadIssue] = useState(null);
  const limits = limitsFrom(meta);
  const fileErrors = fileProblems(files, limits);

  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }));
  const accountId = user.is_agent ? Number(f.account) || null : user.account?.id;
  const cis = useMemo(() => meta.cis.filter((c) => !accountId || !c.account_id || c.account_id === accountId), [meta.cis, accountId]);
  const cats = meta.categories.filter((c) => !c.ticket_type_id || c.ticket_type_id === type?.id);

  useEffect(() => {
    const text = `${f.subject}\n${f.description}`.trim();
    if (text.length < 12) { setHints({ tickets: [], articles: [] }); return; }
    const t = setTimeout(() => api("/ai/search/", { method: "POST", body: { text } }).then(setHints).catch(() => {}), 450);
    return () => clearTimeout(t);
  }, [f.subject, f.description]);

  const submit = async (e) => {
    e.preventDefault();
    const errs = {};
    if (!f.subject.trim()) errs.subject = "Add a short summary of the problem or request.";
    (type?.custom_fields || []).forEach((d) => { if (d.required && !custom[d.key]) errs[d.key] = `${d.label} is required.`; });
    setErrors(errs);
    if (Object.keys(errs).length || fileErrors.length) return;
    setBusy(true);
    const body = { ticket_type: type.id, subject: f.subject.trim(), description: f.description, custom_fields: custom,
      source: window.matchMedia("(max-width: 820px)").matches ? "mobile" : "portal" };
    for (const k of ["account", "ci", "category", "impact", "urgency", "team"]) if (f[k]) body[k] = Number(f[k]);
    try {
      const t = await api("/tickets/", { method: "POST", body });
      if (files.length) {
        const form = new FormData();
        files.forEach((x) => form.append("files", x));
        // The ticket exists by now, so a refused upload is reported here instead of being dropped silently.
        let problem = "";
        try {
          const r = await api(`/tickets/${t.id}/attachments/`, { method: "POST", form });
          if (r?.errors?.length) problem = r.errors.join("; ");
        } catch (upErr) {
          problem = uploadErrorMessage(upErr, limits);
        }
        if (problem) {
          refreshCounts();
          setUploadIssue({ ticket: t, message: problem });
          setBusy(false);
          return;
        }
      }
      refreshCounts();
      navigate(`/tickets/${t.id}`);
    } catch (err) {
      setErrors(err.data && typeof err.data === "object" ? Object.fromEntries(Object.entries(err.data).map(([k, v]) => [k, [].concat(v).join(" ")])) : { form: err.message });
      setBusy(false);
    }
  };

  if (uploadIssue) {
    return (
      <div className="page">
        <div className="page-head"><h1>{uploadIssue.ticket.number} was created</h1></div>
        <div className="card card-b stack" role="alert">
          <div className="error">Not every attachment was saved. {uploadIssue.message}</div>
          <p style={{ margin: 0 }}>Your {user.is_agent ? "ticket" : "request"} itself was submitted. Open it and attach the files again from the reply box.</p>
          <div className="row"><button type="button" className="btn primary" onClick={() => navigate(`/tickets/${uploadIssue.ticket.id}`)}>Open {uploadIssue.ticket.number}</button></div>
        </div>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-head"><h1>{user.is_agent ? "New ticket" : "Raise a request"}</h1></div>
      <div className="tk-body" style={{ padding: 0 }}>
        <form className="card card-b stack" onSubmit={submit} noValidate>
          <div className="seg" role="radiogroup" aria-label="Ticket type" style={{ justifySelf: "start", flexWrap: "wrap" }}>
            {meta.ticket_types.map((t) => (
              <button type="button" key={t.id} role="radio" aria-checked={t.id === type?.id} className={t.id === type?.id ? "on" : ""}
                onClick={() => { setTypeId(t.id); setCustom({}); }}>{t.name}</button>
            ))}
          </div>
          <label className="field"><span>Summary</span>
            <input className="input" value={f.subject} onChange={set("subject")} maxLength={300} placeholder="e.g. Payments API returning timeouts for merchants" aria-invalid={!!errors.subject} />
            {errors.subject && <span className="error">{errors.subject}</span>}
          </label>
          <label className="field"><span>Details</span>
            <textarea className="input" value={f.description} onChange={set("description")} rows={6}
              placeholder="What happened, when it started, who is affected, any error messages." />
          </label>
          <div className="grid-2">
            {user.is_agent && (
              <label className="field"><span>Account</span>
                <select className="input" value={f.account} onChange={set("account")}>
                  <option value="">Choose account...</option>
                  {meta.accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </select>
              </label>
            )}
            <label className="field"><span>Affected system</span>
              <select className="input" value={f.ci} onChange={set("ci")}>
                <option value="">Not sure</option>
                {cis.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.type_label})</option>)}
              </select>
            </label>
            <label className="field"><span>Category</span>
              <select className="input" value={f.category} onChange={set("category")}>
                <option value="">Choose...</option>
                {cats.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </label>
            <label className="field"><span>How many people are affected?</span>
              <select className="input" value={f.impact} onChange={set("impact")}>
                <option value="">Default</option>
                {meta.impact.map((x) => <option key={x.value} value={x.value}>{x.label}</option>)}
              </select>
            </label>
            <label className="field"><span>How urgent is it?</span>
              <select className="input" value={f.urgency} onChange={set("urgency")}>
                <option value="">Default</option>
                {meta.urgency.map((x) => <option key={x.value} value={x.value}>{x.label}</option>)}
              </select>
            </label>
            {user.is_agent && (
              <label className="field"><span>Team</span>
                <select className="input" value={f.team} onChange={set("team")}>
                  <option value="">Route automatically</option>
                  {meta.teams.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
                </select>
              </label>
            )}
          </div>
          {(type?.custom_fields || []).length > 0 && (
            <div className="grid-2">
              {type.custom_fields.map((d) => (
                <label key={d.key} className="field" style={d.type === "textarea" ? { gridColumn: "1 / -1" } : undefined}>
                  <span>{d.label}{d.required ? "" : " (optional)"}</span>
                  {d.type === "select" ? (
                    <select className="input" value={custom[d.key] || ""} onChange={(e) => setCustom({ ...custom, [d.key]: e.target.value })}>
                      <option value="">Choose...</option>{d.options.map((o) => <option key={o}>{o}</option>)}
                    </select>
                  ) : d.type === "textarea" ? (
                    <textarea className="input" value={custom[d.key] || ""} onChange={(e) => setCustom({ ...custom, [d.key]: e.target.value })} />
                  ) : (
                    <input className="input" type={d.type === "date" ? "date" : d.type === "number" ? "number" : "text"} value={custom[d.key] || ""}
                      onChange={(e) => setCustom({ ...custom, [d.key]: e.target.value })} />
                  )}
                  {d.help_text && <span className="hint">{d.help_text}</span>}
                  {errors[d.key] && <span className="error">{errors[d.key]}</span>}
                </label>
              ))}
            </div>
          )}
          <label className="field"><span>Attachments (optional)</span>
            <input type="file" multiple onChange={(e) => setFiles([...e.target.files])} aria-invalid={fileErrors.length > 0} />
            <span className="hint">Up to {limits.max_file_mb} MB per file.</span>
            {fileErrors.map((m) => <span key={m} className="error" role="alert">{m}</span>)}
          </label>
          {Object.entries(errors).filter(([k]) => !["subject", ...(type?.custom_fields || []).map((d) => d.key)].includes(k)).map(([k, v]) => (
            <div key={k} className="error">{v}</div>
          ))}
          <div className="row"><button className="btn primary" disabled={busy || fileErrors.length > 0}>{busy ? "Submitting..." : user.is_agent ? "Create ticket" : "Submit request"}</button>
            <button type="button" className="btn ghost" onClick={() => history.back()}>Cancel</button></div>
        </form>

        <aside className="stack">
          <section className="card">
            <div className="card-h"><h2>{user.is_agent ? "Similar past tickets" : "This might already be answered"}</h2></div>
            <div className="card-b">
              {!hints.tickets.length && !hints.articles.length && (
                <p className="small muted" style={{ margin: 0 }}>Start typing a summary and matching articles and past fixes appear here.</p>
              )}
              {hints.articles.map((a) => (
                <div key={`a${a.id}`} className="ai-item"><a href={`/kb/${a.id}`} target="_blank" rel="noreferrer">{a.title}</a><span className="small muted">{a.excerpt.slice(0, 140)}</span></div>
              ))}
              {hints.tickets.map((t) => (
                <div key={t.id} className="ai-item">
                  <div className="row small"><a href={`/tickets/${t.id}`} target="_blank" rel="noreferrer">{t.number}</a><span className="spacer" /><span className="score" title={`score ${t.score}`}>{t.match}</span></div>
                  <div className="small">{t.subject}</div>
                  {t.resolution_notes && <div className="fix">{t.resolution_notes}</div>}
                </div>
              ))}
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}
