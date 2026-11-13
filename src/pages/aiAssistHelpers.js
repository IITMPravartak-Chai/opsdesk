// AI assistance on the ticket page: the summary, the triage suggestion and the draft reply.
// Pure functions (recording takes the api client as an argument), so `node --test` covers them.

/** Shown under a summary that came from the built-in template because no language model is configured (AI_LLM_PROVIDER). */
export const TEMPLATE_SUMMARY_NOTE = "Basic summary from the ticket's own fields. No AI model is configured, so this was not written by one.";

/** Shown under the reply box when the draft came from the built-in template. */
export const TEMPLATE_DRAFT_NOTE = "Template draft: no AI model is configured, so this was not written by one. Edit it before sending.";

// ---- summary: its text and where it came from ("ai", "template", or "" when it was stored earlier and is not known)

export const storedSummary = (ticket) => ({ text: ticket.ai?.summary || "", method: "" });

export const summaryFrom = (response) => ({ text: response.summary, method: response.method || "" });

/** Text on the summary button: working, refresh an existing summary, or make the first one. */
export function summaryButtonLabel({ busy, summary }) {
  if (busy === "summary") return "Summarising...";
  return summary.text ? "Refresh" : "Summarise";
}

/** The note to show under the summary, or "" when it needs none. */
export const summaryNote = (summary) => (summary.text && summary.method === "template" ? TEMPLATE_SUMMARY_NOTE : "");

// ---- triage

/** What to say in the triage section when there is no change to offer, so it is never just empty. */
export function noSuggestionText(triage) {
  return triage?.basis
    ? "No change suggested: the category, team, impact and urgency already match similar tickets."
    : "No suggestion yet. Suggestions appear once there are similar tickets that already have a category or team.";
}

// ---- draft reply

/** The draft now in the reply box: its text as generated, what it was based on, and where it came from. */
export const draftFrom = (response) => ({ text: response.draft, sources: response.sources || [], method: response.method || "" });

/** The note to show under the reply box, or "" when the draft needs none (or there is no draft). */
export const draftNote = (draft) => (draft?.method === "template" ? TEMPLATE_DRAFT_NOTE : "");

/**
 * A draft counts as accepted only when the reply is actually sent, so this runs after a successful send. It records the
 * use, with whether the agent edited the draft first. An internal note is not a reply to the customer and records
 * nothing. Best effort: failing to record never affects the reply that was sent.
 */
export function recordDraftUse(api, ticketId, draft, { kind, body }) {
  if (!draft || kind !== "public") return;
  const suggestion = { sources: draft.sources, edited: body.trim() !== draft.text.trim() };
  api("/ai/feedback/", { method: "POST", body: { ticket_id: ticketId, feature: "draft", accepted: true, suggestion } }).catch(() => {});
}
