import { useEffect } from "react";
import { downloadUrl, type ImageResult } from "./api";

interface Props {
  image: ImageResult;
  onClose: () => void;
  onFindSimilar: (id: string) => void;
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "Unknown";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = units[0];
  for (let index = 1; value >= 1024 && index < units.length; index += 1) {
    value /= 1024;
    unit = units[index];
  }
  return `${value >= 10 ? value.toFixed(1) : value.toFixed(2)} ${unit}`;
}

function formatDate(timestamp: number): string {
  if (!timestamp) return "Unknown";
  const date = new Date(timestamp * 1000);
  return Number.isNaN(date.getTime()) ? "Unknown" : date.toLocaleString();
}

export function ImageModal({ image, onClose, onFindSimilar }: Props) {
  const filename = image.path.split(/[\\/]/).pop() ?? image.path;
  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, []);

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="image-title" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div>
            <h2 id="image-title">{filename}</h2>
            <p className="image-path" title={image.path}>{image.path}</p>
          </div>
          <button type="button" className="icon-button" aria-label="Close" onClick={onClose}>×</button>
        </div>
        <div className="modal-content">
          <div className="modal-preview">
            <img src={image.thumbnail_url} alt={filename} />
          </div>
          <div className="metadata-panel">
            <h3>Image details</h3>
            <dl className="metadata-list">
              <div><dt>Dimensions</dt><dd>{image.width && image.height ? `${image.width} × ${image.height} px` : "Unknown"}</dd></div>
              <div><dt>Format</dt><dd>{image.format || "Unknown"}</dd></div>
              <div><dt>File size</dt><dd>{formatBytes(image.size)}</dd></div>
              <div><dt>Date</dt><dd>{formatDate(image.date_taken)}</dd></div>
              <div><dt>Modified</dt><dd>{formatDate(image.mtime)}</dd></div>
              <div><dt>Indexed</dt><dd>{formatDate(image.indexed_at)}</dd></div>
            </dl>
            <h3>Recognized objects</h3>
            <div className="tag-list">
              {image.objects.length > 0 ? image.objects.map((object) => <span key={object}>{object}</span>) : <p>None detected</p>}
            </div>
            {image.ocr_text && <><h3>Detected text</h3><p className="ocr-text">{image.ocr_text}</p></>}
          </div>
        </div>
        <div className="modal-actions">
          <button type="button" className="btn-ghost" onClick={() => onFindSimilar(image.id)}>Find Similar</button>
          <a className="download-button primary" href={downloadUrl(image.id)} download>Download original</a>
        </div>
      </div>
    </div>
  );
}
