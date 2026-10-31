import { describe, expect, it } from "vitest";
import { sections } from "../src/components/chat/AnswerSections";
import daily from "./fixtures/conversation_v2.json" with { type: "json" };
import research from "./fixtures/conversation_research.json" with { type: "json" };
import dataset from "./fixtures/conversation.json" with { type: "json" };

// The chat shows the server's labelled paragraphs as sections; any other
// paragraph stays as it is, so no text is ever dropped.
describe("answer sections", () => {
  it("splits a daily answer into its three labelled parts", () => {
    const parts = sections(daily.turns[0].answer.text);
    expect(parts.map((part) => part.kind === "section" && part.label)).toEqual([
      "Qué dicen los datos",
      "Interpretación",
      "Lo que no se puede afirmar",
    ]);
    expect(parts[0]).toMatchObject({
      text: expect.stringMatching(/^En todas las plataformas/),
    });
  });

  it("keeps a research answer's intro and labels each step", () => {
    const parts = sections(research.turns[0].answer!.text);
    expect(parts[0]).toEqual({
      kind: "paragraph",
      text: "Investigación en 2 pasos.",
    });
    expect(
      parts.slice(1).map((part) => part.kind === "section" && part.label),
    ).toEqual([
      "Paso 1 · Evolución mensual",
      "Paso 2 · Queja principal",
      "Interpretación",
      "Lo que no se puede afirmar",
    ]);
  });

  it("leaves an unlabelled answer as plain paragraphs", () => {
    expect(sections(dataset.turns[0].answer.text)).toEqual([
      {
        kind: "paragraph",
        text: "La publicación consultada contiene 8 registros de Google Play.",
      },
    ]);
  });

  it("loses no text", () => {
    for (const text of [
      daily.turns[0].answer.text,
      research.turns[0].answer!.text,
    ]) {
      const rebuilt = sections(text)
        .map((part) =>
          part.kind === "section" ? `${part.label}. ${part.text}` : part.text,
        )
        .join("\n\n");
      expect(rebuilt).toBe(text);
    }
  });
});
