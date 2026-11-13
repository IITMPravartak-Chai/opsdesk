import React, { useState } from "react";
import { api, logout, updateUser } from "../api.js";
import { useApp } from "../App.jsx";
import { isSixDigitCode, mfaErrorMessage, twoStepButtonLabel } from "./profileHelpers.js";

export default function Profile() {
  const { user } = useApp();
  const [setup, setSetup] = useState(null); // { turnOn, sentTo } while a confirmation code is awaited
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState({ kind: "", text: "" });

  const turnOn = !user.mfa_enabled; // the change on offer: on when it is off, off when it is on
  const url = (on) => (on ? "/auth/mfa/" : "/auth/mfa/disable/");

  // Step 1: the server emails a confirmation code to the account's own address.
  const start = async () => {
    setBusy("start");
    setMsg({ kind: "", text: "" });
    try {
      const res = await api(url(turnOn), { method: "POST", body: {} });
      setSetup({ turnOn, sentTo: res?.sent_to || "your email" });
      setCode("");
    } catch (e) {
      if (e.status === 409) updateUser({ ...user, mfa_enabled: turnOn });
      setMsg({ kind: "bad", text: mfaErrorMessage(e, "start", turnOn) });
    } finally {
      setBusy("");
    }
  };
  // Step 2: the code makes the change. Nothing is shown as changed until the server says so.
  const confirm = async () => {
    setBusy("confirm");
    setMsg({ kind: "", text: "" });
    try {
      const res = await api(url(setup.turnOn), { method: "POST", body: { code: code.trim() } });
      if (res?.mfa_enabled !== setup.turnOn) throw new Error("not changed");
      updateUser({ ...user, mfa_enabled: setup.turnOn });
      setSetup(null);
      setMsg({ kind: "ok", text: setup.turnOn
        ? "Two-step sign-in is on. Each time you sign in, we'll email you a code to enter."
        : "Two-step sign-in is off. You'll sign in with your password only." });
    } catch (e) {
      setMsg({ kind: "bad", text: mfaErrorMessage(e, "confirm", setup.turnOn) });
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
        {!setup && (
          <>
            <p className="small" style={{ margin: 0 }}>
              {turnOn
                ? "With two-step sign-in, we email a 6-digit code to your address each time you sign in, and you enter it after your password."
                : "Each time you sign in, we email a 6-digit code to your address."}
            </p>
            <button className="btn" style={{ justifySelf: "start" }} onClick={start} disabled={busy !== ""}>
              {twoStepButtonLabel({ busy, turnOn })}
            </button>
          </>
        )}
        {setup && (
          <div className="stack">
            <p className="small" style={{ margin: 0 }} id="mfa-help">
              We emailed a 6-digit code to {setup.sentTo}. Enter it to turn two-step sign-in {setup.turnOn ? "on" : "off"}. It stays {setup.turnOn ? "off" : "on"} until the code is confirmed.
            </p>
            <div className="row">
              <input className="input" style={{ width: 140 }} inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code}
                onChange={(e) => setCode(e.target.value)} aria-label="Code from your email" aria-describedby="mfa-help" />
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
