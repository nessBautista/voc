import type { ReactNode } from "react";

// One outline family, drawn on a 24-unit grid and shown at 16px: a 2.25 stroke
// on that grid is the design system's 1.5px. Icons only accompany a word;
// they are hidden from assistive technology.
const glyphs = {
  // A filter chip that is on.
  check: <path d="m5 12.5 4.5 4.5L19 7.5" />,
  // The composer's modes.
  daily: <path d="M13 3 5 13.5h6L10 21l8-10.5h-6z" />,
  research: (
    <>
      <circle cx="5" cy="6" r="2" />
      <circle cx="12" cy="12" r="2" />
      <circle cx="19" cy="18" r="2" />
      <path d="m6.6 7.3 3.8 3.4M13.6 13.3l3.8 3.4" />
    </>
  ),
  // The demo flow's suggestions.
  mood: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M8.5 14.5c1 1.3 2.2 2 3.5 2s2.5-.7 3.5-2M9 9.5h.01M15 9.5h.01" />
    </>
  ),
  complaint: <path d="M5 21V4h11l-2 4 2 4H5" />,
  trend: <path d="m3 17 6-6 4 4 8-8M15 7h6v6" />,
  reviews: <path d="M4 5h16v11H9l-5 4zM8 9h8M8 12.5h5" />,
  specific: (
    <>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m16 16 4.5 4.5" />
    </>
  ),
  focus: (
    <>
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="5" />
      <circle cx="12" cy="12" r="1" />
    </>
  ),
  // The data questions.
  sources: (
    <>
      <ellipse cx="12" cy="5.5" rx="7.5" ry="2.5" />
      <path d="M4.5 5.5v13c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5v-13M4.5 12c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5" />
    </>
  ),
  updated: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  coverage: (
    <>
      <rect x="3.5" y="5" width="17" height="15" rx="2" />
      <path d="M3.5 10h17M8 3v4M16 3v4" />
    </>
  ),
} satisfies Record<string, ReactNode>;

export type IconName = keyof typeof glyphs;

export function Icon({ name }: { name: IconName }) {
  return (
    <svg
      className="voc-icon"
      viewBox="0 0 24 24"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth={2.25}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {glyphs[name]}
    </svg>
  );
}
