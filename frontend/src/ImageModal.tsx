import { useCallback, useEffect, useRef, useState } from "react";
import { downloadUrl, type ImageResult } from "./api";

interface Props {
  image: ImageResult;
  onClose: () => void;
  onFindSimilar: (id: string) => void;
}

const MIN_SCALE = 1;
const MAX_SCALE = 8;
const ZOOM_STEP = 1.4;

function clampScale(value: number): number {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, value));
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
  const previewRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const dragRef = useRef<{ pointerId: number; startX: number; startY: number; originX: number; originY: number } | null>(null);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, []);

  // Reset the view whenever a different image is shown.
  useEffect(() => {
    setScale(1);
    setOffset({ x: 0, y: 0 });
  }, [image.id]);

  // Zoom toward an anchor point measured from the preview centre. `anchor`
  // {x,y} of {0,0} zooms toward the centre (used by the buttons).
  const zoomTo = useCallback((nextScale: number, anchor: { x: number; y: number }) => {
    setScale((current) => {
      const target = clampScale(nextScale);
      if (target === current) return current;
      if (target === 1) {
        setOffset({ x: 0, y: 0 });
        return 1;
      }
      const ratio = target / current;
      setOffset((prev) => ({
        x: anchor.x - (anchor.x - prev.x) * ratio,
        y: anchor.y - (anchor.y - prev.y) * ratio,
      }));
      return target;
    });
  }, []);

  const zoomBy = useCallback(
    (factor: number) => zoomTo(scale * factor, { x: 0, y: 0 }),
    [scale, zoomTo],
  );

  const resetView = useCallback(() => {
    setScale(1);
    setOffset({ x: 0, y: 0 });
  }, []);

  // Wheel zoom needs a non-passive listener so we can prevent the page/modal
  // from scrolling while the pointer is over the image.
  useEffect(() => {
    const node = previewRef.current;
    if (!node) return undefined;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = node.getBoundingClientRect();
      const anchor = {
        x: event.clientX - rect.left - rect.width / 2,
        y: event.clientY - rect.top - rect.height / 2,
      };
      const factor = event.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
      zoomTo(scale * factor, anchor);
    };
    node.addEventListener("wheel", onWheel, { passive: false });
    return () => node.removeEventListener("wheel", onWheel);
  }, [scale, zoomTo]);

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (scale === 1) return;
    dragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      originX: offset.x,
      originY: offset.y,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
  };

  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    setOffset({
      x: drag.originX + (event.clientX - drag.startX),
      y: drag.originY + (event.clientY - drag.startY),
    });
  };

  const endDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    dragRef.current = null;
    setDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  const onDoubleClick = (event: React.MouseEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    if (scale > 1) {
      resetView();
      return;
    }
    zoomTo(2.5, {
      x: event.clientX - rect.left - rect.width / 2,
      y: event.clientY - rect.top - rect.height / 2,
    });
  };

  const zoomed = scale > 1;
  const zoomPercent = Math.round(scale * 100);

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
          <div className="modal-preview-wrap">
            <div
              ref={previewRef}
              className={`modal-preview${zoomed ? " is-zoomed" : ""}`}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={endDrag}
              onPointerCancel={endDrag}
              onDoubleClick={onDoubleClick}
            >
              <img
                src={image.thumbnail_url}
                alt={filename}
                draggable={false}
                style={{
                  transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})`,
                  transition: dragging ? "none" : "transform 120ms ease",
                }}
              />
            </div>
            <div className="preview-toolbar" role="toolbar" aria-label="Image zoom controls">
              <button type="button" className="icon-button" aria-label="Zoom out" onClick={() => zoomBy(1 / ZOOM_STEP)} disabled={scale <= MIN_SCALE}>−</button>
              <span className="preview-zoom-level" aria-live="polite">{zoomPercent}%</span>
              <button type="button" className="icon-button" aria-label="Zoom in" onClick={() => zoomBy(ZOOM_STEP)} disabled={scale >= MAX_SCALE}>+</button>
              <button type="button" className="icon-button" aria-label="Reset and center" onClick={resetView} disabled={!zoomed}>⤢</button>
            </div>
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
