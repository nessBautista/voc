import { useEffect, useState, type ReactNode } from "react";
import { Chat } from "./Chat";
import { DataError, loadDashboard } from "./api";
import {
  loginErrorMessage,
  sessionEndedEvent,
  signInPath,
  signOut,
  whoAmI,
  type Session,
} from "./auth";
import type { Selection, Snapshot } from "./types";
import { dashboardViews, View } from "./components/registry";
import { dateLabel } from "./components/format";

type State =
  | { status: "loading"; selection?: Selection }
  | { status: "ready"; snapshot: Snapshot }
  | { status: "error"; error: DataError };
const warningText: Record<string, string> = {
  collection_date_unknown:
    "Faltan fechas de recolección documentadas para una o más fuentes.",
  record_dates_missing:
    "Algunos registros no tienen fecha y se muestran fuera de los conteos mensuales.",
  platform_unknown: "Hay registros sin una plataforma identificada.",
  embeddings_not_reported:
    "La publicación no informa evidencia sobre embeddings.",
  embeddings_not_verified:
    "Los embeddings están registrados, pero su verificación no está documentada.",
  embeddings_partial_coverage:
    "Los embeddings cubren solo una parte del dataset.",
};

function Header({ children }: { children?: ReactNode }) {
  return (
    <header className="topbar">
      <a href="/" className="brand" aria-label="VOC, inicio">
        <span className="brand-mark" aria-hidden="true">
          v.
        </span>
        VOC<span className="brand-divider">/</span>
        <span className="brand-context">Explorador de datos</span>
      </a>
      <nav className="workspace-navigation" aria-label="Navegación del espacio">
        {children}
      </nav>
    </header>
  );
}

function SignIn({ message }: { message: string | null }) {
  return (
    <>
      <a className="skip-link" href="#content">
        Saltar al contenido
      </a>
      <Header />
      <main id="content">
        <div role="status" className="state-panel">
          <p className="eyebrow">Acceso</p>
          <h2>Inicia sesión para continuar</h2>
          <p>VOC muestra los datos publicados solo a personas invitadas.</p>
          {message && (
            <p role="alert" className="chat-error">
              {message}
            </p>
          )}
          <a className="button-link" href={signInPath}>
            Iniciar sesión
          </a>
        </div>
      </main>
    </>
  );
}

export function Dashboard() {
  const [generation, setGeneration] = useState(0);
  const [state, setState] = useState<State>({ status: "loading" });
  const [session, setSession] = useState<Session | "checking" | "signed-out">(
    "checking",
  );
  const [notice, setNotice] = useState<string | null>(() =>
    loginErrorMessage(window.location.search),
  );
  useEffect(() => {
    const controller = new AbortController();
    whoAmI(controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setSession(value ?? "signed-out");
      })
      .catch((error) => {
        if (controller.signal.aborted) return;
        setNotice("No pudimos comprobar la sesión. Recarga la página.");
        setSession("signed-out");
        console.error(error);
      });
    const ended = () => {
      setNotice("Tu sesión terminó. Inicia sesión de nuevo.");
      setSession("signed-out");
    };
    window.addEventListener(sessionEndedEvent, ended);
    return () => {
      controller.abort();
      window.removeEventListener(sessionEndedEvent, ended);
    };
  }, []);
  const signedIn = typeof session === "object";
  useEffect(() => {
    if (!signedIn) return;
    const controller = new AbortController();
    // Ignore stale completions after refresh or unmount, including StrictMode cleanup.
    setState({ status: "loading" });
    const timeout = window.setTimeout(() => {
      controller.abort();
      setState((previous) => ({
        status: "error",
        error: new DataError(
          "La consulta tardó demasiado. Intenta de nuevo.",
          "timeout",
          previous.status === "loading"
            ? previous.selection?.publication_id
            : undefined,
        ),
      }));
    }, 20000);
    loadDashboard(controller.signal, (selection) => {
      if (!controller.signal.aborted)
        setState({ status: "loading", selection });
    })
      .then((snapshot) => {
        if (!controller.signal.aborted) setState({ status: "ready", snapshot });
      })
      .catch((error) => {
        if (!controller.signal.aborted) setState({ status: "error", error });
      })
      .finally(() => window.clearTimeout(timeout));
    return () => {
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [generation, signedIn]);

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
    state.status === "ready"
      ? [
          ...new Map(
            [
              ...state.snapshot.selection.warnings,
              ...state.snapshot.results.flatMap((result) => result.warnings),
            ].map((warning) => [
              warningText[warning.code] ?? warning.message,
              warning,
            ]),
          ).values(),
        ]
      : [];
  const reload = () => setGeneration((value) => value + 1);
  if (session === "checking")
    return (
      <>
        <Header />
        <main id="content">
          <div role="status" className="state-panel">
            <span className="loader" aria-hidden="true" />
            <h2>Comprobando la sesión…</h2>
          </div>
        </main>
      </>
    );
  if (session === "signed-out") return <SignIn message={notice} />;
  const leave = () => {
    signOut()
      .then((url) => window.location.assign(url))
      .catch(() =>
        setNotice("No pudimos cerrar la sesión. Recarga la página."),
      );
  };
  return (
    <>
      <a className="skip-link" href="#content">
        Saltar al contenido
      </a>
      <Header>
        <span className="badge">Datos publicados</span>
        {session.mode === "cognito" && (
          <>
            <span className="session-email">{session.email}</span>
            <button type="button" onClick={leave}>
              Cerrar sesión
            </button>
          </>
        )}
        <button
          type="button"
          onClick={() => {
            const question = document.getElementById("chat-question");
            question?.scrollIntoView({ block: "center" });
            question?.focus({ preventScroll: true });
          }}
        >
          Ir al chat
        </button>
      </Header>
      <main id="content">
        {notice && (
          <p role="alert" className="chat-error">
            {notice}
          </p>
        )}
        <div className="page-heading">
          <div>
            <p className="eyebrow">Tu punto de partida</p>
            <h1>Estado de los datos</h1>
            <p>
              Conoce el alcance y la procedencia de la información disponible.
            </p>
          </div>
          <button
            type="button"
            onClick={reload}
            disabled={state.status === "loading"}
          >
            Actualizar vista <span aria-hidden="true">↻</span>
          </button>
        </div>
        {publicationId && (
          <div className="publication-line">
            <span className="dot" aria-hidden="true" />
            <span>Publicación</span>
            <code>{publicationId}</code>
            {selected && (
              <span className="activated">
                Activada {dateLabel(selected.activated_at, true)} UTC
              </span>
            )}
          </div>
        )}
        {state.status === "loading" && (
          <div role="status" className="state-panel">
            <span className="loader" aria-hidden="true" />
            <h2>Cargando datos publicados…</h2>
            <p>Consultando una misma versión para todas las secciones.</p>
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
        {state.status === "ready" && (
          <>
            {state.snapshot.results.find((result) => result.kind === "overview")
              ?.data.source_kind === "fixture" && (
              <p className="fixture-notice">
                Ejemplo sintético · Estos datos no provienen de una recolección
                real.
              </p>
            )}
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
            {warnings.length > 0 && (
              <aside className="warnings" aria-labelledby="warning-heading">
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
        <Chat />
        <footer className="page-footer">
          <span>VOC · Información con contexto</span>
          <span>Las cifras corresponden a una publicación guardada.</span>
        </footer>
      </main>
    </>
  );
}
