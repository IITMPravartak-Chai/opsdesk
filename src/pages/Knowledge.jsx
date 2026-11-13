import React, { useCallback, useEffect, useReducer, useRef, useState } from "react";
import PropTypes from "prop-types";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { relTime } from "../util.js";
import SafeMarkdown from "../components/SafeMarkdown.js";
import { inFlightGuard, latestOnly, submitArticleVote } from "./workflowHelpers.js";
import { onField } from "./formHelpers.js";
import {
  EDITING, STATUSES, VIEWING, VISIBILITY, articleFormReducer, articleSaveLabel, audienceLabel, canManageArticles, deleteArticle,
  initialArticleForm, submitArticle,
} from "./articleHelpers.js";

/** Create (no `article`) or edit an article. Staff only; the API refuses anyone else. Its logic is in articleHelpers.js. */
function ArticleForm({ article, onSaved, onCancel }) {
  const [{ form, errors, formError, busy }, dispatch] = useReducer(articleFormReducer, article, initialArticleForm);
  const guard = useRef(inFlightGuard());
  return (
    <form className="card card-b stack" style={{ maxWidth: 820 }} noValidate aria-label={article ? "Edit article" : "New article"}
      onSubmit={(event) => submitArticle({ event, api, article, form, dispatch, guard: guard.current, onSaved })}>
      <h1>{article ? "Edit article" : "New article"}</h1>
      <label className="field"><span>Title</span>
        <input className="input" value={form.title} onChange={onField(dispatch, "title")} maxLength={300} aria-invalid={!!errors.title} />
        {errors.title && <span className="error">{errors.title}</span>}
      </label>
      <label className="field"><span>Article (Markdown supported)</span>
        <textarea className="input" rows={12} value={form.body} onChange={onField(dispatch, "body")} aria-invalid={!!errors.body} />
        {errors.body && <span className="error">{errors.body}</span>}
      </label>
      <div className="grid-2">
        <label className="field"><span>Who can read it</span>
          <select className="input" value={form.visibility} onChange={onField(dispatch, "visibility")}>
            {VISIBILITY.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
        </label>
        <label className="field"><span>Status</span>
          <select className="input" value={form.status} onChange={onField(dispatch, "status")}>
            {STATUSES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
          <span className="hint">Customers only see published articles that are open to customers.</span>
        </label>
      </div>
      <label className="field"><span>Tags (comma separated, optional)</span>
        <input className="input" value={form.tags} onChange={onField(dispatch, "tags")} />
      </label>
      {formError && <div className="error" role="alert">{formError}</div>}
      <div className="row">
        <button className="btn primary" disabled={busy}>{articleSaveLabel({ busy, editing: Boolean(article) })}</button>
        <button type="button" className="btn ghost" onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
    </form>
  );
}

ArticleForm.propTypes = {
  article: PropTypes.shape({ id: PropTypes.number.isRequired }), // absent when creating
  onSaved: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
};

function Article({ id }) {
  const { navigate, user } = useApp();
  const [a, setA] = useState(null);
  const [view, setView] = useState(VIEWING); // reading, editing or deleting (see articleHelpers.js)
  const [voted, setVoted] = useState(false);
  const [voting, setVoting] = useState(false);
  const [voteError, setVoteError] = useState("");
  const voteGuard = useRef(inFlightGuard());
  useEffect(() => { api(`/kb/articles/${id}/`).then(setA).catch(() => setA(false)); }, [id]);
  if (a === false) return <div className="notice bad" role="alert">This article isn't available.</div>;
  if (!a) return <p className="muted" role="status">Loading article...</p>;
  if (view.editing) return <ArticleForm article={a} onSaved={(saved) => { setA(saved); setView(VIEWING); }} onCancel={() => setView(VIEWING)} />;
  // "Thanks" only after the server records the vote; buttons stay disabled while it is in flight.
  const vote = async (helpful) => {
    setVoteError("");
    setVoting(true);
    await submitArticleVote(api, id, helpful, { guard: voteGuard.current, onDone: () => setVoted(true), onError: setVoteError });
    setVoting(false);
  };
  return (
    <article className="card card-b stack" style={{ maxWidth: 820 }}>
      <div className="row">
        <button className="btn ghost sm" onClick={() => navigate("/kb")}>All articles</button>
        <span className="spacer" />
        {canManageArticles(user) && <>
          <button className="btn sm" onClick={() => setView(EDITING)} disabled={view.deleting}>Edit</button>
          <button className="btn sm danger" disabled={view.deleting}
            onClick={() => deleteArticle({ api, article: a, confirm: (text) => globalThis.confirm(text), setView, onDeleted: () => navigate("/kb") })}>
            {view.deleting ? "Deleting..." : "Delete"}
          </button>
        </>}
      </div>
      {view.error && <div className="error" role="alert">{view.error}</div>}
      <h1>{a.title}</h1>
      {canManageArticles(user) && <div className="small muted">{audienceLabel(a)}</div>}
      <div className="small muted">Updated {relTime(a.updated_at)}{a.author ? ` by ${a.author}` : ""} / {a.views} views</div>
      <SafeMarkdown content={a.body} />
      <div className="row">{voted ? <span className="muted small" role="status">Thanks, noted.</span> : <>
        <span className="small muted">Did this help?</span>
        <button className="btn sm" onClick={() => vote(true)} disabled={voting}>Yes</button>
        <button className="btn sm" onClick={() => vote(false)} disabled={voting}>No</button></>}
      </div>
      {voteError && <div className="error" role="alert">{voteError}</div>}
    </article>
  );
}

export default function Knowledge({ id }) {
  const { user, navigate } = useApp();
  const [q, setQ] = useState("");
  const [items, setItems] = useState(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const gate = useRef(latestOnly());
  const retry = useCallback(() => { setError(""); setItems(null); setAttempt((n) => n + 1); }, []);
  useEffect(() => {
    if (id) return undefined;
    // Each search supersedes the previous one: a slower, older response can never replace newer results.
    const token = gate.current.next();
    const t = setTimeout(() => {
      api(`/kb/articles/${q ? `?q=${encodeURIComponent(q)}` : ""}`)
        .then((d) => { if (gate.current.isCurrent(token)) { setItems(d.results || d); setError(""); } })
        .catch((e) => { if (gate.current.isCurrent(token)) { setItems(null); setError(e.message || "Articles couldn't be loaded."); } });
    }, q ? 250 : 0);
    return () => clearTimeout(t);
  }, [q, id, attempt]);
  if (id === "new" && canManageArticles(user)) return <div className="page"><ArticleForm onSaved={(saved) => navigate(`/kb/${saved.id}`)} onCancel={() => navigate("/kb")} /></div>;
  return (
    <div className="page">
      {id ? <Article key={id} id={id} /> : (
        <>
          <div className="page-head">
            <h1>Knowledge base</h1>
            {canManageArticles(user) && <button className="btn primary" onClick={() => navigate("/kb/new")}>New article</button>}
          </div>
          <input className="input" type="search" placeholder="Search articles" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 520, marginBottom: 16 }} aria-label="Search articles" />
          <div className="card">
            {error && (
              <div className="notice bad" role="alert" style={{ margin: 16, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                <span>Articles couldn't be loaded: {error}</span>
                <button type="button" className="btn sm" onClick={retry}>Retry</button>
              </div>
            )}
            {items === null && !error && <div className="empty" role="status">Loading...</div>}
            {items?.length === 0 && <div className="empty">No articles match. {user.is_agent ? "Use New article, or resolve a ticket and turn its fix into one." : "Raise a request and we'll help."}</div>}
            {items?.map((a) => (
              <button key={a.id} className="trow" onClick={() => navigate(`/kb/${a.id}`)}>
                <div className="trow-subject">{a.title}</div>
                <div className="trow-meta">
                  {user.is_agent && <span>{audienceLabel(a)}</span>}
                  <span>{a.views} views</span><span>updated {relTime(a.updated_at)}</span>
                </div>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
