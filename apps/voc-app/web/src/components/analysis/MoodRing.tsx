import type { AnalysisResultOf } from "../../analysis";
import { number } from "../format";
import { labelName, percent } from "./shared";

export function MoodRing({ result }: { result: AnalysisResultOf<"summary"> }) {
  const { negative, neutral, positive, classified } = result.data;
  const share = (value: number) =>
    classified > 0 ? (value / classified) * 100 : 0;
  const n = share(negative.count);
  const u = share(neutral.count);
  const gradient = `conic-gradient(var(--negative) 0 ${n}%, var(--neutral) ${n}% ${n + u}%, var(--positive) ${n + u}% 100%)`;
  return (
    <div className="mood">
      <div
        className="mood-ring"
        style={{ background: classified > 0 ? gradient : "var(--raised)" }}
        role="img"
        aria-label={`Ánimo general: ${percent(negative.share)} negativas, ${percent(neutral.share)} neutras, ${percent(positive.share)} positivas`}
      >
        <div className="mood-centre">
          <strong>{percent(positive.share)}</strong>
          <span>positivas</span>
        </div>
      </div>
      <ul className="mood-legend">
        {(["negative", "neutral", "positive"] as const).map((label) => (
          <li key={label}>
            <span className={`swatch seg-${label}`} aria-hidden="true" />
            <span>{labelName[label].toLowerCase()}</span>
            <strong>
              {number(result.data[label].count)} ·{" "}
              {percent(result.data[label].share)}
            </strong>
          </li>
        ))}
      </ul>
    </div>
  );
}
