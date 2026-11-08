/**
 * Local marketing capture — real app components, seeded state.
 * Open /capture.html#trace (etc). Not part of the packaged build.
 */
import React, { useEffect, useMemo, useState } from "react";
import ReactDOM from "react-dom/client";
import { type AvatarId } from "./components/Avatar";
import { ControlsBar } from "./components/ControlsBar";
import { HeroPanel } from "./components/HeroPanel";
import { InfoQuoteCard } from "./components/InfoQuoteCard";
import { Logo } from "./components/Logo";
import { MetricCard } from "./components/MetricCard";
import { todayPlan } from "./components/SplashScreen";
import { ReportChart } from "./components/ReportChart";
import { seedRateHistoryForCapture } from "./hooks/rateHistory";
import type { ReportPayload } from "./api";
import { EMPTY_FACE_MAP, EMPTY_STATE, type EngineState } from "./types";
import { oriTone } from "./utils/format";
import "./styles/global.css";
import "./capture.css";

const SCENES = [
  "trace",
  "avatars",
  "cameras",
  "alerts",
  "fps",
  "reports",
  "poise",
  "dashboard",
] as const;
type Scene = (typeof SCENES)[number];

function sceneFromHash(): Scene {
  const raw = window.location.hash.replace("#", "");
  return (SCENES as readonly string[]).includes(raw) ? (raw as Scene) : "trace";
}

const live: EngineState = {
  ...EMPTY_STATE,
  continuous_bpm: 17.2,
  average_bpm: 16.8,
  ocular_rhythm_index: 78,
  ocular_rhythm_full: true,
  screen_time: "3h 12m",
  screen_time_seconds: 11520,
  fps: 30,
  regularity: 84,
  regularity_ready: true,
  regularity_gaps: 48,
  blinks_today: 2184,
  session_blinks: 412,
  face_found: true,
  camera_ok: true,
  camera_message: "live",
  is_frontal: true,
  face_angle_deg: 8,
  face_direction: "straight",
  beep: true,
  alert: true,
  alert_mode: "visual",
  running: true,
  timestamp: Date.now() / 1000,
};

function seedTrace(): void {
  const points: {
    agoS: number;
    ori: number | null;
    present?: boolean;
    bpm?: number;
    regularity?: number;
  }[] = [];
  for (let ago = 300; ago >= 0; ago -= 0.8) {
    const t = 300 - ago;
    let present = true;
    let ori = 74 + Math.sin(t / 38) * 8 + Math.sin(t / 11) * 3.2;
    if (t > 118 && t < 148) {
      present = false;
      ori = 0;
    } else if (t > 175 && t < 210) {
      ori = 46 + (t - 175) * 0.2;
    } else if (t > 250) {
      ori = 76 + (t - 250) * 0.08;
    }
    points.push({
      agoS: ago,
      ori: present ? Math.max(38, Math.min(92, ori)) : null,
      present,
      bpm: present ? 14 + (ori - 50) * 0.12 : undefined,
      regularity: present ? 70 + (ori - 50) * 0.35 : undefined,
    });
  }
  seedRateHistoryForCapture(points);
}

function dailyReport(): ReportPayload {
  const points = Array.from({ length: 288 }, (_, i) => {
    const hour = i / 12;
    const working = hour >= 9 && hour <= 18.5;
    const ori = working
      ? Math.round(
          72 +
            Math.sin(hour / 2.4) * 8 +
            (hour > 13 && hour < 14.5 ? -14 : 0) +
            (hour > 16.5 ? -6 : 0),
        )
      : null;
    return {
      i,
      t: `${String(Math.floor(hour)).padStart(2, "0")}:${String((i % 12) * 5).padStart(2, "0")}`,
      ori,
      screen_s: ori == null ? 0 : 300,
      blinks: ori == null ? 0 : 12,
    };
  });
  const livePts = points.filter((p) => p.ori != null);
  const mean =
    livePts.reduce((s, p) => s + (p.ori as number), 0) / Math.max(1, livePts.length);
  return {
    kind: "daily",
    ori_threshold: 50,
    date: "2026-09-16",
    bin_minutes: 5,
    points,
    summary: {
      mean_ori: mean,
      screen_s: livePts.length * 300,
      blinks: livePts.length * 12,
      days_with_data: 1,
    },
  };
}

function CaptureFrame({
  title,
  wide,
  children,
}: {
  title: string;
  wide?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={`capture-win${wide ? " capture-win--wide" : ""}`} data-capture-frame>
      <div className="capture-bar">
        <span className="capture-bar__dots">
          <i></i>
          <i></i>
          <i></i>
        </span>
        <span className="capture-bar__title">{title}</span>
      </div>
      <div className="capture-body">{children}</div>
    </div>
  );
}

