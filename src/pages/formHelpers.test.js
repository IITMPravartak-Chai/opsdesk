import test from "node:test";
import assert from "node:assert/strict";
import { onField } from "./formHelpers.js";

test("a field's onChange tells the reducer which field changed and its new value", () => {
  const actions = [];
  const handler = onField((action) => actions.push(action), "title");
  handler({ target: { value: "Reset VPN" } });
  handler({ target: { value: "" } });
  assert.deepEqual(actions, [{ type: "field", key: "title", value: "Reset VPN" }, { type: "field", key: "title", value: "" }]);
});
