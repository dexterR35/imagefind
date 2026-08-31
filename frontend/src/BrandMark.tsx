export function BrandMark({ size = 28 }: { size?: number }) {
  return (
    <span className="brand-mark" aria-hidden="true">
      <svg viewBox="0 0 24 24" width={size} height={size}>
        <path
          fill="currentColor"
          d="M13.2 2.1 4.8 13.4c-.35.47-.01 1.15.58 1.15h5.07l-1.7 7.2c-.16.7.72 1.16 1.2.63l8.5-11.4c.34-.46 0-1.13-.58-1.13h-5.1l1.78-7.1c.17-.7-.7-1.18-1.2-.65Z"
        />
      </svg>
    </span>
  );
}
