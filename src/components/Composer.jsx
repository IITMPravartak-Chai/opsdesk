import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import SafeMarkdown from "./SafeMarkdown.js";
import { cx } from "../util.js";
import { inFlightGuard } from "../pages/workflowHelpers.js";

export default function Composer({ t, onSent }) {
  const { user } = useApp();
  const [body, setBody] = useState("");
  const [kind, setKind] = useState("public");
  const [tab, setTab] = useState("write"); // 'write' or 'preview'
  const [files, setFiles] = useState([]);
  const [canned, setCanned] = useState([]);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const fileRef = useRef(null);
  const textareaRef = useRef(null);
  // The Send button is disabled while sending, but Ctrl/Cmd+Enter calls send() directly;
  // this guard stops a second (or key-repeat) submission while the first is in flight.
  const sendGuard = useRef(inFlightGuard());

  useEffect(() => {
    if (user.is_agent) {
      api("/canned-responses/")
        .then((d) => setCanned(d.results || d))
        .catch(() => {});
    }
  }, [user.is_agent]);

  const insertCanned = async (cid) => {
    if (!cid) return;
    try {
      const r = await api(`/canned-responses/${cid}/render/?ticket=${t.id}`);
      setBody((b) => (b ? `${b}\n\n${r.body}` : r.body));
    } catch (e) {
      setErr(e.message);
    }
  };

  const draft = async () => {
    setBusy("draft");
    try {
      const r = await api(`/tickets/${t.id}/ai_draft/`, { method: "POST" });
      setBody(r.draft);
      setKind("public");
      api("/ai/feedback/", {
        method: "POST",
        body: { ticket_id: t.id, feature: "draft", accepted: true, suggestion: { sources: r.sources } },
      }).catch(() => {});
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy("");
    }
  };

  const applyFormatting = (prefix, suffix = "", defaultText = "text") => {
    const el = textareaRef.current;
    if (!el) {
      setBody((b) => `${b}${prefix}${defaultText}${suffix}`);
      return;
    }
    const start = el.selectionStart;
    const end = el.selectionEnd;
    const selected = body.substring(start, end) || defaultText;
    const replacement = `${prefix}${selected}${suffix}`;
    const nextBody = body.substring(0, start) + replacement + body.substring(end);
    setBody(nextBody);
    setTimeout(() => {
      el.focus();
      el.setSelectionRange(start + prefix.length, start + prefix.length + selected.length);
    }, 10);
  };

  const send = async () => {
    if (!body.trim() && !files.length) {
      setErr("Write a message or attach a file.");
      return;
    }
    if (!sendGuard.current.enter()) return;
    setBusy("send");
    setErr("");
    const form = new FormData();
    form.append("body", body);
    form.append("kind", kind);
    files.forEach((f) => form.append("files", f));
    try {
      const r = await api(`/tickets/${t.id}/interactions/`, { method: "POST", form });
      if (r.attachment_errors?.length) setErr(r.attachment_errors.join("; "));
      setBody("");
      setFiles([]);
      setTab("write");
      await onSent?.();
    } catch (e) {
      setErr(e.message);
    } finally {
      sendGuard.current.exit();
      setBusy("");
    }
  };

  return (
    <div className={cx("composer", kind === "internal" && "internal")}>
      <div className="composer-topbar">
        <div className="composer-md-tools">
          <button
            type="button"
            className="composer-md-btn"
            title="Bold (**text**)"
            onClick={() => applyFormatting("**", "**", "bold")}
          >
            B
          </button>
          <button
            type="button"
            className="composer-md-btn"
            title="Italic (*text*)"
            style={{ fontStyle: "italic" }}
            onClick={() => applyFormatting("*", "*", "italic")}
          >
            I
          </button>
          <button
            type="button"
            className="composer-md-btn"
            title="Inline Code (`code`)"
            style={{ fontFamily: "monospace" }}
            onClick={() => applyFormatting("`", "`", "code")}
          >
            &lt;/&gt;
          </button>
          <button
            type="button"
            className="composer-md-btn"
            title="Code Block"
            onClick={() => applyFormatting("\n```\n", "\n```\n", "code block")}
          >
            {`{ }`}
          </button>
          <button
            type="button"
            className="composer-md-btn"
            title="Bulleted List"
            onClick={() => applyFormatting("\n- ", "", "list item")}
          >
            •—
          </button>
          <button
            type="button"
            className="composer-md-btn"
            title="Numbered List"
            onClick={() => applyFormatting("\n1. ", "", "numbered item")}
          >
            1.
          </button>
          <button
            type="button"
            className="composer-md-btn"
            title="Blockquote"
            onClick={() => applyFormatting("\n> ", "", "quote")}
          >
            ”
          </button>
          <button
            type="button"
            className="composer-md-btn"
            title="Insert Link"
            onClick={() => applyFormatting("[", "](https://example.com)", "link title")}
          >
            🔗
          </button>
        </div>

        <div className="composer-tabs">
          <button
            type="button"
            className={cx("composer-tab", tab === "write" && "active")}
            onClick={() => setTab("write")}
          >
            Write
          </button>
          <button
            type="button"
            className={cx("composer-tab", tab === "preview" && "active")}
            onClick={() => setTab("preview")}
          >
            Preview
          </button>
        </div>
      </div>

      {tab === "write" ? (
        <>
          <label className="sr-only" htmlFor="reply-composer">Message</label>
          <textarea
            ref={textareaRef}
            id="reply-composer"
            value={body}
            onChange={(e) => {
              setBody(e.target.value);
              setErr("");
            }}
            placeholder={
              kind === "internal"
                ? "Internal note (visible only to agents & staff)... Markdown supported."
                : user.is_agent
                ? "Reply to the requester... Markdown supported."
                : "Add a comment or details... Markdown supported."
            }
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                e.preventDefault();
                send();
              }
            }}
          />
        </>
      ) : (
        <div className="composer-preview">
          {body.trim() ? (
            <SafeMarkdown content={body} />
          ) : (
            <span style={{ color: "var(--ink-3)", fontStyle: "italic" }}>
              Nothing to preview. Switch to "Write" to compose a message.
            </span>
          )}
        </div>
      )}

      <div className="composer-bar">
        {user.is_agent && (
          <div className="seg" role="group" aria-label="Message type">
            <button
              type="button"
              className={kind === "public" ? "on" : ""}
              aria-pressed={kind === "public"}
              onClick={() => setKind("public")}
            >
              Public Reply
            </button>
            <button
              type="button"
              className={kind === "internal" ? "on" : ""}
              aria-pressed={kind === "internal"}
              onClick={() => setKind("internal")}
            >
              Internal Note
            </button>
          </div>
        )}

        {user.is_agent && canned.length > 0 && (
          <select
            className="input"
            style={{ width: "auto", minHeight: 28, padding: "2px 8px" }}
            value=""
            onChange={(e) => insertCanned(e.target.value)}
            aria-label="Insert canned response"
          >
            <option value="">Insert response...</option>
            {canned.map((c) => (
              <option key={c.id} value={c.id}>{c.title}</option>
            ))}
          </select>
        )}

        {user.is_agent && (
          <button
            type="button"
            className="btn ghost sm"
            onClick={draft}
            disabled={busy === "draft"}
          >
            {busy === "draft" ? "Drafting..." : "Draft with AI"}
          </button>
        )}

        <button
          type="button"
          className="btn ghost sm"
          onClick={() => fileRef.current?.click()}
        >
          Attach{files.length ? ` (${files.length})` : ""}
        </button>
        <input
          ref={fileRef}
          type="file"
          multiple
          hidden
          onChange={(e) => setFiles([...e.target.files])}
        />

        <span className="spacer" />

        {err && <span className="error" role="alert" style={{ fontSize: 12 }}>{err}</span>}

        <button
          type="button"
          className="btn primary sm"
          onClick={send}
          disabled={busy === "send"}
        >
          {busy === "send"
            ? "Sending..."
            : kind === "internal"
            ? "Add note"
            : user.is_agent
            ? "Send reply"
            : "Send"}
        </button>
      </div>
    </div>
  );
}
