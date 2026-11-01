import type { ReactNode } from "react";
import type {
  AnalysisResult,
  AnalysisResultOf,
  AnalysisKind,
} from "../../../analysis";
import type { Conversation } from "../../../conversation";
import { MoodSummary } from "../../analysis/MoodSummary";
import { View, type ViewSpec } from "../../registry";
import { sections, stepIndex, type Section } from "../AnswerSections";
import { longMonth, MonthShareChart } from "./MonthShareChart";
import { ReviewHighlights } from "./ReviewHighlights";
import { TopicCompare } from "./TopicCompare";

type Answer = NonNullable<Conversation["turns"][number]["answer"]>;
type Step = NonNullable<Answer["steps"]>[number];

// What each step is called on the rail, by its plan's intent.
const STEP_NAME: Record<string, string> = {
  overview: "Estado de los datos",
  sources: "Fuentes",
  coverage: "Cobertura",
  freshness: "Actualización",
  embeddings: "Embeddings",
  mood: "Ánimo general",
  main_complaints: "Queja principal",
  trend: "Evolución mensual",
  topic_reviews: "Reseñas del tema",
  specific_problems: "Problemas específicos",
  focus: "Prioridades",
  praise: "Temas mejor valorados",
  unsupported: "Fuera de alcance",
  clarify: "Aclaración",
};

const monthOf = (step?: Step) => {
  const selection = step?.plan.selection;
  return selection?.month_from && selection.month_from === selection.month_to
    ? selection.month_from
    : null;
};

// The compact visual a step's question calls for, drawn from its own results.
function StepVisual({
  step,
  next,
  results,
}: {
  step: Step;
  next?: Step;
  results: AnalysisResult[];
}) {
  const find = <K extends AnalysisKind>(kind: K) =>
    results.find(
      (result) =>
        result.kind === kind && step.result_ids.includes(result.result_id),
    ) as AnalysisResultOf<K> | undefined;
  const intent = step.plan.intent;
  if (intent === "trend") {
    const timeline = find("timeline");
    return timeline ? (
      <MonthShareChart data={timeline.data} highlight={monthOf(next)} />
    ) : null;
  }
  if (
    intent === "main_complaints" ||
    intent === "specific_problems" ||
    intent === "focus"
  ) {
    const topics = find("topic_list");
    if (!topics) return null;
    const featured =
      find("summary")?.data.most_negative_feature?.topic_id ??
      next?.plan.topic?.topic_id ??
      null;
    const month = monthOf(step);
    return (
      <TopicCompare
        result={topics}
        featured={featured}
        scope={month ? longMonth(month) : null}
      />
    );
  }
  if (intent === "praise") {
    const topics = find("topic_list");
    if (!topics) return null;
    const month = monthOf(step);
    return (
      <TopicCompare
        result={topics}
        featured={next?.plan.topic?.topic_id ?? null}
        scope={month ? longMonth(month) : null}
        measure="positive"
      />
    );
  }
  if (intent === "topic_reviews") {
    const detail = find("topic_detail");
    const reviews = find("reviews");
    return detail && reviews ? (
      <ReviewHighlights
        detail={detail}
        reviews={reviews}
        labels={step.plan.selection?.labels ?? null}
      />
    ) : null;
  }
  if (intent === "mood") {
    const summary = find("summary");
    return summary ? <MoodSummary result={summary} /> : null;
  }
  return null;
}

// A research answer as a short report: the opening, then one block per step
// on a numbered rail (its finding, a visual made for it, and its full text and
// panels folded), then the interpretation and the limits, folded.
export function ResearchReport({
  answer,
  publicationId,
  onAsk,
}: {
  answer: Answer;
  publicationId: string;
  onAsk?: (question: string) => void;
}) {
  const steps = answer.steps ?? [];
  const parts = sections(answer.text);
  // Each visual looks its result up by kind and id; dataset results never match.
  const results = answer.results as AnalysisResult[];
  const paragraph = (index: number) =>
    parts.find((part) => stepIndex(part) === index) as
      Extract<Section, { kind: "section" }> | undefined;
  const closing = parts.filter(
    (part) => part.kind === "section" && stepIndex(part) === null,
  ) as Extract<Section, { kind: "section" }>[];
  const viewsOf = (step: Step) =>
    answer.views.filter((view) =>
      view.result_ids.every((id) => step.result_ids.includes(id)),
    );
  const claimed = new Set(steps.flatMap(viewsOf));
  const loose = answer.views.filter((view) => !claimed.has(view));
  const panels = (views: ViewSpec[]): ReactNode =>
    views.map((spec) => (
      <View
        key={spec.component + spec.result_ids.join()}
        spec={spec}
        results={answer.results}
        publicationId={publicationId}
        onAsk={onAsk}
      />
    ));
  const lead = answer.lead;
  return (
    <div className="voc-research">
      {lead && (
        <section className="voc-answer-lead" aria-label="Respuesta breve">
          <p className="voc-answer-lead-text">{lead.text}</p>
          <p className="voc-answer-lead-note">
            Resumen del modelo · las cifras vienen del análisis completo
          </p>
        </section>
      )}
      <ol className="voc-research-steps">
        {steps.map((step, index) => {
          const text = paragraph(index);
          // The model's line for this step when there is one; otherwise the
          // server's own paragraph, so the finding is never missing.
          const finding = lead?.steps[index] ?? text?.text ?? null;
          const detailText = lead?.steps[index] ? text?.text : null;
          // A step that reads only data an earlier step fetched (main complaint,
          // then specific problems) gets a note, not the same chart again.
          const source = steps.findIndex(
            (earlier, at) =>
              at < index &&
              step.result_ids.length > 0 &&
              step.result_ids.every((id) => earlier.result_ids.includes(id)),
          );
          const views = source >= 0 ? [] : viewsOf(step);
          return (
            <li key={index} className="voc-research-step">
              <span className="voc-research-marker" aria-hidden="true">
                {index + 1}
              </span>
              <div className="voc-research-body">
                <h3 className="voc-research-label">
                  {`Paso ${index + 1} · ${STEP_NAME[step.plan.intent] ?? step.plan.intent}`}
                </h3>
                {finding && <p className="voc-research-finding">{finding}</p>}
                {source >= 0 ? (
                  <p className="voc-meta">
                    {`Con los mismos datos del paso ${source + 1}: otra lectura de la misma evidencia.`}
                  </p>
                ) : (
                  <StepVisual
                    step={step}
                    next={steps[index + 1]}
                    results={results}
                  />
                )}
                {(detailText || views.length > 0) && (
                  <details className="voc-answer-more">
                    <summary>
                      {views.length > 0
                        ? "Ver el detalle y el panel completo"
                        : "Ver el detalle"}
                    </summary>
                    <div className="voc-research-detail">
                      {detailText && (
                        <p className="voc-answer-text">{detailText}</p>
                      )}
                      {panels(views)}
                    </div>
                  </details>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      {loose.length > 0 && (
        <div className="voc-answer-views">{panels(loose)}</div>
      )}
      <div className="voc-answer-folds">
        {closing.map((part) => (
          <details key={part.label} className="voc-answer-more">
            <summary>{part.label}</summary>
            <p className="voc-answer-text">{part.text}</p>
          </details>
        ))}
      </div>
    </div>
  );
}
