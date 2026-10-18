import type { ResultOf } from "../types";
import { number, platformName } from "./format";
import { Panel } from "./Panel";

export function Sources({ result }: { result: ResultOf<"sources"> }) {
  const total = result.data.reduce((sum, row) => sum + row.record_count, 0);
  return (
    <Panel title="De dónde vienen" eyebrow="02 / Fuentes" results={[result]}>
      <p className="description">
        Registros por plataforma en esta publicación.
      </p>
      {result.data.length === 0 ? (
        <p>No hay fuentes con registros en esta publicación.</p>
      ) : (
        <div className="table-scroll">
          <table>
            <caption className="sr-only">
              Registros y fechas desconocidas por fuente
            </caption>
            <thead>
              <tr>
                <th scope="col">Fuente</th>
                <th scope="col">Registros</th>
                <th scope="col">Sin fecha</th>
              </tr>
            </thead>
            <tbody>
              {result.data.map((row) => (
                <tr key={row.platform}>
                  <th scope="row">
                    <span>{platformName(row.platform)}</span>
                    <div className="bar-track" aria-hidden="true">
                      <div
                        style={{
                          width: `${total ? (row.record_count / total) * 100 : 0}%`,
                        }}
                      />
                    </div>
                  </th>
                  <td>{number(row.record_count)}</td>
                  <td>{number(row.unknown_dates)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
