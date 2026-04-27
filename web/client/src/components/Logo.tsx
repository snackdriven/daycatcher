// Custom inline SVG logo. A small smiling figure inside a frame.
// "Meat-Suit" — a body, contained, but cared for.

export function Logo({ size = 28, className = "" }: { size?: number; className?: string }) {
  return (
    <svg
      role="img"
      aria-label="Meat-Suit"
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      className={className}
    >
      {/* outer frame, hand-drawn feel */}
      <rect
        x="2.5"
        y="2.5"
        width="27"
        height="27"
        rx="4"
        stroke="currentColor"
        strokeWidth="1.6"
        fill="none"
      />
      {/* eyes */}
      <circle cx="12" cy="13" r="1.6" fill="currentColor" />
      <circle cx="20" cy="13" r="1.6" fill="currentColor" />
      {/* soft mouth */}
      <path
        d="M10 19 C 12 22, 20 22, 22 19"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        fill="none"
      />
    </svg>
  );
}

export function Wordmark({ className = "" }: { className?: string }) {
  return (
    <span
      className={`inline-flex items-center gap-2 ${className}`}
      style={{ fontFamily: "var(--font-serif)" }}
    >
      <Logo size={26} className="text-foreground" />
      <span className="text-base font-medium tracking-tight whitespace-nowrap">Meat-Suit</span>
    </span>
  );
}
