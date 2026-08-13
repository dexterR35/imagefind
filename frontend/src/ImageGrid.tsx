import type { ImageResult } from "./api";
import { ImageCard } from "./ImageCard";

interface Props {
  images: ImageResult[];
  onSelect: (image: ImageResult) => void;
}

export function ImageGrid({ images, onSelect }: Props) {
  if (images.length === 0) {
    return <p className="empty-state">No images match these filters.</p>;
  }
  return (
    <div className="image-grid">
      {images.map((img) => (
        <ImageCard key={img.id} image={img} onClick={onSelect} />
      ))}
    </div>
  );
}
