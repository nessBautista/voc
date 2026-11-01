import type { AnalysisResultOf, Label } from "../../../analysis";
import { number, platformName } from "../../format";
import { percent, shortMonth, StackedBar } from "../../analysis/shared";
import { Highlighted, SentimentBadge } from "../../detail/ReviewFeed";

const QUOTES = 3;
const plural: Record<Label, string> = {
  negative: "negativas",
  neutral: "neutras",
  positive: "positivas",
};

// A topic's mix in one line and the first reviews of its list, verbatim, so
// the step that "shows reviews" shows them. The list is ordered by sentiment
// and confidence, not by how representative a review is; the caption says so.
export function ReviewHighlights({
  detail,
  reviews,
  labels,
}: {
  detail: AnalysisResultOf<"topic_detail">;
  reviews: AnalysisResultOf<"reviews">;
  // The step's sentiment filter. Filtered, the counts cover only those labels,
  // so a share would read 100%: the card shows the count instead.
  labels?: Label[] | null;
}) {
  const data = detail.data;
  const only = labels && labels.length < 3 ? labels : null;
  const named = only ? only.map((label) => plural[label]).join(" y ") : null;
  const share = data.classified > 0 ? data.negative / data.classified : null;
  const items = reviews.data.items.slice(0, QUOTES);
  return (
    <figure className="voc-highlights">
      {only ? (
        <div className="voc-highlights-head">
          <p className="voc-highlights-stat">
            <span className="voc-num">
              {number(only.reduce((sum, label) => sum + data[label], 0))}
            </span>
            <span>{`reseñas ${named} (solo ${named}, como pidió el paso)`}</span>
          </p>
        </div>
      ) : (
        <div className="voc-highlights-head">
          <p className="voc-highlights-stat">
            <span className="voc-num voc-text-negative">{percent(share)}</span>
            <span>
              negativas · {number(data.negative)} de {number(data.classified)}{" "}
              reseñas clasificadas
            </span>
          </p>
          <StackedBar
            negative={data.negative}
            neutral={data.neutral}
            positive={data.positive}
            scale={data.classified}
            label={`Negativas ${number(data.negative)}, neutras ${number(data.neutral)}, positivas ${number(data.positive)}`}
          />
        </div>
      )}
      {items.length > 0 ? (
        <ol>
          {items.map((item) => (
            <li key={item.record_id} className="voc-review">
              <blockquote lang="es">
                <Highlighted text={item.text} keywords={data.keywords} />
              </blockquote>
              <p className="voc-review-meta">
                <SentimentBadge
                  label={item.label}
                  confidence={item.confidence}
                />
                <span>{platformName(item.platform)}</span>
                {item.month && <span>{shortMonth(item.month)}</span>}
                {item.rating !== null && <span>★ {item.rating}</span>}
              </p>
            </li>
          ))}
        </ol>
      ) : (
        <p className="voc-hint">Ninguna reseña cumple los filtros.</p>
      )}
      <figcaption className="voc-meta">
        {items.length > 0
          ? `${items.length} de ${number(reviews.data.total)} reseñas${named ? ` ${named}` : ""}, las primeras de la lista: ${named ? "las de mayor confianza" : "negativas con mayor confianza"}`
          : ""}
      </figcaption>
    </figure>
  );
}
