import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ChatTurn } from "../src/components/chat/ChatTurn";
import { askAboutTopic } from "../src/components/registry";
import { parseConversation } from "../src/conversation";
import daily from "./fixtures/conversation_v2.json" with { type: "json" };
import research from "./fixtures/conversation_research.json" with { type: "json" };

// The answer's layout: with the model's opening it leads with it and folds the
// analysis; without one, the analysis shows in full with panels beside it.
const html = (fixture: unknown, lead?: { text: string; steps: string[] }) => {
  const conversation = parseConversation(
    lead
      ? (() => {
          const copy = structuredClone(fixture) as typeof daily;
          (copy.turns[0].answer as Record<string, unknown>).lead = lead;
          return copy;
        })()
      : fixture,
  );
  return renderToString(
    <ChatTurn
      turn={conversation.turns[0]}
      publicationId={conversation.publication_id}
    />,
  );
};
const before = (text: string, first: string, second: string) =>
  text.indexOf(first) >= 0 && text.indexOf(first) < text.indexOf(second);

describe("chat turn layout", () => {
  it("without an opening shows the analysis with the panels after the data", () => {
    const page = html(daily);
    expect(page).not.toContain("Ver el análisis completo");
    expect(before(page, "Qué dicen los datos", "Ánimo general")).toBe(true);
    expect(before(page, "Ánimo general", "Interpretación")).toBe(true);
    expect(page).toContain("voc-answer-caveat");
  });

  it("with an opening leads with it and folds the analysis and the limits", () => {
    const page = html(daily, {
      text: "El ánimo está dividido: 34% negativas y 29% positivas.",
      steps: [],
    });
    expect(before(page, "El ánimo está dividido", "Ánimo general")).toBe(true);
    expect(before(page, "Ánimo general", "Ver el análisis completo")).toBe(
      true,
    );
    expect(page).toContain("Resumen del modelo");
    expect(page).not.toContain("voc-answer-caveat");
  });

  it("orders panels summary first, then charts, then lists", () => {
    const page = html(daily);
    // mood_ring (a summary) before platforms (a chart), whatever the server order.
    // Panel titles, not the same words in the answer's text.
    expect(before(page, ">Ánimo general</h2>", ">Por plataforma</h2>")).toBe(
      true,
    );
  });

  it("puts each research step's panel after its step", () => {
    const page = html(research);
    expect(
      before(page, "Paso 1 · Evolución mensual", "Sentimiento en el tiempo"),
    ).toBe(true);
    expect(
      before(page, "Sentimiento en el tiempo", "Paso 2 · Queja principal"),
    ).toBe(true);
    expect(
      before(
        page,
        "Paso 2 · Queja principal",
        "Temas por volumen y sentimiento",
      ),
    ).toBe(true);
  });

  it("lets a topic in an answer be asked about, only in a conversation", () => {
    const conversation = parseConversation(research);
    const turn = conversation.turns[0];
    const read = renderToString(
      <ChatTurn turn={turn} publicationId={conversation.publication_id} />,
    );
    const asking = renderToString(
      <ChatTurn
        turn={turn}
        publicationId={conversation.publication_id}
        onAsk={() => {}}
      />,
    );
    expect(read).not.toContain('<button type="button" class="voc-topic-row"');
    expect(asking).toContain('<button type="button" class="voc-topic-row"');
    expect(asking).toContain("Elige un tema para ver sus reseñas.");
    expect(askAboutTopic(73, "Pagos")).toBe(
      "¿Qué dicen los clientes del tema 73, «Pagos»?",
    );
    expect(askAboutTopic(-1)).toBe(
      "¿Qué dicen las reseñas que no se asignaron a ningún tema?",
    );
  });

  it("with an opening shows the research outcome and folds the earlier steps", () => {
    const page = html(research, {
      text: "Septiembre de 2025 tuvo más reseñas, 5.",
      steps: [
        "Mes con más reseñas: septiembre de 2025",
        "Ningún tema destacado",
      ],
    });
    const fold = page.indexOf("Ver el análisis completo");
    // The last step's panel leads; the first step's panel sits inside the fold.
    expect(page.indexOf("Temas por volumen y sentimiento")).toBeLessThan(fold);
    expect(page.indexOf("Sentimiento en el tiempo")).toBeGreaterThan(fold);
    expect(page).toContain("voc-answer-chain");
  });
});
