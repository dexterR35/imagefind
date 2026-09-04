import type { ImageResult } from "./api";
import { FavoriteButton } from "./FavoriteButton";

interface Props {
  image: ImageResult;
  onClick: (image: ImageResult) => void;
  onToggleFavorite?: (id: string, next: boolean) => void;
  selected?: boolean;
  onToggleSelect?: (id: string) => void;
}

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
  const extension = filename.includes(".") ? filename.split(".").pop() : image.format;
  const details = [
    extension?.toUpperCase() || "IMAGE",
    image.width > 0 && image.height > 0 ? `${image.width} × ${image.height}` : "",
    formatBytes(image.size),
    "Added " + formatDate(image.indexed_at),
  ].filter(Boolean).join(" · ");
  return (
    <div className={`image-card-wrap${selected ? " is-selected" : ""}`}>
      <button type="button" className="image-card" onClick={() => onClick(image)}>
        <img src={image.thumbnail_url} alt={filename} />
        <span className="filename" title={filename}>{filename}</span>
        <span className="card-metadata">{details}</span>
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
