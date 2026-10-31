import { useEffect, useRef, type KeyboardEvent, type ReactNode } from "react";

// The floating window of the list–detail pattern: a non-modal sheet docked
// beside the topic list. The head stays fixed; the body scrolls and resets to
// the top whenever `scrollKey` changes (another topic).
export function DetailSheet({
  titleId,
  head,
  children,
  scrollKey,
  busy = false,
  onClose,
  onPrevious,
  onNext,
}: {
  titleId: string;
  head: ReactNode;
  children: ReactNode;
  scrollKey: unknown;
  busy?: boolean;
  onClose: () => void;
  onPrevious?: () => void;
  onNext?: () => void;
}) {
  const body = useRef<HTMLDivElement>(null);
  useEffect(() => {
    body.current?.scrollTo({ top: 0 });
  }, [scrollKey]);

  const keys = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    // Up and down move between topics, except where they already mean
    // something: text fields, selects and the tab list.
    const target = event.target as HTMLElement;
    if (target.closest("input, textarea, select, [role='tablist']")) return;
    if (event.key === "ArrowUp" && onPrevious) {
      event.preventDefault();
      onPrevious();
    } else if (event.key === "ArrowDown" && onNext) {
      event.preventDefault();
      onNext();
    }
  };

  return (
    <section
      className="voc-sheet voc-detail app-sheet"
      aria-labelledby={titleId}
      aria-busy={busy}
      onKeyDown={keys}
    >
      <div className="voc-sheet-head">{head}</div>
      <div className="voc-sheet-body" ref={body}>
        {children}
      </div>
    </section>
  );
}
