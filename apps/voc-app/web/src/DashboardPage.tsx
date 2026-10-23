import { AnalysisDashboard } from "./AnalysisDashboard";
import { dashboardViews, View } from "./components/registry";
import { dateLabel } from "./components/format";
import {
  dedupedWarnings,
  hasAnalysis,
  Header,
  SessionControls,
  SignIn,
  usePublication,
  useSession,
  warningText,
} from "./shell";

const CONTEXT = "Panel de análisis";

// "/dashboard/": the analysis of the active package, nothing else. The dataset
// panels stay reachable, folded at the bottom; the conversation lives on "/".
export function DashboardPage() {
  const { session, notice, leave } = useSession();
  const signedIn = typeof session === "object";
  const { state, reload } = usePublication(signedIn);
  const selected =
    state.status === "ready"
      ? state.snapshot.selection
      : state.status === "loading"
        ? state.selection
        : undefined;
  if (session === "checking")
    return (
      <>
        <Header context={CONTEXT} />
        <main id="content">
          <div role="status" className="state-panel">
            <span className="loader" aria-hidden="true" />
            <h2>Comprobando la sesión…</h2>
          </div>
        </main>
      </>
    );
  if (session === "signed-out")
    return <SignIn context={CONTEXT} message={notice} />;
  return (
    <>
      <a className="skip-link" href="#content">
        Saltar al contenido
      </a>
      <Header context={CONTEXT}>
        <span className="badge">Datos publicados</span>
        <a className="button-link nav-link" href="/">
          Conversar con los datos
        </a>
        <SessionControls session={session} leave={leave} />
      </Header>
      <main id="content">
        {notice && (
          <p role="alert" className="chat-error">
            {notice}
          </p>
        )}
        {selected && (
          <div className="publication-line">
            <span className="dot" aria-hidden="true" />
            <span>Publicación</span>
            <code>{selected.publication_id}</code>
            <span className="activated">
              Activada {dateLabel(selected.activated_at, true)} UTC
            </span>
          </div>
        )}
        {state.status === "loading" && (
          <div role="status" className="state-panel">
            <span className="loader" aria-hidden="true" />
            <h2>Cargando la publicación…</h2>
          </div>
        )}
        {state.status === "error" && (
          <div role="alert" className="state-panel error-panel">
            <p className="eyebrow">
              {state.error.code === "no_active_publication"
                ? "Sin publicación"
                : "Consulta no disponible"}
            </p>
            <h2>
              {state.error.code === "no_active_publication"
                ? "Aún no hay datos publicados"
                : "No pudimos mostrar los datos"}
            </h2>
            <p>{state.error.message}</p>
            <button onClick={reload}>Reintentar</button>
          </div>
        )}
        {state.status === "ready" && !hasAnalysis(state.snapshot.selection) && (
          <div role="status" className="state-panel">
            <p className="eyebrow">Sin análisis</p>
            <h2>Esta publicación no incluye temas ni sentimiento</h2>
            <p>
              El panel se activa cuando el Operador publica un paquete con
              análisis.
            </p>
            <a className="button-link" href="/">
              Ver el estado de los datos
            </a>
          </div>
        )}
        {state.status === "ready" && hasAnalysis(state.snapshot.selection) && (
          <>
            <AnalysisDashboard
              key={state.snapshot.selection.publication_id}
              publicationId={state.snapshot.selection.publication_id}
            />
            <details className="dataset-state">
              <summary>
                <span className="eyebrow">Estado de los datos</span>
                <span>Alcance y procedencia de la publicación</span>
              </summary>
              <div className="dashboard-grid">
                {dashboardViews(state.snapshot.results).map((spec) => (
                  <View
                    key={spec.component}
                    spec={spec}
                    results={state.snapshot.results}
                    publicationId={state.snapshot.selection.publication_id}
                  />
                ))}
              </div>
              {dedupedWarnings(state.snapshot).length > 0 && (
                <aside className="warnings" aria-labelledby="warning-heading">
                  <h2 id="warning-heading">Antes de interpretar estos datos</h2>
                  <ul>
                    {dedupedWarnings(state.snapshot).map((warning) => (
                      <li key={warning.code + warning.message}>
                        {warningText[warning.code] ?? warning.message}
                      </li>
                    ))}
                  </ul>
                </aside>
              )}
            </details>
          </>
        )}
        <footer className="page-footer">
          <span>VOC · Voz del cliente</span>
          <span>
            Las etiquetas y los nombres de los temas son resultados de modelos.
          </span>
        </footer>
      </main>
    </>
  );
}
