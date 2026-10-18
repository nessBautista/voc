import { describe, it, expect } from "vitest";
import { parseConversation } from "../src/conversation";
import conversation from "./fixtures/conversation.json" with { type: "json" };

describe("saved conversation contracts", () => {
  it("accepts cited answers from the real server composer", () => {
    expect(parseConversation(conversation).turns[0].answer?.text).toContain(
      "8 registros de Google Play",
    );
  });
  it.each([
    "identity",
    "reference",
    "trace",
    "duplicate",
    "status",
    "mode",
    "extra",
    "kind",
  ])("rejects corrupt %s", (field) => {
    const value = structuredClone(conversation);
    if (field === "identity")
      value.turns[0].answer.publication_id =
        "00000000-0000-4000-8000-000000000000";
    if (field === "reference")
      value.turns[0].answer.views[0].result_ids[0] = "0".repeat(64);
    if (field === "trace")
      value.turns[0].answer.trace[0].result_id = "0".repeat(64);
    if (field === "duplicate") value.turns.push(value.turns[0]);
    if (field === "status") value.turns[0].status = "running";
    if (field === "mode") value.turns[0].planner_mode = "model";
    if (field === "extra")
      Object.assign(value.turns[0].answer.views[0], { html: "<script/>" });
    if (field === "kind") value.turns[0].answer.plan.intent = "coverage";
    expect(() => parseConversation(value)).toThrow();
  });
});
