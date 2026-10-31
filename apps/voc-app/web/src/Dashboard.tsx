import { Chat } from "./Chat";
import { dateLabel } from "./components/format";
import {
  hasAnalysis,
  Header,
  SessionControls,
  SignIn,
  usePublication,
  useSession,
} from "./shell";

const CONTEXT = "Conversación";

// "/": the conversation with the published data. The page only says which
// publication is active; the dataset panels appear inside answers when a
// question asks about the data. The analysis has its own page at /dashboard/.
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
  const synthetic =
    state.status === "ready" &&
    state.snapshot.results.find((result) => result.kind === "overview")?.data
      .source_kind === "fixture";
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
      </Header>
      <main id="content" className="app-main app-chat-main">
        {notice && (
          <p role="alert" className="app-notice is-error">
            {notice}
          </p>
        )}
        {state.status === "loading" && !publicationId && (
          <p role="status" className="app-publication">
            <span className="app-loader is-small" aria-hidden="true" />
            Cargando la publicación activa…
          </p>
        )}
        {publicationId && (
          <div
            className="app-publication"
            role={state.status === "loading" ? "status" : undefined}
          >
            <span className="app-dot" aria-hidden="true" />
            <span>Publicación</span>
            <code>{publicationId}</code>
            {selected && (
              <span className="app-publication-time">
                Activada {dateLabel(selected.activated_at, true)} UTC
              </span>
            )}
            <button
              type="button"
              className="voc-btn voc-btn-ghost voc-btn-sm"
              onClick={reload}
              disabled={state.status === "loading"}
            >
              Actualizar vista
            </button>
          </div>
        )}
        {synthetic && (
          <p className="app-notice is-warning">
            Ejemplo sintético · Estos datos no provienen de una recolección
            real.
          </p>
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
        <Chat
          analysis={
            state.status === "ready" && hasAnalysis(state.snapshot.selection)
          }
        />
      </main>
    </>
  );
}
