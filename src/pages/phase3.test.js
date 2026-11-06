import test from "node:test";
import assert from "node:assert/strict";
import {
  toggleTicketSelection,
  selectAllVisible,
  buildBulkPayload,
  formatMarkdown,
  filterCommandPalette,
  shouldIgnoreKeyboardShortcut,
  mergeEarlierInteractions,
} from "./phase3Helpers.js";

test("P3-01 Bulk Selection: toggleTicketSelection adds and removes IDs predictably", () => {
  let selection = [];
  selection = toggleTicketSelection(selection, 101);
  assert.deepEqual(selection, [101]);

  selection = toggleTicketSelection(selection, 102);
  assert.deepEqual(selection, [101, 102]);

  selection = toggleTicketSelection(selection, 101);
  assert.deepEqual(selection, [102]);
});

test("P3-01 Bulk Selection: selectAllVisible toggles all or none", () => {
  const visible = [1, 2, 3];
  // None selected -> selects all
  assert.deepEqual(selectAllVisible([], visible), [1, 2, 3]);

  // Partial selected -> selects all
  assert.deepEqual(selectAllVisible([1], visible), [1, 2, 3]);

  // All selected -> deselects all
  assert.deepEqual(selectAllVisible([1, 2, 3], visible), []);
});

test("P3-01 Bulk Payload: buildBulkPayload validates action and ticketIds", () => {
  const payload = buildBulkPayload("assign", [10, 20], { assignee_id: 5 });
  assert.deepEqual(payload, {
    action: "assign",
    ticket_ids: [10, 20],
    assignee_id: 5,
  });

  assert.throws(() => buildBulkPayload("", [10]), /action is required/);
  assert.throws(() => buildBulkPayload("assign", []), /non-empty array/);
});

test("P3-03 Keyboard Guard: shouldIgnoreKeyboardShortcut ignores input/textarea/select", () => {
  assert.equal(shouldIgnoreKeyboardShortcut({ tagName: "INPUT" }), true);
  assert.equal(shouldIgnoreKeyboardShortcut({ tagName: "TEXTAREA" }), true);
  assert.equal(shouldIgnoreKeyboardShortcut({ tagName: "SELECT" }), true);
  assert.equal(shouldIgnoreKeyboardShortcut({ tagName: "DIV", isContentEditable: true }), true);
  assert.equal(shouldIgnoreKeyboardShortcut({ tagName: "DIV", isContentEditable: false }), false);
  assert.equal(shouldIgnoreKeyboardShortcut({ tagName: "BUTTON" }), false);
  assert.equal(shouldIgnoreKeyboardShortcut(null), false);
});

test("P3-03 Command Palette: filterCommandPalette matches labels and ticket numbers", () => {
  const actions = [
    { id: "1", label: "Open Queue", category: "Queues" },
    { id: "2", label: "Knowledge Base", category: "Navigation" },
    { id: "3", label: "Pending Approvals", category: "Navigation" },
  ];

  // Empty query returns all
  assert.equal(filterCommandPalette(actions, "").length, 3);

  // Filter by label
  const filtered = filterCommandPalette(actions, "know");
  assert.equal(filtered.length, 1);
  assert.equal(filtered[0].id, "2");

  // Filter by category
  const navFiltered = filterCommandPalette(actions, "nav");
  assert.equal(navFiltered.length, 2);

  // Jump to ticket number
  const jumpFiltered = filterCommandPalette(actions, "123");
  assert.equal(jumpFiltered[0].id, "ticket-jump-123");
  assert.equal(jumpFiltered[0].label, "Jump to Ticket #123");

  const jumpPrefix = filterCommandPalette(actions, "INC-00045");
  assert.equal(jumpPrefix[0].id, "ticket-jump-00045");
  assert.equal(jumpPrefix[0].label, "Jump to Ticket #INC-00045");
});

test("P3-04 Markdown Formatting: formatMarkdown applies tags around selected text", () => {
  const text = "Hello world";
  // Select "world" (index 6 to 11) and bold it
  const res = formatMarkdown(text, 6, 11, "**", "**");
  assert.equal(res.nextText, "Hello **world**");
  assert.equal(res.selectionStart, 8);
  assert.equal(res.selectionEnd, 13);
});

test("P3-04 Markdown Formatting: formatMarkdown inserts default text when nothing is selected", () => {
  const text = "Hello ";
  const res = formatMarkdown(text, 6, 6, "`", "`", "code");
  assert.equal(res.nextText, "Hello `code`");
});

test("P3-06 Conversation Pagination: mergeEarlierInteractions preserves chronological order", () => {
  // Existing messages on page 1 (loaded newest-first and reversed to: 3, 4, 5)
  const existing = [{ id: 3, text: "msg 3" }, { id: 4, text: "msg 4" }, { id: 5, text: "msg 5" }];
  // Earlier page (fetched with ordering=-created_at: [msg 2, msg 1])
  const earlierPage = [{ id: 2, text: "msg 2" }, { id: 1, text: "msg 1" }];

  const merged = mergeEarlierInteractions(existing, earlierPage);
  // Merged should be: 1, 2, 3, 4, 5
  assert.deepEqual(merged.map((m) => m.id), [1, 2, 3, 4, 5]);
});

test("P3-06 Conversation Pagination: mergeEarlierInteractions avoids duplicates on page boundaries", () => {
  const existing = [{ id: 2, text: "msg 2" }, { id: 3, text: "msg 3" }];
  // Earlier page returned msg 2 again due to shift during pagination
  const earlierPage = [{ id: 2, text: "msg 2" }, { id: 1, text: "msg 1" }];

  const merged = mergeEarlierInteractions(existing, earlierPage);
  assert.deepEqual(merged.map((m) => m.id), [1, 2, 3]);
});

test("P3-06 Conversation Pagination: mergeEarlierInteractions handles empty inputs safely", () => {
  assert.deepEqual(mergeEarlierInteractions([], []), []);
  assert.deepEqual(mergeEarlierInteractions(null, [{ id: 1 }]), [{ id: 1 }]);
  assert.deepEqual(mergeEarlierInteractions([{ id: 1 }], null), [{ id: 1 }]);
});
