import type { TimelineData } from "../../analysis";
import { number } from "../format";
import { shortMonth } from "./shared";

const WIDTH = 640;
const HEIGHT = 200;
const PAD = { top: 10, right: 8, bottom: 26, left: 40 };

export function Timeline({
  data,
  compact = false,
}: {
  data: TimelineData;
  compact?: boolean;
}) {
  const months = data.months;
  const peak = Math.max(...months.map((m) => m.total), 1);
  const inner = {
    w: WIDTH - PAD.left - PAD.right,
    h: HEIGHT - PAD.top - PAD.bottom,
  };
  const slot = months.length > 0 ? inner.w / months.length : inner.w;
  const bar = Math.max(4, slot * 0.68);
  const y = (value: number) => PAD.top + inner.h - (value / peak) * inner.h;
  const everyOther = months.length > 8;
  return (
    <div className={`timeline${compact ? " compact" : ""}`}>
      {months.length === 0 ? (
        <p className="hint">No hay reseñas con fecha en esta selección.</p>
      ) : (
        <svg
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          role="img"
          aria-label={`Reseñas por mes, apiladas por sentimiento, de ${shortMonth(months[0].month)} a ${shortMonth(months[months.length - 1].month)}`}
        >
          {[0.5, 1].map((fraction) => (
            <g key={fraction} className="gridline">
              <line
                x1={PAD.left}
                x2={WIDTH - PAD.right}
                y1={y(peak * fraction)}
                y2={y(peak * fraction)}
              />
              <text
                x={PAD.left - 6}
                y={y(peak * fraction) + 4}
                textAnchor="end"
              >
                {number(Math.round(peak * fraction))}
              </text>
            </g>
          ))}
          {months.map((m, index) => {
            const x = PAD.left + index * slot + (slot - bar) / 2;
            const stack = [
              ["negative", m.negative],
              ["neutral", m.neutral],
              ["positive", m.positive],
            ] as const;
            let base = 0;
            return (
              <g key={m.month}>
                {stack.map(([label, value]) => {
                  const top = y(base + value);
                  const height = y(base) - top;
                  base += value;
                  return (
                    <rect
                      key={label}
                      className={`seg-${label}`}
                      x={x}
                      y={top}
                      width={bar}
                      height={height}
                    >
                      <title>{`${shortMonth(m.month)} · ${label === "negative" ? "negativas" : label === "neutral" ? "neutras" : "positivas"}: ${number(value)} de ${number(m.total)}`}</title>
                    </rect>
                  );
                })}
                {(!everyOther || index % 2 === 0) && (
                  <text
                    className="axis"
                    x={x + bar / 2}
                    y={HEIGHT - 8}
                    textAnchor="middle"
                  >
                    {shortMonth(m.month)}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
      )}
      {!compact && months.length > 0 && (
        <p className="note">
          {number(data.reviews_in_view)} reseñas en {months.length} meses
          {data.busiest
            ? `; el más activo, ${shortMonth(data.busiest.month)} con ${number(data.busiest.total)}`
            : ""}
          .
        </p>
      )}
    </div>
  );
}
