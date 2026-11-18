/**
 * Talking to the local Python server
 * ==================================
 *
 * The dashboard never talks to the internet. All of these helpers call
 * `http://127.0.0.1:8742` (or whatever port this window was opened on):
 *
 * * REST  — one-shot questions (“save this avatar”, “flip Beep”, “activate”)
 * * WebSocket — a live pipe of blink numbers (~10 times a second)
 *
 * License activation is forwarded by Python to Dodo Payments. Blink data
 * stays on this machine.
 *
 * If a call fails, the UI stays on its last known values rather than crashing.
 */
import type { EngineState, ToggleName } from "./types";

/** Settings remembered from last launch (avatar, window size, switches). */
export interface Preferences {
  avatar: string;
  compact: boolean;
  window_width: number;
  window_height: number;
  beep: boolean;
  alert: boolean;
  alert_mode?: "visual" | "sound";
  ontop: boolean;
  /** Last splash self-check: comfortable | mild | moderate | marked | decline */
  eye_comfort?: string;
  /** Skip the comfort check and the Before-you-start notes on the next launch. */
  skip_startup_screens?: boolean;
  dock?: boolean;
  ori_threshold?: number;
  alert_low_minutes?: number;
  writing_angle_deg?: number;
  turn_angle_deg?: number;
  close_delta?: number;
  camera_index?: number;
  camera_mode?: "single" | "hybrid";
}

export interface LiveSettings {
  ori_threshold: number;
  alert_low_minutes: number;
  writing_angle_deg: number;
  turn_angle_deg: number;
  close_delta: number;
}

export interface AuditEntry {
  at: string;
  setting: string;
  from: number | string;
  to: number | string;
  label?: string;
}

export interface ReportPoint {
  date?: string;
  label?: string;
  t?: string;
  i?: number;
  ori: number | null;
  screen_s: number;
  blinks: number;
}

export interface ReportPayload {
  kind: "daily" | "weekly" | "monthly";
  ori_threshold: number;
  date?: string;
  week_start?: string;
  week_end?: string;
  year?: number;
  month?: number;
  bin_minutes?: number;
  points?: ReportPoint[];
  days?: ReportPoint[];
  summary: {
    mean_ori: number | null;
    screen_s: number;
    blinks: number;
    days_with_data: number;
  };
}

/** One Practice line shown on the Information card. */
export interface QuoteItem {
  text: string;
  author: string;
}

/** Bundle the Practice screen uses: quotes + how often to rotate them. */
export interface QuotesPayload {
  count: number;
  interval_seconds: number;
  practice_duration_seconds: number;
  quotes: QuoteItem[];
}

/** Load the quote list from disk via the local API. */
/** Third-party license notice bundled with this copy. */
export async function fetchNotices(): Promise<string> {
  const res = await fetch("/api/notices", { cache: "no-store" });
  if (!res.ok) throw new Error("notices fetch failed");
  return res.text();
}

export async function fetchQuotes(): Promise<QuotesPayload> {
  const res = await fetch("/api/quotes", { cache: "no-store" });
  if (!res.ok) throw new Error("quotes fetch failed");
  const data = await res.json();
  // Normalize legacy string quotes if any slip through.
  const quotes: QuoteItem[] = Array.isArray(data.quotes)
    ? data.quotes.map((q: unknown) => {
        if (typeof q === "string") return { text: q, author: "Unknown" };
        const obj = q as { text?: string; author?: string };
        return {
          text: String(obj.text ?? ""),
          author: String(obj.author ?? "Unknown"),
        };
      }).filter((q: QuoteItem) => q.text.trim().length > 0)
    : [];
  return { ...data, quotes };
}


export interface HealthPayload {
  ok: boolean;
  service: string;
  version: string;
}

/** Product SemVer from the local Python process. */
export async function fetchHealth(): Promise<HealthPayload> {
  const res = await fetch("/api/health", { cache: "no-store" });
  if (!res.ok) throw new Error("health fetch failed");
  return res.json();
}

