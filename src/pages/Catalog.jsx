import React, { useEffect, useRef, useState, useMemo } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import Modal from "../components/Modal.jsx";
import {
  groupCatalogByCategory,
  validateCatalogForm,
  buildCatalogSubmissionPayload,
  catalogEmptyMessage,
  catalogFieldIds,
  catalogLoadErrorMessage,
  catalogSubmitErrorMessage,
  requestCreatedPath,
} from "./catalogHelpers.js";
import { inFlightGuard } from "./workflowHelpers.js";

export default function Catalog() {
  const { user, navigate, refreshCounts } = useApp();
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState("");
  const [activeCategory, setActiveCategory] = useState("All");

  // Modal / submission state
  const [activeItem, setActiveItem] = useState(null);
  const [formData, setFormData] = useState({});
  const [additionalNotes, setAdditionalNotes] = useState("");
  const [formErrors, setFormErrors] = useState({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  const [submitted, setSubmitted] = useState(null); // confirmation when the response has no ticket id
  const submitGuard = useRef(inFlightGuard());

  const loadCatalog = () => {
    setLoading(true);
    setError(null);
    api("/catalog/")
      .then((data) => {
        setItems(Array.isArray(data) ? data : data.results || []);
      })
      .catch((err) => {
        setError(catalogLoadErrorMessage(err)); // BLK-001: unrouted endpoint is named, not shown as "Not Found"
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadCatalog();
  }, []);

  const categories = useMemo(() => {
    const cats = new Set();
    items.forEach((item) => {
      if (item.category) cats.add(item.category);
    });
    return ["All", ...Array.from(cats)];
  }, [items]);

  const filteredItems = useMemo(() => {
    return items.filter((item) => {
      const matchesCat = activeCategory === "All" || item.category === activeCategory;
      const q = search.trim().toLowerCase();
      const matchesSearch =
        !q ||
        item.name.toLowerCase().includes(q) ||
        (item.description && item.description.toLowerCase().includes(q));
      return matchesCat && matchesSearch;
    });
  }, [items, activeCategory, search]);

  const handleOpenRequest = (item) => {
    setActiveItem(item);
    // Initialize default field values
    const initial = {};
    if (item.form_schema?.fields) {
      item.form_schema.fields.forEach((f) => {
        initial[f.name] = f.default !== undefined ? f.default : "";
      });
    }
    setFormData(initial);
    setAdditionalNotes("");
    setFormErrors({});
    setSubmitError(null);
  };

  const handleCloseModal = () => {
    if (submitting) return;
    setActiveItem(null);
    setFormData({});
    setFormErrors({});
    setSubmitError(null);
  };

  const handleFieldChange = (name, value) => {
    setFormData((prev) => ({ ...prev, [name]: value }));
    if (formErrors[name]) {
      setFormErrors((prev) => {
        const next = { ...prev };
        delete next[name];
        return next;
      });
    }
  };

  const handleSubmitRequest = async (e) => {
    if (e) e.preventDefault();
    if (!activeItem || submitting) return;

    const validation = validateCatalogForm(activeItem.form_schema, formData);
    if (!validation.isValid) {
      setFormErrors(validation.errors);
      // Move focus to the first invalid field so keyboard and screen-reader users land on the problem.
      const first = activeItem.form_schema.fields.find((f) => validation.errors[f.name]);
      if (first) requestAnimationFrame(() => document.getElementById(catalogFieldIds(first.name).input)?.focus());
      return;
    }
    if (!submitGuard.current.enter()) return;

    setSubmitting(true);
    setSubmitError(null);

    try {
      const payload = buildCatalogSubmissionPayload(activeItem, formData, additionalNotes);
      const res = await api(`/catalog/${activeItem.id}/request/`, {
        method: "POST",
        body: payload,
      });

      // Success is shown only now, after the server confirmed the request.
      const path = requestCreatedPath(res);
      const item = activeItem;
      // Close directly: handleCloseModal() ignores calls while `submitting` (stale in this closure) is true.
      setActiveItem(null);
      setFormData({});
      setFormErrors({});
      refreshCounts();
      if (path) navigate(path);
      else setSubmitted(`Your request for ${item.name} was submitted${res?.number ? ` as ${res.number}` : ""}. You'll find it under My requests.`);
    } catch (err) {
      setSubmitError(catalogSubmitErrorMessage(err)); // the dialog and answers stay as they were
    } finally {
      submitGuard.current.exit();
      setSubmitting(false);
    }
  };

  return (
    <div className="page" style={{ maxWidth: 1040, margin: "0 auto", width: "100%" }}>
      <div className="page-head">
        <div>
          <h1>Service Catalog</h1>
          <div className="muted small">
            Request internal hardware, software, cloud access, and organizational services.
          </div>
        </div>
        <div className="row">
          <input
            type="search"
            className="input"
            style={{ width: 220 }}
            placeholder="Search services..."
            aria-label="Search services"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
      </div>

      {submitted && <div className="notice" role="status" style={{ marginBottom: 16 }}>{submitted}</div>}

      {/* Category selector */}
      <div className="row" role="group" aria-label="Category" style={{ gap: 8, marginBottom: 16 }}>
        {categories.map((cat) => (
          <button
            key={cat}
            type="button"
            className={`btn sm ${activeCategory === cat ? "primary" : "ghost"}`}
            aria-pressed={activeCategory === cat}
            onClick={() => setActiveCategory(cat)}
          >
            {cat}
          </button>
        ))}
      </div>

      {/* Content state */}
      {loading ? (
        <div className="placeholder" role="status">Loading available services...</div>
      ) : error ? (
        <div className="notice bad stack" role="alert">
          <div>{error}</div>
          <div className="row">
            <button type="button" className="btn sm" onClick={loadCatalog}>
              Retry
            </button>
            <button type="button" className="btn ghost sm" onClick={() => navigate("/new")}>
              Raise a standard request
            </button>
          </div>
        </div>
      ) : filteredItems.length === 0 ? (
        <div className="empty">
          <div>{catalogEmptyMessage({ total: items.length, search: search.trim(), category: activeCategory })}</div>
          {search && (
            <button className="btn ghost sm" onClick={() => setSearch("")}>
              Clear search
            </button>
          )}
        </div>
      ) : (
        <div className="catalog-grid">
          {filteredItems.map((item) => (
            <div key={item.id} className="catalog-card">
              <div className="catalog-card-h">
                <div className="catalog-icon">
                  {item.icon || "⚙️"}
                </div>
                {item.category && (
                  <span className="tag">{item.category}</span>
                )}
              </div>
              <div className="catalog-card-b">
                <div className="catalog-card-title">{item.name}</div>
                <div className="catalog-card-desc">{item.description}</div>
              </div>
              <div className="catalog-card-f">
                <div>
                  {item.requires_approval ? (
                    <span className="chip warn" title="Requires managerial sign-off">
                      Needs Approval
                    </span>
                  ) : (
                    <span className="chip ok">Instant Route</span>
                  )}
                </div>
                <button
                  className="btn primary sm"
                  onClick={() => handleOpenRequest(item)}
                >
                  Request
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Request dynamic form modal */}
      {activeItem && (
        <Modal
          title={`Request: ${activeItem.name}`}
          onClose={handleCloseModal}
          footer={
            <>
              <button
                type="button"
                className="btn ghost"
                onClick={handleCloseModal}
                disabled={submitting}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn primary"
                onClick={handleSubmitRequest}
                disabled={submitting}
              >
                {submitting ? "Submitting..." : "Submit Request"}
              </button>
            </>
          }
        >
          <form onSubmit={handleSubmitRequest} className="stack" style={{ gap: 14 }}>
            {activeItem.description && (
              <div className="notice muted small">
                {activeItem.description}
              </div>
            )}

            {activeItem.requires_approval && (
              <div className="callout" style={{ padding: "8px 12px" }}>
                <b>Approval required:</b> This service needs approval before it is fulfilled. Approval status appears on the request once it is submitted.
              </div>
            )}

            {submitError && (
              <div className="notice bad" role="alert">
                {submitError}
              </div>
            )}

            {/* Dynamic schema fields */}
            {activeItem.form_schema?.fields?.map((field) => {
              const fieldError = formErrors[field.name];
              const value = formData[field.name] ?? "";
              const ids = catalogFieldIds(field.name);
              const describedBy = [field.help_text && field.type !== "checkbox" && ids.hint, fieldError && ids.error].filter(Boolean).join(" ") || undefined;
              const a11y = { id: ids.input, "aria-invalid": fieldError ? true : undefined, "aria-describedby": describedBy, "aria-required": field.required || undefined };
              // A checkbox carries its own <label>, so its wrapper must not be a <label> too.
              const Wrapper = field.type === "checkbox" ? "div" : "label";

              return (
                <Wrapper key={field.name} className="field">
                  <span id={`${ids.input}-label`}>
                    {field.label || field.name}
                    {field.required && <><span aria-hidden="true" style={{ color: "var(--bad)", marginLeft: 4 }}>*</span><span className="sr-only"> (required)</span></>}
                  </span>

                  {field.type === "select" ? (
                    <select
                      className="input"
                      {...a11y}
                      value={value}
                      onChange={(e) => handleFieldChange(field.name, e.target.value)}
                    >
                      <option value="">Select an option...</option>
                      {field.options?.map((opt) => (
                        <option key={opt} value={opt}>
                          {opt}
                        </option>
                      ))}
                    </select>
                  ) : field.type === "textarea" ? (
                    <textarea
                      className="input"
                      {...a11y}
                      rows={3}
                      placeholder={field.placeholder || ""}
                      value={value}
                      onChange={(e) => handleFieldChange(field.name, e.target.value)}
                    />
                  ) : field.type === "checkbox" ? (
                    <label className="toggle" style={{ marginTop: 4 }}>
                      <input
                        type="checkbox"
                        {...a11y}
                        aria-labelledby={`${ids.input}-label ${ids.input}-text`}
                        checked={Boolean(value)}
                        onChange={(e) => handleFieldChange(field.name, e.target.checked)}
                      />
                      <span id={`${ids.input}-text`}>{field.help_text || "Enable"}</span>
                    </label>
                  ) : (
                    <input
                      type={field.type === "number" ? "number" : "text"}
                      className="input"
                      {...a11y}
                      placeholder={field.placeholder || ""}
                      value={value}
                      onChange={(e) => handleFieldChange(field.name, e.target.value)}
                    />
                  )}

                  {field.help_text && field.type !== "checkbox" && (
                    <span className="hint" id={ids.hint}>{field.help_text}</span>
                  )}
                  {fieldError && <span className="error" id={ids.error}>{fieldError}</span>}
                </Wrapper>
              );
            })}

            {/* General additional notes */}
            <label className="field">
              <span>Additional justification or notes</span>
              <textarea
                className="input"
                rows={3}
                placeholder="Provide any additional context or details for fulfillment..."
                value={additionalNotes}
                onChange={(e) => setAdditionalNotes(e.target.value)}
              />
            </label>
          </form>
        </Modal>
      )}
    </div>
  );
}
