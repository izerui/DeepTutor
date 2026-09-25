import test from "node:test";
import assert from "node:assert/strict";
import { fileTabIdFor } from "@/lib/viewer-tab-id";

type Att = Parameters<typeof fileTabIdFor>[0];

test("different workspace images get distinct tab ids", () => {
  const a: Att = {
    type: "image",
    filename: "chart.png",
    url: "/workspace/files/a",
    workspace_item_id: "ws-item-1",
    origin: "workspace",
  };
  const b: Att = {
    type: "image",
    filename: "chart.png",
    url: "/workspace/files/b",
    workspace_item_id: "ws-item-2",
    origin: "workspace",
  };
  assert.notEqual(fileTabIdFor(a, 0), fileTabIdFor(b, 1));
});

test("images with different urls but no id get distinct tab ids", () => {
  const a: Att = { type: "image", filename: "output.png", url: "/files/1" };
  const b: Att = { type: "image", filename: "output.png", url: "/files/2" };
  assert.notEqual(fileTabIdFor(a, 0), fileTabIdFor(b, 1));
});

test("images with distinct attachment ids get distinct tab ids", () => {
  const a: Att = { type: "image", filename: "img.png", id: "att-1" };
  const b: Att = { type: "image", filename: "img.png", id: "att-2" };
  assert.notEqual(fileTabIdFor(a, 0), fileTabIdFor(b, 1));
});

test("fallback index is used when no distinguishing field exists", () => {
  const a: Att = { type: "image" };
  const b: Att = { type: "image" };
  assert.notEqual(fileTabIdFor(a, 0), fileTabIdFor(b, 1));
});

test("workspace_item_id takes priority over id", () => {
  const a: Att = {
    type: "image",
    id: "same-id",
    workspace_item_id: "ws-1",
  };
  const id = fileTabIdFor(a, 0);
  assert.ok(id.includes("ws-1"));
  assert.ok(!id.includes("same-id"));
});
