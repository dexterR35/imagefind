import { useState } from "react";
import type { Box } from "./api";

export type FindStatus =
  | { kind: "idle" }
  | { kind: "searching"; word: string }
  | { kind: "done"; word: string; count: number }
  | { kind: "error"; message: string };

interface Props {
  findStatus: FindStatus;
  onFind: (word: string) => void;
  onClearBoxes: () => void;
  hasBoxes: boolean;
  drawing: boolean;
  onToggleDrawing: () => void;
  draft: Box | null;
  saving: boolean;
  teachMessage: string | null;
  onTeach: (word: string) => void;
}

export function BoxTools({
  findStatus, onFind, onClearBoxes, hasBoxes,
  drawing, onToggleDrawing, draft, saving, teachMessage, onTeach,
}: Props) {
  const [findWord, setFindWord] = useState("");
  const [teachWord, setTeachWord] = useState("");

  return (
    <>
      <section className="meta-section">
        <h3>Find in image</h3>
        <form
          className="box-tool-row"
          onSubmit={(event) => {
            event.preventDefault();
            if (findWord.trim()) onFind(findWord.trim());
          }}
        >
          <input
            type="text"
            aria-label="Word to find in image"
            placeholder="gun, zeus, coin…"
            maxLength={60}
            value={findWord}
            onChange={(e) => setFindWord(e.target.value)}
          />
          <button type="submit" className="btn-ghost" disabled={!findWord.trim() || findStatus.kind === "searching"}>
            Find
          </button>
        </form>
        <p className="box-tool-status" aria-live="polite">
          {findStatus.kind === "searching" && `Looking for “${findStatus.word}”… the first search loads the detector and can take a minute.`}
          {findStatus.kind === "done" && (findStatus.count > 0
            ? `Found ${findStatus.count} for “${findStatus.word}”.`
            : `“${findStatus.word}” not found in this image.`)}
          {findStatus.kind === "error" && findStatus.message}
          {findStatus.kind === "idle" && "Or click a recognized object below."}
        </p>
        {hasBoxes && (
          <button type="button" className="btn-ghost" onClick={onClearBoxes}>Clear boxes</button>
        )}
      </section>

      <section className="meta-section">
        <h3>Teach a word</h3>
        <button type="button" className="btn-ghost" aria-pressed={drawing} onClick={onToggleDrawing}>
          {drawing ? "Cancel drawing" : "Draw a box"}
        </button>
        {drawing && !draft && (
          <p className="box-tool-status">Drag over the thing on the image.</p>
        )}
        {draft && (
          <form
            className="box-tool-row"
            onSubmit={(event) => {
              event.preventDefault();
              if (teachWord.trim()) onTeach(teachWord.trim());
            }}
          >
            <input
              type="text"
              aria-label="What is in the box"
              placeholder="What is it? e.g. zeus"
              maxLength={60}
              autoFocus
              value={teachWord}
              onChange={(e) => setTeachWord(e.target.value)}
            />
            <button type="submit" className="btn-primary" disabled={!teachWord.trim() || saving}>
              {saving ? "Saving…" : "Save example"}
            </button>
          </form>
        )}
        {teachMessage && <p className="box-tool-status" aria-live="polite">{teachMessage}</p>}
      </section>
    </>
  );
}
