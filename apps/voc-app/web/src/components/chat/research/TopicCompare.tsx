import type { AnalysisResultOf } from "../../../analysis";
import { number } from "../../format";
import { percent } from "../../analysis/shared";

const TOP = 5;
// The server's ranking rules: feature topics (one feature, or mixed) with at least
// five classified reviews, ordered by the 95% Wilson lower bound of the share.
const MINIMUM = 5;
const Z = 1.959963984540054;
function wilsonLower(hits: number, total: number) {
  if (total <= 0) return 0;
  const p = hits / total;
  const lower =
    (p +
      (Z * Z) / (2 * total) -
      Z * Math.sqrt((p * (1 - p)) / total + (Z * Z) / (4 * total * total))) /
    (1 + (Z * Z) / total);
  return Math.max(0, Math.min(1, lower));
}

type Row = AnalysisResultOf<"topic_list">["data"][number];
const measures = {
  // Complaints: the bound each row carries from the server.
  negative: {
    count: (row: Row) => row.negative,
    share: (row: Row) => row.negative_share,
    bound: (row: Row) => row.wilson_lower ?? 0,
    word: "negativas",
    tone: "is-negative",
  },
  // Praise: the same bound, of the positive share.
  positive: {
    count: (row: Row) => row.positive,
    share: (row: Row) => row.positive_share,
    bound: (row: Row) => wilsonLower(row.positive, row.classified),
    word: "positivas",
    tone: "is-positive",
  },
} as const;

// The step's topics side by side, in the order the answer ranks them (the
// share at 95% confidence), with the featured topic marked: most negative for a
// complaint step, best rated for a praise step.
export function TopicCompare({
  result,
  featured,
  scope,
  measure = "negative",
}: {
  result: AnalysisResultOf<"topic_list">;
  featured: number | null;
  // Where the comparison happens, such as "agosto de 2026".
  scope: string | null;
  measure?: keyof typeof measures;
}) {
  const m = measures[measure];
  const rows = result.data
    .filter(
      (row) =>
        row.topic_id !== -1 &&
        (row.assessment === "single_feature" || row.assessment === "mixed") &&
        row.classified >= MINIMUM,
    )
    .sort(
      (a, b) =>
        m.bound(b) - m.bound(a) ||
        b.classified - a.classified ||
        a.topic_id - b.topic_id,
    );
  // The featured topic always shows, even if it falls outside the first five.
  const mark = featured ?? rows[0]?.topic_id ?? null;
  const shown = rows.slice(0, TOP);
  const pick = rows.find((row) => row.topic_id === mark);
  if (pick && !shown.includes(pick)) shown.push(pick);
  if (shown.length === 0)
    return (
      <p className="voc-hint">
        {`Ningún tema de una funcionalidad tiene al menos ${MINIMUM} reseñas clasificadas aquí.`}
      </p>
    );
  return (
    <figure className={`voc-compare ${m.tone}`}>
      <ol>
        {shown.map((row) => {
          const marked = row.topic_id === mark;
          return (
            <li key={row.topic_id} className={marked ? "is-marked" : undefined}>
              <span className="voc-compare-name">
                <span className="voc-meta">Tema {row.topic_id}</span>
                <span>{row.display_name}</span>
                {marked && (
                  <span className="voc-compare-tag">
                    {measure === "positive" ? "Mejor valorado" : "Destacado"}
                  </span>
                )}
              </span>
              <span
                className="voc-compare-track"
                aria-hidden="true"
                title={`${number(m.count(row))} de ${number(row.classified)} ${m.word}`}
              >
                <span style={{ width: `${(m.share(row) ?? 0) * 100}%` }} />
              </span>
              <span className="voc-compare-value">
                <span className="voc-num">{percent(m.share(row))}</span>
                <span className="voc-meta">
                  {number(m.count(row))} de {number(row.classified)}
                </span>
              </span>
            </li>
          );
        })}
      </ol>
      <figcaption className="voc-meta">
        Temas de una funcionalidad{scope ? ` en ${scope}` : ""}, ordenados por
        su proporción de {m.word} con 95% de confianza
      </figcaption>
    </figure>
  );
}
