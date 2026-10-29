/**
 * Core agent workflow helpers (U3, UI-DEC-014).
 * Request ordering, duplicate-submit guards and honest error wording for the queue,
 * ticket detail, composer and knowledge pages. No API contract is changed here: callers
 * pass the existing `api` client and the same paths, methods and bodies as before.
 */

/** Only the most recent request may update the screen; earlier responses are dropped. */
export function latestOnly() {
  let seq = 0;
  return {
    next: () => ++seq,
    isCurrent: (token) => token === seq,
  };
}

/** One-at-a-time guard for actions that must not run twice concurrently. */
export function inFlightGuard() {
  let busy = false;
  return {
    enter() {
      if (busy) return false;
      busy = true;
      return true;
    },
    exit() { busy = false; },
    get busy() { return busy; },
  };
}

/** The server has no route for this request (gateway/endpoint not available), so nothing ran. */
export const endpointUnavailable = (err) => [404, 405, 501].includes(err?.status);

const SAVED_QUEUE_ACTIONS = { load: "load saved queues", save: "save this queue", delete: "delete this saved queue" };

export function savedQueueErrorMessage(action, err) {
  if (endpointUnavailable(err)) return "Saved queues aren't available on this server yet. Your current filters still work.";
  return `Couldn't ${SAVED_QUEUE_ACTIONS[action] || action}: ${err?.message || "request failed"}.`;
}

export function bulkErrorMessage(err) {
  if (endpointUnavailable(err)) return "Bulk actions aren't available on this server yet. No tickets were changed.";
  return `Bulk action failed: ${err?.message || "request failed"}. Some tickets may not have been updated; refresh to check.`;
}

/** The people who can be picked once a team is chosen: that team's members, in the order of the staff list. */
export function teamEmployees(teams, agents, teamId) {
  const team = (teams || []).find((t) => String(t.id) === String(teamId));
  if (!team) return [];
  const ids = new Set(team.member_ids || []);
  return (agents || []).filter((a) => ids.has(a.id));
}

/** Summary of a bulk action: how many tickets changed and, for the ones that didn't, why (the server's reasons). */
export function bulkResultMessage(res) {
  const ok = res?.succeeded || 0;
  const bad = res?.failed || 0;
  let msg = `Bulk update: ${ok} ticket${ok === 1 ? "" : "s"} updated.`;
  if (bad > 0) {
    const reasons = [...new Set(Object.values(res.results || {}).filter((r) => r && r.success === false && r.error).map((r) => r.error))];
    msg += ` ${bad} couldn't be updated${reasons.length ? `: ${reasons.slice(0, 3).join("; ")}${reasons.length > 3 ? "; …" : ""}` : "."}`;
  }
  return msg;
}

export function queueEmptyMessage({ search, filtered, isAgent }) {
  if (search) return "No tickets match that search.";
  if (filtered) return "No tickets match these filters.";
  return isAgent ? "This queue is clear." : "You have no requests in this list.";
}

/** Interactions may arrive paginated ({count, results}, newest first) or as a plain list (oldest first). */
export function normalizeInteractions(m) {
  if (m && Array.isArray(m.results)) return { items: [...m.results].reverse(), total: m.count ?? m.results.length };
  const items = Array.isArray(m) ? m : [];
  return { items, total: items.length };
}

/**
 * Run a workflow transition once. A failure of the follow-up conversation refresh is
 * reported separately (`refreshError`), so it is never presented as a failed transition.
 */
export async function runTicketTransition(apiClient, ticketId, transitionId, extra, { guard, reloadMessages }) {
  if (guard && !guard.enter()) return { skipped: true };
  let ticket;
  try {
    ticket = await apiClient(`/tickets/${ticketId}/transition/`, { method: "POST", body: { transition_id: transitionId, ...extra } });
  } catch (error) {
    guard?.exit();
    return { ok: false, error };
  }
  guard?.exit();
  let refreshError = null;
  try {
    await reloadMessages?.();
  } catch (e) {
    refreshError = e;
  }
  return { ok: true, ticket, refreshError };
}

/** Submit an article vote once; success is reported only after the server accepts it. */
export async function submitArticleVote(apiClient, articleId, helpful, { guard, onDone = () => {}, onError = () => {} } = {}) {
  if (guard && !guard.enter()) return { skipped: true };
  try {
    await apiClient(`/kb/articles/${articleId}/vote/`, { method: "POST", body: { helpful } });
    onDone();
    return { ok: true };
  } catch (err) {
    onError(`Your feedback wasn't recorded: ${err?.message || "request failed"}. Try again.`);
    return { ok: false, error: err };
  } finally {
    guard?.exit();
  }
}
