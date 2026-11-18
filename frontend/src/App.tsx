/**
 * The whole dashboard lives in this one component.
 *
 * Layout (top → bottom):
 *   1. Title bar with a Live / Connecting light
 *   2. Hero: blinking avatar (or live camera / Trace graph)
 *   3. More / Less — hide extra cards in the compact companion window
 *   4. Four metric cards + the Information & Quote strip
 *   5. Beep / Alert / Practice buttons
 *   6. Full-screen Practice overlay when that mode is on
 *
 * Live numbers arrive from the Python engine via a WebSocket
 * (`useEngineState`). Last-session avatar and compact/full layout are
 * restored from local preferences.
 */
import { useCallback, useEffect, useLayoutEffect, useState } from "react";
import {
  fetchCameras,
  fetchLicense,
  fetchPreferences,
  savePreferences,
  setToggle,
  wakeCamera,
} from "./api";
import { useAppVersion } from "./hooks/useAppVersion";
import { LicenseScreen } from "./components/LicenseScreen";
import {
  measureCompactOuterHeight,
  measureMainColumnOuterHeight,
  setCompactLayout,
  setDockLayout,
  whenBridgeReady,
} from "./bridge";
import { type AvatarId } from "./components/Avatar";
import { ControlsBar } from "./components/ControlsBar";
import { DockPanel } from "./components/DockPanel";
import { HeroPanel } from "./components/HeroPanel";
import { Logo } from "./components/Logo";
import { MetricCard } from "./components/MetricCard";
import { InfoQuoteCard } from "./components/InfoQuoteCard";
import { PracticeScreen } from "./components/PracticeScreen";
import { SplashScreen, todayPlan } from "./components/SplashScreen";
import { useEngineState } from "./hooks/useEngineState";
import { useRateHistory } from "./hooks/useRateHistory";
import { lookAngleTone, oriTone } from "./utils/format";

const AVATARS: AvatarId[] = ["atlas", "lumen", "vesper", "lens", "graph", "poise"];

/** Sound-mode cue: at most one play per minute, even if the latch flickers. */
const ALERT_SOUND_GAP_MS = 60_000;
let lastAlertSoundMs = 0;

function playAlertSound() {
  const now = Date.now();
  if (now - lastAlertSoundMs < ALERT_SOUND_GAP_MS) return;
  lastAlertSoundMs = now;
  const audio = new Audio("/alert.mp3");
  audio.currentTime = 0;
  void audio.play().catch(() => {
    /* autoplay / missing file */
  });
}

function asAvatarId(v: unknown): AvatarId {
  // Unknown / corrupted preference → fall back to Atlas (the default face).
  if (typeof v === "string" && (AVATARS as string[]).includes(v)) {
    return v as AvatarId;
  }
  return "atlas";
}

