import React, { useState } from "react";
import { api, logout, updateUser } from "../api.js";
import { useApp } from "../App.jsx";
import { isSixDigitCode, mfaErrorMessage } from "./profileHelpers.js";

export default function Profile() {
  const { user } = useApp();
  const [setup, setSetup] = useState(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState({ kind: "", text: "" });

  const start = async () => {
    setBusy("start");
    setMsg({ kind: "", text: "" });
    try {
      setSetup(await api("/auth/mfa/", { method: "POST", body: {} }));
      setCode("");
    } catch (e) {
      if (e.status === 409) updateUser({ ...user, mfa_enabled: true });
      setMsg({ kind: "bad", text: mfaErrorMessage(e, "start") });
    } finally {
      setBusy("");
    }
  };
  const confirm = async () => {
    setBusy("confirm");
    setMsg({ kind: "", text: "" });
    try {
      const res = await api("/auth/mfa/", { method: "POST", body: { code: code.trim() } });
      if (!res?.mfa_enabled) throw new Error("not enabled");
      updateUser({ ...user, mfa_enabled: true });
      setSetup(null);
      setMsg({ kind: "ok", text: "Two-step sign-in is on. You'll be asked for a code each time you sign in." });
    } catch (e) {
      setMsg({ kind: "bad", text: mfaErrorMessage(e, "confirm") });
    } finally {
      setBusy("");
    }
  };
  const cancel = () => { setSetup(null); setCode(""); setMsg({ kind: "", text: "" }); };

  return (
    <div className="page">
      <div className="page-head"><h1>Your profile</h1></div>
      <div className="card card-b stack" style={{ maxWidth: 560 }}>
        <dl className="props">
          <dt>Name</dt><dd>{user.name}</dd>
          <dt>Email</dt><dd>{user.email}</dd>
          <dt>Role</dt><dd style={{ textTransform: "capitalize" }}>{user.role}</dd>
          {user.account && <><dt>Organisation</dt><dd>{user.account.name}</dd></>}
          <dt>Two-step sign-in</dt><dd>{user.mfa_enabled ? "On" : "Off"}</dd>
        </dl>
        {!user.mfa_enabled && !setup && (
          <button className="btn" style={{ justifySelf: "start" }} onClick={start} disabled={busy !== ""}>
            {busy === "start" ? "Starting…" : "Turn on two-step sign-in"}
          </button>
        )}
        {setup && (
          <div className="stack">
            <p className="small" style={{ margin: 0 }} id="mfa-help">Add this key to an authenticator app (Microsoft Authenticator, Google Authenticator, 1Password), then enter the 6-digit code it shows. Two-step sign-in stays off until the code is confirmed.</p>
            <code style={{ background: "var(--sunk)", padding: 8, borderRadius: 6, wordBreak: "break-all" }} aria-label="Authenticator setup key">{setup.secret}</code>
            <div className="row">
              <input className="input" style={{ width: 140 }} inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code}
                onChange={(e) => setCode(e.target.value)} aria-label="Authenticator code" aria-describedby="mfa-help" />
              <button className="btn primary" onClick={confirm} disabled={!isSixDigitCode(code) || busy !== ""}>
                {busy === "confirm" ? "Confirming…" : "Confirm"}
              </button>
              <button className="btn ghost" onClick={cancel} disabled={busy !== ""}>Cancel</button>
            </div>
          </div>
        )}
        {msg.kind === "bad" && <div className="notice bad" role="alert">{msg.text}</div>}
        {msg.kind === "ok" && <div className="notice" role="status">{msg.text}</div>}
        <button className="btn ghost" style={{ justifySelf: "start" }} onClick={logout}>Sign out</button>
      </div>
    </div>
  );
}
