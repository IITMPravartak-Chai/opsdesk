import React, { useEffect, useState } from "react";

const LOAD_FAILED = "This survey link isn't valid.";
const OFFLINE = "We couldn't reach OpsDesk. Check your connection and try again.";

// Server-provided detail when there is one; never echo a raw parser or network message.
const detailOf = async (r) => (await r.json().catch(() => ({}))).detail;

export default function Csat({ token }) {
  const [info, setInfo] = useState(null);
  const [score, setScore] = useState(0);
  const [comment, setComment] = useState("");
  const [error, setError] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetch(`/api/csat/${token}/`).then(async (r) => {
      const d = await r.json().catch(() => null);
      if (!r.ok || !d) throw new Error((d && d.detail) || LOAD_FAILED);
      setInfo(d);
      setSent(d.responded);
    }).catch((e) => setError(e instanceof TypeError ? OFFLINE : e.message || LOAD_FAILED));
  }, [token]);

  // Thanks is shown only after the server accepts the rating.
  const submit = async () => {
    if (!score) { setError("Choose a rating from 1 to 5."); return; }
    setBusy(true);
    setError("");
    try {
      const r = await fetch(`/api/csat/${token}/`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ score, comment }) });
      if (r.ok) setSent(true); else setError((await detailOf(r)) || "Your rating wasn't saved. Please try again.");
    } catch {
      setError(OFFLINE);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="public-card">
      {!info && !error && <p className="muted" role="status">Loading your survey…</p>}
      {error && !info && <><h1 className="sr-only">Satisfaction survey</h1><div className="notice bad" role="alert">{error}</div></>}
      {info && sent && <div role="status"><h1>Thanks for the feedback</h1><p className="muted">It goes straight to the team that handled {info.ticket}.</p></div>}
      {info && !sent && (
        <>
          <h1>How did we do?</h1>
          <p className="muted">{info.ticket}: {info.subject}</p>
          <div className="stars" role="radiogroup" aria-label="Rating" aria-describedby={error ? "csat-error" : undefined}>
            {[1, 2, 3, 4, 5].map((n) => (
              <button type="button" key={n} className={n <= score ? "on" : ""} onClick={() => { setScore(n); setError(""); }} role="radio" aria-checked={n === score} aria-label={`${n} of 5`}>{n}</button>
            ))}
          </div>
          <label className="field"><span>Anything we could do better? (optional)</span>
            <textarea className="input" value={comment} onChange={(e) => setComment(e.target.value)} />
          </label>
          {error && <div className="error" id="csat-error" role="alert">{error}</div>}
          <button type="button" className="btn primary" onClick={submit} disabled={busy}>{busy ? "Sending…" : "Send rating"}</button>
        </>
      )}
    </div>
  );
}
