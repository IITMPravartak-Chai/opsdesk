import { test } from "node:test";
import assert from "node:assert/strict";
import { layoutGraphNodes } from "./cmdbHelpers.js";

test("layoutGraphNodes handles empty array safely", () => {
  const res = layoutGraphNodes([], [], "root");
  assert.deepEqual(res, []);
});

test("layoutGraphNodes positions single node at center", () => {
  const nodes = [{ id: 1, name: "Database" }];
  const res = layoutGraphNodes(nodes, [], 1, 800, 600);
  assert.equal(res.length, 1);
  assert.equal(res[0].x, 400);
  assert.equal(res[0].y, 300);
});

test("layoutGraphNodes partitions upstream and downstream correctly", () => {
  const nodes = [
    { id: 10, name: "Web App" },
    { id: 20, name: "API Server" },
    { id: 30, name: "Database" },
  ];
  const links = [
    { source: 10, target: 20, relation: "depends_on", direction: "upstream" },
    { source: 30, target: 10, relation: "depends_on", direction: "downstream" },
  ];

  const res = layoutGraphNodes(nodes, links, 10, 800, 600);
  assert.equal(res.length, 3);

  const root = res.find((n) => n.id === 10);
  assert.equal(root.x, 400);
  assert.equal(root.y, 300);
  assert.equal(root.isRoot, true);

  const upstream = res.find((n) => n.id === 20);
  assert.ok(upstream.x < root.x, "Upstream node should be positioned to the left");

  const downstream = res.find((n) => n.id === 30);
  assert.ok(downstream.x > root.x, "Downstream node should be positioned to the right");
});

test("layoutGraphNodes handles cyclic links without crashing", () => {
  const nodes = [
    { id: 1, name: "Node A" },
    { id: 2, name: "Node B" },
  ];
  const links = [
    { source: 1, target: 2, relation: "depends_on", direction: "upstream" },
    { source: 2, target: 1, relation: "depends_on", direction: "upstream" },
  ];

  const res = layoutGraphNodes(nodes, links, 1, 800, 600);
  assert.equal(res.length, 2);
  assert.equal(res[0].id, 1);
  assert.equal(res[1].id, 2);
});
