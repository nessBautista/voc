import type { ResultOf } from "../types";
import { dateLabel, number } from "./format";
import { Panel } from "./Panel";

export function DatasetSummary({ result }: { result: ResultOf<"overview"> }) {
  const data = result.data;
  return (
    <Panel
      title="Una vista del dataset"
      eyebrow="01 / Resumen"
      results={[result]}
      className="summary-panel"
    >
      <div className="summary-stats">
        <div>
          <strong className="big-number">{number(data.row_count)}</strong>
          <span>registros publicados</span>
        </div>
        <div>
          <strong>{number(data.unknown_dates)}</strong>
          <span>registros sin fecha</span>
        </div>
        <div>
          <strong>
            {data.record_start === null
              ? "No disponible"
              : `${dateLabel(data.record_start)} — ${dateLabel(data.record_end)}`}
          </strong>
          <span>fechas de los registros · UTC</span>
        </div>
      </div>
      {data.row_count === 0 && (
        <p>Esta publicación está disponible y contiene cero registros.</p>
      )}
      <dl className="metadata">
        <div>
          <dt>Dataset</dt>
          <dd>
            <code>{data.dataset_id}</code>
          </dd>
        </div>
        <div>
          <dt>Origen de la publicación</dt>
          <dd>
            {
              {
                s3: "Release S3",
                personal: "Catálogo personal",
                fixture: "Ejemplo sintético",
              }[data.source_kind]
            }
          </dd>
        </div>
      </dl>
    </Panel>
  );
}
