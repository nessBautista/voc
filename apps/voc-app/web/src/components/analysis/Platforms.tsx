import type { AnalysisResultOf } from "../../analysis";
import { number } from "../format";
import { percent, PlatformTag, StackedBar } from "./shared";

// One row per platform on a shared scale: the largest platform total. The
// bar counts classified reviews; the total beside it is the full count.
export function Platforms({
  result,
}: {
  result: AnalysisResultOf<"platforms">;
}) {
  const rows = result.data;
  if (rows.length === 0)
    return <p className="voc-hint">Ninguna plataforma en vista.</p>;
  const scale = Math.max(...rows.map((row) => row.total), 1);
  return (
    <ul className="voc-platform-rows">
      {rows.map((row) => (
        <li key={row.platform}>
          <span>
            <PlatformTag platform={row.platform} />
          </span>
          <StackedBar
            negative={row.negative}
            neutral={row.neutral}
            positive={row.positive}
            scale={scale}
          />
          <span className="voc-count">
            <span className="voc-num">{number(row.total)}</span>
            <span className="voc-meta">
              {percent(row.negative_share)} negativas
            </span>
          </span>
        </li>
      ))}
    </ul>
  );
}
