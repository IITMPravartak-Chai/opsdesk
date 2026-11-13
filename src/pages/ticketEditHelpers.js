// Editing a ticket's subject and description from the ticket page. Framework-free so `node --test` covers it.
// The server enforces the same rules (api/routers/tickets.py update_ticket): staff may edit any ticket they can see;
// a customer only a ticket they raised themselves, and only while it is open.

/** Whether the Edit button is offered. */
export function canEditTicket(user, ticket) {
  if (!user || !ticket) return false;
  if (ticket.state?.is_final || ["closed", "cancelled"].includes(ticket.state?.category)) return false;
  if (user.is_agent) return true;
  return ticket.requester?.id === user.id;
}

/** A message when the edit cannot be saved as typed, otherwise "". */
export function ticketEditProblem({ subject }) {
  return (subject || "").trim() ? "" : "Add a short summary of the problem or request.";
}

/** Only the fields that changed, so an untouched form sends nothing. */
export function ticketEditChanges(ticket, { subject, description }) {
  const out = {};
  const s = (subject || "").trim();
  if (s !== (ticket.subject || "")) out.subject = s;
  if ((description || "") !== (ticket.description || "")) out.description = description || "";
  return out;
}

// ---- the editor's state lives in a reducer, so each transition is a plain, tested function

export const initialTicketEdit = (ticket) => ({
  subject: ticket.subject || "", description: ticket.description || "", error: "", busy: false,
});

export function ticketEditReducer(state, action) {
  switch (action.type) {
    case "field": // typing clears the message
      return { ...state, [action.key]: action.value, error: "" };
    case "saving":
      return { ...state, error: "", busy: true };
    case "failed":
      return { ...state, error: action.message, busy: false };
    default:
      return state;
  }
}

/**
 * Submit the editor. An empty summary is refused; with nothing changed nothing is sent and the editor just closes.
 * `save(body)` resolves to "" when the ticket was saved, otherwise to the message to show (see saveTicketEdit).
 */
export async function submitTicketEdit({ event, ticket, state, dispatch, save, close }) {
  event.preventDefault();
  const problem = ticketEditProblem(state);
  if (problem) {
    dispatch({ type: "failed", message: problem });
    return;
  }
  const body = ticketEditChanges(ticket, state);
  if (Object.keys(body).length === 0) {
    close();
    return;
  }
  dispatch({ type: "saving" });
  const message = await save(body);
  if (message) dispatch({ type: "failed", message });
}

/**
 * PATCH the ticket. Once it is saved, `changed(ticket)` refreshes the page with the server's copy and `done()` closes
 * the editor; resolves to "". If it is refused, resolves to the message to show inside the editor and changes nothing.
 */
export async function saveTicketEdit({ api, id, body, changed, done }) {
  try {
    await changed(await api(`/tickets/${id}/`, { method: "PATCH", body }));
    done();
    return "";
  } catch (error_) {
    return error_.message || "Your changes weren't saved.";
  }
}
