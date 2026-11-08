import { updatedAt, type ImageResult } from "./api";
import { ImageCard } from "./ImageCard";
import { ImageTable } from "./ImageTable";

export type ResultView = "cards" | "table";

interface Props {
  images: ImageResult[];
  view?: ResultView;
  onSelect: (image: ImageResult) => void;
  onToggleFavorite?: (id: string, next: boolean) => void;
  selectedIds?: Set<string>;
  onToggleSelect?: (id: string) => void;
  // Split the grid into day-by-day sections, keyed by updatedAt (when the file
  // appeared on the NAS or was last edited) so freshly-added files group as
  // "new" regardless of the photo's original EXIF date. Only meaningful when
  // the results are actually ordered by that date (see App.tsx).
  groupByDate?: boolean;
}

const UNKNOWN_DATE_KEY = "unknown";

// Local day key ("2026-09-07") from a unix-seconds timestamp. A zero
// timestamp groups under "Unknown date" instead of rendering as Jan 1 1970.
function dayKey(seconds: number): string {
  if (!seconds) return UNKNOWN_DATE_KEY;
  const d = new Date(seconds * 1000);
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

function dayLabel(seconds: number): string {
  if (!seconds) return "Unknown date";
  const d = new Date(seconds * 1000);
  const now = new Date();
  const opts: Intl.DateTimeFormatOptions = { day: "numeric", month: "short" };
  if (d.getFullYear() !== now.getFullYear()) opts.year = "numeric";
  return d.toLocaleDateString(undefined, opts);
}

function groupByDay(images: ImageResult[]): { key: string; label: string; images: ImageResult[] }[] {
  const groups: { key: string; label: string; images: ImageResult[] }[] = [];
  for (const img of images) {
    const updated = updatedAt(img);
    const key = dayKey(updated);
    const last = groups[groups.length - 1];
    if (last && last.key === key) {
      last.images.push(img);
    } else {
      groups.push({ key, label: dayLabel(updated), images: [img] });
    }
  }
  return groups;
}

export function ImageGrid({
  images, view = "cards", onSelect, onToggleFavorite, selectedIds, onToggleSelect, groupByDate,
}: Props) {
  if (images.length === 0) {
    return <p className="empty-state">No images match these filters.</p>;
  }
  if (view === "table") {
    return (
      <ImageTable
        images={images}
        onSelect={onSelect}
        onToggleFavorite={onToggleFavorite}
        selectedIds={selectedIds}
        onToggleSelect={onToggleSelect}
      />
    );
  }

  const cardProps = (img: ImageResult) => ({
    image: img,
    onClick: onSelect,
    onToggleFavorite,
    selected: selectedIds?.has(img.id),
    onToggleSelect,
  });

  if (groupByDate) {
    return (
      <div className="image-grid-groups">
        {groupByDay(images).map((group) => (
          <section className="image-grid-group" key={group.key}>
            <h2 className="image-grid-date">{group.label}</h2>
            <div className="image-grid">
              {group.images.map((img) => <ImageCard key={img.id} {...cardProps(img)} />)}
            </div>
          </section>
        ))}
      </div>
    );
  }

  return (
    <div className="image-grid">
      {images.map((img) => <ImageCard key={img.id} {...cardProps(img)} />)}
    </div>
  );
}
