/**
 * Optional bridge to the pywebview desktop shell.
 * When running in a browser, these calls are no-ops.
 */

export interface EyeRhythmBridge {
  set_camera_layout: (expanded: boolean) => Promise<void> | void;
  /** Expand to fullscreen for Practice; restore on exit. */
  set_practice_fullscreen?: (enabled: boolean) => Promise<void> | void;
  /** Fullscreen sweep dialog on every attached display. */
  set_sweep_overlays?: (enabled: boolean) => Promise<void> | void;
}

declare global {
  interface Window {
    pywebview?: {
      api?: EyeRhythmBridge;
    };
  }
}

export function isDesktopShell(): boolean {
  return Boolean(window.pywebview?.api);
}

/** Grow / shrink the native window to fit the camera panel. */
export async function setCameraLayout(expanded: boolean): Promise<void> {
  const api = window.pywebview?.api;
  if (!api?.set_camera_layout) return;
  try {
    await api.set_camera_layout(expanded);
  } catch {
    /* browser / unsupported */
  }
}

/**
 * Full-screen the native window for Practice mode.
 * Falls back to the Fullscreen API in browser mode.
 */
export async function setPracticeFullscreen(enabled: boolean): Promise<void> {
  const api = window.pywebview?.api;
  if (api?.set_practice_fullscreen) {
    try {
      await api.set_practice_fullscreen(enabled);
      return;
    } catch {
      /* try browser API below */
    }
  }

  try {
    if (enabled) {
      if (!document.fullscreenElement) {
        await document.documentElement.requestFullscreen?.();
      }
    } else if (document.fullscreenElement) {
      await document.exitFullscreen?.();
    }
  } catch {
    /* permission / unsupported */
  }
}

/**
 * Open / close extra fullscreen sweep windows on secondary displays.
 * No-op in browser mode (only the current tab can go fullscreen).
 */
export async function setSweepOverlays(enabled: boolean): Promise<void> {
  const api = window.pywebview?.api;
  if (!api?.set_sweep_overlays) return;
  try {
    await api.set_sweep_overlays(enabled);
  } catch {
    /* unsupported / already closed */
  }
}

export function isSweepOverlayWindow(): boolean {
  try {
    return new URLSearchParams(window.location.search).get("sweepOverlay") === "1";
  } catch {
    return false;
  }
}

/** Where this overlay sits relative to the camera / main window. */
export type OverlayPlace = "left" | "right" | "above" | "below" | "other";

export function overlayPlace(): OverlayPlace {
  try {
    const raw = new URLSearchParams(window.location.search).get("place");
    if (raw === "left" || raw === "right" || raw === "above" || raw === "below") {
      return raw;
    }
  } catch {
    /* ignore */
  }
  return "other";
}

/** Wait until pywebview has injected the API (desktop only). */
export function whenBridgeReady(timeoutMs = 3000): Promise<boolean> {
  if (window.pywebview?.api) return Promise.resolve(true);
  return new Promise((resolve) => {
    const onReady = () => {
      window.clearTimeout(timer);
      resolve(Boolean(window.pywebview?.api));
    };
    const timer = window.setTimeout(() => {
      window.removeEventListener("pywebviewready", onReady);
      resolve(Boolean(window.pywebview?.api));
    }, timeoutMs);
    window.addEventListener("pywebviewready", onReady, { once: true });
  });
}
