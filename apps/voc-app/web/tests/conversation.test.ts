import { describe, it, expect } from "vitest";
import { parseConversation, planScope } from "../src/conversation";
import conversation from "./fixtures/conversation.json" with { type: "json" };
import analysis from "./fixtures/conversation_v2.json" with { type: "json" };
import research from "./fixtures/conversation_research.json" with { type: "json" };

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

describe("analysis answers from the server composer", () => {
  it("accepts the six demo answers and their dashboard views", () => {
    const parsed = parseConversation(analysis);
    expect(parsed.turns.map((turn) => turn.answer?.plan.intent)).toEqual([
      "mood",
      "main_complaints",
      "trend",
      "topic_reviews",
      "specific_problems",
      "focus",
    ]);
    expect(parsed.turns[0].answer?.views.map((view) => view.component)).toEqual(
      ["mood_ring", "platforms"],
    );
    expect(parsed.turns[3].answer?.plan.topic).toEqual({
      topic_id: 0,
      reference: null,
    });
    expect(parsed.turns[3].answer?.text).toContain("Tema 0");
    expect(planScope(parsed.turns[3].answer!.plan)).toBe("Tema: 0.");
    expect(planScope(parsed.turns[0].answer!.plan)).toBe(
      "Alcance: dataset completo.",
    );
    expect(
      planScope(parseConversation(conversation).turns[0].answer!.plan),
    ).toBe("Plataforma: google_play.");
  });
  it.each(["component", "count", "kinds", "selection", "limit"])(
    "rejects corrupt %s",
    (field) => {
      type Loose = {
        turns: {
          answer: {
            plan: Record<string, unknown>;
            results: { result_id: string }[];
            views: { result_ids: string[] }[];
          };
        }[];
      };
      const value = structuredClone(analysis) as unknown as Loose;
      const first = value.turns[0].answer;
      // The mood ring cannot draw from the platforms result.
      if (field === "component")
        first.views[0].result_ids = [first.results[1].result_id];
      if (field === "count")
        first.results.push(
          ...value.turns[1].answer.results,
          ...value.turns[2].answer.results,
          ...value.turns[3].answer.results,
        );
      if (field === "kinds") first.plan.intent = "trend";
      if (field === "selection")
        Object.assign(first.plan, { selection: { platforms: [] } });
      if (field === "limit") Object.assign(first.plan, { limit: 11 });
      expect(() => parseConversation(value)).toThrow();
    },
  );
});

describe("research answers", () => {
  it("accepts a chain of steps that partition the evidence", () => {
    const parsed = parseConversation(research);
    const answer = parsed.turns[0].answer!;
    expect(answer.mode).toBe("research");
    expect(answer.steps?.map((step) => step.plan.intent)).toEqual([
      "trend",
      "main_complaints",
    ]);
    expect(answer.results.map((result) => result.kind)).toEqual([
      "timeline",
      "summary",
      "topic_list",
    ]);
    expect(answer.text.startsWith("Investigación en 2 pasos.")).toBe(true);
    expect(answer.views.map((view) => view.component)).toEqual([
      "timeline",
      "topic_list",
    ]);
  });
  it.each(["missing-id", "order", "no-steps", "first-plan"])(
    "rejects corrupt %s",
    (field) => {
      type Loose = {
        turns: {
          answer: {
            mode?: string;
            plan: Record<string, unknown>;
            steps?: { plan: Record<string, unknown>; result_ids: string[] }[];
          };
        }[];
      };
      const value = structuredClone(research) as unknown as Loose;
      const answer = value.turns[0].answer;
      if (field === "missing-id") answer.steps![1].result_ids.pop();
      if (field === "order") answer.steps!.reverse();
      if (field === "no-steps") delete answer.steps;
      if (field === "first-plan")
        answer.plan = { ...answer.plan, intent: "mood" };
      expect(() => parseConversation(value)).toThrow();
    },
  );
});
