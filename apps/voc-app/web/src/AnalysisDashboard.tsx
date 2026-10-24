import { useEffect, useRef, useState } from "react";
import {
  allFilters,
  isDefault,
  type AnalysisResult,
  type AnalysisResultOf,
  type Filters,
  type Sort,
} from "./analysis";
import {
  DataError,
  loadAnalysis,
  loadTopic,
  type AnalysisSnapshot,
  type TopicSnapshot,
} from "./api";
import { FilterBar } from "./components/analysis/FilterBar";
import { KpiTiles } from "./components/analysis/KpiTiles";
import { MoodRing } from "./components/analysis/MoodRing";
import { Platforms } from "./components/analysis/Platforms";
import { Timeline } from "./components/analysis/Timeline";
import { TopicDetail } from "./components/analysis/TopicDetail";
import { TopicList } from "./components/analysis/TopicList";
import { Panel } from "./components/Panel";
import { analysisPanels, topicDetailPanel } from "./components/registry";
import { number } from "./components/format";
import { shortMonth } from "./components/analysis/shared";

type Loaded =
  | { status: "ready"; snapshot: AnalysisSnapshot }
  | { status: "loading" }
  | { status: "error"; error: DataError };
type TopicState =
  | { status: "closed" }
  | { status: "loading"; topicId: number; page: number }
  | { status: "ready"; topicId: number; snapshot: TopicSnapshot }
  | { status: "error"; topicId: number; error: DataError };
const PAGE_SIZE = 6;

function useDebounced<T>(value: T, delay: number) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const handle = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(handle);
  }, [value, delay]);
  return debounced;
}

