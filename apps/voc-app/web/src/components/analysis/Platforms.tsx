import type { AnalysisResultOf } from "../../analysis";
import { number } from "../format";
import { percent, PlatformTag, StackedBar } from "./shared";

export function Platforms({
  result,
}: {
  result: AnalysisResultOf<"platforms">;
}) {
  const rows = result.data;
  const scale = Math.max(...rows.map((row) => row.total), 1);
  return (
    <ul className="platform-rows">
      {rows.length === 0 && (
        <li className="hint">Ninguna plataforma en vista.</li>
      )}
      {rows.map((row) => (
        <li key={row.platform}>
          <PlatformTag platform={row.platform} />
          <StackedBar
            negative={row.negative}
            neutral={row.neutral}
            positive={row.positive}
            scale={scale}
          />
          <div className="topic-count">
            <strong>{number(row.total)}</strong>
            <span>{percent(row.negative_share)} negativas</span>
          </div>
        </li>
      ))}
    </ul>
  );
}
