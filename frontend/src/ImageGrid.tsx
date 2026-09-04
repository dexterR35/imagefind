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
}

export function ImageGrid({
  images, view = "cards", onSelect, onToggleFavorite, selectedIds, onToggleSelect,
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
  return (
    <div className="image-grid">
      {images.map((img) => (
        <ImageCard
          key={img.id}
          image={img}
          onClick={onSelect}
          onToggleFavorite={onToggleFavorite}
          selected={selectedIds?.has(img.id)}
          onToggleSelect={onToggleSelect}
        />
      ))}
    </div>
  );
}
