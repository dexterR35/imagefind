import { useRef } from "react";
import type { Box, DetectedBox } from "./api";

interface Props {
  // The image element's untransformed layout box inside the preview, plus the
  // preview's zoom/pan transform, so the overlay sits exactly on the image.
  rect: { left: number; top: number; width: number; height: number };
  transform: string;
  scale: number;
  boxes: DetectedBox[];
  drawing: boolean;
  draft: Box | null;
  onDraftChange: (box: Box | null) => void;
}

const MIN_DRAFT_FRACTION = 0.01;

function clamp01(value: number): number {
  return Math.min(1, Math.max(0, value));
}

function boxStyle(box: Box, scale: number): React.CSSProperties {
  return {
    left: `${box[0] * 100}%`,
    top: `${box[1] * 100}%`,
    width: `${(box[2] - box[0]) * 100}%`,
    height: `${(box[3] - box[1]) * 100}%`,
    // Keep the outline the same on-screen thickness at any zoom level.
    borderWidth: `${2 / scale}px`,
  };
}

export function BoxOverlay({ rect, transform, scale, boxes, drawing, draft, onDraftChange }: Props) {
  const overlayRef = useRef<HTMLDivElement>(null);
  // A ref, not state: the next pointer event can arrive before a re-render.
  const startRef = useRef<{ x: number; y: number; pointerId: number } | null>(null);

  // Pointer position as fractions of the image. getBoundingClientRect already
  // includes the zoom/pan transform, so this holds at any zoom level.
  function toFraction(event: React.PointerEvent): { x: number; y: number } {
    const r = overlayRef.current!.getBoundingClientRect();
    return { x: clamp01((event.clientX - r.left) / r.width), y: clamp01((event.clientY - r.top) / r.height) };
  }

  function boxTo(event: React.PointerEvent): Box | null {
    const start = startRef.current;
    if (!start || start.pointerId !== event.pointerId) return null;
    const point = toFraction(event);
    return [
      Math.min(start.x, point.x), Math.min(start.y, point.y),
      Math.max(start.x, point.x), Math.max(start.y, point.y),
    ];
  }

  function onPointerDown(event: React.PointerEvent<HTMLDivElement>) {
    if (!drawing) return;
    event.stopPropagation();
    startRef.current = { ...toFraction(event), pointerId: event.pointerId };
    onDraftChange(null);
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function onPointerMove(event: React.PointerEvent<HTMLDivElement>) {
    const box = boxTo(event);
    if (box) onDraftChange(box);
  }

  function onPointerUp(event: React.PointerEvent<HTMLDivElement>) {
    const box = boxTo(event);
    if (!box) return;
    startRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    // A click without a real drag is not a box.
    const tooSmall = box[2] - box[0] < MIN_DRAFT_FRACTION || box[3] - box[1] < MIN_DRAFT_FRACTION;
    onDraftChange(tooSmall ? null : box);
  }

  return (
    <div
      ref={overlayRef}
      className={`box-overlay${drawing ? " is-drawing" : ""}`}
      data-testid="box-overlay"
      style={{ ...rect, transform }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onDoubleClick={(event) => { if (drawing) event.stopPropagation(); }}
    >
      {boxes.map((found, index) => (
        <div
          key={`${found.box.join(",")}-${index}`}
          className={`found-box is-${found.source}`}
          style={boxStyle(found.box, scale)}
        >
          <span className="found-box-label" style={{ transform: `scale(${1 / scale})` }}>
            {found.label} {Math.round(found.score * 100)}%
          </span>
        </div>
      ))}
      {draft && <div className="draft-box" style={boxStyle(draft, scale)} />}
    </div>
  );
}
