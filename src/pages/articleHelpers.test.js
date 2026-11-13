// Knowledge articles: staff create, edit and delete; customers only read.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  EDITING, VIEWING, articleErrorMessage, articleFormReducer, articlePayload, articleProblems, articleSaveLabel, articleToForm,
  audienceLabel, canManageArticles, deleteArticle, deleteConfirmText, emptyArticle, initialArticleForm, parseTags, submitArticle,
} from "./articleHelpers.js";
import { inFlightGuard } from "./workflowHelpers.js";

const src = (f) => readFileSync(new URL(f, import.meta.url), "utf8");
const event = () => ({ prevented: 0, preventDefault() { this.prevented += 1; } });
/** Collects what the code under test dispatches / calls. */
const recorder = () => { const calls = []; const fn = (...args) => { calls.push(args.length === 1 ? args[0] : args); }; fn.calls = calls; return fn; };

test("only staff manage articles", () => {
  assert.equal(canManageArticles({ is_agent: true }), true);
  assert.equal(canManageArticles({ is_agent: false }), false);
  assert.equal(canManageArticles(null), false);
});

test("a new article starts as a draft for customers and agents", () => {
  assert.deepEqual(emptyArticle(), { title: "", body: "", visibility: "public", status: "draft", tags: "" });
});

test("title and text are required", () => {
  assert.deepEqual(Object.keys(articleProblems({ title: " ", body: "" })).sort(), ["body", "title"]);
  assert.deepEqual(Object.keys(articleProblems({})).sort(), ["body", "title"]);
  assert.deepEqual(articleProblems({ title: "Reset VPN", body: "Open the client" }), {});
});

test("form values and request body round-trip, with tidy tags", () => {
  const a = { title: "Reset VPN", body: "Steps", visibility: "internal", status: "published", tags: ["vpn", "net"] };
  assert.equal(articleToForm(a).tags, "vpn, net");
  assert.deepEqual(articleToForm({}), { title: "", body: "", visibility: "public", status: "draft", tags: "" }, "missing values fall back");
  assert.deepEqual(articlePayload({ ...articleToForm(a), title: "  Reset the VPN  ", tags: "vpn, net, vpn, ,wifi" }),
    { title: "Reset the VPN", body: "Steps", visibility: "internal", status: "published", tags: ["vpn", "net", "wifi"] });
  assert.deepEqual(parseTags(""), []);
  assert.deepEqual(parseTags(undefined), []);
});

test("who an article is for", () => {
  assert.equal(audienceLabel({ status: "published", visibility: "public" }), "Customers and agents");
  assert.equal(audienceLabel({ status: "published", visibility: "internal" }), "Agents only");
  assert.equal(audienceLabel({ status: "draft", visibility: "public" }), "draft");
  assert.equal(audienceLabel({ status: "retired", visibility: "internal" }), "retired");
});

test("the save button label", () => {
  assert.equal(articleSaveLabel({ busy: true, editing: false }), "Saving...");
  assert.equal(articleSaveLabel({ busy: false, editing: true }), "Save changes");
  assert.equal(articleSaveLabel({ busy: false, editing: false }), "Create article");
});

test("delete asks first, naming the article", () => {
  assert.match(deleteConfirmText("Reset VPN"), /Delete "Reset VPN"\? This cannot be undone\./);
});

test("a failed save shows the server's message, never raw HTML", () => {
  assert.equal(articleErrorMessage({ message: "Only agents can edit articles." }, "saved"), "Only agents can edit articles.");
  assert.equal(articleErrorMessage({ message: "<html>502</html>" }, "saved"), "The article couldn't be saved.");
  assert.equal(articleErrorMessage(null, "deleted"), "The article couldn't be deleted.");
});

// ---- the form's state

test("the form starts empty for a new article and filled for an existing one", () => {
  assert.deepEqual(initialArticleForm(undefined), { form: emptyArticle(), errors: {}, formError: "", busy: false });
  const existing = { id: 3, title: "Reset VPN", body: "Steps", visibility: "internal", status: "published", tags: ["vpn"] };
  assert.equal(initialArticleForm(existing).form.title, "Reset VPN");
  assert.equal(initialArticleForm(existing).form.tags, "vpn");
});

test("typing in a field keeps the others and clears only that field's message", () => {
  const before = { ...initialArticleForm(), errors: { title: "Give the article a title.", body: "Write the article text." } };
  const after = articleFormReducer(before, { type: "field", key: "title", value: "Reset VPN" });
  assert.equal(after.form.title, "Reset VPN");
  assert.equal(after.form.status, "draft");
  assert.deepEqual(after.errors, { title: "", body: "Write the article text." });
  assert.notEqual(after, before, "state is replaced, never changed in place");
  assert.equal(before.form.title, "");
});

test("the form shows what is wrong, then that it is saving, then why a save failed", () => {
  const start = { ...initialArticleForm(), formError: "old message" };
  const invalid = articleFormReducer(start, { type: "invalid", errors: { title: "Give the article a title." } });
  assert.deepEqual([invalid.errors, invalid.formError, invalid.busy], [{ title: "Give the article a title." }, "", false]);
  const saving = articleFormReducer(invalid, { type: "saving" });
  assert.deepEqual([saving.errors, saving.formError, saving.busy], [{}, "", true]);
  const failed = articleFormReducer(saving, { type: "failed", message: "Only agents can edit articles." });
  assert.deepEqual([failed.formError, failed.busy], ["Only agents can edit articles.", false]);
  assert.equal(articleFormReducer(failed, { type: "something else" }), failed, "an unknown action changes nothing");
});

// ---- submitting the form

const filled = { title: "Reset VPN", body: "Steps", visibility: "public", status: "draft", tags: "vpn" };

