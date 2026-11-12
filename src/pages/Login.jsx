import React, { useState } from "react";
import { login } from "../api.js";
import { Fuse } from "../components/Fuse.jsx";

const now = Date.now();
const iso = (mins) => new Date(now + mins * 60000).toISOString();

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [otp, setOtp] = useState("");
  const [needOtp, setNeedOtp] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [busy, setBusy] = useState(false);

  // Without a code the server emails one (two-step sign-in) and answers mfa_required; with a code it checks it.
  const attempt = async (code) => {
    setBusy(true);
    setError("");
    setInfo("");
    try {
      await login(email.trim(), password, code || undefined);
    } catch (err) {
      if (err.data?.mfa_required) setNeedOtp(true);
      if (err.data?.otp_sent) setInfo(err.message);
      else setError(err.message);
    } finally {
      setBusy(false);
    }
  };
  const submit = (e) => { e.preventDefault(); attempt(needOtp ? otp.trim() : ""); };
  const resend = () => { setOtp(""); attempt(""); };

  return (
    <div className="auth-wrap">
      <section className="auth-side">
        <div className="brand" style={{ color: "inherit" }}><img src="/icon.svg" alt="" />OpsDesk</div>
        <div style={{ display: "grid", gap: 18 }}>
          <p className="auth-hero">Every request, from every channel, on the clock.</p>
          <p>Email, WhatsApp, monitoring alerts and the portal land in one queue, with SLA timers, ITIL workflows and similar past fixes beside every ticket.</p>
          <div className="auth-demo" aria-hidden="true">
            <Fuse label="Respond" start={iso(-50)} due={iso(10)} status="at_risk" />
            <Fuse label="Resolve" start={iso(-60)} due={iso(420)} status="on_track" />
          </div>
        </div>
        <small style={{ color: "#7fa6ad" }}>ITIL 4 / ITIL (Version 5) aligned practices</small>
      </section>
      <main className="auth-form">
        <form className="auth-card" onSubmit={submit}>
          <h1>Sign in</h1>
          <label className="field"><span>Work email</span>
            <input className="input" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </label>
          <label className="field"><span>Password</span>
            <input className="input" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          {needOtp && (
            <label className="field"><span>Code from your email</span>
              <input className="input" inputMode="numeric" autoComplete="one-time-code" maxLength={6} required value={otp} onChange={(e) => setOtp(e.target.value)} autoFocus />
            </label>
          )}
          {info && <div className="notice" role="status">{info}</div>}
          {error && <div className="error" role="alert">{error}</div>}
          <button className="btn primary" disabled={busy}>{busy ? "Signing in..." : "Sign in"}</button>
          {needOtp && <button type="button" className="btn ghost" onClick={resend} disabled={busy}>Send a new code</button>}
        </form>
      </main>
    </div>
  );
}
