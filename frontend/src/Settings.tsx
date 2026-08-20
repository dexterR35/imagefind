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
  isTunnelAccess?: boolean;
}

function formatMB(bytes: number): string {
  return (bytes / (1024 * 1024)).toFixed(0);
}

export function Settings({
  onReindexComplete,
  isTunnelAccess = window.location.hostname.endsWith(".trycloudflare.com"),
}: Props) {
  const [open, setOpen] = useState(false);
  const [settings, setSettings] = useState<SettingsType | null>(null);
  const [customTagsText, setCustomTagsText] = useState("");
  const [settingsLoadError, setSettingsLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [reindexing, setReindexing] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [status, setStatus] = useState<ReindexStatus | null>(null);
  const pollRef = useRef<number | null>(null);
  const jobIdRef = useRef<string | null>(null);
  const savedImagesDirRef = useRef<string | null>(null);

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
          savedImagesDirRef.current = s.images_dir;
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
    if (!window.confirm("Download and install the RAM++ object-tagging model?\n\nContinue?")) return;
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

  async function handleSave() {
    if (!settings) return;
    const folderChanged = savedImagesDirRef.current !== null
      && settings.images_dir !== savedImagesDirRef.current;
    if (
      folderChanged
      && !window.confirm(
        "Change the indexed image folder from:\n" + savedImagesDirRef.current
        + "\n\nto:\n" + settings.images_dir
        + "\n\nThis will switch the watched folder but does not reindex automatically. Continue?",
      )
    ) return;

    setSaving(true);
    setSaveMessage(null);
    setStatus(null);
    const ram_custom_tags = customTagsText
      .split(",")
      .map((v) => v.trim())
      .filter(Boolean);

    try {
      const saved = await updateSettings({ ...settings, ram_custom_tags });
      setSettings(saved);
      savedImagesDirRef.current = saved.images_dir;
      setSaveMessage("Settings saved.");
    } catch (err) {
      setStatus({
        processed: 0, total: 0, failed: 0, done: true, cancelled: false,
        error: `Failed to save settings: ${err instanceof Error ? err.message : String(err)}`,
      });
    } finally {
      setSaving(false);
    }
  }

  async function handleReindex() {
    if (isTunnelAccess) return;
    if (!window.confirm("Force a full image reindex now? This can take a while.\n\nContinue?")) return;

    setReindexing(true);
    setStopping(false);
    setSaveMessage(null);
    setStatus(null);
    let jobId: string;
    try {
      jobId = await startReindex(true);
    } catch (err) {
      setReindexing(false);
      setStatus({
        processed: 0, total: 0, failed: 0, done: true, cancelled: false,
        error: `Failed to start reindex: ${err instanceof Error ? err.message : String(err)}`,
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
          setReindexing(false);
          setStopping(false);
          jobIdRef.current = null;
          onReindexComplete();
        }
      } catch {
        stopPolling();
        setReindexing(false);
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
      <button
        type="button"
        className="icon-button settings-toggle"
        aria-label={open ? "Close settings" : "Open settings"}
        aria-expanded={open}
        title="Settings"
        onClick={() => setOpen((o) => !o)}
      >
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
          <path fill="currentColor" d="M19.4 13a7.8 7.8 0 0 0 .05-1 7.8 7.8 0 0 0-.05-1l2.1-1.65-2-3.46-2.57 1.03a7.4 7.4 0 0 0-1.72-1L14.82 3h-4l-.4 2.92a7.4 7.4 0 0 0-1.71 1L6.13 5.9l-2 3.46L6.23 11a7.8 7.8 0 0 0-.05 1 7.8 7.8 0 0 0 .05 1l-2.1 1.65 2 3.46 2.58-1.03a7.4 7.4 0 0 0 1.71 1l.4 2.92h4l.39-2.92a7.4 7.4 0 0 0 1.72-1l2.57 1.03 2-3.46L19.4 13Zm-6.58 2.5a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7Z" />
        </svg>
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
          <button type="button" onClick={handleSave} disabled={saving || reindexing}>
            {saving ? "Saving..." : "Save"}
          </button>
          <button
            type="button"
            onClick={handleReindex}
            disabled={saving || reindexing || isTunnelAccess}
            title={isTunnelAccess ? "Reindexing is available only from the local app." : undefined}
          >
            {reindexing ? "Reindexing..." : "Reindex"}
          </button>
          {isTunnelAccess && (
            <span>Reindexing is disabled through the public tunnel. Open ImageFind locally to run it.</span>
          )}
          {saveMessage && <span>{saveMessage}</span>}
          {reindexing && (
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