function LiveTopbar({ alertPulse = false }: { alertPulse?: boolean }) {
  return (
    <header className="topbar">
      <div className="topbar__brand">
        <Logo size={30} alertPulse={alertPulse} />
        <div>
          <div className="topbar__title">EyesRhythm</div>
          <div className="topbar__tag">Blink companion</div>
        </div>
      </div>
      <div className="topbar__status">
        <span className="status-dot ok" />
        <span className="status-text">Live</span>
      </div>
    </header>
  );
}

function TraceScene() {
  const [avatar, setAvatar] = useState<AvatarId>("graph");
  useEffect(() => {
    seedTrace();
  }, []);
  return (
    <CaptureFrame title="Trace">
      <div className="app-shell">
        <LiveTopbar />
        <HeroPanel state={live} avatarId={avatar} onAvatarChange={setAvatar} />
      </div>
    </CaptureFrame>
  );
}

function AvatarsScene() {
  const [avatar, setAvatar] = useState<AvatarId>("lumen");
  return (
    <CaptureFrame title="Avatars">
      <div className="app-shell">
        <LiveTopbar />
        <HeroPanel state={live} avatarId={avatar} onAvatarChange={setAvatar} />
      </div>
    </CaptureFrame>
  );
}

function CamerasScene() {
  return (
    <CaptureFrame title="Settings">
      <div className="app-shell capture-camera">
        <LiveTopbar />
        <div className="settings">
          <label className="settings__field">
            <div className="settings__row">
              <span className="settings__name">Camera</span>
            </div>
            <select className="settings__select" defaultValue="hybrid">
              <option value="hybrid">Hybrid (utilizes CPU extensively)</option>
              <option>FaceTime HD Camera</option>
              <option>USB Capture HDMI</option>
            </select>
            <p className="settings__help">
              One webcam, or Hybrid to use every built-in and USB camera at
              once (not a phone). Hybrid keeps full frame rate by running
              every camera together. Each tick grabs all cameras at the same
              instant, then counts blinks on the front-facing view.
            </p>
          </label>
        </div>
      </div>
    </CaptureFrame>
  );
}

function AlertsScene() {
  const alertState: EngineState = {
    ...live,
    alert_active: true,
    alert_mode: "visual",
    beep: true,
  };
  return (
    <CaptureFrame title="EyesRhythm">
      <div className="app-shell is-alert-glow">
        <div className="alert-glow" aria-hidden />
        <LiveTopbar alertPulse />
        <HeroPanel
          state={alertState}
          avatarId="atlas"
          onAvatarChange={() => undefined}
        />
        <ControlsBar state={alertState} />
      </div>
    </CaptureFrame>
  );
}

function FpsScene() {
  return (
    <CaptureFrame title="EyesRhythm">
      <div className="app-shell capture-fps">
        <LiveTopbar />
        <section className="metrics-grid" aria-label="Blink metrics">
          <MetricCard
            title="Ocular Rhythm Index"
            value={78}
            unit="/100"
            subtitle="70% rate · 30% rhythm"
            tone={oriTone(78, true, 50)}
            palette="mint"
          />
          <MetricCard
            title="Screen time"
            value="3h 12m"
            subtitle="face visible today"
            tone="neutral"
            palette="peach"
          />
          <MetricCard
            title="FPS"
            value="30"
            subtitle="face in view · auto"
            tone="neutral"
            palette="sky"
          />
          <MetricCard
            title="Look angle"
            value={8}
            unit="°"
            subtitle="straight"
            tone="good"
            palette="lavender"
          />
        </section>
        <p className="capture-fps__note">
          <strong>30 fps</strong> while you are in view. A short rest after
          each blink. <strong>1 fps</strong> when you leave the desk.
        </p>
      </div>
    </CaptureFrame>
  );
}

/** Fractions of a full day, one per 30° direction, starting at the person's right. */
const POISE_DAY = [0.3, 0.22, 0.08, 0.05, 0.02, 0.04, 0.12, 0.03, 0, 0.01, 0, 0.14];

function packDots(fractions: number[]): string {
  const bytes = new Uint8Array(fractions.length * 2);
  fractions.forEach((fraction, i) => {
    const value = Math.round(Math.max(0, Math.min(1, fraction)) * 65535);
    bytes[i * 2] = value & 255;
    bytes[i * 2 + 1] = (value >> 8) & 255;
  });
  let text = "";
  for (const byte of bytes) text += String.fromCharCode(byte);
  return btoa(text);
}

const POISE_DOTS = packDots(POISE_DAY);

function PoiseScene() {
  const [avatar, setAvatar] = useState<AvatarId>("poise");
  const [mesh, setMesh] = useState("");
  useEffect(() => {
    // Optional local wireframe for this capture. Absent in the repo.
    fetch("/poise-capture-mesh.txt")
      .then((response) => (response.ok ? response.text() : ""))
      .then((text) => setMesh(text.trim()))
      .catch(() => setMesh(""));
  }, []);
  const state: EngineState = {
    ...live,
    face_yaw_deg: 28,
    face_pitch_deg: -6,
    face_direction: "right",
    face_map: {
      ...EMPTY_FACE_MAP,
      ready: mesh.length > 0,
      samples: mesh ? 1 : 0,
      lr: 28,
      up: 0,
      down: 6,
      mesh,
      dots: POISE_DOTS,
    },
  };
  return (
    <CaptureFrame title="Poise">
      <div className="app-shell">
        <LiveTopbar />
        <HeroPanel state={state} avatarId={avatar} onAvatarChange={setAvatar} />
      </div>
    </CaptureFrame>
  );
}

