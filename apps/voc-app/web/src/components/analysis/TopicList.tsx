import type { AnalysisResultOf, Sort } from "../../analysis";
import { number } from "../format";
import { assessmentName, Legend, percent, StackedBar } from "./shared";

const sortName: Record<Sort, string> = {
  volume: "Volumen",
  negative: "Negativas",
  positive: "Positivas",
};

// The list half of the list–detail pattern: one button per topic; the open
// topic is marked current and its detail sits in the sheet beside the list.
export function TopicList({
  result,
  sort,
  onSort,
  openTopic,
  onOpenTopic,
  limit,
}: {
  result: AnalysisResultOf<"topic_list">;
  sort?: Sort;
  onSort?: (sort: Sort) => void;
  openTopic?: number | null;
  onOpenTopic?: (topicId: number) => void;
  limit?: number;
}) {
  const rows = limit ? result.data.slice(0, limit) : result.data;
  // Topic -1 holds the reviews no topic took and can outnumber every topic,
  // shrinking their bars to slivers. The bars compare topics, so it stays off
  // the scale; its own bar is capped and its number tells the real size.
  const topics = rows.filter((row) => row.topic_id !== -1);
  const scale = Math.max(
    ...(topics.length > 0 ? topics : rows).map((row) => row.total),
    1,
  );
  return (
    <>
      <div className="voc-topic-tools">
        <Legend />
        {onSort && (
          <div
            className="voc-segmented"
            role="group"
            aria-label="Ordenar temas"
          >
            {(Object.keys(sortName) as Sort[]).map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={sort === value}
                onClick={() => onSort(value)}
              >
                {sortName[value]}
              </button>
            ))}
          </div>
        )}
      </div>
      {onOpenTopic && rows.length > 0 && (
        <p className="voc-hint">Elige un tema para ver sus reseñas.</p>
      )}
      {rows.length === 0 && (
        <p className="voc-hint">Ningún tema cumple los filtros.</p>
      )}
      <ol className="voc-topics">
        {rows.map((row) => {
          const content = (
            <>
              <span className="voc-topic-head">
                <span className="voc-eyebrow">
                  {row.topic_id === -1
                    ? "Sin asignar"
                    : `Tema ${row.topic_id} · ${assessmentName[row.assessment]}`}
                </span>
                <span
                  className={`voc-topic-title${row.wording_source === "original" ? " is-keywords" : ""}`}
                >
                  {row.display_name}
                </span>
              </span>
              <StackedBar
                negative={row.negative}
                neutral={row.neutral}
                positive={row.positive}
                scale={scale}
                title={
                  row.topic_id === -1 && row.total > scale
                    ? `${number(row.total)} reseñas sin tema, más que cualquier tema; la barra se recorta`
                    : undefined
                }
              />
              <span className="voc-count">
                <span className="voc-num">{number(row.total)}</span>
                <span className="voc-meta">
                  {percent(row.negative_share)} negativas
                </span>
              </span>
            </>
          );
          return (
            <li key={row.topic_id}>
              {onOpenTopic ? (
                <button
                  type="button"
                  className="voc-topic-row"
                  data-topic-id={row.topic_id}
                  aria-current={openTopic === row.topic_id ? "true" : undefined}
                  onClick={() => onOpenTopic(row.topic_id)}
                >
                  {content}
                </button>
              ) : (
                <div className="voc-topic-row is-static">{content}</div>
              )}
            </li>
          );
        })}
      </ol>
    </>
  );
}
