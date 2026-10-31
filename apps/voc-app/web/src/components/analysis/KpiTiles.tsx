import type { AnalysisResultOf } from "../../analysis";
import { number } from "../format";
import { percent } from "./shared";

// The selection in four counts, in one bordered strip. The strongest negative
// topic has its own component, TopSignal, above the tiles.
export function KpiTiles({ result }: { result: AnalysisResultOf<"summary"> }) {
  const data = result.data;
  const tiles = [
    [
      number(data.reviews_in_view),
      "Reseñas en vista",
      `de ${number(data.reviews_total)} publicadas`,
    ],
    [
      number(data.topics_in_view),
      "Temas representados",
      `de ${number(data.topics_total)} encontrados`,
    ],
    [
      percent(data.negative.share),
      "Negativas",
      `${number(data.negative.count)} reseñas`,
    ],
    [
      percent(data.positive.share),
      "Positivas",
      `${number(data.positive.count)} reseñas`,
    ],
  ];
  return (
    <section className="voc-kpis" aria-label="Indicadores de la selección">
      {tiles.map(([value, label, context]) => (
        <div className="voc-kpi" key={label}>
          <span className="voc-kpi-value">{value}</span>
          <span className="voc-kpi-label">{label}</span>
          <span className="voc-meta">{context}</span>
        </div>
      ))}
    </section>
  );
}
