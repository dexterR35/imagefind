import { useState } from "react";
import {
  createCollection,
  deleteCollection,
  renameCollection,
  type Collection,
} from "./api";

interface Props {
  collections: Collection[];
  onChanged: () => void;
}

export function Collections({ collections, onChanged }: Props) {
  const [open, setOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleCreate() {
    const name = newName.trim();
    if (!name) return;
    await run(() => createCollection(name));
    setNewName("");
  }

  async function handleRename(id: string) {
    const name = editingName.trim();
    if (!name) return;
    await run(() => renameCollection(id, name));
    setEditingId(null);
  }

  async function handleDelete(collection: Collection) {
    if (!window.confirm(`Delete collection "${collection.name}"? The images stay indexed.`)) return;
    await run(() => deleteCollection(collection.id));
  }

  return (
    <div className="collections">
      <button
        type="button"
        className="icon-button collections-toggle"
        aria-label={open ? "Close collections" : "Open collections"}
        aria-expanded={open}
        title="Collections"
        onClick={() => setOpen((value) => !value)}
      >
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
          <path fill="currentColor" d="M3 5h8l2 2h8v3H3V5Zm0 6h18v8H3v-8Z" />
        </svg>
      </button>
      {open && (
        <div className="collections-panel">
          <div className="collections-new">
            <input
              type="text"
              aria-label="New collection name"
              placeholder="New collection…"
              maxLength={120}
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void handleCreate();
              }}
            />
            <button type="button" className="btn-primary" onClick={() => void handleCreate()} disabled={busy || !newName.trim()}>
              Add
            </button>
          </div>
          {error && <span className="reindex-error" role="alert">{error}</span>}
          {collections.length === 0 ? (
            <p className="collections-empty">No collections yet.</p>
          ) : (
            <ul className="collections-list">
              {collections.map((collection) => (
                <li key={collection.id}>
                  {editingId === collection.id ? (
                    <>
                      <input
                        type="text"
                        aria-label={`Rename ${collection.name}`}
                        value={editingName}
                        maxLength={120}
                        onChange={(e) => setEditingName(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") void handleRename(collection.id);
                          if (e.key === "Escape") setEditingId(null);
                        }}
                      />
                      <button type="button" className="btn-ghost" onClick={() => void handleRename(collection.id)} disabled={busy}>
                        Save
                      </button>
                    </>
                  ) : (
                    <>
                      <span className="collections-name">
                        {collection.name} <span className="collections-count">{collection.count}</span>
                      </span>
                      <button
                        type="button"
                        className="icon-button"
                        aria-label={`Rename ${collection.name}`}
                        title="Rename"
                        onClick={() => {
                          setEditingId(collection.id);
                          setEditingName(collection.name);
                        }}
                      >
                        ✎
                      </button>
                      <button
                        type="button"
                        className="icon-button"
                        aria-label={`Delete ${collection.name}`}
                        title="Delete"
                        onClick={() => void handleDelete(collection)}
                      >
                        ×
                      </button>
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
