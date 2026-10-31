import type { Conversation, Mode } from "../../conversation";
import { View, type ViewSpec } from "../registry";
import { AnswerFooter } from "./AnswerFooter";
import {
  AnswerSections,
  DATA,
  LIMITS,
  sections,
  stepIndex,
  type Section,
} from "./AnswerSections";
import { TurnFailed, TurnRunning } from "./TurnStatus";

type Turn = Conversation["turns"][number];
type Answer = NonNullable<Turn["answer"]>;

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
// Within an answer: the summary first, then charts, then lists, then a topic.
const ORDER = [
  "kpi_tiles",
  "mood_ring",
  "timeline",
  "platforms",
  "topic_list",
  "topic_detail",
  "dataset_summary",
  "sources",
  "coverage",
  "preparation",
];
const ordered = (views: ViewSpec[]) =>
  [...views].sort(
    (a, b) => ORDER.indexOf(a.component) - ORDER.indexOf(b.component),
  );

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

// Which step drew a view: the step whose evidence holds all of the view's ids.
function stepOf(answer: Answer, view: ViewSpec) {
  const index = (answer.steps ?? []).findIndex((step) =>
    view.result_ids.every((id) => step.result_ids.includes(id)),
  );
  return index < 0 ? null : index;
}

function Panels({
  views,
  answer,
  publicationId,
}: {
  views: ViewSpec[];
  answer: Answer;
  publicationId: string;
}) {
  if (views.length === 0) return null;
  return (
    <div className="voc-answer-views">
      {ordered(views).map((spec) => (
        <div
          key={spec.component + spec.result_ids.join()}
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
  );
}

// The model's opening: the finding first, then one line per research step.
function AnswerLead({ lead }: { lead: NonNullable<Answer["lead"]> }) {
  return (
    <section className="voc-answer-lead" aria-label="Respuesta breve">
      <p className="voc-answer-lead-text">{lead.text}</p>
      {lead.steps.length > 0 && (
        <ol className="voc-answer-chain">
          {lead.steps.map((step, index) => (
            <li key={index}>{step}</li>
          ))}
        </ol>
      )}
      <p className="voc-answer-lead-note">
        Resumen del modelo · las cifras vienen del análisis completo
      </p>
    </section>
  );
}

// One question and its answer. With an opening, the answer leads with it and
// its key panels, and folds the full analysis and its limits; without one, the
// analysis shows in full with each panel beside the text it supports.
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
  const research = answer?.mode === "research";
  const status = answer
    ? [
        research
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

  let body = null;
  if (answer) {
    const parts = sections(answer.text);
    const limits = parts.find(
      (part) => part.kind === "section" && part.label === LIMITS,
    );
    const analysis = parts.filter((part) => part !== limits);
    // Research: each step's panels; the last step with panels is the outcome.
    const byStep = (index: number) =>
      answer.views.filter((view) => stepOf(answer, view) === index);
    const outcome = research
      ? Math.max(-1, ...answer.views.map((view) => stepOf(answer, view) ?? -1))
      : -1;
    const placed = (part: Section, shown: number | null) => {
      if (research) {
        const step = stepIndex(part);
        if (step === null || step === shown) return null;
        return (
          <Panels
            views={byStep(step)}
            answer={answer}
            publicationId={publicationId}
          />
        );
      }
      return null;
    };
    // Views no step claims, so nothing is ever dropped.
    const loose = research
      ? answer.views.filter((view) => stepOf(answer, view) === null)
      : [];

    if (answer.lead) {
      const key = research ? byStep(outcome).concat(loose) : answer.views;
      body = (
        <>
          <AnswerLead lead={answer.lead} />
          <Panels views={key} answer={answer} publicationId={publicationId} />
          <div className="voc-answer-folds">
            <details className="voc-answer-more">
              <summary>Ver el análisis completo</summary>
              <AnswerSections
                parts={analysis}
                after={(part) => placed(part, outcome)}
              />
            </details>
            {limits && limits.kind === "section" && (
              <details className="voc-answer-more">
                <summary>{LIMITS}</summary>
                <p className="voc-answer-text">{limits.text}</p>
              </details>
            )}
          </div>
        </>
      );
    } else {
      const data = analysis.findIndex(
        (part) => part.kind === "section" && part.label === DATA,
      );
      body = (
        <>
          <AnswerSections
            parts={analysis}
            after={(part, index) =>
              research ? (
                placed(part, null)
              ) : index === Math.max(data, 0) ? (
                <Panels
                  views={answer.views}
                  answer={answer}
                  publicationId={publicationId}
                />
              ) : null
            }
          />
          <Panels views={loose} answer={answer} publicationId={publicationId} />
          {limits && <AnswerSections parts={[limits]} />}
        </>
      );
    }
  }

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
          {body}
          <AnswerFooter answer={answer} />
        </>
      )}
    </article>
  );
}
