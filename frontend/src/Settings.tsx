import { useEffect, useRef, useState } from "react";
import {
  cancelModelDownload,
  cancelReindex,
  fetchModelDownloadStatus,
  fetchModelStatus,
  fetchReindexStatus,
  fetchSettings,
  startModelDownload,
  startReindex,
  updateSettings,
  type ModelDownloadStatus,
  type ReindexStatus,
  type Settings as SettingsType,
} from "./api";

interface Props {
  onReindexComplete: () => void;
}

function formatMB(bytes: number): string {
  return (bytes / (1024 * 1024)).toFixed(0);
}

export function Settings({ onReindexComplete }: Props) {
  const [open, setOpen] = useState(false);
  const [settings, setSettings] = useState<SettingsType | null>(null);
  const [customTagsText, setCustomTagsText] = useState("");
  const [settingsLoadError, setSettingsLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [status, setStatus] = useState<ReindexStatus | null>(null);
  const pollRef = useRef<number | null>(null);
  const jobIdRef = useRef<string | null>(null);

  const [modelInstalled, setModelInstalled] = useState<boolean | null>(null);
  const [modelDownloading, setModelDownloading] = useState(false);
  const [modelStatus, setModelStatus] = useState<ModelDownloadStatus | null>(null);
  const modelPollRef = useRef<number | null>(null);
  const modelJobIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (open && settings === null) {
      setSettingsLoadError(null);
      fetchSettings()
        .then((s) => {
          setSettings(s);
          setCustomTagsText(s.ram_custom_tags.join(", "));
        })
        .catch(() => setSettingsLoadError("Could not load settings. Check the backend connection."));
    }
    if (open && modelInstalled === null) {
      fetchModelStatus()
        .then((s) => setModelInstalled(s.installed))
        .catch(() => setModelInstalled(null));
    }
  }, [open, settings, modelInstalled]);

  useEffect(() => {
    return () => {
      stopPolling();
      stopModelPolling();
    };
  }, []);

  function stopPolling() {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }

  function stopModelPolling() {
    if (modelPollRef.current !== null) {
      window.clearInterval(modelPollRef.current);
      modelPollRef.current = null;
    }
  }

  async function handleInstallModel() {
    setModelDownloading(true);
    setModelStatus(null);
    let jobId: string;
    try {
      jobId = await startModelDownload();
    } catch (err) {
      setModelDownloading(false);
      setModelStatus({
        downloaded_bytes: 0, total_bytes: 0, done: true, cancelled: false,
        error: `Failed to start download: ${err instanceof Error ? err.message : String(err)}`,
      });
      return;
    }
    modelJobIdRef.current = jobId;

    modelPollRef.current = window.setInterval(async () => {
      try {
        const s = await fetchModelDownloadStatus(jobId);
        setModelStatus(s);
        if (s.done) {
          stopModelPolling();
          setModelDownloading(false);
          modelJobIdRef.current = null;
          if (!s.error && !s.cancelled) setModelInstalled(true);
        }
      } catch {
        stopModelPolling();
        setModelDownloading(false);
        modelJobIdRef.current = null;
        setModelStatus({
          downloaded_bytes: 0, total_bytes: 0, done: true, cancelled: false,
          error: "Lost connection while checking download status.",
        });
      }
    }, 500);
  }

  async function handleCancelInstallModel() {
    if (!modelJobIdRef.current) return;
    try {
      await cancelModelDownload(modelJobIdRef.current);
    } catch {
      // best-effort — the polling loop above will still notice job.done and
      // stop showing a "downloading" state either way
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
    } catch (err) {
      setSaving(false);
      setStatus({
        processed: 0, total: 0, failed: 0, done: true, cancelled: false,
        error: `Failed to save settings: ${err instanceof Error ? err.message : String(err)}`,
      });
      return;
    }

    let jobId: string;
    try {
      jobId = await startReindex(true);
    } catch (err) {
      setSaving(false);
      setStatus({
        processed: 0, total: 0, failed: 0, done: true, cancelled: false,
        error: `Settings saved, but failed to start reindex: ${err instanceof Error ? err.message : String(err)}`,
      });
      return;
    }
    jobIdRef.current = jobId;

    pollRef.current = window.setInterval(async () => {
      try {
        const s = await fetchReindexStatus(jobId);
        setStatus(s);
        if (s.done) {
          stopPolling();
          setSaving(false);
          setStopping(false);
          jobIdRef.current = null;
          onReindexComplete();
        }
      } catch {
        stopPolling();
        setSaving(false);
        setStopping(false);
        jobIdRef.current = null;
        setStatus({
          processed: 0, total: 0, failed: 0, done: true, cancelled: false,
          error: "Lost connection while checking reindex status.",
        });
      }
    }, 500);
  }

  async function handleStopReindex() {
    if (!jobIdRef.current) return;
    setStopping(true);
    try {
      await cancelReindex(jobIdRef.current);
    } catch {
      setStopping(false);
    }
  }

  return (
    <div className="settings">
      <button type="button" className="settings-toggle" onClick={() => setOpen((o) => !o)}>
        {open ? "Close Settings" : "Settings"}
      </button>
      {open && settingsLoadError && (
        <span className="reindex-error" role="alert">{settingsLoadError}</span>
      )}
      {open && settings && (
        <div className="settings-panel">
          {modelInstalled === false && (
            <div className="model-install">
              <span>RAM++ object-tagging model isn't installed yet.</span>
              <button type="button" onClick={handleInstallModel} disabled={modelDownloading}>
                {modelDownloading ? "Installing..." : "Install RAM++ Model"}
              </button>
              {modelDownloading && modelStatus && !modelStatus.done && (
                <button type="button" onClick={handleCancelInstallModel}>
                  Cancel
                </button>
              )}
              {modelStatus && !modelStatus.done && modelStatus.total_bytes > 0 && (
                <span>
                  {formatMB(modelStatus.downloaded_bytes)} / {formatMB(modelStatus.total_bytes)} MB
                </span>
              )}
              {modelStatus?.done && modelStatus.cancelled && <span>Download cancelled.</span>}
              {modelStatus?.error && <span className="reindex-error">{modelStatus.error}</span>}
            </div>
          )}
          <label>
            Image folder path
            <input
              type="text"
              value={settings.images_dir}
              onChange={(e) => updateField("images_dir", e.target.value)}
            />
          </label>
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
          {saving && (
            <button type="button" onClick={handleStopReindex} disabled={stopping}>
              {stopping ? "Stopping..." : "Stop"}
            </button>
          )}
          {status && !status.done && (
            <span>
              {status.processed} / {status.total}
            </span>
          )}
          {status?.done && status.cancelled && (
            <span>Reindex stopped — kept {status.processed} already-processed image(s).</span>
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