function DashboardScene() {
  const [avatar, setAvatar] = useState<AvatarId>("atlas");
  const plan = todayPlan("comfortable");
  return (
    <div className="capture-app" data-capture-frame>
      <div className="app-shell">
        <header className="topbar">
          <div className="topbar__brand">
            <Logo size={30} />
            <div>
              <div className="topbar__title">EyesRhythm</div>
              <div className="topbar__tag">Blink companion</div>
            </div>
          </div>
          <div className="topbar__status">
            <span className="status-dot ok" />
            <span className="status-text">Live</span>
          </div>
        </header>
        <main className="dashboard">
          <HeroPanel
            state={live}
            avatarId={avatar}
            onAvatarChange={setAvatar}
          />
          <div className="layout-toggles">
            <button type="button" className="layout-toggle">
              <span>Less</span>
              <span className="layout-toggle__chevron" aria-hidden>
                ▴
              </span>
            </button>
            <button type="button" className="layout-toggle">
              <span>Settings</span>
              <span className="layout-toggle__chevron" aria-hidden>
                ▸
              </span>
            </button>
          </div>
          <div className={`target-strip target-strip--${plan.tone}`} role="note">
            <span className="target-strip__label">Today&apos;s plan</span>
            <span className="target-strip__value">{plan.text}</span>
          </div>
          <section className="metrics-grid" aria-label="Blink metrics">
            <MetricCard
              title="Ocular Rhythm Index"
              value={78}
              unit="/100"
              subtitle="70% rate · 30% rhythm"
              tone={oriTone(78, true, 50)}
              palette="mint"
            />
            <MetricCard
              title="Screen time"
              value="3h 12m"
              subtitle="face visible today"
              tone="neutral"
              palette="peach"
            />
            <MetricCard
              title="FPS"
              value="30"
              subtitle="face in view · auto"
              tone="neutral"
              palette="sky"
            />
            <MetricCard
              title="Look angle"
              value={8}
              unit="°"
              subtitle="straight"
              tone="good"
              palette="lavender"
            />
            <InfoQuoteCard
              practice={false}
              practicePhase="off"
              countdownS={0}
              remainingS={0}
              oriThreshold={50}
              alertLowMinutes={3}
            />
          </section>
        </main>
        <div className="shell-footer">
          <ControlsBar state={live} />
          <p className="footer-disclaimer">
            <strong>For general wellness use only.</strong> EyesRhythm is not a
            medical device and does not diagnose, treat, cure, or prevent disease —
            full disclaimer in Settings → About.
          </p>
        </div>
      </div>
    </div>
  );
}

function ReportsScene() {
  const report = useMemo(() => dailyReport(), []);
  return (
    <CaptureFrame title="Reports" wide>
      <div className="app-shell capture-reports">
        <div className="reports">
          <div className="reports__kinds" role="tablist" aria-label="Report period">
            <button type="button" className="reports__kind is-active">
              Daily
            </button>
            <button type="button" className="reports__kind">
              Weekly
            </button>
            <button type="button" className="reports__kind">
              Monthly
            </button>
          </div>
          <section className="reports__section">
            <h3 className="reports__section-title">Ocular rhythm</h3>
            <p className="reports__caption">
              5-minute mean ORI across 24 hours · Green dotted line is the
              trained-eye target (75)
            </p>
            <ReportChart report={report} metric="ori" />
          </section>
          <section className="reports__section">
            <h3 className="reports__section-title">Screen time</h3>
            <p className="reports__caption">
              Minutes looking at the screen in each 5-minute slot
            </p>
            <ReportChart report={report} metric="screen" />
          </section>
        </div>
      </div>
    </CaptureFrame>
  );
}

function CaptureApp() {
  const [scene, setScene] = useState<Scene>(sceneFromHash);

  useEffect(() => {
    const onHash = () => setScene(sceneFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  return (
    <div className="capture-page">
      <nav className="capture-nav" hidden>
        {SCENES.map((id) => (
          <a key={id} href={`#${id}`} className={scene === id ? "is-on" : ""}>
            {id}
          </a>
        ))}
      </nav>
      {scene === "trace" && <TraceScene />}
      {scene === "avatars" && <AvatarsScene />}
      {scene === "cameras" && <CamerasScene />}
      {scene === "alerts" && <AlertsScene />}
      {scene === "fps" && <FpsScene />}
      {scene === "reports" && <ReportsScene />}
      {scene === "poise" && <PoiseScene />}
      {scene === "dashboard" && <DashboardScene />}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <CaptureApp />,
);
