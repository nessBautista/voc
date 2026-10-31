import { Fragment } from "react";
import {
  labels as allLabels,
  type AnalysisResultOf,
  type Label,
  type Review,
} from "../../analysis";
import { number, platformName } from "../format";
import { percent, shortMonth } from "../analysis/shared";

const one: Record<Label, string> = {
  negative: "Negativa",
  neutral: "Neutra",
  positive: "Positiva",
};
const many: Record<Label, string> = {
  negative: "Negativas",
  neutral: "Neutras",
  positive: "Positivas",
};

// The model's label as a word with a dot; its confidence sits in the tooltip.
export function SentimentBadge({
  label,
  confidence,
}: {
  label: Label | null;
  confidence: number | null;
}) {
  return (
    <span
      className={`voc-sentiment is-${label ?? "unclassified"}`}
      title={
        confidence === null
          ? undefined
          : `Confianza del modelo: ${percent(confidence)}`
      }
    >
      <span className="voc-dot" aria-hidden="true" />
      {label ? one[label] : "Sin clasificar"}
    </span>
  );
}

// The review's own words, verbatim, with the topic's keywords in semi-bold.
// Whole words only, in any case; letters with accents count as letters.
export function Highlighted({
  text,
  keywords,
}: {
  text: string;
  keywords: string[];
}) {
  const words = keywords
    .filter((keyword) => keyword.trim() !== "")
    .map((keyword) => keyword.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (words.length === 0) return <>{text}</>;
  const pattern = new RegExp(
    `(?<![\\p{L}\\p{N}])(${words.join("|")})(?![\\p{L}\\p{N}])`,
    "giu",
  );
  // split() with one capture group puts the matches at the odd indexes.
  return (
    <>
      {text.split(pattern).map((piece, index) =>
        index % 2 === 1 ? (
          <span key={index} className="voc-kw">
            {piece}
          </span>
        ) : (
          <Fragment key={index}>{piece}</Fragment>
        ),
      )}
    </>
  );
}

const named = (chosen: Set<Label>) => {
  const names = allLabels
    .filter((label) => chosen.has(label))
    .map((label) => many[label].toLowerCase());
  return names.length > 1
    ? `${names.slice(0, -1).join(", ")} y ${names[names.length - 1]}`
    : names[0];
};

// A topic's reviews, text first. The sentiment filter is the dashboard's own:
// choosing "Negativas" here sets the chips, the tiles and the charts too.
export function ReviewFeed({
  result,
  keywords,
  supportingIds,
  counts,
  labels,
  onLabels,
  onPage,
}: {
  result: AnalysisResultOf<"reviews">;
  keywords: string[];
  supportingIds?: string[] | null;
  // The topic's counts read without the sentiment filter, so the segments'
  // numbers do not collapse when the filter is used.
  counts?: { all: number } & Record<Label, number>;
  labels?: Set<Label>;
  onLabels?: (next: Set<Label>) => void;
  onPage?: (page: number) => void;
}) {
  const data = result.data;
  const supporting = new Set(supportingIds ?? []);
  const filtered = labels !== undefined && labels.size < allLabels.length;
  const showAll = () => onLabels?.(new Set(allLabels));
  return (
    <section className="voc-feed" aria-label="Reseñas del tema">
      {labels && onLabels && counts && (
        <div className="voc-feed-tools">
          <div
            className="voc-segmented"
            role="group"
            aria-label="Filtrar por sentimiento"
            title="Este filtro también se aplica al resto del panel"
          >
            <button type="button" aria-pressed={!filtered} onClick={showAll}>
              Todas<span className="voc-seg-count">{number(counts.all)}</span>
            </button>
            {allLabels.map((label) => (
              <button
                key={label}
                type="button"
                aria-pressed={labels.size === 1 && labels.has(label)}
                onClick={() => onLabels(new Set([label]))}
              >
                {many[label]}
                <span className="voc-seg-count">{number(counts[label])}</span>
              </button>
            ))}
          </div>
        </div>
      )}
      {filtered && labels && onLabels && (
        <p className="voc-filter-status">
          Mostrando {named(labels)} ·{" "}
          <button type="button" className="voc-link" onClick={showAll}>
            Ver todas
          </button>
        </p>
      )}
      {data.items.length === 0 && (
        <p className="voc-hint">Ninguna reseña cumple los filtros.</p>
      )}
      <ol>
        {data.items.map((item: Review) => (
          <li key={item.record_id} className="voc-review">
            <p className="voc-review-meta">
              <SentimentBadge label={item.label} confidence={item.confidence} />
              <span>{platformName(item.platform)}</span>
              {item.month && <span>{shortMonth(item.month)}</span>}
              {item.rating !== null && <span>★ {item.rating}</span>}
              {supporting.has(item.record_id) && (
                <span className="voc-rep">Representativa</span>
              )}
            </p>
            <blockquote lang="es">
              <Highlighted text={item.text} keywords={keywords} />
            </blockquote>
          </li>
        ))}
      </ol>
      {data.pages > 1 && onPage && (
        <nav className="voc-pager" aria-label="Páginas de reseñas">
          <button
            type="button"
            className="voc-btn voc-btn-ghost voc-btn-sm"
            disabled={data.page <= 1}
            onClick={() => onPage(data.page - 1)}
          >
            ← Anterior
          </button>
          <span className="voc-meta">
            {number(data.total)} reseñas · página {data.page} de {data.pages}
          </span>
          <button
            type="button"
            className="voc-btn voc-btn-ghost voc-btn-sm"
            disabled={data.page >= data.pages}
            onClick={() => onPage(data.page + 1)}
          >
            Siguiente →
          </button>
        </nav>
      )}
    </section>
  );
}
