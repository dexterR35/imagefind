import type { ImageResult } from "./api";
import { FavoriteButton } from "./FavoriteButton";

interface Props {
  image: ImageResult;
  onClick: (image: ImageResult) => void;
  onToggleFavorite?: (id: string, next: boolean) => void;
  selected?: boolean;
  onToggleSelect?: (id: string) => void;
}

const MAX_CHIPS = 3;

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "Unknown size";
  if (bytes < 1024) return bytes + " B";
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = units[0];
  for (let index = 1; value >= 1024 && index < units.length; index += 1) {
    value /= 1024;
    unit = units[index];
  }
  return (value >= 10 ? value.toFixed(1) : value.toFixed(2)) + " " + unit;
}

function formatDate(timestamp: number): string {
  if (!timestamp) return "Unknown date";
  const date = new Date(timestamp * 1000);
  return Number.isNaN(date.getTime())
    ? "Unknown date"
    : date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function ImageCard({ image, onClick, onToggleFavorite, selected, onToggleSelect }: Props) {
  const filename = image.path.split(/[\\/]/).pop() ?? image.path;
  const extension = (filename.includes(".") ? filename.split(".").pop() : image.format) || image.format;
  const badge = (extension || "image").toUpperCase();
  // Footer line: everything the badge does not already say.
  const details = [
    image.width > 0 && image.height > 0 ? `${image.width} × ${image.height}` : "",
    formatBytes(image.size),
    "Added " + formatDate(image.indexed_at),
  ].filter(Boolean).join(" · ");
  // Only the user's own note. OCR text is too noisy to show here (a logo gets
  // read a dozen ways); it stays in the modal's "Detected text" section.
  const note = image.note?.trim() ?? "";
  const chips = [...(image.user_tags ?? []), ...image.objects];
  const visibleChips = chips.slice(0, MAX_CHIPS);
  const hiddenChips = chips.length - visibleChips.length;

  return (
    <div className={`image-card-wrap${selected ? " is-selected" : ""}`}>
      <button type="button" className="image-card" onClick={() => onClick(image)}>
        <span className="card-shot">
          <img loading="lazy" src={image.thumbnail_url} alt={filename} />
          <span className="card-badge">{badge}</span>
        </span>
        <span className="card-body">
          <span className="filename" title={filename}>{filename}</span>
          {note && <span className="card-note" title={note}>{note}</span>}
          {visibleChips.length > 0 && (
            <span className="card-tags">
              {visibleChips.map((chip, index) => (
                <span className="card-tag" key={`${chip}-${index}`}>{chip}</span>
              ))}
              {hiddenChips > 0 && <span className="card-tag is-more">+{hiddenChips}</span>}
            </span>
          )}
        </span>
        <span className="card-foot">
          <span className="card-dot" aria-hidden="true" />
          <span className="card-metadata" title={details}>{details}</span>
        </span>
      </button>
      {onToggleSelect && (
        <label className="card-select" title="Select">
          <input
            type="checkbox"
            checked={!!selected}
            aria-label={`Select ${filename}`}
            onChange={() => onToggleSelect(image.id)}
          />
        </label>
      )}
      {onToggleFavorite && (
        <FavoriteButton
          className="card-favorite"
          favorite={!!image.favorite}
          onToggle={(next) => onToggleFavorite(image.id, next)}
        />
      )}
    </div>
  );
}
