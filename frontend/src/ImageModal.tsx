import { useCallback, useEffect, useRef, useState } from "react";
import {
  addExample, detectInImage, downloadUrl, imageUrl,
  type Box, type Collection, type DetectedBox, type ImageResult,
} from "./api";
import { BoxOverlay } from "./BoxOverlay";
import { BoxTools, type FindStatus } from "./BoxTools";
import { FavoriteButton } from "./FavoriteButton";
import { TagEditor } from "./TagEditor";

interface Props {
  image: ImageResult;
  onClose: () => void;
  onFindSimilar: (id: string) => void;
  onPrev?: () => void;
  onNext?: () => void;
  onToggleFavorite?: (id: string, next: boolean) => void;
  onTagsChange?: (id: string, tags: string[]) => void;
  onNoteChange?: (id: string, note: string) => void;
  collections?: Collection[];
  onAddToCollection?: (collectionId: string, imageId: string) => void;
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

export function ImageModal({
  image, onClose, onFindSimilar, onPrev, onNext,
  onToggleFavorite, onTagsChange, onNoteChange, collections = [], onAddToCollection,
}: Props) {
  const filename = image.path.split(/[\\/]/).pop() ?? image.path;
  const [noteDraft, setNoteDraft] = useState(image.note ?? "");
  useEffect(() => setNoteDraft(image.note ?? ""), [image.id, image.note]);
  const previewRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const [fullLoaded, setFullLoaded] = useState(false);
  const imgRef = useRef<HTMLImageElement>(null);
  const [imgRect, setImgRect] = useState<{ left: number; top: number; width: number; height: number } | null>(null);
  const [boxes, setBoxes] = useState<DetectedBox[]>([]);
  const [findStatus, setFindStatus] = useState<FindStatus>({ kind: "idle" });
  const [drawing, setDrawing] = useState(false);
  const [draft, setDraft] = useState<Box | null>(null);
  const [savingExample, setSavingExample] = useState(false);
  const [teachMessage, setTeachMessage] = useState<string | null>(null);
  const findAbortRef = useRef<AbortController | null>(null);
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
    setFullLoaded(false);
    findAbortRef.current?.abort();
    setBoxes([]);
    setFindStatus({ kind: "idle" });
    setDrawing(false);
    setDraft(null);
    setTeachMessage(null);
  }, [image.id]);

  useEffect(() => () => findAbortRef.current?.abort(), []);

  // The overlay mirrors the image's untransformed layout box; the zoom/pan
  // transform is applied to both identically.
  const measureImage = useCallback(() => {
    const img = imgRef.current;
    if (!img || !img.offsetWidth) return;
    setImgRect({ left: img.offsetLeft, top: img.offsetTop, width: img.offsetWidth, height: img.offsetHeight });
  }, []);

  useEffect(() => {
    const node = previewRef.current;
    if (!node || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(measureImage);
    observer.observe(node);
    return () => observer.disconnect();
  }, [measureImage]);

  const findWord = useCallback((word: string) => {
    findAbortRef.current?.abort();
    const controller = new AbortController();
    findAbortRef.current = controller;
    setFindStatus({ kind: "searching", word });
    detectInImage(image.id, word, controller.signal)
      .then((found) => {
        setBoxes(found);
        setFindStatus({ kind: "done", word, count: found.length });
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setFindStatus({ kind: "error", message: `Find failed: ${err instanceof Error ? err.message : String(err)}` });
      });
  }, [image.id]);

  const teach = useCallback((word: string) => {
    if (!draft) return;
    setSavingExample(true);
    setTeachMessage(null);
    addExample(image.id, word, draft)
      .then((saved) => {
        setBoxes([{ label: saved.tag, score: 1, source: "example", box: draft }]);
        setDraft(null);
        setDrawing(false);
        setTeachMessage(
          `Saved example ${saved.examples} of “${saved.tag}” and tagged this image. ` +
          "Click Reindex in Settings to find it in other images.",
        );
        onTagsChange?.(image.id, saved.user_tags);
      })
      .catch((err: unknown) => {
        setTeachMessage(`Could not save: ${err instanceof Error ? err.message : String(err)}`);
      })
      .finally(() => setSavingExample(false));
  }, [draft, image.id, onTagsChange]);

  // Zoom toward an anchor point measured from the preview centre. `anchor`
  // {x,y} of {0,0} zooms toward the centre (used by the buttons/keys).
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

  // Keyboard: Esc closes, arrows page between results, +/-/0 drive the zoom.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) {
        // Let the tag/note fields keep their own keys; one Esc just blurs
        // (which saves the note) instead of closing the modal.
        if (event.key === "Escape") target.blur();
        return;
      }
      switch (event.key) {
        case "Escape":
          if (drawing) {
            setDrawing(false);
            setDraft(null);
          } else {
            onClose();
          }
          break;
        case "ArrowLeft":
          onPrev?.();
          break;
        case "ArrowRight":
          onNext?.();
          break;
        case "+":
        case "=":
          event.preventDefault();
          zoomBy(ZOOM_STEP);
          break;
        case "-":
        case "_":
          event.preventDefault();
          zoomBy(1 / ZOOM_STEP);
          break;
        case "0":
          resetView();
          break;
        default:
          break;
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose, onPrev, onNext, zoomBy, resetView, drawing]);

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (scale === 1 || drawing) return;
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
    if (drawing) return;
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
  const viewTransform = `translate(${offset.x}px, ${offset.y}px) scale(${scale})`;
  const transition = dragging ? "none" : "transform 120ms ease";
  const zoomPercent = Math.round(scale * 100);

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="image-title" onClick={(e) => e.stopPropagation()}>
        <header className="modal-header">
          <div className="modal-heading">
            <h2 id="image-title" title={filename}>{filename}</h2>
            <p className="image-path" title={image.path}>{image.path}</p>
          </div>
          <div className="modal-header-actions">
            {onToggleFavorite && (
              <FavoriteButton
                favorite={!!image.favorite}
                onToggle={(next) => onToggleFavorite(image.id, next)}
              />
            )}
            <button type="button" className="icon-button" aria-label="Close" onClick={onClose}>×</button>
          </div>
        </header>
        <div className="modal-content">
          <div className="modal-preview-wrap">
            <div
              ref={previewRef}
              className={`modal-preview${zoomed ? " is-zoomed" : ""}${drawing ? " is-drawing" : ""}`}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={endDrag}
              onPointerCancel={endDrag}
              onDoubleClick={onDoubleClick}
            >
              {!fullLoaded && (
                <img
                  className="preview-placeholder"
                  src={image.thumbnail_url}
                  alt=""
                  aria-hidden="true"
                  draggable={false}
                />
              )}
              <img
                ref={imgRef}
                src={imageUrl(image.id)}
                alt={filename}
                draggable={false}
                onLoad={() => { setFullLoaded(true); measureImage(); }}
                style={{ transform: viewTransform, transition }}
              />
              {fullLoaded && imgRect && (boxes.length > 0 || drawing || draft) && (
                <BoxOverlay
                  rect={imgRect}
                  transform={viewTransform}
                  scale={scale}
                  boxes={boxes}
                  drawing={drawing}
                  draft={draft}
                  onDraftChange={setDraft}
                />
              )}
              {onPrev && (
                <button
                  type="button"
                  className="preview-nav prev"
                  aria-label="Previous image"
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={onPrev}
                >
                  ‹
                </button>
              )}
              {onNext && (
                <button
                  type="button"
                  className="preview-nav next"
                  aria-label="Next image"
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={onNext}
                >
                  ›
                </button>
              )}
              <div
                className="preview-toolbar"
                role="toolbar"
                aria-label="Image zoom controls"
                onPointerDown={(e) => e.stopPropagation()}
                onDoubleClick={(e) => e.stopPropagation()}
              >
                <button type="button" className="icon-button" aria-label="Zoom out" onClick={() => zoomBy(1 / ZOOM_STEP)} disabled={scale <= MIN_SCALE}>−</button>
                <span className="preview-zoom-level" aria-live="polite">{zoomPercent}%</span>
                <button type="button" className="icon-button" aria-label="Zoom in" onClick={() => zoomBy(ZOOM_STEP)} disabled={scale >= MAX_SCALE}>+</button>
                <span className="preview-toolbar-sep" aria-hidden="true" />
                <button type="button" className="icon-button" aria-label="Reset and center" onClick={resetView} disabled={!zoomed}>⤢</button>
              </div>
            </div>
          </div>
          <aside className="metadata-panel">
            {onAddToCollection && collections.length > 0 && (
              <div className="curation-bar">
                <select
                  aria-label="Add to collection"
                  value=""
                  onChange={(e) => {
                    if (e.target.value) onAddToCollection(e.target.value, image.id);
                    e.target.value = "";
                  }}
                >
                  <option value="">Add to collection…</option>
                  {collections.map((c) => (
                    <option key={c.id} value={c.id}>{c.name}</option>
                  ))}
                </select>
              </div>
            )}
            <BoxTools
              findStatus={findStatus}
              onFind={findWord}
              onClearBoxes={() => { setBoxes([]); setFindStatus({ kind: "idle" }); }}
              hasBoxes={boxes.length > 0}
              drawing={drawing}
              onToggleDrawing={() => { setDrawing((on) => !on); setDraft(null); setTeachMessage(null); }}
              draft={draft}
              saving={savingExample}
              teachMessage={teachMessage}
              onTeach={teach}
            />
            <section className="meta-section">
              <h3>Image details</h3>
              <dl className="metadata-list">
                <div><dt>Dimensions</dt><dd>{image.width && image.height ? `${image.width} × ${image.height} px` : "Unknown"}</dd></div>
                <div><dt>Format</dt><dd>{image.format || "Unknown"}</dd></div>
                <div><dt>File size</dt><dd>{formatBytes(image.size)}</dd></div>
                <div><dt>Date</dt><dd>{formatDate(image.date_taken)}</dd></div>
                <div><dt>Modified</dt><dd>{formatDate(image.mtime)}</dd></div>
                <div><dt>Added</dt><dd>{formatDate(image.added_at)}</dd></div>
                <div><dt>Indexed</dt><dd>{formatDate(image.indexed_at)}</dd></div>
              </dl>
            </section>
            <section className="meta-section">
              <h3>Recognized objects</h3>
              <div className="tag-list">
                {image.objects.length > 0
                  ? image.objects.map((object) => (
                    <button
                      type="button"
                      key={object}
                      className="tag-find"
                      title={`Show where “${object}” is`}
                      onClick={() => findWord(object)}
                    >
                      {object}
                    </button>
                  ))
                  : <p>None detected</p>}
              </div>
            </section>
            {image.ocr_text && (
              <section className="meta-section">
                <h3>Detected text</h3>
                <p className="ocr-text">{image.ocr_text}</p>
              </section>
            )}
            {onTagsChange && (
              <section className="meta-section">
                <h3>Your tags</h3>
                <TagEditor
                  tags={image.user_tags ?? []}
                  onChange={(tags) => onTagsChange(image.id, tags)}
                />
              </section>
            )}
            {onNoteChange && (
              <section className="meta-section">
                <h3>Note</h3>
                <textarea
                  className="note-editor"
                  aria-label="Note"
                  rows={3}
                  maxLength={5000}
                  placeholder="Add a private note…"
                  value={noteDraft}
                  onChange={(e) => setNoteDraft(e.target.value)}
                  onBlur={() => {
                    if (noteDraft !== (image.note ?? "")) onNoteChange(image.id, noteDraft);
                  }}
                />
              </section>
            )}
          </aside>
        </div>
        <footer className="modal-actions">
          <span className="modal-hint" aria-hidden="true">Scroll or double-click to zoom · ← → to page · Esc to close</span>
          <button type="button" className="btn-ghost" onClick={() => onFindSimilar(image.id)}>Find Similar</button>
          <a className="download-button primary" href={downloadUrl(image.id)} download>Download original</a>
        </footer>
      </div>
    </div>
  );
}
