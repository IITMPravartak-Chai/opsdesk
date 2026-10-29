import { FEATURES } from "../features.js";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, download } from "../api.js";
import { useApp } from "../App.jsx";
import { PriorityMark, SlaChip, StateChip } from "../components/Chips.jsx";
import { Fuse } from "../components/Fuse.jsx";
import Modal from "../components/Modal.jsx";
import SafeMarkdown from "../components/SafeMarkdown.js";
import Composer from "../components/Composer.jsx";
import { isPrivilegedRole } from "../roles.js";
import { cx, dateTime, initials, relTime, SOURCE_LABEL } from "../util.js";
import { canPromoteTicketToKb, createTicketKbDraft } from "./ticketKbPromotion.js";
import { mergeEarlierInteractions } from "./phase3Helpers.js";
import { inFlightGuard, normalizeInteractions, runTicketTransition, teamEmployees } from "./workflowHelpers.js";
import { decisionErrorMessage, isStaleDecision, requesterApprovalStatus, submitApprovalDecision } from "./approvalHelpers.js";
import { alertGroupFrom, nonAlertEntries } from "./opsHelpers.js";

const RESOLUTION_CODES = [["fixed", "Fixed"], ["workaround", "Workaround provided"], ["config_change", "Configuration change"],
  ["user_error", "User guidance"], ["no_fault_found", "No fault found"], ["duplicate", "Duplicate"], ["vendor", "Resolved by vendor"]];

