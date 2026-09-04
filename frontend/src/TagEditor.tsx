import { useState } from "react";

interface Props {
  tags: string[];
  onChange: (tags: string[]) => void;
}

const MAX_TAG_LENGTH = 60;

export function TagEditor({ tags, onChange }: Props) {
  const [draft, setDraft] = useState("");

  function commit(raw: string) {
    const tag = raw.trim().slice(0, MAX_TAG_LENGTH);
    if (!tag) return;
    if (!tags.some((existing) => existing.toLowerCase() === tag.toLowerCase())) {
      onChange([...tags, tag]);
    }
    setDraft("");
  }

  function remove(tag: string) {
    onChange(tags.filter((existing) => existing !== tag));
  }

  return (
    <div className="tag-editor">
      <div className="tag-editor-chips">
        {tags.map((tag) => (
          <span key={tag} className="tag-chip">
            {tag}
            <button
              type="button"
              aria-label={`Remove tag ${tag}`}
              onClick={() => remove(tag)}
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <input
        type="text"
        aria-label="Add a tag"
        placeholder="Add a tag…"
        maxLength={MAX_TAG_LENGTH}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            commit(draft);
          } else if (e.key === "Backspace" && draft === "" && tags.length > 0) {
            remove(tags[tags.length - 1]);
          }
        }}
        onBlur={() => commit(draft)}
      />
    </div>
  );
}
