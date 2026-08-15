import { downloadUrl, type ImageResult } from "./api";

interface Props {
  image: ImageResult;
  onClose: () => void;
  onFindSimilar: (id: string) => void;
}

export function ImageModal({ image, onClose, onFindSimilar }: Props) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <img src={image.thumbnail_url} alt={image.path} />
        <p>{image.ocr_text}</p>
        <p>Colors: {image.colors.join(", ")}</p>
        <p>Objects: {image.objects.join(", ")}</p>
        <button type="button" onClick={() => onFindSimilar(image.id)}>
          Find Similar
        </button>
        <a className="download-button" href={downloadUrl(image.id)} download>
          Download
        </a>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
    </div>
  );
}
