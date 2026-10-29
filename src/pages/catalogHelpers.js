/**
 * Helper utilities for the Service Catalog and dynamic request forms.
 */
import { endpointUnavailable } from "./workflowHelpers.js";

export function groupCatalogByCategory(items) {
  if (!Array.isArray(items)) return {};
  return items.reduce((acc, item) => {
    const cat = item.category || "General";
    if (!acc[cat]) acc[cat] = [];
    acc[cat].push(item);
    return acc;
  }, {});
}

export function validateCatalogForm(schema, values = {}) {
  const errors = {};
  if (!schema || !Array.isArray(schema.fields)) {
    return { isValid: true, errors };
  }

  for (const field of schema.fields) {
    const val = values[field.name];

    if (field.required) {
      if (val === undefined || val === null || val === "" || (typeof val === "string" && !val.trim())) {
        errors[field.name] = `${field.label || field.name} is required.`;
        continue;
      }
    }

    if (val !== undefined && val !== null && val !== "") {
      if (field.type === "number" && isNaN(Number(val))) {
        errors[field.name] = `${field.label || field.name} must be a valid number.`;
      }
      if (field.type === "select" && Array.isArray(field.options) && field.options.length > 0) {
        if (!field.options.includes(val)) {
          errors[field.name] = `${field.label || field.name} contains an invalid selection.`;
        }
      }
    }
  }

  return {
    isValid: Object.keys(errors).length === 0,
    errors,
  };
}

export function buildCatalogSubmissionPayload(item, values = {}, additionalNotes = "") {
  return {
    fields: values,
    additional_notes: (additionalNotes || "").trim(),
  };
}

// ---- U4 (UI-DEC-015): honest catalog states. No API contract change. ----

export function catalogLoadErrorMessage(err) {
  if (endpointUnavailable(err)) {
    return "The service catalog isn't available on this server yet. You can still raise a standard request.";
  }
  return `The service catalog couldn't be loaded: ${err?.message || "request failed"}.`;
}

/**
 * Submission is not idempotent, so the wording says what is actually known:
 * an unrouted endpoint or a 4xx rejection means nothing was created; a 5xx or network
 * failure means it may or may not have been, so the user is told to check before retrying.
 */
export function catalogSubmitErrorMessage(err) {
  const s = err?.status;
  if (endpointUnavailable(err)) {
    return "Catalog requests can't be submitted on this server yet. Nothing was submitted; your answers are still here.";
  }
  if (s >= 400 && s < 500) return `Your request wasn't submitted: ${err?.message || "it was rejected"}. Your answers are still here.`;
  return `Your request may not have been submitted (${err?.message || "no response"}). Check My requests before trying again.`;
}

export function catalogEmptyMessage({ total, search, category }) {
  if (total === 0) return "No services are published in the catalog yet.";
  if (search) return "No services match your search.";
  if (category && category !== "All") return "No services in this category.";
  return "No services found.";
}

/** Where to go after a confirmed submission; null when the response doesn't identify the request. */
export function requestCreatedPath(res) {
  const id = res?.id;
  return Number.isInteger(id) && id > 0 ? `/tickets/${id}` : null;
}

/** Stable ids that tie a dynamic field to its hint and error text. */
export function catalogFieldIds(name) {
  const slug = String(name).replace(/[^A-Za-z0-9_-]/g, "-");
  return { input: `catalog-field-${slug}`, hint: `catalog-field-${slug}-hint`, error: `catalog-field-${slug}-error` };
}
