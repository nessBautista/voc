import { useId } from "react";
import type { AnalysisResultOf } from "../../analysis";
import { number } from "../format";
import { percent } from "./shared";

// The dashboard's strongest negative signal: the feature topic with the
// highest reliable negative share, from the summary, as one full-width alert.
export function TopSignal({
  result,
  onOpenTopic,
}: {
  result: AnalysisResultOf<"summary">;
  onOpenTopic?: (topicId: number) => void;
}) {
  const id = useId();
  const feature = result.data.most_negative_feature;
  if (!feature)
    return (
      <section className="voc-signal is-empty" aria-labelledby={id}>
        <div className="voc-signal-main">
          <p className="voc-signal-eyebrow" id={id}>
            Señal negativa más fuerte
          </p>
          <p className="voc-signal-stat">
            Ningún tema de funcionalidad con 5 o más reseñas en vista.
          </p>
        </div>
      </section>
    );
  return (
    <section className="voc-signal" aria-labelledby={id}>
      <div className="voc-signal-main">
        <p className="voc-signal-eyebrow" id={id}>
          <span className="voc-dot" aria-hidden="true" />
          Señal negativa más fuerte
        </p>
        <h2 className="voc-signal-title">{feature.display_name}</h2>
        <p className="voc-signal-stat">
          <span className="voc-signal-value">{percent(feature.share)}</span>
          <span>
            de sus {number(feature.total)} reseñas son negativas · al menos{" "}
            {percent(feature.wilson_lower)} (95%)
          </span>
        </p>
      </div>
      {onOpenTopic && (
        <button
          type="button"
          className="voc-btn"
          onClick={() => onOpenTopic(feature.topic_id)}
        >
          Ver tema →
        </button>
      )}
    </section>
  );
}
