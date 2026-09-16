import type { ImageResult } from "./api";
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
  // Split the grid into day-by-day sections, keyed by added_at (the file's
  // creation time, i.e. when it appeared on the NAS) so freshly-added files
  // group as "new" regardless of the photo's original EXIF date. Only
  // meaningful when the results are actually ordered by added_at (see App.tsx).
  groupByDate?: boolean;
}

// Local day key ("2026-09-07") from a unix-seconds timestamp.
function dayKey(seconds: number): string {
  const d = new Date(seconds * 1000);
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

function dayLabel(seconds: number): string {
  const d = new Date(seconds * 1000);
  const now = new Date();
  const opts: Intl.DateTimeFormatOptions = { day: "numeric", month: "short" };
  if (d.getFullYear() !== now.getFullYear()) opts.year = "numeric";
  return d.toLocaleDateString(undefined, opts);
}

function groupByDay(images: ImageResult[]): { key: string; label: string; images: ImageResult[] }[] {
  const groups: { key: string; label: string; images: ImageResult[] }[] = [];
  for (const img of images) {
    const key = dayKey(img.added_at);
    const last = groups[groups.length - 1];
    if (last && last.key === key) {
      last.images.push(img);
    } else {
      groups.push({ key, label: dayLabel(img.added_at), images: [img] });
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
    key: img.id,
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
              {group.images.map((img) => <ImageCard {...cardProps(img)} />)}
            </div>
          </section>
        ))}
      </div>
    );
  }

  return (
    <div className="image-grid">
      {images.map((img) => <ImageCard {...cardProps(img)} />)}
    </div>
  );
}
