interface Props {
  favorite: boolean;
  onToggle: (next: boolean) => void;
  className?: string;
}

// A star toggle. Stops propagation so it works when overlaid on a clickable
// card or table row.
export function FavoriteButton({ favorite, onToggle, className }: Props) {
  return (
    <button
      type="button"
      className={`favorite-button${favorite ? " is-favorite" : ""}${className ? ` ${className}` : ""}`}
      aria-pressed={favorite}
      aria-label={favorite ? "Remove from favorites" : "Add to favorites"}
      title={favorite ? "Remove from favorites" : "Add to favorites"}
      onClick={(event) => {
        event.stopPropagation();
        onToggle(!favorite);
      }}
      onPointerDown={(event) => event.stopPropagation()}
    >
      {favorite ? "★" : "☆"}
    </button>
  );
}
