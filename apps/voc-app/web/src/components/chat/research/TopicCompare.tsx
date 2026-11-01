import type { AnalysisResultOf } from "../../../analysis";
import { number } from "../../format";
import { percent } from "../../analysis/shared";

const TOP = 5;

// The step's topics side by side: feature topics in the order the answer
// ranks them, by the share of negatives at 95% confidence (the Wilson bound
// each row carries), with the featured topic marked.
export function TopicCompare({
  result,
  featured,
  scope,
}: {
  result: AnalysisResultOf<"topic_list">;
  featured: number | null;
  // Where the comparison happens, such as "agosto de 2026".
  scope: string | null;
}) {
  const rows = result.data
    .filter((row) => row.topic_id !== -1 && row.assessment === "single_feature")
    .sort((a, b) => (b.wilson_lower ?? 0) - (a.wilson_lower ?? 0));
  // The featured topic always shows, even if it falls outside the first five.
  const shown = rows.slice(0, TOP);
  const pick = rows.find((row) => row.topic_id === featured);
  if (pick && !shown.includes(pick)) shown.push(pick);
  if (shown.length === 0)
    return (
      <p className="voc-hint">
        Ningún tema de una funcionalidad tiene reseñas clasificadas aquí.
      </p>
    );
  return (
    <figure className="voc-compare">
      <ol>
        {shown.map((row) => {
          const marked = row.topic_id === featured;
          return (
            <li key={row.topic_id} className={marked ? "is-marked" : undefined}>
              <span className="voc-compare-name">
                <span className="voc-meta">Tema {row.topic_id}</span>
                <span>{row.display_name}</span>
                {marked && <span className="voc-compare-tag">Destacado</span>}
              </span>
              <span
                className="voc-compare-track"
                aria-hidden="true"
                title={`${number(row.negative)} de ${number(row.classified)} negativas`}
              >
                <span
                  style={{ width: `${(row.negative_share ?? 0) * 100}%` }}
                />
              </span>
              <span className="voc-compare-value">
                <span className="voc-num">{percent(row.negative_share)}</span>
                <span className="voc-meta">
                  {number(row.negative)} de {number(row.classified)}
                </span>
              </span>
            </li>
          );
        })}
      </ol>
      <figcaption className="voc-meta">
        Temas de una funcionalidad{scope ? ` en ${scope}` : ""}, ordenados por
        su proporción de negativas con 95% de confianza
      </figcaption>
    </figure>
  );
}