export function AnalysisDashboard({
  publicationId,
  onEvidence,
}: {
  publicationId: string;
  onEvidence?: (results: AnalysisResult[]) => void;
}) {
  const [filters, setFilters] = useState<Filters>(allFilters);
  const [sort, setSort] = useState<Sort>("volume");
  const [state, setState] = useState<Loaded>({ status: "loading" });
  const [topic, setTopic] = useState<TopicState>({ status: "closed" });
  const [months, setMonths] = useState<string[]>([]);
  const debounced = useDebounced(filters, 250);
  const latest = useRef(0);
  const settled = useRef(false);

  // A filter changed while the reader was down at a topic: bring the tiles and
  // the list back into view, otherwise the change seems to do nothing.
  const reveal = () => {
    const tiles = document.querySelector(".kpi-tiles");
    if (tiles && tiles.getBoundingClientRect().bottom < 0)
      document
        .getElementById("analysis-heading")
        ?.scrollIntoView({ block: "start", behavior: "smooth" });
  };

  useEffect(() => {
    const controller = new AbortController();
    const generation = ++latest.current;
    setState((previous) =>
      previous.status === "ready" ? previous : { status: "loading" },
    );
    loadAnalysis(publicationId, debounced, sort, controller.signal)
      .then((snapshot) => {
        if (controller.signal.aborted || generation !== latest.current) return;
        setState({ status: "ready", snapshot });
        if (settled.current) reveal();
        settled.current = true;
        // The month range of the package comes from the first unfiltered answer.
        setMonths((known) =>
          known.length > 0
            ? known
            : snapshot.timeline.data.months.map((m) => m.month),
        );
        onEvidence?.([
          snapshot.summary,
          snapshot.topics,
          snapshot.timeline,
          snapshot.platforms,
        ]);
      })
      .catch((error) => {
        if (controller.signal.aborted || generation !== latest.current) return;
        setState({
          status: "error",
          error:
            error instanceof DataError
              ? error
              : new DataError(String(error), "invalid_response"),
        });
      });
    return () => controller.abort();
  }, [publicationId, debounced, sort, onEvidence]);

  const topicId = topic.status === "closed" ? null : topic.topicId;
  const page =
    topic.status === "loading"
      ? topic.page
      : topic.status === "ready"
        ? topic.snapshot.reviews.data.page
        : 1;
  useEffect(() => {
    if (topicId === null) return;
    const controller = new AbortController();
    loadTopic(
      publicationId,
      topicId,
      debounced,
      page,
      PAGE_SIZE,
      controller.signal,
    )
      .then((snapshot) => {
        if (!controller.signal.aborted)
          setTopic({ status: "ready", topicId, snapshot });
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setTopic({
            status: "error",
            topicId,
            error:
              error instanceof DataError
                ? error
                : new DataError(String(error), "invalid_response"),
          });
      });
    return () => controller.abort();
  }, [publicationId, topicId, page, debounced]);

  const open = (id: number) => {
    setTopic((current) =>
      current.status !== "closed" && current.topicId === id
        ? { status: "closed" }
        : { status: "loading", topicId: id, page: 1 },
    );
    window.setTimeout(
      () =>
        document
          .getElementById("topic-detail")
          ?.scrollIntoView({ block: "start", behavior: "smooth" }),
      0,
    );
  };
  const reset = () => {
    setFilters(allFilters());
    setSort("volume");
  };

  const ready = state.status === "ready" ? state.snapshot : null;
  const header = ready
    ? `${number(ready.summary.data.reviews_total)} reseñas de ${ready.platforms.data.length} plataformas` +
      (months.length
        ? `, ${shortMonth(months[0])} a ${shortMonth(months[months.length - 1])}`
        : "") +
      ` · ${number(ready.summary.data.topics_total)} temas`
    : null;
  return (
    <section className="analysis" aria-labelledby="analysis-heading">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Voz del cliente</p>
          <h1 id="analysis-heading">Qué dicen las reseñas</h1>
          {header && <p>{header}</p>}
        </div>
        {!isDefault(filters) && (
          <button type="button" onClick={reset}>
            Quitar filtros
          </button>
        )}
      </div>
      <FilterBar
        filters={filters}
        months={months}
        onChange={setFilters}
        onReset={reset}
      />
      {state.status === "loading" && (
        <div role="status" className="state-panel">
          <span className="loader" aria-hidden="true" />
          <h2>Consultando el análisis…</h2>
        </div>
      )}
      {state.status === "error" && (
        <div role="alert" className="state-panel error-panel">
          <p className="eyebrow">Consulta no disponible</p>
          <h2>No pudimos mostrar el análisis</h2>
          <p>{state.error.message}</p>
        </div>
      )}
      {ready && (
        <>
          <Panel {...analysisPanels.kpi_tiles} results={[ready.summary]}>
            <KpiTiles result={ready.summary} onOpenTopic={open} />
          </Panel>
          <div className="analysis-grid">
            <Panel {...analysisPanels.topic_list} results={[ready.topics]}>
              <TopicList
                result={ready.topics}
                sort={sort}
                onSort={setSort}
                openTopic={topicId}
                onOpenTopic={open}
              />
            </Panel>
            <div className="side-column">
              <Panel {...analysisPanels.timeline} results={[ready.timeline]}>
                <Timeline data={ready.timeline.data} />
              </Panel>
              <Panel {...analysisPanels.platforms} results={[ready.platforms]}>
                <Platforms result={ready.platforms} />
              </Panel>
              <Panel {...analysisPanels.mood_ring} results={[ready.summary]}>
                <MoodRing result={ready.summary} />
              </Panel>
            </div>
          </div>
        </>
      )}
      <div id="topic-detail">
        {topic.status === "loading" && (
          <div role="status" className="state-panel">
            <span className="loader" aria-hidden="true" />
            <h2>Abriendo el tema {topic.topicId}…</h2>
          </div>
        )}
        {topic.status === "error" && (
          <div role="alert" className="state-panel error-panel">
            <h2>No pudimos abrir el tema</h2>
            <p>{topic.error.message}</p>
            <button
              type="button"
              onClick={() => setTopic({ status: "closed" })}
            >
              Cerrar
            </button>
          </div>
        )}
        {topic.status === "ready" && (
          <Panel
            title={topic.snapshot.detail.data.display_name}
            {...topicDetailPanel}
            results={
              [
                topic.snapshot.detail,
                topic.snapshot.timeline,
                topic.snapshot.reviews,
              ] as AnalysisResultOf<"topic_detail" | "timeline" | "reviews">[]
            }
          >
            <TopicDetail
              detail={topic.snapshot.detail}
              timeline={topic.snapshot.timeline}
              reviews={topic.snapshot.reviews}
              onPage={(next) =>
                setTopic({
                  status: "loading",
                  topicId: topic.topicId,
                  page: next,
                })
              }
              onClose={() => setTopic({ status: "closed" })}
            />
          </Panel>
        )}
      </div>
      <p className="note">
        Las etiquetas de sentimiento y los nombres de los temas son resultados
        de modelos; las cifras describen la selección publicada.
      </p>
    </section>
  );
}
