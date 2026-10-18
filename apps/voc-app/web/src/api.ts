import {
  kinds,
  parseResult,
  parseSelection,
  validateSnapshot,
  type Selection,
  type Snapshot,
} from "./types";

export class DataError extends Error {
  constructor(
    message: string,
    public code: string,
    public publicationId?: string,
  ) {
    super(message);
  }
}

async function readJSON(path: string, signal: AbortSignal): Promise<unknown> {
  const response = await fetch(path, {
    signal,
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const noSelection =
      response.status === 503 && body?.error?.code === "no_active_publication";
    throw new DataError(
      noSelection
        ? "Todavía no hay una publicación disponible para explorar."
        : "No pudimos consultar los datos publicados. Intenta de nuevo.",
      noSelection ? "no_active_publication" : "query_failed",
    );
  }
  return response.json();
}

export async function loadDashboard(
  signal: AbortSignal,
  onSelected: (selection: Selection) => void,
): Promise<Snapshot> {
  let selection: Selection | undefined;
  try {
    // Capture one identity, then use it for every query in this page load.
    selection = parseSelection(
      await readJSON("/api/v1/publications/current", signal),
    );
    onSelected(selection);
    const publicationId = selection.publication_id;
    const results = await Promise.all(
      kinds.map(async (kind) =>
        parseResult(
          await readJSON(
            `/api/v1/publications/${publicationId}/${kind}`,
            signal,
          ),
          publicationId,
          kind,
        ),
      ),
    );
    return validateSnapshot({ selection, results });
  } catch (error) {
    if (signal.aborted) throw error;
    if (error instanceof DataError) {
      error.publicationId = selection?.publication_id;
      throw error;
    }
    throw new DataError(
      "No pudimos validar la respuesta de datos. Intenta cargar de nuevo.",
      "invalid_response",
      selection?.publication_id,
    );
  }
}
