import React, { useEffect, useState } from "react";
import { ratingButtonLabel, savedNote } from "./csatHelpers.js";

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
      if (d.score) setScore(d.score);
    }).catch((e) => setError(e instanceof TypeError ? OFFLINE : e.message || LOAD_FAILED));
  }, [token]);

  // Thanks is shown only after the server accepts the rating. The stars can be changed again from the same link;
  // written feedback is accepted once (the server keeps the first one), so its box goes away after that.
  const saved = (d) => {
    if (d) setInfo(d);
    setComment("");
    setSent(true);
  };
  const submit = async () => {
    if (!score) { setError("Choose a rating from 1 to 5."); return; }
    setBusy(true);
    setError("");
    try {
      const r = await fetch(`/api/csat/${token}/`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ score, comment }) });
      if (r.ok) {
        saved(await r.json().catch(() => null));
      } else {
        setError((await detailOf(r)) || "Your rating wasn't saved. Please try again.");
      }
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
      {info && (
        <>
          {sent ? (
            <div>
              <h1>Thanks for the feedback</h1>
              <output className="muted" style={{ display: "block" }}>It goes straight to the team that handled {info.ticket}.{savedNote(info, score)} You can change your star rating below.</output>
            </div>
          ) : (
            <>
              <h1>How did we do?</h1>
              <p className="muted">{info.ticket}: {info.subject}</p>
            </>
          )}
          <div className="stars" role="radiogroup" aria-label="Rating" aria-describedby={error ? "csat-error" : undefined}>
            {[1, 2, 3, 4, 5].map((n) => (
              <button type="button" key={n} className={n <= score ? "on" : ""} onClick={() => { setScore(n); setError(""); }} role="radio" aria-checked={n === score} aria-label={`${n} of 5`}>{n}</button>
            ))}
          </div>
          {info.has_comment ? (
            <p className="small muted">Your written feedback was received. It can be given once.</p>
          ) : (
            <label className="field"><span>Anything we could do better? (optional, can be sent once)</span>
              <textarea className="input" value={comment} onChange={(e) => setComment(e.target.value)} />
            </label>
          )}
          {error && <div className="error" id="csat-error" role="alert">{error}</div>}
          <button type="button" className="btn primary" onClick={submit} disabled={busy}>{ratingButtonLabel({ busy, sent })}</button>
        </>
      )}
    </div>
  );
}
