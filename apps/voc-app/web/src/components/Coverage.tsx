import type { ResultOf } from "../types";
import { monthLabel, number } from "./format";
import { Panel } from "./Panel";

export function Coverage({ result }: { result: ResultOf<"coverage"> }) {
  const months = [...result.data.months].sort((a, b) =>
    a.month.localeCompare(b.month),
  );
  const maximum = Math.max(...months.map((row) => row.record_count), 1);
  return (
    <Panel
      title="Cómo se distribuyen en el tiempo"
      eyebrow="03 / Cobertura mensual"
      results={[result]}
    >
      <p className="description">
        Fechas de los registros; no fechas de recolección.
      </p>
      {months.length === 0 ? (
        <p>No hay registros con fecha utilizable para mostrar meses.</p>
      ) : (
        <>
          <div className="month-chart" aria-hidden="true">
            {months.map((row) => (
              <div className="month-column" key={row.month}>
                <span className="chart-value">{number(row.record_count)}</span>
                <div className="month-bar-space">
                  <div
                    style={{ height: `${(row.record_count / maximum) * 100}%` }}
                  />
                </div>
                <span>{monthLabel(row.month)}</span>
              </div>
            ))}
          </div>
          <details className="exact-values">
            <summary>Ver valores por mes</summary>
            <div className="table-scroll">
              <table>
                <caption className="sr-only">
                  Cantidad exacta de registros por mes
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Mes</th>
                    <th scope="col">Registros</th>
                  </tr>
                </thead>
                <tbody>
                  {months.map((row) => (
                    <tr key={row.month}>
                      <th scope="row">{monthLabel(row.month)}</th>
                      <td>{number(row.record_count)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </>
      )}
      <p className="note">
        Sin fecha: <strong>{number(result.data.unknown_dates)}</strong>. Los
        meses ausentes no se interpretan como cero; la cobertura de recolección
        puede ser incompleta.
      </p>
    </Panel>
  );
}
