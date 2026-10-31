import { Chat } from "./Chat";
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

const CONTEXT = "Explorador de datos";

// "/": the state of the published data and the conversation. The analysis has
// its own page at /dashboard/, linked from here when the package carries it.
export function Dashboard() {
  const { session, notice, leave } = useSession();
  const signedIn = typeof session === "object";
  const { state, reload } = usePublication(signedIn);
  const selected =
    state.status === "ready"
      ? state.snapshot.selection
      : state.status === "loading"
        ? state.selection
        : undefined;
  const publicationId =
    selected?.publication_id ??
    (state.status === "error" ? state.error.publicationId : undefined);
  const warnings =
    state.status === "ready" ? dedupedWarnings(state.snapshot) : [];
  if (session === "checking")
    return (
      <>
        <Header context={CONTEXT} />
        <main id="content" className="app-main">
          <div role="status" className="app-state">
            <span className="app-loader" aria-hidden="true" />
            <h2>Comprobando la sesión…</h2>
          </div>
        </main>
      </>
    );
  if (session === "signed-out")
    return <SignIn context={CONTEXT} message={notice} />;
  return (
    <>
      <a className="app-skip" href="#content">
        Saltar al contenido
      </a>
      <Header context={CONTEXT}>
        <span className="voc-badge">Datos publicados</span>
        {selected && hasAnalysis(selected) && (
          <a className="voc-btn voc-btn-sm" href="/dashboard/">
            Panel de análisis
          </a>
        )}
        <SessionControls session={session} leave={leave} />
        <button
          type="button"
          className="voc-btn voc-btn-sm"
          onClick={() => {
            const question = document.getElementById("chat-question");
            question?.scrollIntoView({ block: "center" });
            question?.focus({ preventScroll: true });
          }}
        >
          Ir al chat
        </button>
      </Header>
      <main id="content" className="app-main">
        {notice && (
          <p role="alert" className="app-notice is-error">
            {notice}
          </p>
        )}
        <div className="app-page-head">
          <div>
            <p className="voc-eyebrow">Tu punto de partida</p>
            <h1>Estado de los datos</h1>
            <p>
              Conoce el alcance y la procedencia de la información disponible.
            </p>
          </div>
          <button
            type="button"
            className="voc-btn"
            onClick={reload}
            disabled={state.status === "loading"}
          >
            Actualizar vista <span aria-hidden="true">↻</span>
          </button>
        </div>
        {publicationId && (
          <div className="app-publication">
            <span className="app-dot" aria-hidden="true" />
            <span>Publicación</span>
            <code>{publicationId}</code>
            {selected && (
              <span className="app-publication-time">
                Activada {dateLabel(selected.activated_at, true)} UTC
              </span>
            )}
          </div>
        )}
        {state.status === "loading" && (
          <div role="status" className="app-state">
            <span className="app-loader" aria-hidden="true" />
            <h2>Cargando datos publicados…</h2>
            <p>Consultando una misma versión para todas las secciones.</p>
          </div>
        )}
        {state.status === "error" && (
          <div role="alert" className="app-state is-error">
            <p className="voc-eyebrow">
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
            <button className="voc-btn" onClick={reload}>
              Reintentar
            </button>
          </div>
        )}
        {state.status === "ready" && (
          <>
            {state.snapshot.results.find((result) => result.kind === "overview")
              ?.data.source_kind === "fixture" && (
              <p className="app-notice is-warning">
                Ejemplo sintético · Estos datos no provienen de una recolección
                real.
              </p>
            )}
            {hasAnalysis(state.snapshot.selection) && (
              <p className="app-callout">
                Esta publicación incluye el análisis de temas y sentimiento.{" "}
                <a className="voc-link" href="/dashboard/">
                  Abrir el panel de análisis
                </a>
              </p>
            )}
            <div className="app-grid">
              {dashboardViews(state.snapshot.results).map((spec) => (
                <View
                  key={spec.component}
                  spec={spec}
                  results={state.snapshot.results}
                  publicationId={state.snapshot.selection.publication_id}
                />
              ))}
            </div>
            {warnings.length > 0 && (
              <aside className="app-warnings" aria-labelledby="warning-heading">
                <h2 id="warning-heading">Antes de interpretar estos datos</h2>
                <ul>
                  {warnings.map((warning) => (
                    <li key={warning.code + warning.message}>
                      {warningText[warning.code] ?? warning.message}
                    </li>
                  ))}
                </ul>
              </aside>
            )}
          </>
        )}
        <Chat
          analysis={
            state.status === "ready" && hasAnalysis(state.snapshot.selection)
          }
        />
        <footer className="app-footer">
          <span>VOC · Información con contexto</span>
          <span>Las cifras corresponden a una publicación guardada.</span>
        </footer>
      </main>
    </>
  );
}
