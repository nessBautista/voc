import type { TimelineData } from "../../../analysis";
import { number } from "../../format";
import { percent, shortMonth } from "../../analysis/shared";

const WIDTH = 640;
const HEIGHT = 168;
const PAD = { top: 22, right: 4, bottom: 22, left: 36 };

export const longMonth = (value: string) =>
  new Intl.DateTimeFormat("es-MX", {
    timeZone: "UTC",
    month: "long",
    year: "numeric",
  }).format(new Date(`${value}-01T00:00:00Z`));

// The share of negative reviews per month: every month muted, the month the
// research chose in the negative color with its value written above it, and a
// dashed line at the period's average so the peak reads against a baseline.
export function MonthShareChart({
  data,
  highlight,
}: {
  data: TimelineData;
  // The month the next step narrowed to; the server chose it, the chart shows it.
  highlight: string | null;
}) {
  const months = data.months;
  if (months.length === 0)
    return (
      <p className="voc-hint">No hay reseñas con fecha en esta selección.</p>
    );
  const share = (m: (typeof months)[number]) =>
    m.classified > 0 ? m.negative / m.classified : null;
  const peak = Math.max(...months.map((m) => share(m) ?? 0), 0.1);
  // A round top in tens of points, with one gridline halfway.
  const top = Math.min(Math.ceil(peak * 10) / 10, 1);
  const inner = {
    w: WIDTH - PAD.left - PAD.right,
    h: HEIGHT - PAD.top - PAD.bottom,
  };
  const slot = inner.w / months.length;
  const bar = Math.max(4, slot * 0.56);
  const y = (value: number) => PAD.top + inner.h - (value / top) * inner.h;
  const everyOther = months.length > 8;
  const totals = months.reduce(
    (sum, m) => ({
      negative: sum.negative + m.negative,
      classified: sum.classified + m.classified,
    }),
    { negative: 0, classified: 0 },
  );
  const average =
    totals.classified > 0 ? totals.negative / totals.classified : null;
  const chosen = months.find((m) => m.month === highlight);
  const chosenShare = chosen ? share(chosen) : null;
  return (
    <figure className="voc-monthshare">
      <svg
        className="voc-chart"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label={
          `Proporción de reseñas negativas por mes, de ${shortMonth(months[0].month)} a ${shortMonth(months[months.length - 1].month)}` +
          (chosen && chosenShare !== null
            ? `; ${longMonth(chosen.month)}, el mes elegido, con ${percent(chosenShare)}`
            : "")
        }
      >
        {[0, top / 2, top].map((value) => (
          <g key={value}>
            <line
              className={value === 0 ? "axis" : "grid"}
              x1={PAD.left}
              x2={WIDTH - PAD.right}
              y1={y(value)}
              y2={y(value)}
            />
            <text x={PAD.left - 8} y={y(value) + 4} textAnchor="end">
              {percent(value)}
            </text>
          </g>
        ))}
        {months.map((m, index) => {
          const value = share(m);
          const x = PAD.left + index * slot + (slot - bar) / 2;
          const marked = m.month === highlight;
          return (
            <g key={m.month}>
              {value !== null && (
                <rect
                  className={marked ? "neg is-marked" : "is-muted"}
                  x={x}
                  y={y(value)}
                  width={bar}
                  height={Math.max(y(0) - y(value), 1)}
                  rx={1}
                >
                  <title>{`${shortMonth(m.month)} · ${percent(value)} negativas (${number(m.negative)} de ${number(m.classified)})`}</title>
                </rect>
              )}
              {marked && value !== null && (
                <text
                  className="is-marked"
                  x={x + bar / 2}
                  y={y(value) - 6}
                  textAnchor="middle"
                >
                  {percent(value)}
                </text>
              )}
              {(marked || !everyOther || index % 2 === 0) && (
                <text
                  className={marked ? "is-marked" : undefined}
                  x={x + bar / 2}
                  y={HEIGHT - 6}
                  textAnchor="middle"
                >
                  {shortMonth(m.month)}
                </text>
              )}
            </g>
          );
        })}
        {/* Drawn last, so the average's label stays above the bars. */}
        {average !== null && (
          <g className="is-average">
            <line
              x1={PAD.left}
              x2={WIDTH - PAD.right}
              y1={y(average)}
              y2={y(average)}
            />
            <text x={PAD.left + 6} y={y(average) - 5} textAnchor="start">
              promedio {percent(average)}
            </text>
          </g>
        )}
      </svg>
      <figcaption className="voc-meta">
        Proporción de reseñas negativas por mes
        {chosen && chosenShare !== null
          ? ` · ${longMonth(chosen.month)}: ${percent(chosenShare)} (${number(chosen.negative)} de ${number(chosen.classified)})`
          : ""}
      </figcaption>
    </figure>
  );
}
