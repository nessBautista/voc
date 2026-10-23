import type { AnalysisResultOf } from "../../analysis";
import { number } from "../format";
import { percent } from "./shared";

export function KpiTiles({
  result,
  onOpenTopic,
}: {
  result: AnalysisResultOf<"summary">;
  onOpenTopic?: (topicId: number) => void;
}) {
  const data = result.data;
  const feature = data.most_negative_feature;
  return (
    <section className="kpi-tiles" aria-label="Indicadores de la selección">
      <div className="tile">
        <strong>{number(data.reviews_in_view)}</strong>
        <span className="tile-label">Reseñas en vista</span>
        <span className="tile-context">
          de {number(data.reviews_total)} publicadas
        </span>
      </div>
      <div className="tile">
        <strong>{number(data.topics_in_view)}</strong>
        <span className="tile-label">Temas representados</span>
        <span className="tile-context">
          de {number(data.topics_total)} encontrados
        </span>
      </div>
      <div className="tile">
        <strong>{percent(data.negative.share)}</strong>
        <span className="tile-label">Negativas</span>
        <span className="tile-context">
          {number(data.negative.count)} reseñas
        </span>
      </div>
      <div className="tile">
        <strong>{percent(data.positive.share)}</strong>
        <span className="tile-label">Positivas</span>
        <span className="tile-context">
          {number(data.positive.count)} reseñas
        </span>
      </div>
      <div className="tile tile-feature">
        {feature ? (
          <>
            <strong className="tile-title">
              {onOpenTopic ? (
                <button
                  type="button"
                  className="link-button"
                  onClick={() => onOpenTopic(feature.topic_id)}
                >
                  {feature.display_name}
                </button>
              ) : (
                feature.display_name
              )}
            </strong>
            <span className="tile-label">Tema negativo destacado</span>
            <span className="tile-context">
              {percent(feature.share)} de {number(feature.total)} reseñas · al
              menos {percent(feature.wilson_lower)} (límite 95 %)
            </span>
          </>
        ) : (
          <>
            <strong className="tile-title">—</strong>
            <span className="tile-label">Tema negativo destacado</span>
            <span className="tile-context">
              Sin tema de funcionalidad con 5 o más reseñas en vista
            </span>
          </>
        )}
      </div>
    </section>
  );
}
