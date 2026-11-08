import { useState } from "react";
import { zipDownloadUrl, type Collection } from "./api";

interface Props {
  ids: string[];
  collections: Collection[];
  onFavorite: (favorite: boolean) => void;
  onAddTags: (tags: string[]) => void;
  onAddToCollection: (collectionId: string) => void;
  onClear: () => void;
}

export function BulkActionBar({
  ids, collections, onFavorite, onAddTags, onAddToCollection, onClear,
}: Props) {
  const [tagDraft, setTagDraft] = useState("");

  function commitTags() {
    const tags = tagDraft.split(",").map((t) => t.trim()).filter(Boolean);
    if (tags.length === 0) return;
    onAddTags(tags);
    setTagDraft("");
  }

  return (
    <div className="bulk-bar" role="region" aria-label="Bulk actions">
      <span className="bulk-count">{ids.length} selected</span>

      <button type="button" className="btn-ghost" onClick={() => onFavorite(true)}>★ Favorite</button>
      <button type="button" className="btn-ghost" onClick={() => onFavorite(false)}>☆ Unfavorite</button>

      <span className="bulk-tag-add">
        <input
          type="text"
          aria-label="Tags to add"
          placeholder="tag, tag…"
          value={tagDraft}
          maxLength={200}
          onChange={(e) => setTagDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") commitTags();
          }}
        />
        <button type="button" className="btn-ghost" onClick={commitTags} disabled={!tagDraft.trim()}>
          Add tags
        </button>
      </span>

      {collections.length > 0 && (
        <select
          aria-label="Add selection to collection"
          value=""
          onChange={(e) => {
            if (e.target.value) onAddToCollection(e.target.value);
            e.target.value = "";
          }}
        >
          <option value="">Add to collection…</option>
          {collections.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
      )}

      <a className="btn-ghost" href={zipDownloadUrl(ids)} download>Download .zip</a>

      <button type="button" className="btn-ghost bulk-clear" onClick={onClear}>Clear</button>
    </div>
  );
}
