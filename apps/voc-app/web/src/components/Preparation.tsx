import type { ResultOf } from "../types";
import { dateLabel, number, platformName } from "./format";
import { Panel } from "./Panel";

export function Preparation({
  freshness,
  embeddings,
}: {
  freshness: ResultOf<"freshness">;
  embeddings: ResultOf<"embeddings">;
}) {
  const data = freshness.data;
  const embedded = embeddings.data;
  const status = {
    not_reported: "No informados",
    registered: "Registrados, sin verificar",
    verified: "Verificados",
  }[embedded.state];
  return (
    <Panel
      title="Qué sabemos de su preparación"
      eyebrow="04 / Actualización y embeddings"
      results={[freshness, embeddings]}
      className="preparation-panel"
    >
      <p className="description">
        Publicar datos no significa haberlos recolectado de nuevo.
      </p>
      <div className="preparation-grid">
        <div>
          <dl className="dates">
            <div>
              <dt>Dataset preparado</dt>
              <dd>{dateLabel(data.prepared_at, true)}</dd>
            </div>
            <div>
              <dt>Paquete creado</dt>
              <dd>{dateLabel(data.publication_created_at, true)}</dd>
            </div>
          </dl>
          <h3>Última recolección documentada</h3>
          {data.sources.length === 0 ? (
            <p>No hay fuentes registradas.</p>
          ) : (
            <ul className="collection-list">
              {data.sources.map((source) => (
                <li key={source.platform}>
                  <span>{platformName(source.platform)}</span>
                  <span>
                    {source.collection_state === "verified"
                      ? dateLabel(source.latest_collection_at, true)
                      : "No disponible"}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="preparation-detail">
          <div className="embedding-block">
            <div>
              <h3>Embeddings</h3>
              <span
                className={`badge ${embedded.state === "verified" ? "verified" : "muted"}`}
              >
                {status}
              </span>
            </div>
            {embedded.state === "not_reported" ? (
              <p>
                No hay evidencia publicada para confirmar su disponibilidad.
              </p>
            ) : (
              <dl className="metadata">
                <div>
                  <dt>Modelo</dt>
                  <dd>{embedded.model ?? "No disponible"}</dd>
                </div>
                <div>
                  <dt>Registros cubiertos</dt>
                  <dd>
                    {embedded.covered_rows === null
                      ? "No disponible"
                      : number(embedded.covered_rows)}
                  </dd>
                </div>
                <div>
                  <dt>Release</dt>
                  <dd>
                    <code>{embedded.release_id ?? "No disponible"}</code>
                  </dd>
                </div>
              </dl>
            )}
          </div>
          {(data.evidence.length > 0 || embedded.evidence.length > 0) && (
            <details className="exact-values">
              <summary>Ver procedencia documentada</summary>
              <ul className="provenance">
                {[...data.evidence, ...embedded.evidence].map((item) => (
                  <li key={item.evidence_id}>
                    <p>{item.description}</p>
                    <code>{item.evidence_ref}</code>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      </div>
      <p className="note">
        Fechas expresadas en UTC. «No disponible» significa que falta evidencia,
        no que la operación no haya ocurrido.
      </p>
    </Panel>
  );
}
