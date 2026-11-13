// Knowledge article create / edit / delete (staff). Framework-free so `node --test` covers it.
// The API already enforces staff-only writes (api/routers/kb.py); these helpers keep the form honest.

export const VISIBILITY = [["public", "Customers and agents"], ["internal", "Agents only"]];
export const STATUSES = [["published", "Published"], ["draft", "Draft"], ["retired", "Retired"]];

export const emptyArticle = () => ({ title: "", body: "", visibility: "public", status: "draft", tags: "" });

/** Form values for an existing article. */
export const articleToForm = (a) => ({
  title: a.title || "", body: a.body || "", visibility: a.visibility || "public", status: a.status || "draft",
  tags: (a.tags || []).join(", "),
});

export const parseTags = (text) => [...new Set((text || "").split(",").map((t) => t.trim()).filter(Boolean))];

/** Field messages for a form that cannot be saved yet; an empty object means it can. */
export function articleProblems(form) {
  const out = {};
  if (!(form.title || "").trim()) out.title = "Give the article a title.";
  if (!(form.body || "").trim()) out.body = "Write the article text.";
  return out;
}

/** Request body for POST / PATCH. */
export const articlePayload = (form) => ({
  title: form.title.trim(), body: form.body, visibility: form.visibility, status: form.status, tags: parseTags(form.tags),
});

/** Who an article is for, as staff see it in the list and on the article. */
export function audienceLabel(article) {
  if (article.status !== "published") return article.status;
  return article.visibility === "public" ? "Customers and agents" : "Agents only";
}

/** Text on the save button. */
export function articleSaveLabel({ busy, editing }) {
  if (busy) return "Saving...";
  return editing ? "Save changes" : "Create article";
}

export const canManageArticles = (user) => Boolean(user?.is_agent);

export const deleteConfirmText = (title) => `Delete "${title}"? This cannot be undone.`;

/** The server's message when it has one, else a plain fallback. */
export const articleErrorMessage = (err, action) =>
  err?.message && !/<\/?[a-z][^>]*>/i.test(err.message) ? err.message : `The article couldn't be ${action}.`;

// ---- the article form (new and edit): its state lives in a reducer so every transition is a plain, tested function

/** State of the form: the fields, a message per field, a message for the whole form, and whether a save is running. */
export const initialArticleForm = (article) => ({
  form: article ? articleToForm(article) : emptyArticle(), errors: {}, formError: "", busy: false,
});

export function articleFormReducer(state, action) {
  switch (action.type) {
    case "field": // typing in a field clears that field's message
      return { ...state, form: { ...state.form, [action.key]: action.value }, errors: { ...state.errors, [action.key]: "" } };
    case "invalid":
      return { ...state, errors: action.errors, formError: "" };
    case "saving":
      return { ...state, errors: {}, formError: "", busy: true };
    case "failed":
      return { ...state, formError: action.message, busy: false };
    default:
      return state;
  }
}

/**
 * Submit the form: check it, then POST a new article or PATCH an existing one, and hand the stored article to
 * `onSaved`. `guard` (see inFlightGuard) stops a second submit while one is in flight. A refusal is shown on the form;
 * nothing is thrown.
 */
export async function submitArticle({ event, api, article, form, dispatch, guard, onSaved }) {
  event.preventDefault();
  const errors = articleProblems(form);
  if (Object.keys(errors).length > 0) {
    dispatch({ type: "invalid", errors });
    return;
  }
  if (!guard.enter()) return;
  dispatch({ type: "saving" });
  try {
    const path = article ? `/kb/articles/${article.id}/` : "/kb/articles/";
    onSaved(await api(path, { method: article ? "PATCH" : "POST", body: articlePayload(form) }));
  } catch (error_) {
    dispatch({ type: "failed", message: articleErrorMessage(error_, article ? "saved" : "created") });
  } finally {
    guard.exit();
  }
}

// ---- one article on screen: being read, being edited, or being deleted

export const VIEWING = Object.freeze({ editing: false, deleting: false, error: "" });
export const EDITING = Object.freeze({ editing: true, deleting: false, error: "" });

/**
 * Ask first, then delete. `setView` shows the delete in progress and, if the server refuses, why; `onDeleted` runs once
 * the article is gone. Saying no to the question changes nothing.
 */
export async function deleteArticle({ api, article, confirm, setView, onDeleted }) {
  if (!confirm(deleteConfirmText(article.title))) return;
  setView({ ...VIEWING, deleting: true });
  try {
    await api(`/kb/articles/${article.id}/`, { method: "DELETE" });
    onDeleted();
  } catch (error_) {
    setView({ ...VIEWING, error: articleErrorMessage(error_, "deleted") });
  }
}
