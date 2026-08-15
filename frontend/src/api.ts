import type { EngineState, ToggleName } from "./types";

export interface Preferences {
  avatar: string;
  window_width: number;
  window_height: number;
  beep: boolean;
  alert: boolean;
  ontop: boolean;
}

export interface QuoteItem {
  text: string;
  author: string;
}

export interface QuotesPayload {
  count: number;
  interval_seconds: number;
  practice_duration_seconds: number;
  quotes: QuoteItem[];
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


function wsBase(): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}`;
}

export async function fetchPreferences(): Promise<Preferences> {
  const res = await fetch("/api/preferences", { cache: "no-store" });
  if (!res.ok) throw new Error("preferences fetch failed");
  return res.json();
}

export async function savePreferences(
  patch: Partial<Preferences>,
): Promise<Preferences> {
  const res = await fetch("/api/preferences", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error("preferences save failed");
  return res.json();
}

export function connectStatsSocket(
  onState: (s: EngineState) => void,
  onStatus?: (connected: boolean) => void,
): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
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

export async function finishSweep(): Promise<{
  ok: boolean;
  sweep_phase: string;
  sweep_tl_deg: number | null;
  sweep_t_deg: number | null;
  sweep_tr_deg: number | null;
  sweep_l_deg: number | null;
  sweep_r_deg: number | null;
  sweep_bl_deg: number | null;
  sweep_b_deg: number | null;
  sweep_br_deg: number | null;
}> {
  const res = await fetch("/api/sweep/finish", { method: "POST" });
  if (!res.ok) {
    throw new Error("sweep finish failed");
  }
  return res.json();
}

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

export async function setPreviewEnabled(enabled: boolean): Promise<void> {
  await fetch(`/api/preview/${enabled ? "enable" : "disable"}`, {
    method: "POST",
  });
}

/** Poll JPEG preview (simple + reliable across pywebview). */
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