export interface LicenseStatus {
  required: boolean;
  activated: boolean;
  platform: "mac" | "windows" | "other" | string;
  platform_label: string;
  email?: string;
}

export interface CameraOption {
  index: number;
  label: string;
  name: string;
  width: number;
  height: number;
}

export interface CamerasPayload {
  cameras: CameraOption[];
  selected: number;
  mode: "single" | "hybrid";
  pick_required: boolean;
}

function apiErrorMessage(data: unknown, fallback: string): string {
  if (data && typeof data === "object" && "detail" in data) {
    const detail = (data as { detail: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0];
      if (first && typeof first === "object" && "msg" in first) {
        return String((first as { msg: unknown }).msg);
      }
    }
  }
  return fallback;
}

/** Whether this copy must activate, and whether it already has. */
export async function fetchLicense(): Promise<LicenseStatus> {
  const res = await fetch("/api/license", { cache: "no-store" });
  if (!res.ok) throw new Error("license status failed");
  return res.json();
}

/** First-run: send the emailed key. Needs the internet once. */
export async function activateLicense(key: string): Promise<LicenseStatus> {
  const res = await fetch("/api/license/activate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(
      apiErrorMessage(data, "Could not activate this license key."),
    );
  }
  return data as LicenseStatus;
}

function wsBase(): string {
  // Same host as the page; use the encrypted `wss` only if the page is https.
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}`;
}

/** Last-session avatar, compact mode, window size, and durable toggles. */
export async function fetchPreferences(): Promise<Preferences> {
  const res = await fetch("/api/preferences", { cache: "no-store" });
  if (!res.ok) throw new Error("preferences fetch failed");
  return res.json();
}

/** Merge a few changed settings (e.g. `{ avatar: "lumen" }`) onto disk. */
export async function savePreferences(
  patch: Partial<Preferences>,
): Promise<Preferences> {
  const res = await fetch("/api/preferences", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
    // Survive the window closing immediately after the click.
    keepalive: true,
  });
  if (!res.ok) throw new Error("preferences save failed");
  return res.json();
}

/**
 * Open a live numbers pipe. Calls `onState` every time a snapshot arrives.
 *
 * If the engine restarts, this reconnects automatically (with a growing wait)
 * until the caller unsubscribes by running the returned cleanup function.
 */
export function connectStatsSocket(
  onState: (s: EngineState) => void,
  onStatus?: (connected: boolean) => void,
): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  // First retry is quick; later retries slow down so we do not hammer the CPU.
  let retryMs = 400;

  const connect = () => {
    if (closed) return;
    ws = new WebSocket(`${wsBase()}/ws/stats`);
    ws.onopen = () => {
      retryMs = 400;
      onStatus?.(true);
    };
    ws.onmessage = (ev) => {
      try {
        onState(JSON.parse(ev.data) as EngineState);
      } catch {
        /* ignore bad frames */
      }
    };
    ws.onclose = () => {
      onStatus?.(false);
      if (!closed) {
        window.setTimeout(connect, retryMs);
        retryMs = Math.min(4000, retryMs * 1.5);
      }
    };
    ws.onerror = () => {
      ws?.close();
    };
  };

  connect();

  return () => {
    closed = true;
    ws?.close();
  };
}

export async function fetchSettings(): Promise<LiveSettings> {
  const res = await fetch("/api/settings", { cache: "no-store" });
  if (!res.ok) throw new Error("settings fetch failed");
  return res.json();
}

export async function saveSettings(
  patch: Partial<LiveSettings>,
): Promise<LiveSettings> {
  const res = await fetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error("settings save failed");
  return res.json();
}

export async function fetchAudit(limit = 24): Promise<AuditEntry[]> {
  const res = await fetch(`/api/audit?limit=${limit}`, { cache: "no-store" });
  if (!res.ok) throw new Error("audit fetch failed");
  const data = await res.json();
  return Array.isArray(data.entries) ? data.entries : [];
}

export async function fetchReportIndex(): Promise<{
  today: string;
  days: string[];
  ori_threshold: number;
}> {
  const res = await fetch("/api/reports?kind=index", { cache: "no-store" });
  if (!res.ok) throw new Error("reports index failed");
  return res.json();
}

export async function fetchReport(params: {
  kind: "daily" | "weekly" | "monthly";
  date?: string;
  year?: number;
  month?: number;
}): Promise<ReportPayload> {
  const q = new URLSearchParams({ kind: params.kind });
  if (params.date) q.set("date", params.date);
  if (params.year != null) q.set("year", String(params.year));
  if (params.month != null) q.set("month", String(params.month));
  const res = await fetch(`/api/reports?${q.toString()}`, { cache: "no-store" });
  if (!res.ok) throw new Error("report fetch failed");
  return res.json();
}

/** Turn Beep / Alert / Practice on or off (omit value = flip). On top remains in the engine API. */
export async function setToggle(
  name: ToggleName,
  value?: boolean,
): Promise<Record<string, boolean>> {
  const res = await fetch(`/api/toggles/${name}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value === undefined ? {} : { value }),
  });
  if (!res.ok) {
    throw new Error(`toggle ${name} failed`);
  }
  return res.json();
}

