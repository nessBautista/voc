import type { TimelineData } from "../../analysis";
import { number } from "../format";
import { Legend, shortMonth } from "./shared";

const WIDTH = 640;
const HEIGHT = 200;
const PAD = { top: 8, right: 4, bottom: 24, left: 40 };
const names = {
  negative: "negativas",
  neutral: "neutras",
  positive: "positivas",
} as const;
const fills = { negative: "neg", neutral: "neu", positive: "pos" } as const;

// Round gridline steps (1, 2, 2.5 or 5 times a power of ten), about three
// lines up to the busiest month.
function ticks(peak: number) {
  const raw = Math.max(peak / 3, 1);
  const power = 10 ** Math.floor(Math.log10(raw));
  const step =
    [1, 2, 2.5, 5, 10].map((f) => f * power).find((s) => s >= raw) ??
    10 * power;
  const whole = Math.max(Math.round(step), 1);
  const top = Math.max(Math.ceil(peak / whole) * whole, whole);
  return {
    top,
    lines: Array.from({ length: top / whole + 1 }, (_, i) => i * whole),
  };
}

// Reviews per month, stacked by sentiment: negative at the bottom, then
// neutral, then positive, so volume and mood read together.
export function Timeline({ data }: { data: TimelineData }) {
  const months = data.months;
  if (months.length === 0)
    return (
      <p className="voc-hint">No hay reseñas con fecha en esta selección.</p>
    );
  const peak = Math.max(...months.map((m) => m.total), 1);
  const scale = ticks(peak);
  const inner = {
    w: WIDTH - PAD.left - PAD.right,
    h: HEIGHT - PAD.top - PAD.bottom,
  };
  const slot = inner.w / months.length;
  const bar = Math.max(4, slot * 0.56);
  const y = (value: number) =>
    PAD.top + inner.h - (value / scale.top) * inner.h;
  const everyOther = months.length > 8;
  return (
    <div className="voc-timeline">
      <Legend />
      <svg
        className="voc-chart"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label={`Reseñas por mes, apiladas por sentimiento, de ${shortMonth(months[0].month)} a ${shortMonth(months[months.length - 1].month)}`}
      >
        {scale.lines.map((value) => (
          <g key={value}>
            <line
              className={value === 0 ? "axis" : "grid"}
              x1={PAD.left}
              x2={WIDTH - PAD.right}
              y1={y(value)}
              y2={y(value)}
            />
            <text x={PAD.left - 8} y={y(value) + 4} textAnchor="end">
              {number(value)}
            </text>
          </g>
        ))}
        {months.map((m, index) => {
          const x = PAD.left + index * slot + (slot - bar) / 2;
          let base = 0;
          return (
            <g key={m.month}>
              {(["negative", "neutral", "positive"] as const).map((label) => {
                const value = m[label];
                if (value === 0) return null;
                const top = y(base + value);
                // A 1px gap between segments, never below a visible sliver.
                const height = Math.max(y(base) - top - 1, 0.5);
                base += value;
                return (
                  <rect
                    key={label}
                    className={fills[label]}
                    x={x}
                    y={top}
                    width={bar}
                    height={height}
                    rx={1}
                  >
                    <title>{`${shortMonth(m.month)} · ${names[label]}: ${number(value)} de ${number(m.total)}`}</title>
                  </rect>
                );
              })}
              {(!everyOther || index % 2 === 0) && (
                <text x={x + bar / 2} y={HEIGHT - 6} textAnchor="middle">
                  {shortMonth(m.month)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      <p className="voc-hint">
        {number(data.reviews_in_view)} reseñas en {months.length} meses
        {data.busiest
          ? `; el más activo, ${shortMonth(data.busiest.month)} con ${number(data.busiest.total)}`
          : ""}
        .
      </p>
    </div>
  );
}
