// Raising a ticket: validation, the request body, the file upload that follows it, and the button label.
// Pure functions (the upload takes the api client as an argument) so `node --test` covers them.
import { uploadErrorMessage } from "./attachmentHelpers.js";

const OPTIONAL_NUMBERS = ["account", "ci", "category", "impact", "urgency", "team"];

/** Field messages for a form that cannot be sent yet; an empty object means it can. */
export function ticketProblems(form, type, custom) {
  const errors = {};
  if (!form.subject.trim()) errors.subject = "Add a short summary of the problem or request.";
  for (const field of type?.custom_fields || []) {
    if (field.required && !custom[field.key]) errors[field.key] = `${field.label} is required.`;
  }
  return errors;
}

/** The POST /tickets/ body: chosen references are sent as numbers, empty ones are left out. */
export function ticketBody({ form, type, custom, mobile }) {
  const body = {
    ticket_type: type.id, subject: form.subject.trim(), description: form.description, custom_fields: custom,
    source: mobile ? "mobile" : "portal",
  };
  for (const key of OPTIONAL_NUMBERS) if (form[key]) body[key] = Number(form[key]);
  return body;
}

/** The server's field errors as one message per field, or a single form-level message. */
export function createErrors(error) {
  if (error?.data && typeof error.data === "object") {
    return Object.fromEntries(Object.entries(error.data).map(([key, value]) => [key, [].concat(value).join(" ")]));
  }
  return { form: error?.message };
}

/**
 * Upload the chosen files to a ticket that now exists. Resolves to "" when every file was stored, otherwise to the
 * reason, so the person is told instead of the failure being dropped.
 */
export async function uploadAttachments(api, ticketId, files, limits) {
  const form = new FormData();
  for (const file of files) form.append("files", file);
  try {
    const result = await api(`/tickets/${ticketId}/attachments/`, { method: "POST", form });
    return result?.errors?.length ? result.errors.join("; ") : "";
  } catch (error_) {
    return uploadErrorMessage(error_, limits);
  }
}

/** Text on the submit button. */
export function submitLabel({ busy, isAgent }) {
  if (busy) return "Submitting...";
  return isAgent ? "Create ticket" : "Submit request";
}

/** Server errors for fields the form has no input of its own for (shown under the form). */
export function otherErrors(errors, type) {
  const own = new Set(["subject", ...(type?.custom_fields || []).map((field) => field.key)]);
  return Object.entries(errors).filter(([key]) => !own.has(key));
}

/**
 * Create the ticket, then upload its files. Resolves to the ticket and, when not every file was stored, the reason
 * (the ticket exists by then, so a refused upload is reported instead of being dropped). A refused ticket throws.
 */
export async function raiseTicket(api, { form, type, custom, mobile, files, limits }) {
  const ticket = await api("/tickets/", { method: "POST", body: ticketBody({ form, type, custom, mobile }) });
  const uploadProblem = files.length > 0 ? await uploadAttachments(api, ticket.id, files, limits) : "";
  return { ticket, uploadProblem };
}
