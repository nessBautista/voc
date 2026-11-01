import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SuggestionList } from "../src/components/chat/ChatHeader";
import { ChatTurn } from "../src/components/chat/ChatTurn";
import { Composer } from "../src/components/chat/Composer";
import { TurnRunning } from "../src/components/chat/TurnStatus";
import { parseConversation } from "../src/conversation";
import daily from "./fixtures/conversation_v2.json" with { type: "json" };
import research from "./fixtures/conversation_research.json" with { type: "json" };

const text = (html: string) =>
  html
    .replace(/<!-- -->/g, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ");
const composer = (mode: "daily" | "research", start = false) =>
  renderToString(
    <Composer
      value=""
      onChange={() => {}}
      mode={mode}
      onMode={() => {}}
      onSubmit={() => {}}
      disabled={false}
      sending={false}
      start={start}
    />,
  );

describe("the start page", () => {
  it("suggests short buttons that carry the full question", () => {
    const page = renderToString(
      <SuggestionList
        suggestions={[
          {
            label: "Queja principal",
            question: "¿Cuál es la queja principal?",
            icon: "complaint",
          },
        ]}
        onPick={() => {}}
      />,
    );
    // The label shows; the question completes the button's accessible name.
    expect(text(page)).toContain(
      "Queja principal : ¿Cuál es la queja principal?",
    );
    expect(page).toContain('title="¿Cuál es la queja principal?"');
    expect(page).toContain('class="voc-icon"');
  });

  it("makes the composer the hero's control and names the mode in words", () => {
    const start = composer("daily", true);
    expect(start).toContain('class="voc-composer is-start"');
    expect(text(start)).toContain("responde en segundos");
    const deep = composer("research");
    expect(deep).toContain('class="voc-composer is-research"');
    expect(deep).toContain('class="voc-segmented is-strong"');
    expect(text(deep)).toContain("hasta seis consultas encadenadas");
    // The chosen mode is pressed; its icon is hidden from assistive technology.
    expect(deep).toMatch(
      /aria-pressed="true"[^>]*><svg[^>]*aria-hidden="true"/,
    );
  });
});

describe("a running turn", () => {
  it("holds a daily answer's place with its status and placeholder lines", () => {
    const page = renderToString(
      <TurnRunning mode="daily" createdAt={new Date().toISOString()} />,
    );
    expect(page).toContain("Consultando la publicación…");
    expect(page.match(/class="voc-skeleton"/g)).toHaveLength(3);
    expect(page).not.toContain("progressbar");
  });

  it("shows research time against the 90 s limit, not invented steps", () => {
    const createdAt = new Date(Date.now() - 24_000).toISOString();
    const page = renderToString(
      <TurnRunning mode="research" createdAt={createdAt} />,
    );
    expect(page).toContain("Investigando en varios pasos…");
    expect(page).toContain('role="progressbar"');
    expect(page).toContain('aria-valuemax="90"');
    const now = Number(page.match(/aria-valuenow="(\d+)"/)?.[1]);
    expect(now).toBeGreaterThanOrEqual(24);
    expect(now).toBeLessThanOrEqual(26);
    expect(text(page)).toContain("de ~90 s");
  });
});

describe("each question's mode", () => {
  const turnOf = (fixture: unknown) => {
    const conversation = parseConversation(fixture);
    return renderToString(
      <ChatTurn
        turn={conversation.turns[0]}
        publicationId={conversation.publication_id}
      />,
    );
  };

  it("tags a question with the mode its answer came from", () => {
    expect(turnOf(daily)).toMatch(
      /class="voc-turn-mode"><svg[^]*?<\/svg>Diaria/,
    );
    expect(turnOf(research)).toMatch(
      /class="voc-turn-mode"><svg[^]*?<\/svg>Investigación/,
    );
  });

  it("tags a running question with the mode the page sent", () => {
    const copy = structuredClone(research);
    Object.assign(copy.turns[0], {
      status: "running",
      answer: null,
      finished_at: null,
    });
    const conversation = parseConversation(copy);
    const page = renderToString(
      <ChatTurn
        turn={conversation.turns[0]}
        publicationId={conversation.publication_id}
        runningMode="research"
      />,
    );
    expect(page).toMatch(/class="voc-turn-mode"><svg[^]*?<\/svg>Investigación/);
    expect(page).toContain("voc-turn-progress");
  });
});
