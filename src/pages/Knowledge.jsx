import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { relTime } from "../util.js";
import SafeMarkdown from "../components/SafeMarkdown.js";
import { inFlightGuard, latestOnly, submitArticleVote } from "./workflowHelpers.js";

function Article({ id }) {
  const { navigate } = useApp();
  const [a, setA] = useState(null);
  const [voted, setVoted] = useState(false);
  const [voting, setVoting] = useState(false);
  const [voteError, setVoteError] = useState("");
  const voteGuard = useRef(inFlightGuard());
  useEffect(() => { api(`/kb/articles/${id}/`).then(setA).catch(() => setA(false)); }, [id]);
  if (a === false) return <div className="notice bad" role="alert">This article isn't available.</div>;
  if (!a) return <p className="muted" role="status">Loading article...</p>;
  // "Thanks" only after the server records the vote; buttons stay disabled while it is in flight.
  const vote = async (helpful) => {
    setVoteError("");
    setVoting(true);
    await submitArticleVote(api, id, helpful, { guard: voteGuard.current, onDone: () => setVoted(true), onError: setVoteError });
    setVoting(false);
  };
  return (
    <article className="card card-b stack" style={{ maxWidth: 820 }}>
      <button className="btn ghost sm" style={{ justifySelf: "start" }} onClick={() => navigate("/kb")}>All articles</button>
      <h1>{a.title}</h1>
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
  return (
    <div className="page">
      {id ? <Article id={id} /> : (
        <>
          <div className="page-head"><h1>Knowledge base</h1></div>
          <input className="input" type="search" placeholder="Search articles" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 520, marginBottom: 16 }} aria-label="Search articles" />
          <div className="card">
            {error && (
              <div className="notice bad" role="alert" style={{ margin: 16, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                <span>Articles couldn't be loaded: {error}</span>
                <button type="button" className="btn sm" onClick={retry}>Retry</button>
              </div>
            )}
            {items === null && !error && <div className="empty" role="status">Loading...</div>}
            {items?.length === 0 && <div className="empty">No articles match. {user.is_agent ? "Resolve a ticket and turn its fix into an article." : "Raise a request and we'll help."}</div>}
            {items?.map((a) => (
              <button key={a.id} className="trow" onClick={() => navigate(`/kb/${a.id}`)}>
                <div className="trow-subject">{a.title}</div>
                <div className="trow-meta">
                  {user.is_agent && <span>{a.status === "published" ? (a.visibility === "public" ? "Customers and agents" : "Agents only") : a.status}</span>}
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