export default function TicketDetail({ id, onChanged, onBack }) {
  const { user, navigate } = useApp();
  const [t, setT] = useState(null);
  const [msgs, setMsgs] = useState([]);
  const [totalMsgs, setTotalMsgs] = useState(0);
  const [msgPage, setMsgPage] = useState(1);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(null); // transition awaiting input
  const [kbBusy, setKbBusy] = useState(false);
  const [kbFeedback, setKbFeedback] = useState(null);
  const [transitionBusy, setTransitionBusy] = useState(false);
  const transitionGuard = useRef(inFlightGuard());

  const applyMessages = (m) => {
    const { items, total } = normalizeInteractions(m);
    setMsgs(items);
    setTotalMsgs(total);
    setMsgPage(1);
  };
  const reloadMessages = async () => applyMessages(await api(`/tickets/${id}/interactions/?ordering=-created_at&page=1&page_size=20`));

  const load = useCallback(async () => {
    try {
      const [d, m] = await Promise.all([
        api(`/tickets/${id}/`),
        api(`/tickets/${id}/interactions/?ordering=-created_at&page=1&page_size=20`),
      ]);
      setT(d);
      applyMessages(m);
      setError("");
    } catch (e) {
      setError(e.status === 404 ? "This ticket doesn't exist or you don't have access to it." : e.message);
    }
  }, [id]);
  useEffect(() => { load(); }, [load]);

  const loadEarlier = async () => {
    setLoadingEarlier(true);
    try {
      const nextPage = msgPage + 1;
      const res = await api(`/tickets/${id}/interactions/?ordering=-created_at&page=${nextPage}&page_size=20`);
      if (res?.results) {
        setMsgs((current) => mergeEarlierInteractions(current, res.results));
        setMsgPage(nextPage);
        setTotalMsgs(res.count);
      }
    } catch (e) {
      setError(`Earlier messages couldn't be loaded: ${e.message}`);
    } finally {
      setLoadingEarlier(false);
    }
  };

  const changed = async (next) => {
    if (next) setT(next); else await load();
    onChanged?.();
  };

  // On failure the controlled fields fall back to the unchanged ticket; the error names what failed.
  const patch = async (body) => {
    try { await changed(await api(`/tickets/${id}/`, { method: "PATCH", body })); setError(""); }
    catch (e) { setError(`Couldn't update ${Object.keys(body).join(", ").replace(/_/g, " ")}: ${e.message}`); }
  };

  // One transition at a time. A failed conversation refresh after a successful transition is
  // reported as such, never as a failed transition.
  const runTransition = async (tr, extra = {}) => {
    setTransitionBusy(true);
    const r = await runTicketTransition(api, id, tr.id, extra, { guard: transitionGuard.current, reloadMessages });
    setTransitionBusy(false);
    if (r.skipped) return;
    if (!r.ok) {
      if (pending) throw r.error;
      setError(r.error.message);
      return;
    }
    setPending(null);
    setError(r.refreshError ? `${tr.name} done. The conversation couldn't be refreshed; reload to see the latest messages.` : "");
    await changed(r.ticket);
  };

  const createKbArticle = async () => {
    await createTicketKbDraft(api, id, {
      isBusy: kbBusy,
      setBusy: setKbBusy,
      onFeedback: setKbFeedback,
      onError: setError,
      onNavigate: navigate,
      navigateDelayMs: 400,
    });
  };

  if (error && !t) {
    return (
      <div className="page">
        <h1 className="sr-only">Ticket unavailable</h1>
        <div className="notice bad" role="alert">{error}</div>
        <div className="row" style={{ marginTop: 12 }}>
          <button type="button" className="btn sm" onClick={onBack}>Back to list</button>
          <button type="button" className="btn sm" onClick={() => { setError(""); load(); }}>Retry</button>
        </div>
      </div>
    );
  }
  if (!t) return <div className="placeholder" role="status">Loading ticket...</div>;

  const done = ["resolved", "closed", "cancelled"].includes(t.state.category);
  const clickTransition = (tr) => {
    const needsInput = tr.requires_comment || tr.required_fields.length || tr.to_category === "resolved";
    if (needsInput) setPending(tr); else runTransition(tr);
  };

  const reply = t.state.category !== "closed" && t.state.category !== "cancelled"
    ? <Composer t={t} onSent={async () => { await load(); onChanged?.(); }} />
    : <div className="notice">This ticket is closed. Raise a new one if the issue comes back.</div>;

  return (
    <article>
      <header className="tk-head">
        <div className="row small muted">
          <button className="btn ghost sm" onClick={onBack} aria-label="Back to list">Back</button>
          <span className="num" style={{ color: "var(--ink)", fontWeight: 600 }}>{t.number}</span>
          <span>{t.type.name}</span>
          <span>via {SOURCE_LABEL[t.source]}</span>
          <span>opened {relTime(t.created_at)}</span>
          {t.is_major_incident && <span className="chip major">Major incident</span>}
        </div>
        <h1 className="tk-title">{t.subject}</h1>
        <div className="row">
          <StateChip state={t.state} />
          <PriorityMark p={t.priority} />
          <SlaChip status={t.sla_status} />
          {t.account && <span className="small muted">{t.account.name}</span>}
          <span className="spacer" />
          {canPromoteTicketToKb(user, t) && (
            <button
              type="button"
              className="btn sm"
              disabled={kbBusy}
              onClick={createKbArticle}
              title="Create Knowledge Base article draft from this fix"
              aria-label="Create Knowledge Base draft article"
            >
              {kbBusy ? "Drafting article..." : "Create KB draft"}
            </button>
          )}
          {t.transitions.map((tr) => (
            <button key={tr.id} className={cx("btn sm", tr.to_category === "resolved" && "primary", tr.to_category === "cancelled" && "danger")}
              onClick={() => clickTransition(tr)} disabled={transitionBusy} title={`Moves to ${tr.to}`}>{tr.name}</button>
          ))}
        </div>
        {(t.response_due_at || t.resolution_due_at) && (
          <div className="tk-fuses">
            <Fuse size="lg" label="First response" start={t.created_at} due={t.response_due_at} done={t.first_responded_at}
              status={t.response_breached ? "breached" : t.sla_status} />
            <Fuse size="lg" label="Resolution" start={t.created_at} due={t.resolution_due_at} done={done ? t.resolved_at : null}
              status={t.sla_status} paused={t.sla_status === "paused"} />
          </div>
        )}
        {kbFeedback && <div className="notice" style={{ marginTop: 8 }}>{kbFeedback}</div>}
        {error && <div className="notice bad" role="alert">{error}</div>}
      </header>

      <div className={cx("tk-body", user.is_agent && "tk-agent")}>
        {user.is_agent && (
          <div className="stack tk-top">
            {isPrivilegedRole(user) && <MajorIncidentCommand t={t} reload={load} />}
            <AlertGroupCard t={t} />
            <DuplicateCallout t={t} onMerged={(pid) => { onChanged?.(); navigate(`/tickets/${pid}`); }} />
          </div>
        )}
        <div className="stack tk-main">
          <div className="tk-main-in">
            <section className="card tk-desc">
              <div className="card-h"><h2>Description</h2><span className="small muted">{t.requester?.name}</span></div>
              <div className="card-b stack">
                <div className="desc">{t.description || <span className="muted">No description provided.</span>}</div>
                <CustomFieldList t={t} />
                {t.attachments.length > 0 && <Attachments items={t.attachments} />}
                {t.resolution_notes && (
                  <div className="fix"><b>Resolution{t.resolution_code ? ` (${t.resolution_code.replace("_", " ")})` : ""}:</b> {t.resolution_notes}</div>
                )}
              </div>
            </section>

            <section aria-label="Conversation" className="stack tk-conv">
              <h2>Conversation</h2>
              {msgs.length < totalMsgs && (
                <div className="timeline-load-earlier">
                  <button
                    type="button"
                    onClick={loadEarlier}
                    disabled={loadingEarlier}
                  >
                    {loadingEarlier
                      ? "Loading earlier messages..."
                      : `Load earlier messages (${totalMsgs - msgs.length} remaining)`}
                  </button>
                </div>
              )}
              <div className="timeline tk-timeline">
                {msgs.length === 0 && <p className="muted small">No replies yet.</p>}
                {msgs.map((m) => <Message key={m.id} m={m} />)}
              </div>
              {!user.is_agent && reply}
            </section>
          </div>
        </div>

        <aside className="stack tk-side">
          {user.is_agent ? <Properties t={t} patch={patch} /> : <CustomerSummary t={t} />}
          {!user.is_agent && <KbSuggestions id={id} />}
          {FEATURES.approvals && user.is_agent && <Approvals t={t} reload={load} />}
        </aside>

        {user.is_agent && (
          <>
            {/* Row 1: major incident, full width. Row 2: description and chat end level with Properties (the chat scrolls).
                Row 3: the reply box, full width. Row 4: activity log level with AI assist (the log scrolls). Row 5: related work and time. */}
            <div className="tk-compose">{reply}</div>
            <div className="tk-log"><Activity id={id} /></div>
            <div className="tk-ai"><AiAssist t={t} onApplied={changed} /></div>
            <div className="tk-rel"><Links t={t} reload={load} /></div>
            <div className="tk-time"><TimeLog id={id} total={t.time_logged_minutes} reload={load} /></div>
          </>
        )}
      </div>

      {pending && <TransitionModal t={t} tr={pending} onCancel={() => setPending(null)} onSubmit={(extra) => runTransition(pending, extra)} />}
    </article>
  );
}

