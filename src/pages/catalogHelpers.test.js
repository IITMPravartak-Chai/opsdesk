import test from "node:test";
import assert from "node:assert/strict";
import {
  groupCatalogByCategory,
  validateCatalogForm,
  buildCatalogSubmissionPayload,
} from "./catalogHelpers.js";

test("groupCatalogByCategory: groups items by category properly", () => {
  const items = [
    { id: "1", name: "Laptop", category: "Hardware" },
    { id: "2", name: "Monitor", category: "Hardware" },
    { id: "3", name: "AWS Access", category: "Cloud" },
    { id: "4", name: "General Question", category: null },
  ];

  const grouped = groupCatalogByCategory(items);
  assert.equal(grouped.Hardware.length, 2);
  assert.equal(grouped.Cloud.length, 1);
  assert.equal(grouped.General.length, 1);
  assert.equal(grouped.General[0].name, "General Question");
});

test("groupCatalogByCategory: handles null or non-array inputs safely", () => {
  assert.deepEqual(groupCatalogByCategory(null), {});
  assert.deepEqual(groupCatalogByCategory(undefined), {});
  assert.deepEqual(groupCatalogByCategory([]), {});
});

test("validateCatalogForm: passes when all required fields are present", () => {
  const schema = {
    fields: [
      { name: "device_type", label: "Device Type", required: true, type: "select", options: ["MacBook", "Dell"] },
      { name: "reason", label: "Business Justification", required: true, type: "textarea" },
      { name: "budget_code", label: "Budget Code", required: false, type: "text" },
    ],
  };

  const values = {
    device_type: "MacBook",
    reason: "New project assignment",
  };

  const res = validateCatalogForm(schema, values);
  assert.equal(res.isValid, true);
  assert.deepEqual(res.errors, {});
});

test("validateCatalogForm: catches missing required fields and whitespace-only values", () => {
  const schema = {
    fields: [
      { name: "role", label: "Role", required: true, type: "text" },
      { name: "reason", label: "Justification", required: true, type: "text" },
    ],
  };

  const values = {
    role: "   ",
  };

  const res = validateCatalogForm(schema, values);
  assert.equal(res.isValid, false);
  assert.equal(res.errors.role, "Role is required.");
  assert.equal(res.errors.reason, "Justification is required.");
});

test("validateCatalogForm: validates numeric fields", () => {
  const schema = {
    fields: [
      { name: "ram_gb", label: "RAM (GB)", required: true, type: "number" },
    ],
  };

  const invalid = validateCatalogForm(schema, { ram_gb: "sixteen" });
  assert.equal(invalid.isValid, false);
  assert.equal(invalid.errors.ram_gb, "RAM (GB) must be a valid number.");

  const valid = validateCatalogForm(schema, { ram_gb: 32 });
  assert.equal(valid.isValid, true);
});

test("validateCatalogForm: validates select option constraints", () => {
  const schema = {
    fields: [
      { name: "os", label: "Operating System", required: true, type: "select", options: ["Linux", "macOS", "Windows"] },
    ],
  };

  const invalid = validateCatalogForm(schema, { os: "FreeBSD" });
  assert.equal(invalid.isValid, false);
  assert.equal(invalid.errors.os, "Operating System contains an invalid selection.");

  const valid = validateCatalogForm(schema, { os: "macOS" });
  assert.equal(valid.isValid, true);
});

test("buildCatalogSubmissionPayload: trims notes and keeps structured fields", () => {
  const payload = buildCatalogSubmissionPayload(
    { id: "cat-1" },
    { model: "Pro 16", storage: "1TB" },
    "  Please deliver to floor 4  "
  );

  assert.deepEqual(payload, {
    fields: { model: "Pro 16", storage: "1TB" },
    additional_notes: "Please deliver to floor 4",
  });
});
