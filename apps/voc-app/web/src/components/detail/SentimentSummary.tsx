import type { AnalysisResultOf, Label } from "../../analysis";
import { number } from "../format";
import { percent, StackedBar } from "../analysis/shared";

const plural: Record<Label, string> = {
  negative: "negativas",
  neutral: "neutras",
  positive: "positivas",
};
const word: Record<Label, string> = {
  negative: "Negativas",
  neutral: "Neutras",
  positive: "Positivas",
};
const order: Label[] = ["negative", "neutral", "positive"];

// The topic's mix in one sentence, how sure it is, and the split under one
// bar. Read without the sentiment filter, so it stays whole while filtering.
export function SentimentSummary({
  detail,
}: {
  detail: AnalysisResultOf<"topic_detail">;
}) {
  const data = detail.data;
  const share = (label: Label) =>
    data.classified > 0 ? data[label] / data.classified : null;
  if (data.classified === 0)
    return (
      <section className="voc-summary" aria-label="Sentimiento del tema">
        <p className="voc-summary-head">
          <span className="voc-summary-text">Sin reseñas clasificadas</span>
        </p>
      </section>
    );
  // The largest share leads; ties go to the first in reading order.
  const lead = order.reduce((best, label) =>
    data[label] > data[best] ? label : best,
  );
  const tone = lead === "neutral" ? "" : ` voc-text-${lead}`;
  return (
    <section className="voc-summary" aria-label="Sentimiento del tema">
      <p className="voc-summary-head">
        <span className={`voc-summary-value${tone}`}>
          {percent(share(lead))}
        </span>
        <span className="voc-summary-text">
          de las reseñas son {plural[lead]}
        </span>
      </p>
      <p className="voc-meta">
        {lead === "negative" && data.wilson_lower !== null
          ? `Al menos ${percent(data.wilson_lower)} con 95% de confianza`
          : `${number(data[lead])} de ${number(data.classified)} reseñas clasificadas`}
      </p>
      <StackedBar
        negative={data.negative}
        neutral={data.neutral}
        positive={data.positive}
        scale={data.classified}
        label={order
          .map(
            (label) =>
              `${word[label]} ${number(data[label])} (${percent(share(label))})`,
          )
          .join(", ")}
      />
      <dl className="voc-summary-split">
        {order.map((label) => (
          <div key={label}>
            <dt>
              <span
                className={`voc-swatch voc-fill-${label}`}
                aria-hidden="true"
              />
              {word[label]}
            </dt>
            <dd>
              {number(data[label])} <span>· {percent(share(label))}</span>
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