export default function App() {
  // Live blink numbers + whether the local engine is connected.
  const { state, connected } = useEngineState();
  const version = useAppVersion();
  const [avatarId, setAvatarId] = useState<AvatarId>("atlas");
  // Compact = small companion (avatar + buttons). Expanded = full dashboard.
  const [compact, setCompact] = useState(false);
  const [dock, setDock] = useState(false);
  const [prefsReady, setPrefsReady] = useState(false);
  // pending → splash (comfort + notices) | camera (pick only) | app.
  const [launchGate, setLaunchGate] = useState<
    "pending" | "splash" | "camera" | "app"
  >("pending");
  // The license gate is off unless this copy was started with it forced on.
  const [licenseGate, setLicenseGate] = useState<"pending" | "needed" | "ok">(
    "pending",
  );
  const [licensePlatform, setLicensePlatform] = useState("this computer");
  const [cameraHint, setCameraHint] = useState(false);
  const [eyeComfort, setEyeComfort] = useState("");

  // Session-wide Trace history: starts at boot, survives avatar switches + minimize.
  const present = state.face_found && !state.off_screen;
  useRateHistory(
    state.ocular_rhythm_index,
    present,
    present ? state.continuous_bpm : null,
    present && state.regularity_ready ? state.regularity : null,
  );

  useEffect(() => {
    // Wait for the desktop window to inject its resize/fullscreen helpers.
    void whenBridgeReady();
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const license = await fetchLicense();
        if (cancelled) return;
        const needsKey = Boolean(license.required && !license.activated);
        setLicensePlatform(license.platform_label || "this computer");
        setLicenseGate(needsKey ? "needed" : "ok");
      } catch {
        // If the local API is still coming up, do not trap the user.
        if (!cancelled) setLicenseGate("ok");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (
      !state.running ||
      !state.camera_ok ||
      state.camera_message !== "live" ||
      state.face_found
    ) {
      setCameraHint(false);
      return;
    }
    const timer = window.setTimeout(() => setCameraHint(true), 8000);
    return () => window.clearTimeout(timer);
  }, [
    state.running,
    state.camera_ok,
    state.camera_message,
    state.face_found,
  ]);

  // Restore last avatar (and confirm prefs API is up).
  useEffect(() => {
    let cancelled = false;
    let skip = false;
    (async () => {
      try {
        const prefs = await fetchPreferences();
        if (cancelled) return;
        setAvatarId(asAvatarId(prefs.avatar));
        if (typeof prefs.eye_comfort === "string") {
          setEyeComfort(prefs.eye_comfort);
        }
        // Every launch is the default GUI: More, settings panel closed.
        setCompact(false);
        setDock(false);
        skip = Boolean(prefs.skip_startup_screens);
      } catch {
        /* keep default: show the startup screens */
        skip = false;
      }
      if (cancelled) return;
      setPrefsReady(true);
      // Decide from this fetch. A later render can still be holding "off"
      // and would put the comfort and notices screens back up.
      if (!skip) {
        setLaunchGate("splash");
        return;
      }
      let needsCamera = false;
      try {
        const cams = await fetchCameras();
        needsCamera = Boolean(cams.pick_required && cams.cameras.length > 1);
      } catch {
        needsCamera = false;
      }
      if (cancelled) return;
      if (needsCamera) {
        setLaunchGate("camera");
        return;
      }
      setLaunchGate("app");
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const persistAvatar = useCallback((id: AvatarId) => {
    // Update the picture immediately, then remember it for next launch.
    setAvatarId(id);
    void savePreferences({ avatar: id }).catch(() => {
      /* offline / restarting */
    });
  }, []);

  const persistCompact = useCallback((next: boolean) => {
    setCompact(next);
    void savePreferences({ compact: next }).catch(() => {
      /* offline / restarting */
    });
    // Width stays default (or 2× default if docked); only height changes.
    void setCompactLayout(next);
  }, []);

  const persistDock = useCallback((next: boolean) => {
    if (next) {
      setCompact(false);
      setDock(true);
      void savePreferences({ compact: false, dock: true }).catch(() => {
        /* offline / restarting */
      });
      void setDockLayout(true);
      return;
    }
    // Back to the default GUI (More + default width and height, panel closed).
    setDock(false);
    setCompact(false);
    void savePreferences({ compact: false, dock: false }).catch(() => {
      /* offline / restarting */
    });
    void setDockLayout(false);
  }, []);

  // Open the camera once the startup screens are done or were skipped.
  useEffect(() => {
    if (licenseGate !== "ok" || launchGate !== "app") return;
    void wakeCamera();
  }, [licenseGate, launchGate]);

  // Hug compact content, and grow the expanded window so Information &
  // Quote (and the other left-column cards) are not clipped.
  useLayoutEffect(() => {
    if (launchGate !== "app" || licenseGate !== "ok") return;
    let cancelled = false;
    const id = window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        if (cancelled) return;
        if (compact) {
          const height = measureCompactOuterHeight();
          if (height) void setCompactLayout(true, height);
          return;
        }
        const height = measureMainColumnOuterHeight();
        if (!height) return;
        if (dock) void setDockLayout(true, height);
        else void setCompactLayout(false, height);
      });
    });
    return () => {
      cancelled = true;
      window.cancelAnimationFrame(id);
    };
  }, [compact, launchGate, dock, licenseGate]);

  // Keyboard shortcuts (same letters shown on the bottom buttons).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (launchGate !== "app" || licenseGate !== "ok") return;
      const t = e.target as HTMLElement | null;
      // Do not steal keys while someone is typing in a field.
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
      const k = e.key.toLowerCase();
      if (k === "b") void setToggle("beep");
      else if (k === "a") void setToggle("alert");
      else if (k === "p") void setToggle("practice");
      else if (k === "d") persistCompact(!compact);
      else if (k === "w") persistDock(!dock);
      else if (k === "c") {
        persistAvatar(avatarId === "lens" ? "atlas" : "lens");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [avatarId, compact, dock, persistAvatar, persistCompact, persistDock, launchGate, licenseGate]);

  // Colour of each card: good / warn / bad / idle based on the live number.
  // “—” means we do not have a number yet (no face, or still warming up).
  const oriValue =
    state.ocular_rhythm_index !== null
      ? Math.round(state.ocular_rhythm_index)
      : "—";
  const oriSub = !present
    ? "no face"
    : state.ocular_rhythm_index === null
      ? "need a few blinks"
      : state.ocular_rhythm_full
        ? "70% rate · 30% rhythm"
        : "rate only · rhythm warming up";
  const oriCardTone = oriTone(
    state.ocular_rhythm_index,
    present,
    state.ori_threshold,
  );
  const lookTone = lookAngleTone(state.face_angle_deg, state.face_found);

  const lookValue =
    state.face_found && state.face_angle_deg !== null
      ? Math.round(state.face_angle_deg)
      : "—";
  const lookSub = !state.face_found
    ? "no face"
    : state.off_screen
      ? "off-screen"
      : state.face_direction || "straight";
  const plan = todayPlan(eyeComfort);
  const alertMode = state.alert_mode === "sound" ? "sound" : "visual";
  const alertActive = Boolean(state.alert_active);
  const visualAlert = alertActive && alertMode === "visual";

  useEffect(() => {
    if (alertMode !== "sound" || !alertActive) return;
    // Play on latch, then retry every second; playAlertSound enforces 1 min.
    // alert_active can flicker (face / ORI around the line). A 60s interval
    // tied to this effect used to restart and beep again immediately.
    playAlertSound();
    const id = window.setInterval(playAlertSound, 1_000);
    return () => {
      window.clearInterval(id);
    };
  }, [alertMode, alertActive]);

  return (
    <div
      className={`app-shell${compact ? " is-compact" : ""}${dock ? " is-docked" : ""}${
        visualAlert ? " is-alert-glow" : ""
      }`}
      data-prefs-ready={prefsReady ? "1" : "0"}
    >
      {visualAlert ? (
        <div className="alert-glow" aria-hidden />
      ) : null}
      <div className="app-main">
      {/* Brand + camera status light (hidden in compact companion mode). */}
      <header className="topbar">
        <div className="topbar__brand">
          <Logo size={30} alertPulse={visualAlert} />
          <div>
            <div className="topbar__title">EyesRhythm</div>
            <div className="topbar__tag">Blink companion</div>
          </div>
        </div>
        <div className="topbar__status">
          <span
            className={`status-dot ${
              connected &&
              state.running &&
              state.camera_ok &&
              (state.camera_message === "live" || !state.camera_message)
                ? "ok"
                : connected
                  ? "warn"
                  : "bad"
            }`}
          />
          <span className="status-text">
            {!connected
              ? "Connecting…"
              : !state.running
                ? "Starting engine…"
                : state.camera_ok &&
                    (state.camera_message === "live" || !state.camera_message)
                  ? state.face_found
                    ? "Live"
                    : "Live · looking for you"
                  : state.camera_message || "Camera"}
          </span>
        </div>
      </header>

      <main className="dashboard">
        {cameraHint ? (
          <p className="camera-hint" role="status">
            Camera is on but EyesRhythm cannot see your face yet. Tap{" "}
            <strong>Lens</strong> below to check the view, or change the camera
            in Settings (W).
          </p>
        ) : null}
        {/* Avatar / Lens / Trace plus the blinks-today chip. */}
        <HeroPanel
          state={state}
          avatarId={avatarId}
          onAvatarChange={persistAvatar}
        />

        {/* Compact ↔ full dashboard. “D” on the keyboard does the same. */}
        <div className="layout-toggles">
          <button
            type="button"
            className="layout-toggle"
            onClick={() => persistCompact(!compact)}
            title={
              compact
                ? "Show rates, screen time and other cards (D)"
                : "Hide extra cards and shrink the window (D)"
            }
          >
            <span>{compact ? "More" : "Less"}</span>
            <span className="layout-toggle__chevron" aria-hidden>
              {compact ? "▾" : "▴"}
            </span>
          </button>
          <button
            type="button"
            className={`layout-toggle${dock ? " is-on" : ""}`}
            onClick={() => persistDock(!dock)}
            title={
              dock
                ? "Hide Settings and Reports (W)"
                : "Open Settings and Reports beside the companion (W)"
            }
          >
            <span>{dock ? "Hide settings" : "Settings"}</span>
            <span className="layout-toggle__chevron" aria-hidden>
              {dock ? "◂" : "▸"}
            </span>
          </button>
        </div>

        <div
          className={`target-strip target-strip--${plan.tone}`}
          role="note"
          hidden={compact}
        >
          <span className="target-strip__label">Today&apos;s plan</span>
          <span className="target-strip__value">{plan.text}</span>
        </div>

        {/* Four live tiles + the quote card. Hidden while compact. */}
        <section
          className="metrics-grid"
          aria-label="Blink metrics"
          hidden={compact}
        >
          <MetricCard
            title="Ocular Rhythm Index"
            value={oriValue}
            unit={state.ocular_rhythm_index !== null ? "/100" : undefined}
            subtitle={oriSub}
            tone={oriCardTone}
            palette="mint"
          />
          <MetricCard
            title="Screen time"
            value={state.screen_time}
            subtitle="face visible today"
            tone="neutral"
            palette="peach"
          />
          <MetricCard
            title="FPS"
            value={state.fps > 0 ? state.fps.toFixed(0) : "—"}
            subtitle="camera pipeline"
            tone="neutral"
            palette="sky"
          />
          <MetricCard
            title="Look angle"
            value={lookValue}
            unit={
              state.face_found && state.face_angle_deg !== null ? "°" : undefined
            }
            subtitle={lookSub}
            tone={lookTone}
            palette="lavender"
          />
          {/* Always full-width gray card: tips + Practice lines (practice mode overlays coach) */}
          <InfoQuoteCard
            practice={Boolean(state.practice)}
            practicePhase={state.practice_phase ?? "off"}
            countdownS={state.practice_countdown_s ?? 0}
            remainingS={state.practice_remaining_s ?? 0}
            oriThreshold={state.ori_threshold ?? 50}
            alertLowMinutes={state.alert_low_minutes ?? 3}
          />
        </section>
      </main>

      <div className="shell-footer">
        <ControlsBar state={state} />
        <p className="footer-disclaimer">
          <strong>For general wellness use only.</strong> EyesRhythm is not a
          medical device and does not diagnose, treat, cure, or prevent disease —
          full disclaimer in Settings → About.
        </p>
      </div>
      </div>

      {dock ? <DockPanel /> : null}

      {/* Full-screen practice: countdown + ball/string visual coach */}
      <PracticeScreen state={state} />

      {licenseGate === "needed" ? (
        <LicenseScreen
          platformLabel={licensePlatform}
          onActivated={() => setLicenseGate("ok")}
        />
      ) : null}

      {licenseGate === "ok" &&
      (launchGate === "splash" || launchGate === "camera") ? (
        <SplashScreen
          cameraOnly={launchGate === "camera"}
          onDone={(comfort) => {
            if (comfort) setEyeComfort(comfort);
            setLaunchGate("app");
            void wakeCamera();
          }}
        />
      ) : null}

      {licenseGate === "pending" ||
      (licenseGate === "ok" && launchGate === "pending") ? (
        <div className="splash" role="status" aria-live="polite">
          <div className="splash__panel">
            <header className="splash__head">
              <Logo size={32} />
              <div className="splash__brand">
                <div className="splash__title">EyesRhythm</div>
                <div className="splash__tag">
                  {licenseGate === "pending"
                    ? `Checking this copy${version ? ` · ${version}` : "…"}`
                    : "Opening…"}
                </div>
              </div>
            </header>
          </div>
        </div>
      ) : null}
    </div>
  );
}
