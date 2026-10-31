import type { AnalysisResultOf, Label } from "../../analysis";
import { number } from "../format";
import { labelName, percent, StackedBar } from "./shared";

const order: Label[] = ["negative", "neutral", "positive"];

// The overall mood as a verdict a reader can repeat, then the split on one bar
// and the topic most cited on each side. It replaces the ring: shares compare
// along a bar, not as angles.
export function MoodSummary({
  result,
  topics,
  onOpenTopic,
}: {
  result: AnalysisResultOf<"summary">;
  // The topic list, for the drivers; a chat answer may not carry it.
  topics?: AnalysisResultOf<"topic_list">;
  onOpenTopic?: (topicId: number) => void;
}) {
  const data = result.data;
  if (data.classified === 0)
    return (
      <p className="voc-hint">Sin reseñas clasificadas en esta selección.</p>
    );
  // Positive share minus negative share, in whole points.
  const net = Math.round(
    ((data.positive.share ?? 0) - (data.negative.share ?? 0)) * 100,
  );
  const tone =
    net <= -5 ? " voc-text-negative" : net >= 5 ? " voc-text-positive" : "";
  const verdict =
    net <= -5
      ? "Más negativas que positivas"
      : net >= 5
        ? "Más positivas que negativas"
        : "Opiniones divididas";
  // The topic with the most reviews on each side, leaving out topic -1.
  const rows = (topics?.data ?? []).filter((row) => row.topic_id !== -1);
  const driver = (label: "negative" | "positive") =>
    rows.reduce<(typeof rows)[number] | null>(
      (best, row) =>
        row[label] > 0 && (best === null || row[label] > best[label])
          ? row
          : best,
      null,
    );
  const drivers = {
    negative: driver("negative"),
    positive: driver("positive"),
  };
  return (
    <div className="voc-mood">
      <p className="voc-mood-head">
        <span
          className={`voc-mood-net${tone}`}
          title="Positivas menos negativas, en puntos porcentuales"
        >
          {net < 0 ? `−${Math.abs(net)}` : net > 0 ? `+${net}` : "0"} pts
        </span>
        <span className="voc-mood-verdict">{verdict}</span>
      </p>
      <StackedBar
        negative={data.negative.count}
        neutral={data.neutral.count}
        positive={data.positive.count}
        scale={data.classified}
        label={
          order
            .map((label) => `${labelName[label]} ${percent(data[label].share)}`)
            .join(", ") + ` de ${number(data.classified)} reseñas clasificadas`
        }
      />
      <ul className="voc-mood-rows">
        {order.map((label) => {
          const top = label === "neutral" ? null : drivers[label];
          return (
            <li key={label}>
              <span
                className={`voc-swatch voc-fill-${label}`}
                aria-hidden="true"
              />
              <span>{labelName[label]}</span>
              <span className="voc-num">{percent(data[label].share)}</span>
              <span className="voc-num is-count">
                {number(data[label].count)}
              </span>
              <span className="voc-mood-driver">
                {top && (
                  <>
                    Más citado:{" "}
                    {onOpenTopic ? (
                      <button
                        type="button"
                        className="voc-link"
                        onClick={() => onOpenTopic(top.topic_id)}
                      >
                        {top.display_name} →
                      </button>
                    ) : (
                      top.display_name
                    )}
                  </>
                )}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