/** Forget the Poise face and its movement heat. The next still look rebuilds it. */
export async function resetFaceMap(): Promise<void> {
  await fetch("/api/face-map/reset", { method: "POST" });
}

/** Tell the engine to start/stop encoding camera JPEGs for the Lens avatar. */
export async function setPreviewEnabled(enabled: boolean): Promise<void> {
  await fetch(`/api/preview/${enabled ? "enable" : "disable"}`, {
    method: "POST",
  });
}

/** Reopen the webcam after the launch splash or idle sleep. */
export async function wakeCamera(): Promise<void> {
  try {
    await fetch("/api/camera/wake", { method: "POST" });
  } catch {
    /* engine still starting */
  }
}

/** List webcams for the startup picker and Settings. */
export async function fetchCameras(): Promise<CamerasPayload> {
  const res = await fetch("/api/cameras", { cache: "no-store" });
  if (!res.ok) throw new Error("camera list failed");
  return res.json();
}

/** Choose a webcam, or hybrid (every desk camera except phones). */
export type CameraChoice = number | "hybrid";

export async function selectCamera(choice: CameraChoice): Promise<void> {
  const body =
    choice === "hybrid"
      ? { hybrid: true }
      : { index: choice, hybrid: false };
  const res = await fetch("/api/camera/select", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(apiErrorMessage(data, "Could not switch to that camera."));
  }
}

/**
 * Ask for a fresh camera still every ~120 ms (Lens avatar).
 *
 * Returns a stop function: turn off encoding and free the last image.
 */
export function startPreviewPolling(
  onFrame: (url: string | null) => void,
  intervalMs = 120,
): () => void {
  let active = true;
  let objectUrl: string | null = null;
  let timer: number | undefined;

  const tick = async () => {
    if (!active) return;
    try {
      const res = await fetch(`/api/preview?t=${Date.now()}`, {
        cache: "no-store",
      });
      if (res.status === 200) {
        const blob = await res.blob();
        // Drop the previous image URL so the browser can free that memory.
        if (objectUrl) URL.revokeObjectURL(objectUrl);
        objectUrl = URL.createObjectURL(blob);
        onFrame(objectUrl);
      }
    } catch {
      /* transient */
    }
    if (active) {
      timer = window.setTimeout(tick, intervalMs);
    }
  };

  void setPreviewEnabled(true).then(tick);

  return () => {
    active = false;
    if (timer !== undefined) window.clearTimeout(timer);
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    void setPreviewEnabled(false);
  };
}
