import type { ImageResult } from "./api";

interface Props {
  image: ImageResult;
  onClick: (image: ImageResult) => void;
}

export function ImageCard({ image, onClick }: Props) {
  const filename = image.path.split("/").pop() ?? image.path;
  const details = [
    image.format,
    image.width > 0 && image.height > 0 ? `${image.width} × ${image.height}` : "",
  ].filter(Boolean).join(" · ");
  return (
    <button type="button" className="image-card" onClick={() => onClick(image)}>
      <img src={image.thumbnail_url} alt={filename} />
      <span className="filename">{filename}</span>
      {details && <span className="card-metadata">{details}</span>}
    </button>
  );
}
