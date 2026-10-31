import { useId, useState, type KeyboardEvent, type Ref } from "react";
import type { AnalysisResultOf, Label } from "../../analysis";
import { DetailIdentity } from "../detail/DetailHeader";
import { InterpretationBlock } from "../detail/InterpretationBlock";
import { PropertyList } from "../detail/PropertyList";
import { ReviewFeed } from "../detail/ReviewFeed";
import { SentimentSummary } from "../detail/SentimentSummary";
import { TrendSparkline } from "../detail/TrendSparkline";
import { number } from "../format";

export type DetailTab = "summary" | "reviews";

// One topic's detail: identity, the model's interpretation as the lead, then
// "Resumen" and "Reseñas" tabs. The dashboard puts it in the DetailSheet; a
// chat answer draws it inline, read-only.
export function TopicDetail({
  detail,
  mix,
  timeline,
  reviews,
  labels,
  onLabels,
  onPage,
  tab,
  onTab,
  initialTab = "summary",
  titleId,
  titleRef,
}: {
  // Read with every filter: the identity line and the "Reseñas" count.
  detail: AnalysisResultOf<"topic_detail">;
  // The same topic read without the sentiment filter, for the summary and the
  // feed's filter counts. Without it, `detail` stands in.
  mix?: AnalysisResultOf<"topic_detail">;
  timeline?: AnalysisResultOf<"timeline">;
  reviews?: AnalysisResultOf<"reviews">;
  labels?: Set<Label>;
  onLabels?: (next: Set<Label>) => void;
  onPage?: (page: number) => void;
  // The tab can live with the caller, so it survives moving between topics.
  tab?: DetailTab;
  // Where a detail with its own tab starts: a chat answer opens on the reviews.
  initialTab?: DetailTab;
  onTab?: (tab: DetailTab) => void;
  titleId?: string;
  titleRef?: Ref<HTMLHeadingElement>;
}) {
  const [ownTab, setOwnTab] = useState<DetailTab>(initialTab);
  const current = tab ?? ownTab;
  const choose = onTab ?? setOwnTab;
  const id = useId();
  const whole = mix ?? detail;
  const data = detail.data;
  const tabs: [DetailTab, string][] = [
    ["summary", "Resumen"],
    ["reviews", "Reseñas"],
  ];
  // Left and right move between tabs; only the selected tab is in the tab order.
  const arrows = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const next = current === "summary" ? "reviews" : "summary";
    choose(next);
    document.getElementById(`${id}-${next}`)?.focus();
  };
  return (
    <>
      <DetailIdentity detail={detail} titleId={titleId} titleRef={titleRef} />
      <InterpretationBlock detail={detail} />
      <div
        className="voc-tabs"
        role="tablist"
        aria-label="Detalle del tema"
        onKeyDown={arrows}
      >
        {tabs.map(([value, name]) => (
          <button
            key={value}
            id={`${id}-${value}`}
            type="button"
            className="voc-tab"
            role="tab"
            aria-selected={current === value}
            aria-controls={`${id}-panel`}
            tabIndex={current === value ? 0 : -1}
            onClick={() => choose(value)}
          >
            {name}
            {value === "reviews" && (
              <span className="voc-count-pill">{number(data.in_view)}</span>
            )}
          </button>
        ))}
      </div>
      <div
        className="voc-tabpanel"
        role="tabpanel"
        id={`${id}-panel`}
        aria-labelledby={`${id}-${current}`}
      >
        {current === "summary" ? (
          <>
            <SentimentSummary detail={whole} />
            <PropertyList
              rows={[
                {
                  label: "Por mes",
                  chart: true,
                  value: timeline ? (
                    <TrendSparkline months={timeline.data.months} />
                  ) : null,
                },
                {
                  // An uninterpreted topic already shows them in the lead.
                  label: "Palabras clave",
                  value:
                    data.wording_source === "generated"
                      ? data.keywords.join(", ")
                      : null,
                },
              ]}
            />
          </>
        ) : reviews ? (
          <ReviewFeed
            result={reviews}
            keywords={data.keywords}
            supportingIds={data.supporting_review_ids}
            counts={{
              all: whole.data.in_view,
              negative: whole.data.negative,
              neutral: whole.data.neutral,
              positive: whole.data.positive,
            }}
            labels={labels}
            onLabels={onLabels}
            onPage={onPage}
          />
        ) : (
          <p className="voc-hint">Las reseñas no están disponibles aquí.</p>
        )}
      </div>
    </>
  );
}