function CustomFieldList({ t }) {
  const { meta, user } = useApp();
  const defs = meta.ticket_types.find((x) => x.key === t.type.key)?.custom_fields || [];
  const rows = defs.filter((d) => t.custom_fields?.[d.key] !== undefined && t.custom_fields[d.key] !== "");
  // Alert-correlation keys are shown by AlertGroupCard; only the sender remains here.
  const extra = user.is_agent ? nonAlertEntries(t.custom_fields).filter(([k]) => k === "sender") : [];
  if (!rows.length && !extra.length) return null;
  return (
    <dl className="props">
      {rows.map((d) => <React.Fragment key={d.key}><dt>{d.label}</dt><dd>{String(t.custom_fields[d.key])}</dd></React.Fragment>)}
      {extra.map(([k, v]) => <React.Fragment key={k}><dt>{k.replace("_", " ")}</dt><dd>{String(v)}</dd></React.Fragment>)}
    </dl>
  );
}

/** U6 E4: the alert group the monitoring ingest correlated into this ticket (server data only). */
function AlertGroupCard({ t }) {
  const g = alertGroupFrom(t.custom_fields);
  if (!g) return null;
  return (
    <section className="card alert-group" aria-label="Correlated alerts">
      <div className="card-h"><h2>Correlated alerts</h2>{g.status && <span className="small muted">Source status: {g.status}</span>}</div>
      <div className="card-b">
        <dl>
          {g.occurrences !== null && <><dt>Occurrences</dt><dd>{g.occurrences}</dd></>}
          {g.firstAt && <><dt>First seen</dt><dd>{g.firstAt}</dd></>}
          {g.lastAt && <><dt>Last seen</dt><dd>{g.lastAt}</dd></>}
          {g.fingerprint && <><dt>Fingerprint</dt><dd style={{ overflowWrap: "anywhere" }}>{g.fingerprint}</dd></>}
        </dl>
        <div className="small" style={{ marginTop: 8 }}>
          {g.active.length ? <>Still firing ({g.active.length}):<ul>{g.active.map((a) => <li key={a}>{a}</li>)}</ul></> : "No alerts in this group are reported as firing."}
        </div>
        <div className="small muted" style={{ marginTop: 6 }}>Alerts are grouped by the server as they arrive; this ticket is the group.</div>
      </div>
    </section>
  );
}

function Attachments({ items }) {
  const [err, setErr] = useState("");
  return (
    <div className="row">
      {items.map((a) => (
        <button key={a.id} className="att" onClick={() => download(a.url, a.filename).catch((e) => setErr(e.message))}
          title={a.scan_status === "pending" ? "Awaiting malware scan" : `${Math.ceil(a.size / 1024)} KB`}>
          {a.filename}{a.scan_status === "pending" ? " (scanning)" : ""}
        </button>
      ))}
      {err && <span className="error">{err}</span>}
    </div>
  );
}

function Message({ m }) {
  const kind = m.kind === "internal" ? "internal" : m.kind === "system" ? "system" : m.author.is_agent ? "agent" : "customer";
  return (
    <div className={cx("msg", kind)}>
      <span className="avatar" aria-hidden="true">{kind === "system" ? "i" : initials(m.author.name)}</span>
      <div className="msg-bubble">
        <div className="msg-meta">
          <b>{m.author.name}</b>
          {m.kind === "internal" && <span className="chip">Internal note</span>}
          <span>{relTime(m.created_at)}</span>
          {m.source !== "portal" && <span>via {SOURCE_LABEL[m.source] || m.source}</span>}
          {m.direction === "outbound" && m.delivery_status !== "n/a" && (
            <span style={{ color: m.delivery_status === "failed" ? "var(--bad)" : undefined }} title={m.delivery_error}>{m.delivery_status}</span>
          )}
        </div>
        <div className="msg-body">
          <SafeMarkdown content={m.body} />
        </div>
        {m.attachments?.length > 0 && <div style={{ marginTop: 8 }}><Attachments items={m.attachments} /></div>}
      </div>
    </div>
  );
}

