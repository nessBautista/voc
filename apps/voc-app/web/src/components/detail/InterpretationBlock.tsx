import type { AnalysisResultOf } from "../../analysis";

// The lead of a topic's detail: what the model says the reviews are about,
// right under the title. No box and no counts; size and position lead.
export function InterpretationBlock({
  detail,
}: {
  detail: AnalysisResultOf<"topic_detail">;
}) {
  const data = detail.data;
  const interpreted = data.wording_source === "generated";
  return (
    <section className="voc-lead" aria-label="Interpretación del modelo">
      {interpreted ? (
        <>
          <p className="voc-lead-text" lang="es">
            {data.description}
          </p>
          {data.proposed_feature && (
            <dl className="voc-lead-feature">
              <dt>Funcionalidad propuesta</dt>
              <dd>{data.proposed_feature}</dd>
            </dl>
          )}
          <p className="voc-lead-note">
            Interpretación del modelo · puede contener errores
          </p>
        </>
      ) : (
        <>
          <p className="voc-lead-text is-empty">
            El modelo no pudo nombrar este tema; su nombre se forma con sus
            palabras clave.
          </p>
          {data.keywords.length > 0 && (
            <dl className="voc-lead-feature">
              <dt>Palabras clave</dt>
              <dd>{data.keywords.join(", ")}</dd>
            </dl>
          )}
        </>
      )}
    </section>
  );
}
