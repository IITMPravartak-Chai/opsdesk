/**
 * Ticket-to-Knowledge-Base promotion helpers.
 * Manages authorization rules, eligibility checks, duplicate-click prevention,
 * API dispatch, and navigation for converting resolved/closed tickets into KB drafts.
 */

export function canPromoteTicketToKb(user, ticket) {
  if (!user || !user.is_agent) return false;
  if (!ticket || !ticket.state) return false;
  const cat = ticket.state.category;
  return cat === "resolved" || cat === "closed";
}

export async function createTicketKbDraft(apiClient, ticketId, options = {}) {
  const {
    isBusy = false,
    setBusy = () => {},
    onFeedback = () => {},
    onError = () => {},
    onNavigate = () => {},
    navigateDelayMs = 0,
  } = options;

  if (isBusy) {
    return { skipped: true, reason: "busy" };
  }

  setBusy(true);
  try {
    const art = await apiClient("/kb/articles/from_ticket/", {
      method: "POST",
      body: { ticket_id: ticketId },
    });

    let message = "Draft created from template. Opening...";
    if (art?.duplicate) {
      message = "Draft already exists for this ticket. Opening...";
    } else if (art?.generation_method === "ai") {
      message = "AI draft generated. Opening...";
    }
    onFeedback(message);

    if (art?.id) {
      if (navigateDelayMs > 0) {
        setTimeout(() => onNavigate(`/kb/${art.id}`), navigateDelayMs);
      } else {
        onNavigate(`/kb/${art.id}`);
      }
    }

    return { success: true, article: art };
  } catch (err) {
    const msg = err?.message || "Failed to create knowledge base draft";
    onError(msg);
    return { success: false, error: msg };
  } finally {
    setBusy(false);
  }
}
