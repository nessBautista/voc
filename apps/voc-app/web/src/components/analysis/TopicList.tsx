import type { AnalysisResultOf, Sort } from "../../analysis";
import { number } from "../format";
import { assessmentName, Legend, percent, StackedBar } from "./shared";

const sortName: Record<Sort, string> = {
  volume: "Volumen",
  negative: "Negativas",
  positive: "Positivas",
};

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
  const scale = Math.max(...rows.map((row) => row.total), 1);
  return (
    <div className="topic-list">
      <div className="topic-list-tools">
        <Legend />
        {onSort && (
          <div className="segmented" role="group" aria-label="Ordenar temas">
            {(Object.keys(sortName) as Sort[]).map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={sort === value}
                className={sort === value ? "active" : ""}
                onClick={() => onSort(value)}
              >
                {sortName[value]}
              </button>
            ))}
          </div>
        )}
      </div>
      {onOpenTopic && (
        <p className="hint">Elige un tema para ver sus reseñas.</p>
      )}
      {rows.length === 0 && (
        <p className="hint">Ningún tema cumple los filtros.</p>
      )}
      <ol className="topic-rows">
        {rows.map((row) => {
          const content = (
            <>
              <div className="topic-row-head">
                <span className="eyebrow">
                  {row.topic_id === -1
                    ? "Sin asignar"
                    : `Tema ${row.topic_id} · ${assessmentName[row.assessment]}`}
                </span>
                <span
                  className={`topic-title${row.wording_source === "original" ? " keywords" : ""}`}
                >
                  {row.display_name}
                </span>
              </div>
              <StackedBar
                negative={row.negative}
                neutral={row.neutral}
                positive={row.positive}
                scale={scale}
              />
              <div className="topic-count">
                <strong>{number(row.total)}</strong>
                <span>{percent(row.negative_share)} negativas</span>
              </div>
            </>
          );
          return (
            <li
              key={row.topic_id}
              className={openTopic === row.topic_id ? "open" : ""}
            >
              {onOpenTopic ? (
                <button
                  type="button"
                  className="topic-row"
                  aria-expanded={openTopic === row.topic_id}
                  onClick={() => onOpenTopic(row.topic_id)}
                >
                  {content}
                </button>
              ) : (
                <div className="topic-row">{content}</div>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
