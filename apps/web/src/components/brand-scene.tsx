/** Decorative embrace: no emotion, detection or medical state is encoded here. */
export function BrandScene() {
  return (
    <svg
      className="brand-scene"
      viewBox="0 0 160 110"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M15 103C-4 57 22 17 57 19c29 2 15 51 41 48 27-3 4-54 36-43 26 9 29 57 7 79Z"
        fill="currentColor"
      />
      <ellipse
        cx="78"
        cy="57"
        rx="24"
        ry="30"
        fill="var(--palette-black)"
        transform="rotate(-15 78 57)"
      />
      <path
        d="M31 66c14 30 48 36 72 12"
        stroke="var(--palette-black)"
        strokeWidth="5"
        strokeLinecap="round"
      />
    </svg>
  );
}
