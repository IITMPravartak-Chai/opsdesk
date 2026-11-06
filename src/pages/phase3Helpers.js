/**
 * Pure helper functions for Phase 3:
 * - P3-01: Bulk selection and payload validation
 * - P3-02: Saved queue parameter validation
 * - P3-03: Command palette filtering and shortcut guards
 * - P3-04: Markdown formatting insertions
 * - P3-05: In-app notification counting and preference updates
 * - P3-06: Conversation pagination chronological merge
 */

export function toggleTicketSelection(currentSelection, id) {
  if (!Array.isArray(currentSelection)) return [id];
  return currentSelection.includes(id)
    ? currentSelection.filter((x) => x !== id)
    : [...currentSelection, id];
}

export function selectAllVisible(currentSelection, visibleIds) {
  if (!Array.isArray(visibleIds) || visibleIds.length === 0) return [];
  const allSelected = visibleIds.every((id) => currentSelection.includes(id));
  return allSelected ? [] : [...visibleIds];
}

export function buildBulkPayload(action, ticketIds, options = {}) {
  if (!action || typeof action !== "string") {
    throw new Error("action is required");
  }
  if (!Array.isArray(ticketIds) || ticketIds.length === 0) {
    throw new Error("ticketIds must be a non-empty array");
  }
  return {
    action,
    ticket_ids: ticketIds,
    ...options,
  };
}

export function formatMarkdown(text, start, end, prefix, suffix = "", defaultText = "text") {
  const safeText = text || "";
  const s = Math.max(0, Math.min(start, safeText.length));
  const e = Math.max(s, Math.min(end, safeText.length));
  const selected = safeText.substring(s, e) || defaultText;
  const replacement = `${prefix}${selected}${suffix}`;
  const nextText = safeText.substring(0, s) + replacement + safeText.substring(e);
  return {
    nextText,
    selectionStart: s + prefix.length,
    selectionEnd: s + prefix.length + selected.length,
  };
}

export function filterCommandPalette(actions, query) {
  if (!Array.isArray(actions)) return [];
  if (!query || !query.trim()) return actions;
  const q = query.trim().toLowerCase();
  const filtered = actions.filter(
    (a) =>
      a.label?.toLowerCase().includes(q) ||
      (a.category && a.category.toLowerCase().includes(q))
  );

  const ticketMatch = query.trim().match(/^(?:inc-|sr-)?(\d+)$/i);
  if (ticketMatch) {
    return [
      {
        id: `ticket-jump-${ticketMatch[1]}`,
        label: `Jump to Ticket #${query.trim().toUpperCase()}`,
        category: "Tickets",
      },
      ...filtered,
    ];
  }
  return filtered;
}

export function shouldIgnoreKeyboardShortcut(target) {
  if (!target) return false;
  const tagName = target.tagName;
  return (
    ["INPUT", "TEXTAREA", "SELECT"].includes(tagName) ||
    target.isContentEditable === true
  );
}

export function mergeEarlierInteractions(existingMsgs, earlierPageResults) {
  if (!Array.isArray(earlierPageResults) || earlierPageResults.length === 0) {
    return existingMsgs || [];
  }
  const chronologicallyEarlier = [...earlierPageResults].reverse();
  const existingList = Array.isArray(existingMsgs) ? existingMsgs : [];
  const existingIds = new Set(existingList.map((m) => m.id));
  const uniqueEarlier = chronologicallyEarlier.filter((m) => !existingIds.has(m.id));
  return [...uniqueEarlier, ...existingList];
}