function TransitionModal({ t, tr, onCancel, onSubmit }) {
  const { user, meta } = useApp();
  const defs = meta.ticket_types.find((x) => x.key === t.type.key)?.custom_fields || [];
  const needed = new Set(tr.required_fields);
  if (tr.to_category === "resolved" && user.is_agent) { needed.add("resolution_notes"); }
  const [fields, setFields] = useState({ resolution_code: t.resolution_code || "", resolution_notes: t.resolution_notes || "",
    ...Object.fromEntries(defs.map((d) => [d.key, t.custom_fields?.[d.key] ?? ""])) });
  const [comment, setComment] = useState("");
  const [isPublic, setPublic] = useState(tr.to_category === "resolved" || tr.to_category === "pending");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setFields((f) => ({ ...f, [k]: e.target.value }));

  const submit = async () => {
    setBusy(true);
    setErr("");
    const sent = {};
    needed.forEach((k) => { sent[k] = fields[k]; });
    try {
      await onSubmit({ comment, comment_public: isPublic, fields: sent });
    } catch (e) { setErr(e.message); setBusy(false); }
  };

  return (
    <Modal title={`${tr.name}: ${t.number}`} onClose={onCancel}
      footer={<><button className="btn ghost" onClick={onCancel}>Cancel</button><button className="btn primary" onClick={submit} disabled={busy}>{tr.name}</button></>}>
      <p className="muted small" style={{ margin: 0 }}>Moves the ticket to <b>{tr.to}</b>.</p>
      {needed.has("resolution_code") && (
        <label className="field"><span>Resolution code</span>
          <select className="input" value={fields.resolution_code} onChange={set("resolution_code")}>
            <option value="">Choose...</option>
            {RESOLUTION_CODES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </label>
      )}
      {needed.has("resolution_notes") && (
        <label className="field"><span>Resolution notes</span>
          <textarea className="input" value={fields.resolution_notes} onChange={set("resolution_notes")} placeholder="What fixed it? This feeds similar-ticket suggestions and the knowledge base." />
        </label>
      )}
      {defs.filter((d) => needed.has(d.key)).map((d) => (
        <label key={d.key} className="field"><span>{d.label}</span>
          {d.type === "select"
            ? <select className="input" value={fields[d.key]} onChange={set(d.key)}><option value="">Choose...</option>{d.options.map((o) => <option key={o}>{o}</option>)}</select>
            : d.type === "textarea" ? <textarea className="input" value={fields[d.key]} onChange={set(d.key)} />
              : <input className="input" value={fields[d.key]} onChange={set(d.key)} />}
        </label>
      ))}
      <label className="field"><span>Comment{tr.requires_comment ? "" : " (optional)"}</span>
        <textarea className="input" value={comment} onChange={(e) => setComment(e.target.value)} />
      </label>
      {user.is_agent && (
        <label className="toggle"><input type="checkbox" checked={isPublic} onChange={(e) => setPublic(e.target.checked)} />Send the comment to the requester</label>
      )}
      {err && <div className="error" role="alert">{err}</div>}
    </Modal>
  );
}

function Properties({ t, patch }) {
  const { meta, user } = useApp();
  const sel = (field, value, options, { allowNone = true, label } = {}) => (
    <select className="input" value={value ?? ""} aria-label={label || field}
      onChange={(e) => patch({ [field]: e.target.value === "" ? null : Number(e.target.value) })}>
      {allowNone && <option value="">None</option>}
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
  const cis = meta.cis.filter((c) => !t.account || !c.account_id || c.account_id === t.account.id);
  return (
    <section className="card">
      <div className="card-h"><h2>Properties</h2>{t.sla_policy && <span className="small muted">{t.sla_policy}</span>}</div>
      <div className="card-b">
        <dl className="props">
          <dt>Team</dt><dd>
            <select className="input" value={t.team?.id ?? ""} aria-label="Team"
              onChange={(e) => {
                const team = e.target.value === "" ? null : Number(e.target.value);
                // The current assignee is cleared when they don't belong to the newly chosen team.
                const keep = team !== null && t.assignee && teamEmployees(meta.teams, meta.agents, team).some((a) => a.id === t.assignee.id);
                patch(t.assignee && !keep ? { team, assignee: null } : { team });
              }}>
              <option value="">None</option>
              {meta.teams.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
            </select>
          </dd>
          {t.team && (
            <><dt>Assignee</dt><dd>{sel("assignee", t.assignee?.id, teamEmployees(meta.teams, meta.agents, t.team.id).map((a) => ({ value: a.id, label: a.name + (a.is_available ? "" : " (away)") })), { label: "Assignee" })}</dd></>
          )}
          <dt>Impact</dt><dd>{sel("impact", t.impact, meta.impact.map((x) => ({ value: x.value, label: `${x.value} - ${x.label}` })), { allowNone: false, label: "Impact" })}</dd>
          <dt>Urgency</dt><dd>{sel("urgency", t.urgency, meta.urgency.map((x) => ({ value: x.value, label: `${x.value} - ${x.label}` })), { allowNone: false, label: "Urgency" })}</dd>
          <dt>Priority</dt><dd>{sel("priority", t.priority?.id, meta.priorities.map((p) => ({ value: p.id, label: `${p.key} - ${p.name}` })), { allowNone: false, label: "Priority" })}</dd>
          <dt>Category</dt><dd>{sel("category", t.category?.id, meta.categories.map((c) => ({ value: c.id, label: c.name })), { label: "Category" })}</dd>
          <dt>Account</dt><dd>{sel("account", t.account?.id, meta.accounts.map((a) => ({ value: a.id, label: a.name })), { label: "Account" })}</dd>
          <dt>Config item</dt><dd>{sel("ci", t.ci?.id, cis.map((c) => ({ value: c.id, label: `${c.name} (${c.type_label})` })), { label: "Configuration item" })}</dd>
          <dt>Requester</dt>
          <dd>{t.requester ? <><div>{t.requester.name}</div><div className="small muted">{[t.requester.email, t.requester.phone].filter(Boolean).join(" / ")}</div></> : "-"}</dd>
          <dt>Major incident</dt>
          <dd>{isPrivilegedRole(user)
            ? <label className="toggle"><input type="checkbox" checked={t.is_major_incident} onChange={(e) => patch({ is_major_incident: e.target.checked })} />{t.is_major_incident ? "Yes" : "No"}</label>
            : (t.is_major_incident ? "Yes" : "No")}</dd>
        </dl>
        {t.ci?.impacted?.length > 0 && (
          <div className="notice small" style={{ marginTop: 12 }}><b>Downstream impact:</b> {t.ci.impacted.join(", ")}</div>
        )}
        {t.tags.length > 0 && <div className="row" style={{ marginTop: 12 }}>{t.tags.map((x) => <span key={x} className="tag">{x}</span>)}</div>}
      </div>
    </section>
  );
}

function CustomerSummary({ t }) {
  return (
    <section className="card">
      <div className="card-h"><h2>Request details</h2></div>
      <div className="card-b">
        <dl className="props">
          <dt>Status</dt><dd>{t.state.name}</dd>
          <dt>Priority</dt><dd>{t.priority ? `${t.priority.key} - ${t.priority.name}` : "-"}</dd>
          <dt>Raised</dt><dd>{dateTime(t.created_at)}</dd>
          <dt>Handled by</dt><dd>{t.assignee?.name || t.team?.name || "Being assigned"}</dd>
          {t.ci && <><dt>System</dt><dd>{t.ci.name}</dd></>}
          {/* U4.3: status only for the requester (no approver names or comments) */}
          {requesterApprovalStatus(t.approvals) && <><dt>Approval</dt><dd>{requesterApprovalStatus(t.approvals)}</dd></>}
        </dl>
      </div>
    </section>
  );
}

function MajorIncidentCommand({ t, reload }) {
  const { meta, user } = useApp();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [openCoordinate, setOpenCoordinate] = useState(false);
  const [statusVal, setStatusVal] = useState(t.major_incident_status || "investigating");
  const [commanderId, setCommanderId] = useState(t.incident_commander?.id || "");
  const [noteText, setNoteText] = useState("");
  const [pirText, setPirText] = useState(t.post_incident_review || "");
  const [linkNumber, setLinkNumber] = useState("");

  const isMajor = Boolean(t.is_major_incident);

  const designate = async (makeMajor) => {
    setBusy(true);
    setError("");
    try {
      await api(`/tickets/${t.id}/designate_major/`, {
        method: "POST",
        body: {
          is_major_incident: makeMajor,
          commander_id: user.id,
          status: "investigating",
        },
      });
      await reload();
    } catch (e) {
      setError(e.message || "Failed to update major incident status.");
    } finally {
      setBusy(false);
    }
  };

  const coordinate = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const body = {
        status: statusVal,
        commander_id: commanderId ? Number(commanderId) : null,
        note: noteText,
        post_incident_review: pirText,
      };
      if (linkNumber.trim()) {
        // The list API searches with ?q= (ticket number or text). Without an exact match nothing is
        // sent: the update must not report success while silently dropping the requested link.
        const wanted = linkNumber.trim().toLowerCase();
        const found = await api(`/tickets/?q=${encodeURIComponent(linkNumber.trim())}&page_size=100`);
        const exact = (found.results || found).find((x) => x.number.toLowerCase() === wanted);
        if (!exact) {
          setError(`No ticket numbered ${linkNumber.trim()} was found. Nothing was updated.`);
          return;
        }
        body.link_ticket_ids = [exact.id];
      }
      await api(`/tickets/${t.id}/major_coordinate/`, {
        method: "POST",
        body,
      });
      setNoteText("");
      setLinkNumber("");
      setOpenCoordinate(false);
      await reload();
    } catch (e) {
      setError(e.message || "Coordination failed.");
    } finally {
      setBusy(false);
    }
  };

  if (!isMajor) {
    return (
      <div className="card major-incident-banner" style={{ borderLeft: "4px solid #64748b", padding: "10px 14px", display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
          <strong>Major Incident Workflow:</strong> Treat this ticket as high-impact critical incident with dedicated coordinator, timeline, and PIR.
        </div>
        <button
          type="button"
          className="btn sm"
          style={{ background: "#dc2626", color: "#fff", borderColor: "#b91c1c" }}
          onClick={() => designate(true)}
          disabled={busy}
        >
          Declare Major Incident
        </button>
      </div>
    );
  }

  const STATUS_COLORS = {
    investigating: { bg: "#fee2e2", text: "#991b1b", border: "#fca5a5" },
    identified: { bg: "#fef3c7", text: "#92400e", border: "#fcd34d" },
    monitoring: { bg: "#e0f2fe", text: "#075985", border: "#7dd3fc" },
    resolved: { bg: "#dcfce7", text: "#166534", border: "#86efac" },
  };
  const sc = STATUS_COLORS[t.major_incident_status || "investigating"] || STATUS_COLORS.investigating;

  return (
    <div
      className="card major-incident-active-card"
      style={{
        border: "2px solid #ef4444",
        background: "var(--panel)",
        borderRadius: "var(--radius)",
        padding: "14px 16px",
        marginBottom: 16,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 10 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
            <span style={{ background: "#ef4444", color: "#fff", padding: "2px 8px", borderRadius: 4, fontWeight: 700, fontSize: 11, letterSpacing: "0.05em", textTransform: "uppercase" }}>
              Major Incident
            </span>
            <span
              style={{
                background: sc.bg,
                color: sc.text,
                border: `1px solid ${sc.border}`,
                padding: "2px 8px",
                borderRadius: 4,
                fontWeight: 600,
                fontSize: 12,
                textTransform: "capitalize",
              }}
            >
              Status: {t.major_incident_status || "Investigating"}
            </span>
          </div>
          <div style={{ fontSize: 13, color: "var(--ink-2)", marginTop: 6 }}>
            <strong>Incident Commander:</strong> {t.incident_commander?.name || "Unassigned"}
            {t.children && t.children.length > 0 && (
              <span style={{ marginLeft: 14 }}>
                <strong>Linked Child Incidents ({t.children.length}):</strong>{" "}
                {t.children.map((c) => (
                  <a key={c.id} href={`/tickets/${c.id}`} style={{ marginRight: 6, fontWeight: 600 }}>
                    {c.number}
                  </a>
                ))}
              </span>
            )}
          </div>
        </div>

        <div style={{ display: "flex", gap: 8 }}>
          <button
            type="button"
            className="btn primary sm"
            onClick={() => {
              // Start from the ticket as it is now. Field state captured before the incident was declared
              // left the commander blank, and submitting that unassigned the commander.
              if (!openCoordinate) {
                setStatusVal(t.major_incident_status || "investigating");
                setCommanderId(t.incident_commander?.id || "");
                setPirText(t.post_incident_review || "");
                setNoteText("");
                setLinkNumber("");
              }
              setOpenCoordinate(!openCoordinate);
            }}
          >
            {openCoordinate ? "Close Coordinator" : "Post Coordination Update"}
          </button>
          <button
            type="button"
            className="btn ghost sm"
            style={{ color: "#ef4444" }}
            onClick={() => {
              if (window.confirm("Demote this ticket from Major Incident status?")) {
                designate(false);
              }
            }}
            disabled={busy}
          >
            Demote
          </button>
        </div>
      </div>

      {error && <div className="notice bad" role="alert" style={{ marginTop: 10 }}>{error}</div>}

      {t.post_incident_review && (
        <section aria-label="Post-incident review" style={{ marginTop: 12 }}>
          <div style={{ fontSize: 12, fontWeight: 600 }}>Post-incident review (saved)</div>
          <div className="pir-saved">{t.post_incident_review}</div>
        </section>
      )}

      {openCoordinate && (
        <form onSubmit={coordinate} style={{ marginTop: 14, paddingTop: 14, borderTop: "1px solid var(--line)" }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 10 }}>
            <div>
              <label htmlFor="mi-status" style={{ display: "block", fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
                Update Incident Status
              </label>
              <select
                className="input"
                id="mi-status"
                value={statusVal}
                onChange={(e) => setStatusVal(e.target.value)}
                style={{ width: "100%", padding: "6px 8px", fontSize: 13 }}
              >
                <option value="investigating">Investigating</option>
                <option value="identified">Identified</option>
                <option value="monitoring">Monitoring</option>
                <option value="resolved">Resolved</option>
              </select>
            </div>
            <div>
              <label htmlFor="mi-commander" style={{ display: "block", fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
                Incident Commander
              </label>
              <select
                className="input"
                id="mi-commander"
                value={commanderId}
                onChange={(e) => setCommanderId(e.target.value)}
                style={{ width: "100%", padding: "6px 8px", fontSize: 13 }}
              >
                <option value="">Unassigned</option>
                {meta?.agents?.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div style={{ marginBottom: 10 }}>
            <label htmlFor="mi-note" style={{ display: "block", fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
              Coordination Note (Logged internally & broadcast to timeline)
            </label>
            <textarea
              className="input"
              rows={2}
              id="mi-note"
                value={noteText}
              onChange={(e) => setNoteText(e.target.value)}
              placeholder="e.g. Failover to backup DB completed. Mitigation verified by payments team."
              style={{ width: "100%", fontSize: 13 }}
            />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 12 }}>
            <div>
              <label htmlFor="mi-link" style={{ display: "block", fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
                Link Child Incident (by Number)
              </label>
              <input
                type="text"
                className="input"
                id="mi-link"
                value={linkNumber}
                onChange={(e) => setLinkNumber(e.target.value)}
                placeholder="e.g. INC-102"
                style={{ width: "100%", fontSize: 13 }}
              />
            </div>
            <div>
              <label htmlFor="mi-pir" style={{ display: "block", fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
                Post-Incident Review (PIR / Root Cause)
              </label>
              <textarea
                className="input"
                rows={3}
                id="mi-pir"
                value={pirText}
                onChange={(e) => setPirText(e.target.value)}
                placeholder="Root cause & prevention actions"
                style={{ width: "100%", fontSize: 13 }}
              />
            </div>
          </div>

          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
            <button type="button" className="btn ghost sm" onClick={() => setOpenCoordinate(false)}>
              Cancel
            </button>
            <button type="submit" className="btn primary sm" disabled={busy}>
              {busy ? "Broadcasting..." : "Broadcast Update"}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

function DuplicateCallout({ t, onMerged }) {
  const dups = t.ai?.triage?.possible_duplicates || [];
  const [hidden, setHidden] = useState(false);
  const [busy, setBusy] = useState(false);
  if (!dups.length || hidden || ["closed", "cancelled"].includes(t.state.category)) return null;
  const d = dups[0];
  const merge = async () => {
    setBusy(true);
    await api(`/tickets/${t.id}/merge/`, { method: "POST", body: { into: d.number } });
    api("/ai/feedback/", { method: "POST", body: { ticket_id: t.id, feature: "duplicate", accepted: true, suggestion: d } }).catch(() => {});
    onMerged(d.id);
  };
  const dismiss = () => {
    api("/ai/feedback/", { method: "POST", body: { ticket_id: t.id, feature: "duplicate", accepted: false, suggestion: d } }).catch(() => {});
    setHidden(true);
  };
  return (
    <div className="callout" role="status">
      <div><b>Possible duplicate</b> of <a href={`/tickets/${d.id}`}>{d.number}</a>: {d.subject} <span className="score" title={`score ${d.score}`}>{d.match} match</span></div>
      <div className="row">
        <button className="btn sm primary" onClick={merge} disabled={busy}>Merge into {d.number}</button>
        <button className="btn sm ghost" onClick={dismiss}>Not a duplicate</button>
      </div>
    </div>
  );
}

function AiAssist({ t, onApplied }) {
  const { meta } = useApp();
  const [similar, setSimilar] = useState(null);
  const [kb, setKb] = useState([]);
  const [summary, setSummary] = useState(t.ai?.summary || "");
  const [busy, setBusy] = useState("");
  const triage = t.ai?.triage || {};

  useEffect(() => {
    api(`/tickets/${t.id}/similar/`).then(setSimilar).catch(() => setSimilar([]));
    api(`/tickets/${t.id}/kb_suggestions/`).then(setKb).catch(() => {});
  }, [t.id]);

  const summarize = async () => {
    setBusy("summary");
    try { setSummary((await api(`/tickets/${t.id}/ai_summary/`, { method: "POST" })).summary); } finally { setBusy(""); }
  };
  const apply = async () => {
    setBusy("apply");
    try { await onApplied(await api(`/tickets/${t.id}/apply_triage/`, { method: "POST" })); } finally { setBusy(""); }
  };
  const name = (list, id) => list.find((x) => x.id === id)?.name;
  const sugg = [
    triage.category_id && triage.category_id !== t.category?.id && `Category: ${name(meta.categories, triage.category_id)}`,
    triage.team_id && triage.team_id !== t.team?.id && `Team: ${name(meta.teams, triage.team_id)}`,
    triage.impact && triage.impact !== t.impact && `Impact ${triage.impact}`,
    triage.urgency && triage.urgency !== t.urgency && `Urgency ${triage.urgency}`,
  ].filter(Boolean);

  return (
    <section className="card">
      <div className="card-h"><h2>AI assist</h2><span className="small muted">suggestions, you decide</span></div>
      <div className="card-b stack" style={{ gap: 12 }}>
        <div>
          <div className="row"><h3>Summary</h3><span className="spacer" />
            <button className="btn ghost sm" onClick={summarize} disabled={busy === "summary"}>{busy === "summary" ? "Summarising..." : summary ? "Refresh" : "Summarise"}</button>
          </div>
          {summary && <p className="small" style={{ whiteSpace: "pre-wrap", margin: "6px 0 0" }}>{summary}</p>}
        </div>
        {sugg.length > 0 && (
          <div>
            <div className="row"><h3>Suggested triage</h3><span className="spacer" />
              {triage.confidence !== undefined && <span className="score">{Math.round(triage.confidence * 100)}% confidence</span>}
            </div>
            <p className="small" style={{ margin: "6px 0" }}>{sugg.join(" / ")}</p>
            <p className="small muted" style={{ margin: "0 0 6px" }}>Based on {triage.basis === "llm" ? "the language model and similar tickets" : `similar tickets ${(triage.neighbours || []).slice(0, 3).join(", ")}`}</p>
            <button className="btn sm" onClick={apply} disabled={busy === "apply"}>Apply suggestion</button>
          </div>
        )}
        <div>
          <h3>Similar tickets</h3>
          {similar === null && <p className="small muted">Searching...</p>}
          {similar?.length === 0 && <p className="small muted">No close matches yet.</p>}
          {similar?.map((s) => (
            <div key={s.id} className="ai-item">
              <div className="row small"><a href={`/tickets/${s.id}`}>{s.number}</a><span className="muted">{s.state}</span><span className="spacer" /><span className="score" title={`score ${s.score}`}>{s.match}</span></div>
              <div className="match" aria-hidden="true"><i style={{ width: `${s.strength}%` }} /></div>
              <div className="small">{s.subject}</div>
              {s.resolution_notes && <div className="fix">{s.resolution_notes}</div>}
            </div>
          ))}
        </div>
        {kb.length > 0 && (
          <div>
            <h3>Knowledge articles</h3>
            {kb.map((a) => <div key={a.id} className="ai-item small"><a href={`/kb/${a.id}`}>{a.title}</a></div>)}
          </div>
        )}
      </div>
    </section>
  );
}

function KbSuggestions({ id }) {
  const [kb, setKb] = useState([]);
  useEffect(() => { api(`/tickets/${id}/kb_suggestions/`).then(setKb).catch(() => {}); }, [id]);
  if (!kb.length) return null;
  return (
    <section className="card">
      <div className="card-h"><h2>Articles that may help</h2></div>
      <div className="card-b">{kb.map((a) => <div key={a.id} className="ai-item"><a href={`/kb/${a.id}`}>{a.title}</a><span className="small muted">{a.excerpt.slice(0, 120)}...</span></div>)}</div>
    </section>
  );
}

// U4.4 (UI-DEC-015): one action at a time, failures shown instead of unhandled, and a decision the
// server confirmed is shown even if the follow-up reload fails. Payloads are unchanged.
function Approvals({ t, reload }) {
  const { meta, user } = useApp();
  const [approver, setApprover] = useState("");
  const [busy, setBusy] = useState(null); // "request" | approval id being decided
  const [err, setErr] = useState("");
  const [decided, setDecided] = useState({}); // approval id -> server-confirmed status
  const guard = useRef(inFlightGuard());
  const needs = t.transitions.some((x) => x.requires_approval) || t.approvals.length > 0;
  if (!needs) return null;
  const request = async () => {
    if (!approver || !guard.current.enter()) return;
    setBusy("request");
    setErr("");
    try {
      await api(`/tickets/${t.id}/approvals/`, { method: "POST", body: { approver_id: Number(approver) } });
      setApprover("");
      await reload();
    } catch (e) {
      setErr(`Approval wasn't requested: ${e.message}`);
    } finally {
      guard.current.exit();
      setBusy(null);
    }
  };
  const decide = async (a, decision) => {
    setBusy(a.id);
    setErr("");
    const r = await submitApprovalDecision(api, t.id, a.id, { decision }, { guard: guard.current });
    setBusy(null);
    if (r.skipped) return;
    if (!r.ok) {
      setErr(decisionErrorMessage(r.error));
      if (isStaleDecision(r.error)) reload();
      return;
    }
    setDecided((d) => ({ ...d, [a.id]: r.status }));
    await reload(); // load() reports its own failure in the ticket header
  };
  return (
    <section className="card">
      <div className="card-h"><h2>Approvals</h2></div>
      <div className="card-b stack" style={{ gap: 10 }}>
        {t.approvals.map((a) => {
          const status = decided[a.id] || a.status;
          return (
            <div key={a.id} className="row small">
              <span>{a.approver.name}</span><span className="spacer" />
              {status === "pending" && a.approver.id === user.id
                ? <><button type="button" className="btn sm primary" disabled={busy !== null} onClick={() => decide(a, "approved")} aria-label={`Approve as ${a.approver.name}`}>{busy === a.id ? "Recording…" : "Approve"}</button>
                  <button type="button" className="btn sm" disabled={busy !== null} onClick={() => decide(a, "rejected")} aria-label={`Reject as ${a.approver.name}`}>Reject</button></>
                : <span className="chip" style={{ color: status === "approved" ? "var(--ok)" : status === "rejected" ? "var(--bad)" : "var(--warn)" }}><span className="dot" />{status}</span>}
            </div>
          );
        })}
        {err && <div className="error" role="alert">{err}</div>}
        <div className="row">
          <select className="input" style={{ flex: 1, minHeight: 30 }} value={approver} onChange={(e) => setApprover(e.target.value)} aria-label="Approver">
            <option value="">Request approval from...</option>
            {meta.agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
          <button type="button" className="btn sm" onClick={request} disabled={!approver || busy !== null}>{busy === "request" ? "Requesting…" : "Request"}</button>
        </div>
      </div>
    </section>
  );
}

function Links({ t, reload }) {
  const [num, setNum] = useState("");
  const [type, setType] = useState("related");
  const [err, setErr] = useState("");
  const add = async () => {
    try { await api(`/tickets/${t.id}/link/`, { method: "POST", body: { number: num.trim(), link_type: type } }); setNum(""); reload(); }
    catch (e) { setErr(e.status === 404 ? "No ticket with that number." : e.message); }
  };
  return (
    <section className="card">
      <div className="card-h"><h2>Related work</h2></div>
      <div className="card-b stack" style={{ gap: 8 }}>
        {t.parent && <div className="small">Part of <a href={`/tickets/${t.parent.id}`}>{t.parent.number}</a> {t.parent.subject}</div>}
        {t.children.map((c) => <div key={c.id} className="small">Child <a href={`/tickets/${c.id}`}>{c.number}</a> {c.subject} <span className="muted">{c.state}</span></div>)}
        {t.links.map((l, i) => <div key={i} className="small">{l.type} <a href={`/tickets/${l.id}`}>{l.number}</a> <span className="muted">{l.subject}</span></div>)}
        {!t.parent && !t.children.length && !t.links.length && <p className="small muted" style={{ margin: 0 }}>No linked tickets.</p>}
        <div className="row">
          <input className="input" style={{ flex: 1, minHeight: 30 }} placeholder="e.g. PRB-000001" value={num} onChange={(e) => { setNum(e.target.value); setErr(""); }} aria-label="Ticket number to link" />
          <select className="input" style={{ width: "auto", minHeight: 30 }} value={type} onChange={(e) => setType(e.target.value)} aria-label="Link type">
            <option value="related">Related</option><option value="caused_by">Caused by</option><option value="duplicate_of">Duplicate of</option><option value="blocks">Blocks</option>
          </select>
          <button className="btn sm" onClick={add} disabled={!num.trim()}>Link</button>
        </div>
        {err && <span className="error">{err}</span>}
      </div>
    </section>
  );
}

function TimeLog({ id, total, reload }) {
  const [minutes, setMinutes] = useState("");
  const [note, setNote] = useState("");
  const [billable, setBillable] = useState(true);
  const add = async () => {
    await api(`/tickets/${id}/time/`, { method: "POST", body: { minutes: Number(minutes), note, billable } });
    setMinutes(""); setNote(""); reload();
  };
  return (
    <section className="card">
      <div className="card-h"><h2>Time</h2><span className="small muted num">{Math.floor(total / 60)}h {total % 60}m logged</span></div>
      <div className="card-b stack" style={{ gap: 8 }}>
        <div className="row">
          <input className="input" style={{ width: 90, minHeight: 30 }} inputMode="numeric" placeholder="Minutes" value={minutes} onChange={(e) => setMinutes(e.target.value.replace(/\D/g, ""))} aria-label="Minutes" />
          <input className="input" style={{ flex: 1, minHeight: 30 }} placeholder="What did you do?" value={note} onChange={(e) => setNote(e.target.value)} aria-label="Work note" />
        </div>
        <div className="row">
          <label className="toggle"><input type="checkbox" checked={billable} onChange={(e) => setBillable(e.target.checked)} />Billable</label>
          <span className="spacer" />
          <button className="btn sm" onClick={add} disabled={!minutes}>Log time</button>
        </div>
      </div>
    </section>
  );
}

const EVENT_TEXT = {
  created: (d) => `created via ${d.source}`, state_changed: (d) => `moved ${d.from} to ${d.to}${d.via ? ` (${d.via})` : ""}`,
  assigned: (d) => `auto-assigned to ${d.assignee} (${d.strategy?.replace("_", " ")})`, first_response: () => "sent the first response",
  updated: (d) => `changed ${Object.keys(d.changes || {}).join(", ")}`, automation: (d) => `rules ran: ${(d.rules || []).join(", ")}`,
  sla_at_risk: () => "SLA at risk", sla_breached: () => "SLA breached", linked: (d) => `linked ${d.target}`,
  ai_possible_duplicate: (d) => `AI flagged possible duplicate of ${(d.candidates || []).join(", ")}`,
  approval_requested: (d) => `requested approval from ${d.approver}`, approval_approved: () => "approved", approval_rejected: () => "rejected",
  csat: (d) => `customer rated ${d.score}/5`, ai_triage_applied: () => "AI triage applied automatically",
};

function Activity({ id }) {
  const [events, setEvents] = useState(null);
  useEffect(() => { api(`/tickets/${id}/events/`).then(setEvents).catch(() => setEvents([])); }, [id]);
  const rows = (events || []).filter((e) => e.event_type !== "interaction");
  return (
    <section className="card activity-card">
      <div className="card-h"><h2>Activity log</h2><span className="small muted">{events ? `${rows.length} events` : ""}</span></div>
      <div className="card-b activity-scroll">
        {events && rows.length === 0 && <p className="muted small">Nothing has happened on this ticket yet.</p>}
        <table className="data"><tbody>
          {rows.map((e) => (
            <tr key={e.id}><td className="muted small" style={{ whiteSpace: "nowrap" }}>{dateTime(e.created_at)}</td><td>{e.actor?.name || ""}</td>
              <td>{(EVENT_TEXT[e.event_type] || (() => e.event_type.replace(/_/g, " ")))(e.data)}</td></tr>
          ))}
        </tbody></table>
      </div>
    </section>
  );
}
