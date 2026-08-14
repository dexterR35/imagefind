import { useEffect, useRef, useState } from "react";
import {
  fetchReindexStatus,
  fetchSettings,
  startReindex,
  updateSettings,
  type ReindexStatus,
  type Settings as SettingsType,
} from "./api";

interface Props {
  onReindexComplete: () => void;
}

export function Settings({ onReindexComplete }: Props) {
  const [open, setOpen] = useState(false);
  const [settings, setSettings] = useState<SettingsType | null>(null);
  const [customTagsText, setCustomTagsText] = useState("");
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<ReindexStatus | null>(null);
  const pollRef = useRef<number | null>(null);

  useEffect(() => {
    if (open && settings === null) {
      fetchSettings().then((s) => {
        setSettings(s);
        setCustomTagsText(s.ram_custom_tags.join(", "));
      });
    }
  }, [open, settings]);

  useEffect(() => {
    return () => stopPolling();
  }, []);

  function stopPolling() {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }

  function updateField<K extends keyof SettingsType>(key: K, value: SettingsType[K]) {
    setSettings((s) => (s ? { ...s, [key]: value } : s));
  }

  async function handleSaveAndReindex() {
    if (!settings) return;
    setSaving(true);
    setStatus(null);
    const ram_custom_tags = customTagsText
      .split(",")
      .map((v) => v.trim())
      .filter(Boolean);

    // Split into two stages so a failure after settings were already saved
    // (e.g. a reindex is already running elsewhere, giving a 409) reports
    // accurately instead of implying nothing happened at all.
    try {
      const saved = await updateSettings({ ...settings, ram_custom_tags });
      setSettings(saved);
    } catch {
      setSaving(false);
      setStatus({ processed: 0, total: 0, failed: 0, done: true, error: "Failed to save settings." });
      return;
    }

    let jobId: string;
    try {
      jobId = await startReindex(true);
    } catch {
      setSaving(false);
      setStatus({
        processed: 0, total: 0, failed: 0, done: true,
        error: "Settings saved, but failed to start reindex (a reindex may already be running).",
      });
      return;
    }

    pollRef.current = window.setInterval(async () => {
      try {
        const s = await fetchReindexStatus(jobId);
        setStatus(s);
        if (s.done) {
          stopPolling();
          setSaving(false);
          onReindexComplete();
        }
      } catch {
        stopPolling();
        setSaving(false);
        setStatus({
          processed: 0, total: 0, failed: 0, done: true,
          error: "Lost connection while checking reindex status.",
        });
      }
    }, 500);
  }

  return (
    <div className="settings">
      <button type="button" className="settings-toggle" onClick={() => setOpen((o) => !o)}>
        {open ? "Close Settings" : "Settings"}
      </button>
      {open && settings && (
        <div className="settings-panel">
          <label>
            Object confidence (blank uses model defaults)
            <input
              type="number"
              step="0.01"
              min="0"
              max="1"
              value={settings.ram_confidence ?? ""}
              onChange={(e) => updateField("ram_confidence", e.target.value === "" ? null : Number(e.target.value))}
            />
          </label>
          <label>
            Custom tags to also look for (comma-separated). For named entities/
            characters, add example photos in backend/reference_tags/&lt;tag&gt;/
            for better matching.
            <input
              type="text"
              value={customTagsText}
              onChange={(e) => setCustomTagsText(e.target.value)}
            />
          </label>
          <button type="button" onClick={handleSaveAndReindex} disabled={saving}>
            {saving ? "Reindexing..." : "Save & Reindex"}
          </button>
          {status && !status.done && (
            <span>
              {status.processed} / {status.total}
            </span>
          )}
          {status?.done && status.failed > 0 && (
            <span className="reindex-error">{status.failed} image(s) failed to index — check server logs.</span>
          )}
          {status?.error && <span className="reindex-error">{status.error}</span>}
        </div>
      )}
    </div>
  );
}
