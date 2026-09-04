import type { ImageResult } from "./api";
import { ImageCard } from "./ImageCard";
import { ImageTable } from "./ImageTable";

export type ResultView = "cards" | "table";

interface Props {
  images: ImageResult[];
  view?: ResultView;
  onSelect: (image: ImageResult) => void;
}

export function ImageGrid({ images, view = "cards", onSelect }: Props) {
  if (images.length === 0) {
    return <p className="empty-state">No images match these filters.</p>;
  }
  if (view === "table") {
    return <ImageTable images={images} onSelect={onSelect} />;
  }
  return (
    <div className="image-grid">
      {images.map((img) => (
        <ImageCard key={img.id} image={img} onClick={onSelect} />
      ))}
    </div>
  );
}
