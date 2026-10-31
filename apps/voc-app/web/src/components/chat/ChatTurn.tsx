import type { Conversation, Mode } from "../../conversation";
import { View } from "../registry";
import { AnswerFooter } from "./AnswerFooter";
import { AnswerSections } from "./AnswerSections";
import { TurnFailed, TurnRunning } from "./TurnStatus";

type Turn = Conversation["turns"][number];

// Small blocks share a row; lists, tiles, timelines, a topic and the dataset
// panels take the full width.
const WIDE = new Set([
  "kpi_tiles",
  "topic_list",
  "timeline",
  "topic_detail",
  "dataset_summary",
  "sources",
  "coverage",
  "preparation",
]);

const seconds = new Intl.NumberFormat("es-MX", { maximumFractionDigits: 1 });
function elapsed(from: string, to: string | null) {
  if (!to) return null;
  const ms = Date.parse(to) - Date.parse(from);
  if (!Number.isFinite(ms) || ms < 0) return null;
  const s = ms / 1000;
  if (s < 10) return `${seconds.format(Math.max(s, 0.1))} s`;
  if (s < 60) return `${Math.round(s)} s`;
  return `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
}

// One question and its answer: the question, one status line, the answer's
// sections, the panels it cites, and the evidence footer.
export function ChatTurn({
  turn,
  publicationId,
  runningMode = "daily",
  onAskAgain,
}: {
  turn: Turn;
  publicationId: string;
  // A running turn has no answer yet, so the page says which mode it sent.
  runningMode?: Mode;
  onAskAgain?: (question: string) => void;
}) {
  const answer = turn.answer;
  const status = answer
    ? [
        answer.mode === "research"
          ? `Investigación · ${answer.steps?.length ?? 0} pasos`
          : "Diaria",
        elapsed(turn.created_at, turn.finished_at),
        turn.planner_mode === "fixture"
          ? "Planificador de prueba · Sin llamadas a un modelo"
          : "Intención interpretada por el modelo",
      ]
        .filter(Boolean)
        .join(" · ")
    : null;
  return (
    <article className="voc-turn">
      <p className="voc-turn-question">
        <span className="voc-sr">Tú: </span>
        {turn.question}
      </p>
      {turn.status === "running" && (
        <TurnRunning mode={runningMode} createdAt={turn.created_at} />
      )}
      {turn.error && (
        <TurnFailed
          message={turn.error.message}
          onAskAgain={onAskAgain ? () => onAskAgain(turn.question) : undefined}
        />
      )}
      {answer && (
        <>
          <p className="voc-turn-status">{status}</p>
          <AnswerSections text={answer.text} />
          {answer.views.length > 0 && (
            <div className="voc-answer-views">
              {answer.views.map((spec) => (
                <div
                  key={spec.component}
                  className={WIDE.has(spec.component) ? "is-wide" : undefined}
                >
                  <View
                    spec={spec}
                    results={answer.results}
                    publicationId={publicationId}
                  />
                </div>
              ))}
            </div>
          )}
          <AnswerFooter answer={answer} />
        </>
      )}
    </article>
  );
}
