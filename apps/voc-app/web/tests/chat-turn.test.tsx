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

  it("draws a research answer as a report: a block per step, panels folded", () => {
    const page = html(research);
    expect(page.match(/class="voc-research-step"/g)).toHaveLength(2);
    // Step 1 shows the monthly share, with the month step 2 narrowed to marked.
    expect(page).toContain("Proporción de reseñas negativas por mes");
    expect(page).toContain("is-marked");
    // Step 2 compares the month's feature topics; in this tiny month none has
    // the five classified reviews a ranking needs, so it says so.
    expect(page).toContain(
      "Ningún tema de una funcionalidad tiene al menos 5 reseñas clasificadas aquí.",
    );
    // The full panels sit inside each step's fold, after its summary line.
    const fold = page.indexOf("Ver el detalle y el panel completo");
    expect(fold).toBeGreaterThan(-1);
    expect(page.indexOf(">Sentimiento en el tiempo</h2>")).toBeGreaterThan(
      fold,
    );
    // Without an opening, each step's finding is the server's own paragraph.
    expect(page).toContain("la serie cubre 13 meses");
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

  it("with an opening, each step leads with the model's line for it", () => {
    const page = html(research, {
      text: "Septiembre de 2025 tuvo más reseñas, 5.",
      steps: [
        "Mes con más reseñas: septiembre de 2025",
        "Ningún tema destacado",
      ],
    });
    expect(before(page, "Septiembre de 2025 tuvo más reseñas", "Paso 1")).toBe(
      true,
    );
    expect(before(page, "Mes con más reseñas", "Paso 2")).toBe(true);
    // The rail replaces the numbered chain, and the server's paragraph moves
    // into the step's fold.
    expect(page).not.toContain("voc-answer-chain");
    const fold = page.indexOf("Ver el detalle y el panel completo");
    expect(page.indexOf("la serie cubre 13 meses")).toBeGreaterThan(fold);
  });
});

describe("research step visuals", () => {
  it("shows a count, not a share, when the step filtered by sentiment", async () => {
    const { ReviewHighlights } =
      await import("../src/components/chat/research/ReviewHighlights");
    const detail = {
      kind: "topic_detail",
      data: {
        classified: 168,
        negative: 168,
        neutral: 0,
        positive: 0,
        keywords: [],
      },
    } as never;
    const reviews = {
      kind: "reviews",
      data: { total: 168, items: [] },
    } as never;
    const filtered = renderToString(
      <ReviewHighlights
        detail={detail}
        reviews={reviews}
        labels={["negative"]}
      />,
    );
    expect(filtered).not.toContain("100%");
    expect(filtered).toContain("solo negativas, como pidió el paso");
    const whole = renderToString(
      <ReviewHighlights detail={detail} reviews={reviews} labels={null} />,
    );
    expect(whole).toContain("100%");
  });
});

describe("research steps that share evidence", () => {
  // The fourth case: a step that reuses the results an earlier step fetched.
  const shared = () => {
    const copy = structuredClone(research) as typeof research;
    const answer = copy.turns[0].answer!;
    const second = answer.steps![1];
    (answer.steps as unknown[]).push({
      plan: { ...second.plan, intent: "specific_problems" },
      result_ids: [...second.result_ids],
    });
    return copy;
  };

  it("are read, as long as each result is listed once", () => {
    expect(() => parseConversation(shared())).not.toThrow();
    const missing = shared();
    missing.turns[0].answer!.steps![2].result_ids = [
      "0".repeat(64),
      "1".repeat(64),
    ];
    expect(() => parseConversation(missing)).toThrow();
  });

  it("note the reuse instead of drawing the same visual again", () => {
    const conversation = parseConversation(shared());
    const page = renderToString(
      <ChatTurn
        turn={conversation.turns[0]}
        publicationId={conversation.publication_id}
      />,
    );
    expect(page).toContain("Con los mismos datos del paso 2");
    // Step 2's comparison (here, its hint) appears once, not again in step 3.
    expect(
      page.match(/Ningún tema de una funcionalidad tiene al menos/g),
    ).toHaveLength(1);
  });
});

describe("the praise step", () => {
  it("ranks by the positive share at 95% confidence and marks the best rated", async () => {
    const { TopicCompare } =
      await import("../src/components/chat/research/TopicCompare");
    const row = (topic_id: number, positive: number, classified: number) => ({
      topic_id,
      display_name: `Tema de prueba ${topic_id}`,
      assessment: "single_feature",
      classified,
      negative: classified - positive,
      neutral: 0,
      positive,
      negative_share: (classified - positive) / classified,
      positive_share: positive / classified,
      wilson_lower: 0,
    });
    // 6 of 6 positive ranks below 95 of 100: the bound discounts small topics.
    const result = {
      kind: "topic_list",
      data: [row(1, 6, 6), row(2, 95, 100), row(3, 3, 4)],
    } as never;
    const page = renderToString(
      <TopicCompare
        result={result}
        featured={null}
        scope={null}
        measure="positive"
      />,
    );
    expect(page.indexOf("Tema de prueba 2")).toBeLessThan(
      page.indexOf("Tema de prueba 1"),
    );
    // Four classified reviews are too few to rank.
    expect(page).not.toContain("Tema de prueba 3");
    expect(page).toContain("Mejor valorado");
    expect(page).toContain("is-positive");
  });
});
