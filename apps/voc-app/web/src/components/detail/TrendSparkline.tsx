import type { TimelineData } from "../../analysis";
import { number } from "../format";
import { shortMonth } from "../analysis/shared";

const HEIGHT = 28;
const parts = [
  ["negative", "neg", "negativa", "negativas"],
  ["neutral", "neu", "neutra", "neutras"],
  ["positive", "pos", "positiva", "positivas"],
] as const;

// One tiny stacked bar per month, scaled to the busiest month, for spotting
// when a topic's reviews happened. No axes or numbers: Timeline reads values.
export function TrendSparkline({ months }: { months: TimelineData["months"] }) {
  if (months.length === 0) return null;
  const busiest = Math.max(...months.map((m) => m.total), 1);
  const empty = months.filter((m) => m.total === 0).length;
  const first = shortMonth(months[0].month);
  const last = shortMonth(months[months.length - 1].month);
  return (
    <div className="voc-spark">
      <svg
        viewBox={`0 0 ${months.length * 10} ${HEIGHT}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={
          `Reseñas del tema por mes, de ${first} a ${last}: como máximo ${number(busiest)} por mes` +
          (empty > 0
            ? `, ${empty} ${empty === 1 ? "mes" : "meses"} sin reseñas`
            : "")
        }
      >
        {months.map((m, index) => {
          const x = index * 10 + 2;
          const label = shortMonth(m.month);
          if (m.total === 0)
            return (
              <rect
                key={m.month}
                className="empty"
                x={x}
                y={HEIGHT - 2}
                width={6}
                height={2}
              >
                <title>{label} · sin reseñas</title>
              </rect>
            );
          // Stack from the bottom: negative, neutral, positive. Reviews
          // without a label count in the height but draw no segment.
          let base = HEIGHT;
          const summary = parts
            .filter(([key]) => m[key] > 0)
            .map(
              ([key, , one, many]) =>
                `${number(m[key])} ${m[key] === 1 ? one : many}`,
            )
            .join(", ");
          return (
            <g key={m.month}>
              <title>
                {label} · {summary || `${number(m.total)} sin clasificar`}
              </title>
              {parts.map(([key, className]) => {
                if (m[key] === 0) return null;
                const height = (m[key] / busiest) * HEIGHT;
                base -= height;
                return (
                  <rect
                    key={key}
                    className={className}
                    x={x}
                    y={base}
                    width={6}
                    height={height}
                  />
                );
              })}
            </g>
          );
        })}
      </svg>
      <div className="voc-spark-range" aria-hidden="true">
        <span>{first}</span>
        <span>{last}</span>
      </div>
    </div>
  );
}
