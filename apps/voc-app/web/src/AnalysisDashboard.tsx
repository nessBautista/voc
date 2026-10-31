import { useEffect, useId, useRef, useState } from "react";
import {
  allFilters,
  isDefault,
  labels as allLabels,
  type AnalysisResult,
  type Filters,
  type Label,
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
import { MoodSummary } from "./components/analysis/MoodSummary";
import { Platforms } from "./components/analysis/Platforms";
import { Timeline } from "./components/analysis/Timeline";
import { TopSignal } from "./components/analysis/TopSignal";
import { TopicDetail, type DetailTab } from "./components/analysis/TopicDetail";
import { TopicList } from "./components/analysis/TopicList";
import { DetailToolbar } from "./components/detail/DetailHeader";
import { DetailSheet } from "./components/detail/DetailSheet";
import { EvidenceFold, Panel } from "./components/Panel";
import { analysisPanels } from "./components/registry";
import { number } from "./components/format";
import { shortMonth } from "./components/analysis/shared";

type Loaded =
  | { status: "ready"; snapshot: AnalysisSnapshot }
  | { status: "loading" }
  | { status: "error"; error: DataError };
// A topic as read with every filter, and, while the sentiment filter is
// narrowed, read again without it so the detail's summary stays whole.
type TopicView = { snapshot: TopicSnapshot; mix?: TopicSnapshot };
type TopicState =
  | { status: "closed" }
  // `keep` holds the page on screen while the next page or filter loads.
  | { status: "loading"; topicId: number; page: number; keep?: TopicView }
  | { status: "ready"; topicId: number; view: TopicView }
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
    const tiles = document.querySelector(".voc-kpis");
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
        ? topic.view.snapshot.reviews.data.page
        : 1;
  useEffect(() => {
    if (topicId === null) return;
    const controller = new AbortController();
    const narrowed = debounced.labels.size < allLabels.length;
    Promise.all([
      loadTopic(
        publicationId,
        topicId,
        debounced,
        page,
        PAGE_SIZE,
        controller.signal,
      ),
      narrowed
        ? loadTopic(
            publicationId,
            topicId,
            { ...debounced, labels: new Set(allLabels) },
            1,
            1,
            controller.signal,
          )
        : Promise.resolve(undefined),
    ])
      .then(([snapshot, mix]) => {
        if (!controller.signal.aborted)
          setTopic({ status: "ready", topicId, view: { snapshot, mix } });
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

  // The detail sheet: the open topic, its tab, and where focus returns.
  const [tab, setTab] = useState<DetailTab>("summary");
  const titleId = useId();
  const title = useRef<HTMLHeadingElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const focused = useRef<number | null>(null);
  const show = (id: number) =>
    setTopic({ status: "loading", topicId: id, page: 1 });
  const close = () => {
    const back =
      opener.current?.isConnected === true
        ? opener.current
        : topicId === null
          ? null
          : document.querySelector<HTMLElement>(`[data-topic-id="${topicId}"]`);
    setTopic({ status: "closed" });
    focused.current = null;
    opener.current = null;
    back?.focus();
  };
  // Choosing the open topic again closes it; another one swaps the content.
  const open = (id: number) => {
    if (topicId === id) return close();
    if (topicId === null && document.activeElement instanceof HTMLElement)
      opener.current = document.activeElement;
    show(id);
  };
  // Opening a topic moves focus to its title, once per topic.
  useEffect(() => {
    if (topic.status !== "ready" || focused.current === topic.topicId) return;
    focused.current = topic.topicId;
    title.current?.focus({ preventScroll: true });
  }, [topic]);
  const view =
    topic.status === "ready"
      ? topic.view
      : topic.status === "loading" &&
          topic.keep?.snapshot.detail.data.topic_id === topic.topicId
        ? topic.keep
        : null;
  // The review feed's filter is the dashboard's own sentiment filter.
  const setLabels = (next: Set<Label>) => {
    setFilters((current) => ({ ...current, labels: next }));
    if (topic.status === "ready" && page !== 1)
      setTopic({
        status: "loading",
        topicId: topic.topicId,
        page: 1,
        keep: topic.view,
      });
  };
  const reset = () => {
    setFilters(allFilters());
    setSort("volume");
  };

  const ready = state.status === "ready" ? state.snapshot : null;
  // Previous and next follow the list's current order.
  const order = ready?.topics.data ?? [];
  const index =
    topicId === null ? -1 : order.findIndex((row) => row.topic_id === topicId);
  const previous =
    index > 0 ? () => show(order[index - 1].topic_id) : undefined;
  const next =
    index >= 0 && index < order.length - 1
      ? () => show(order[index + 1].topic_id)
      : undefined;
  const header = ready
    ? `${number(ready.summary.data.reviews_total)} reseñas de ${ready.platforms.data.length} plataformas` +
      (months.length
        ? `, ${shortMonth(months[0])} a ${shortMonth(months[months.length - 1])}`
        : "") +
      ` · ${number(ready.summary.data.topics_total)} temas`
    : null;
  return (
    <section className="app-analysis" aria-labelledby="analysis-heading">
      <div className="app-page-head">
        <div>
          <p className="voc-eyebrow">Voz del cliente</p>
          <h1 id="analysis-heading">Qué dicen las reseñas</h1>
          {header && <p>{header}</p>}
        </div>
        {!isDefault(filters) && (
          <button type="button" className="voc-btn" onClick={reset}>
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
        <div role="status" className="app-state">
          <span className="app-loader" aria-hidden="true" />
          <h2>Consultando el análisis…</h2>
        </div>
      )}
      {state.status === "error" && (
        <div role="alert" className="app-state is-error">
          <p className="voc-eyebrow">Consulta no disponible</p>
          <h2>No pudimos mostrar el análisis</h2>
          <p>{state.error.message}</p>
        </div>
      )}
      {ready && (
        <>
          <Panel {...analysisPanels.kpi_tiles} results={[ready.summary]}>
            <div className="app-overview">
              <TopSignal result={ready.summary} onOpenTopic={open} />
              <KpiTiles result={ready.summary} />
            </div>
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
                <MoodSummary
                  result={ready.summary}
                  topics={ready.topics}
                  onOpenTopic={open}
                />
              </Panel>
            </div>
          </div>
        </>
      )}
      {topic.status !== "closed" && (
        <DetailSheet
          titleId={titleId}
          scrollKey={topic.topicId}
          busy={topic.status === "loading"}
          onClose={close}
          onPrevious={previous}
          onNext={next}
          head={
            <DetailToolbar
              position={index >= 0 ? index + 1 : null}
              count={order.length}
              onPrevious={previous}
              onNext={next}
              onClose={close}
            />
          }
        >
          {view ? (
            <>
              <TopicDetail
                detail={view.snapshot.detail}
                mix={view.mix?.detail}
                timeline={(view.mix ?? view.snapshot).timeline}
                reviews={view.snapshot.reviews}
                labels={filters.labels}
                onLabels={setLabels}
                onPage={(page) =>
                  setTopic({
                    status: "loading",
                    topicId: view.snapshot.detail.data.topic_id,
                    page,
                    keep: view,
                  })
                }
                tab={tab}
                onTab={setTab}
                titleId={titleId}
                titleRef={title}
              />
              <EvidenceFold
                results={[
                  view.snapshot.detail,
                  view.snapshot.timeline,
                  view.snapshot.reviews,
                  ...(view.mix ? [view.mix.detail, view.mix.timeline] : []),
                ]}
              />
            </>
          ) : topic.status === "error" ? (
            <div role="alert" className="app-sheet-state is-error">
              <h2 id={titleId}>No pudimos abrir el tema</h2>
              <p>{topic.error.message}</p>
            </div>
          ) : (
            <div role="status" className="app-sheet-state">
              <span className="app-loader" aria-hidden="true" />
              <h2 id={titleId}>Abriendo el tema {topic.topicId}…</h2>
            </div>
          )}
        </DetailSheet>
      )}
      <p className="app-note">
        Las etiquetas de sentimiento y los nombres de los temas son resultados
        de modelos; las cifras describen la selección publicada.
      </p>
    </section>
  );
}