test("an incomplete form is refused without calling the server", async () => {
  const dispatch = recorder();
  const api = recorder();
  const e = event();
  await submitArticle({ event: e, api, article: undefined, form: emptyArticle(), dispatch, guard: inFlightGuard(), onSaved: recorder() });
  assert.equal(e.prevented, 1);
  assert.deepEqual(dispatch.calls, [{ type: "invalid", errors: { title: "Give the article a title.", body: "Write the article text." } }]);
  assert.deepEqual(api.calls, []);
});

test("a new article is POSTed and the stored article handed on", async () => {
  const dispatch = recorder();
  const onSaved = recorder();
  const guard = inFlightGuard();
  let seen;
  const api = async (path, options) => { seen = { path, ...options }; return { id: 9, title: "Reset VPN" }; };
  await submitArticle({ event: event(), api, article: undefined, form: filled, dispatch, guard, onSaved });
  assert.deepEqual(seen, { path: "/kb/articles/", method: "POST", body: { title: "Reset VPN", body: "Steps", visibility: "public", status: "draft", tags: ["vpn"] } });
  assert.deepEqual(dispatch.calls, [{ type: "saving" }]);
  assert.deepEqual(onSaved.calls, [{ id: 9, title: "Reset VPN" }]);
  assert.equal(guard.busy, false, "the guard is released for the next save");
});

test("an existing article is PATCHed at its own address", async () => {
  let seen;
  const api = async (path, options) => { seen = { path, method: options.method }; return { id: 4 }; };
  await submitArticle({ event: event(), api, article: { id: 4 }, form: filled, dispatch: recorder(), guard: inFlightGuard(), onSaved: recorder() });
  assert.deepEqual(seen, { path: "/kb/articles/4/", method: "PATCH" });
});

test("a refused save is shown on the form and says which action failed", async () => {
  const refuse = async () => { throw { message: "<html>502 Bad Gateway</html>" }; };
  for (const [article, text] of [[undefined, "The article couldn't be created."], [{ id: 4 }, "The article couldn't be saved."]]) {
    const dispatch = recorder();
    const onSaved = recorder();
    const guard = inFlightGuard();
    await submitArticle({ event: event(), api: refuse, article, form: filled, dispatch, guard, onSaved });
    assert.deepEqual(dispatch.calls, [{ type: "saving" }, { type: "failed", message: text }]);
    assert.deepEqual(onSaved.calls, []);
    assert.equal(guard.busy, false);
  }
});

test("a second submit while one is in flight does nothing", async () => {
  const guard = inFlightGuard();
  guard.enter(); // a save is already running
  const dispatch = recorder();
  const api = recorder();
  await submitArticle({ event: event(), api, article: undefined, form: filled, dispatch, guard, onSaved: recorder() });
  assert.deepEqual(dispatch.calls, []);
  assert.deepEqual(api.calls, []);
  assert.equal(guard.busy, true, "the running save still holds the guard");
});

// ---- deleting

test("the article view is reading, editing or deleting", () => {
  assert.deepEqual(VIEWING, { editing: false, deleting: false, error: "" });
  assert.deepEqual(EDITING, { editing: true, deleting: false, error: "" });
  assert.ok(Object.isFrozen(VIEWING) && Object.isFrozen(EDITING));
});

test("saying no to the question deletes nothing", async () => {
  const api = recorder();
  const setView = recorder();
  const asked = [];
  await deleteArticle({ api, article: { id: 4, title: "Reset VPN" }, confirm: (text) => { asked.push(text); return false; }, setView, onDeleted: recorder() });
  assert.deepEqual(asked, ['Delete "Reset VPN"? This cannot be undone.']);
  assert.deepEqual([api.calls, setView.calls], [[], []]);
});

test("a confirmed delete shows it is in progress, calls the server and then leaves", async () => {
  const setView = recorder();
  const onDeleted = recorder();
  let seen;
  const api = async (path, options) => { seen = { path, method: options.method }; return null; };
  await deleteArticle({ api, article: { id: 4, title: "Reset VPN" }, confirm: () => true, setView, onDeleted });
  assert.deepEqual(seen, { path: "/kb/articles/4/", method: "DELETE" });
  assert.deepEqual(setView.calls, [{ editing: false, deleting: true, error: "" }]);
  assert.equal(onDeleted.calls.length, 1);
});

test("a refused delete stays on the article and says why", async () => {
  const setView = recorder();
  const onDeleted = recorder();
  const api = async () => { throw { message: "Only agents can edit articles." }; };
  await deleteArticle({ api, article: { id: 4, title: "Reset VPN" }, confirm: () => true, setView, onDeleted });
  assert.deepEqual(setView.calls.at(-1), { editing: false, deleting: false, error: "Only agents can edit articles." });
  assert.equal(onDeleted.calls.length, 0);
});

test("the Knowledge page wires New article, Edit and Delete for staff only, through the helpers", () => {
  const page = src("./Knowledge.jsx");
  assert.match(page, /canManageArticles\(user\) && <button className="btn primary" onClick=\{\(\) => navigate\("\/kb\/new"\)\}>New article/);
  assert.match(page, /id === "new" && canManageArticles\(user\)/);
  assert.match(page, /submitArticle\(\{ event, api, article, form, dispatch, guard: guard\.current, onSaved \}\)/);
  assert.match(page, /deleteArticle\(\{ api, article: a, confirm: \(text\) => globalThis\.confirm\(text\), setView, onDeleted: \(\) => navigate\("\/kb"\) \}\)/);
  assert.match(page, /canManageArticles\(user\) && <>\s*<button className="btn sm" onClick=\{\(\) => setView\(EDITING\)\}/);
  assert.match(page, /<Article key=\{id\} id=\{id\} \/>/, "each article gets a fresh view, so an open editor never carries over");
});
