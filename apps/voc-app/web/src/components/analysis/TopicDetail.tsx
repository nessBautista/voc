import type { AnalysisResultOf, Review } from "../../analysis";
import { number } from "../format";
import { assessmentName, percent, PlatformTag, shortMonth } from "./shared";
import { Timeline } from "./Timeline";

export function ReviewList({
  result,
  onPage,
}: {
  result: AnalysisResultOf<"reviews">;
  onPage?: (page: number) => void;
}) {
  const data = result.data;
  return (
    <div className="review-list">
      {data.items.length === 0 && (
        <p className="hint">Ninguna reseña cumple los filtros.</p>
      )}
      <ol>
        {data.items.map((item: Review) => (
          <li key={item.record_id} className="review">
            <div className="review-meta">
              <PlatformTag platform={item.platform} />
              {item.label ? (
                <span className={`badge-label seg-${item.label}`}>
                  {item.label === "negative"
                    ? "Negativa"
                    : item.label === "neutral"
                      ? "Neutra"
                      : "Positiva"}
                </span>
              ) : (
                <span className="badge-label">Sin clasificar</span>
              )}
              {item.month && <span>{shortMonth(item.month)}</span>}
              {item.rating !== null && <span>{item.rating} ★</span>}
              {item.confidence !== null && (
                <span className="confidence">{percent(item.confidence)}</span>
              )}
            </div>
            <blockquote lang="es">{item.text}</blockquote>
          </li>
        ))}
      </ol>
      {data.pages > 1 && onPage && (
        <div className="pager">
          <button
            type="button"
            disabled={data.page <= 1}
            onClick={() => onPage(data.page - 1)}
          >
            ← Anterior
          </button>
          <span>
            {number(data.total)} reseñas · página {data.page} de {data.pages}
          </span>
          <button
            type="button"
            disabled={data.page >= data.pages}
            onClick={() => onPage(data.page + 1)}
          >
            Siguiente →
          </button>
        </div>
      )}
    </div>
  );
}

export function TopicDetail({
  detail,
  timeline,
  reviews,
  onPage,
  onClose,
}: {
  detail: AnalysisResultOf<"topic_detail">;
  timeline?: AnalysisResultOf<"timeline">;
  reviews?: AnalysisResultOf<"reviews">;
  onPage?: (page: number) => void;
  onClose?: () => void;
}) {
  const data = detail.data;
  const mix = (value: number) =>
    data.classified > 0 ? value / data.classified : null;
  return (
    <div className="topic-detail">
      <div className="topic-detail-head">
        <div>
          <p className="eyebrow">
            {data.topic_id === -1
              ? "Sin asignar"
              : `Tema ${data.topic_id} · ${assessmentName[data.assessment]}`}
          </p>
          <h3 className="topic-detail-title">{data.display_name}</h3>
          <p className="meta">
            {number(data.in_view)} de {number(data.member_count)} reseñas
            cumplen los filtros · negativas {number(data.negative)} · neutras{" "}
            {number(data.neutral)} · positivas {number(data.positive)}
          </p>
        </div>
        {onClose && (
          <button type="button" onClick={onClose}>
            Cerrar
          </button>
        )}
      </div>
      <div className="topic-detail-grid">
        <div>
          <p className="topic-description" lang="es">
            {data.description}
          </p>
          {data.proposed_feature && (
            <p className="meta">
              Funcionalidad propuesta: <strong>{data.proposed_feature}</strong>
            </p>
          )}
          <ul className="keyword-chips" aria-label="Palabras clave">
            {data.keywords.map((keyword) => (
              <li key={keyword}>{keyword}</li>
            ))}
          </ul>
          <dl className="mix-bars">
            {(["negative", "neutral", "positive"] as const).map((label) => (
              <div key={label}>
                <dt>
                  {label === "negative"
                    ? "negativas"
                    : label === "neutral"
                      ? "neutras"
                      : "positivas"}
                </dt>
                <dd>
                  <div className="mix-track">
                    <div
                      className={`seg-${label}`}
                      style={{ width: `${(mix(data[label]) ?? 0) * 100}%` }}
                    />
                  </div>
                  <span>{percent(mix(data[label]))}</span>
                </dd>
              </div>
            ))}
          </dl>
        </div>
        {timeline && (
          <div>
            <p className="eyebrow">Este tema en el tiempo</p>
            <Timeline data={timeline.data} compact />
          </div>
        )}
      </div>
      {reviews && <ReviewList result={reviews} onPage={onPage} />}
    </div>
  );
}
