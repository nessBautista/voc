import { useEffect, useState, type ReactNode } from "react";
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

// The two pages, "/" and "/dashboard/", share the frame, the session and the
// publication load; each decides what to draw with them.
export const analysisCapabilities = ["topics", "sentiment", "reviews"];
export const hasAnalysis = (selection: Selection) =>
  selection.schema_version === "voc-data-state-v2" &&
  analysisCapabilities.every((c) => selection.capabilities.includes(c));

export const warningText: Record<string, string> = {
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
  sentiment_unclassified:
    "Algunas reseñas no tienen etiqueta de sentimiento y cuentan solo en los totales.",
  interpretations_partial:
    "No todos los temas tienen un nombre generado; el resto conserva sus palabras clave.",
};

export function Header({
  context,
  children,
}: {
  context: string;
  children?: ReactNode;
}) {
  return (
    <header className="topbar">
      <a href="/" className="brand" aria-label="VOC, inicio">
        <span className="brand-mark" aria-hidden="true">
          v.
        </span>
        VOC<span className="brand-divider">/</span>
        <span className="brand-context">{context}</span>
      </a>
      <nav className="workspace-navigation" aria-label="Navegación del espacio">
        {children}
      </nav>
    </header>
  );
}

export function SignIn({
  context,
  message,
}: {
  context: string;
  message: string | null;
}) {
  return (
    <>
      <a className="skip-link" href="#content">
        Saltar al contenido
      </a>
      <Header context={context} />
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

export function useSession() {
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
  const leave = () => {
    signOut()
      .then((url) => window.location.assign(url))
      .catch(() =>
        setNotice("No pudimos cerrar la sesión. Recarga la página."),
      );
  };
  return { session, notice, setNotice, leave };
}

export type LoadState =
  | { status: "loading"; selection?: Selection }
  | { status: "ready"; snapshot: Snapshot }
  | { status: "error"; error: DataError };

export function usePublication(signedIn: boolean) {
  const [generation, setGeneration] = useState(0);
  const [state, setState] = useState<LoadState>({ status: "loading" });
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
  return { state, reload: () => setGeneration((value) => value + 1) };
}

export function dedupedWarnings(snapshot: Snapshot) {
  return [
    ...new Map(
      [
        ...snapshot.selection.warnings,
        ...snapshot.results.flatMap((result) => result.warnings),
      ].map((warning) => [
        warningText[warning.code] ?? warning.message,
        warning,
      ]),
    ).values(),
  ];
}

export function SessionControls({
  session,
  leave,
}: {
  session: Session;
  leave: () => void;
}) {
  if (session.mode !== "cognito") return null;
  return (
    <>
      <span className="session-email">{session.email}</span>
      <button type="button" onClick={leave}>
        Cerrar sesión
      </button>
    </>
  );
}
