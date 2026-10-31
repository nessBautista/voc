import type { Ref } from "react";
import type { AnalysisResultOf } from "../../analysis";
import { number } from "../format";
import { assessmentName } from "../analysis/shared";

// The fixed toolbar: previous and next topic in the list's order, the
// position, and close. The detail sheet's glyph buttons live only here.
export function DetailToolbar({
  position,
  count,
  onPrevious,
  onNext,
  onClose,
}: {
  position: number | null;
  count: number;
  onPrevious?: () => void;
  onNext?: () => void;
  onClose: () => void;
}) {
  return (
    <div className="voc-dh-bar">
      <div className="voc-dh-actions">
        <button
          type="button"
          className="voc-icon-btn"
          aria-label="Tema anterior"
          title="Tema anterior (↑)"
          disabled={!onPrevious}
          onClick={onPrevious}
        >
          ↑
        </button>
        <button
          type="button"
          className="voc-icon-btn"
          aria-label="Tema siguiente"
          title="Tema siguiente (↓)"
          disabled={!onNext}
          onClick={onNext}
        >
          ↓
        </button>
        {position !== null && (
          <span className="voc-meta">
            {position} de {count}
          </span>
        )}
      </div>
      <button
        type="button"
        className="voc-icon-btn"
        aria-label="Cerrar"
        title="Cerrar (Esc)"
        onClick={onClose}
      >
        ✕
      </button>
    </div>
  );
}

const reviewCount = (value: number) =>
  `${number(value)} ${value === 1 ? "reseña" : "reseñas"}`;

// The identity that opens the scrolling body: the name, then one muted line.
export function DetailIdentity({
  detail,
  titleId,
  titleRef,
}: {
  detail: AnalysisResultOf<"topic_detail">;
  titleId?: string;
  titleRef?: Ref<HTMLHeadingElement>;
}) {
  const data = detail.data;
  const name =
    data.topic_id === -1
      ? "Sin asignar"
      : `Tema ${data.topic_id} · ${assessmentName[data.assessment]}`;
  const inView =
    data.in_view === data.member_count
      ? reviewCount(data.in_view)
      : `${reviewCount(data.in_view)} de ${number(data.member_count)} con los filtros`;
  return (
    <header className="voc-dh">
      <h2
        id={titleId}
        ref={titleRef}
        tabIndex={-1}
        className={`voc-detail-title${data.wording_source === "original" ? " is-keywords" : ""}`}
      >
        {data.display_name}
      </h2>
      <p className="voc-dh-line">
        {name} · {inView}
      </p>
    </header>
  );
}
