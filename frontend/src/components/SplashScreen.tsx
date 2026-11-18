/**
 * Launch splash: camera pick (when several webcams), comfort check, notices.
 *
 * Stays up until Start is pressed or 3 minutes elapse, whichever is first.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchCameras, savePreferences, selectCamera, type CameraChoice } from "../api";
import { setSplashLayout, whenBridgeReady } from "../bridge";
import { useAppVersion } from "../hooks/useAppVersion";
import { Logo } from "./Logo";

const SPLASH_LIMIT_MS = 180_000;

export const EYE_COMFORT_OPTIONS = [
  {
    id: "comfortable",
    title: "Comfortable",
    detail: "No dryness, strain, or irritation",
  },
  {
    id: "mild",
    title: "Mild symptoms",
    detail: "Slight dryness or visual fatigue",
  },
  {
    id: "moderate",
    title: "Moderate discomfort",
    detail: "Gritty, burning, or tired eyes",
  },
  {
    id: "marked",
    title: "Marked irritation",
    detail: "Persistent dryness, pain, or light sensitivity",
  },
  {
    id: "decline",
    title: "Prefer not to say",
    detail: "Skip this self-check",
  },
] as const;

export type EyeComfortId = (typeof EYE_COMFORT_OPTIONS)[number]["id"];

/** Two-line session plan for the strip under the avatars. No settings changes. */
export function todayPlan(comfort: string | null | undefined): {
  tone: EyeComfortId | "default";
  text: string;
} {
  switch (comfort) {
    case "comfortable":
      return {
        tone: "comfortable",
        text: "Hold 15–20 blinks/min. Blink fully and let Alert stay in the background.",
      };
    case "mild":
      return {
        tone: "mild",
        text: "Blink fully, not just a flicker. A 2-minute Practice can ease mild strain.",
      };
    case "moderate":
      return {
        tone: "moderate",
        text: "Look away on purpose during work. Try a short Practice before a long stretch of screens.",
      };
    case "marked":
      return {
        tone: "marked",
        text: "Recovery day — blink gently and look away often. See an eye-care professional if pain persists.",
      };
    default:
      return {
        tone: comfort === "decline" ? "decline" : "default",
        text: "Aim for 15–20 blinks/min while working. Blink fully, not just a flicker.",
      };
  }
}

type SplashStep = "loading" | "camera" | "comfort" | "notices";

const NOTICES = [
  {
    title: "On-device and private",
    body: "EyesRhythm stays on this computer. Camera frames and blink metrics are processed here only — nothing is uploaded. There is no account and no license key.",
  },
  {
    title: "A wellness companion, not a medical device",
    body: "EyesRhythm has not been cleared or approved by the FDA or any other regulator. It is not intended to diagnose, treat, cure, or prevent disease. If you have an ocular surface disorder, a change in vision, or persistent discomfort, consult a licensed eye-care professional.",
  },
  {
    title: "Ocular Rhythm Index (ORI)",
    body: "ORI combines blink rate and inter-blink regularity into a 0–100 score of blink cadence during screen use. Reduced blink frequency and incomplete blinking are associated with tear-film instability and digital eye strain. With Alert on, an audible cue can sound if ORI stays below the threshold (default 50) for the duration you set in Settings (default 3 minutes in the last 5), prompting a healthier blink rhythm.",
  },
] as const;

interface Props {
  onDone: (comfort?: EyeComfortId | null) => void;
  /** Camera question only. Comfort check and notices are skipped. */
  cameraOnly?: boolean;
}

function formatRemain(ms: number): string {
  const s = Math.max(0, Math.ceil(ms / 1000));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${r.toString().padStart(2, "0")}`;
}

export function SplashScreen({ onDone, cameraOnly = false }: Props) {
  const version = useAppVersion();
  const [step, setStep] = useState<SplashStep>("loading");
  const [comfort, setComfort] = useState<EyeComfortId | null>(null);
  const [remainingMs, setRemainingMs] = useState(SPLASH_LIMIT_MS);
  const [cameras, setCameras] = useState<
    { index: number; label: string }[]
  >([]);
  const [cameraIndex, setCameraIndex] = useState<CameraChoice | null>(null);
  const [cameraBusy, setCameraBusy] = useState(false);
  const [cameraError, setCameraError] = useState("");

  const doneRef = useRef(false);
  const cameraConfirmedRef = useRef(false);
  const comfortRef = useRef(comfort);
  const onDoneRef = useRef(onDone);
  const stepRef = useRef(step);
  comfortRef.current = comfort;
  onDoneRef.current = onDone;
  stepRef.current = step;

  const confirmCamera = useCallback(async () => {
    if (cameraIndex === null || cameraBusy) return false;
    setCameraBusy(true);
    setCameraError("");
    try {
      await selectCamera(cameraIndex);
      cameraConfirmedRef.current = true;
      return true;
    } catch (err) {
      setCameraError(
        err instanceof Error ? err.message : "Could not open that camera.",
      );
      return false;
    } finally {
      setCameraBusy(false);
    }
  }, [cameraIndex, cameraBusy]);

  const exitQuiet = useCallback(() => {
    if (doneRef.current) return;
    doneRef.current = true;
    void setSplashLayout(false).finally(() => {
      onDoneRef.current(null);
    });
  }, []);

  const finish = useCallback(async () => {
    if (doneRef.current) return;
    if (stepRef.current === "loading") return;
    if (
      !cameraConfirmedRef.current &&
      cameraIndex !== null &&
      cameras.length > 1
    ) {
      const ok = await confirmCamera();
      if (!ok) return;
    }
    doneRef.current = true;
    const chosen = comfortRef.current;
    if (chosen) {
      void savePreferences({ eye_comfort: chosen }).catch(() => {
        /* local save is best-effort */
      });
    }
    void setSplashLayout(false).finally(() => {
      onDoneRef.current(comfortRef.current);
    });
  }, [cameraIndex, cameras.length, confirmCamera]);

  useEffect(() => {
    let cancelled = false;
    void fetchCameras()
      .then((payload) => {
        if (cancelled) return;
        const list = payload.cameras.map((c) => ({
          index: c.index,
          label: c.label,
        }));
        setCameras(list);
        if (payload.mode === "hybrid") {
          setCameraIndex("hybrid");
        } else {
          const selected = list.some((c) => c.index === payload.selected)
            ? payload.selected
            : list[0]?.index ?? null;
          setCameraIndex(selected);
        }
        if (payload.pick_required && list.length > 1) {
          setStep("camera");
        } else if (cameraOnly) {
          exitQuiet();
        } else {
          setStep("comfort");
        }
      })
      .catch(() => {
        if (cancelled) return;
        if (cameraOnly) exitQuiet();
        else setStep("comfort");
      });
    return () => {
      cancelled = true;
    };
  }, [cameraOnly, exitQuiet]);

  useEffect(() => {
    let cancelled = false;
    void whenBridgeReady().then((ready) => {
      if (!cancelled && ready) void setSplashLayout(true);
    });
    return () => {
      cancelled = true;
      void setSplashLayout(false);
    };
  }, []);

  useEffect(() => {
    const started = performance.now();
    const id = window.setInterval(() => {
      const left = Math.max(0, SPLASH_LIMIT_MS - (performance.now() - started));
      setRemainingMs(left);
      if (left <= 0) finish();
    }, 200);
    return () => window.clearInterval(id);
  }, [finish]);

  const advanceFromCamera = useCallback(async () => {
    const ok = await confirmCamera();
    if (!ok) return;
    if (cameraOnly) {
      await finish();
      return;
    }
    setStep("comfort");
  }, [cameraOnly, confirmCamera, finish]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      if (step === "loading") {
        return;
      }
      if (step === "camera") {
        void advanceFromCamera();
      } else if (step === "comfort") {
        if (comfort) setStep("notices");
      } else {
        finish();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [step, finish, comfort, advanceFromCamera]);

  const progress = remainingMs / SPLASH_LIMIT_MS;

  return (
    <div className="splash" role="dialog" aria-modal="true" aria-labelledby="splash-title">
      <div className="splash__panel">
        <div className="splash__bar" aria-hidden>
          <div
            className="splash__bar-fill"
            style={{ transform: `scaleX(${progress})` }}
          />
        </div>

        <header className="splash__head">
          <Logo size={32} />
          <div className="splash__brand">
            <div id="splash-title" className="splash__title">
              EyesRhythm
            </div>
            <div className="splash__tag">
              Blink companion
              {version ? ` · ${version}` : ""}
            </div>
          </div>
          <div className="splash__timer" aria-live="polite">
            {formatRemain(remainingMs)}
          </div>
        </header>

        <div className="splash__body">
          {step === "loading" ? (
            <>
              <h2 className="splash__question">Getting ready</h2>
              <p className="splash__lead">
                Checking the cameras on this computer…
              </p>
            </>
          ) : step === "camera" ? (
            <>
              <h2 className="splash__question">Which camera should EyesRhythm use?</h2>
              <p className="splash__lead">
                Several webcams were found. Pick the one you sit in front of,
                or Hybrid to use every built-in and USB camera together
                (phone cameras are left out).
              </p>
              <div
                className="splash__options"
                role="radiogroup"
                aria-label="Camera"
              >
                {cameras.map((cam) => {
                  const active = cameraIndex === cam.index;
                  return (
                    <button
                      key={cam.index}
                      type="button"
                      role="radio"
                      aria-checked={active}
                      className={`splash__option${active ? " is-active" : ""}`}
                      onClick={() => setCameraIndex(cam.index)}
                    >
                      <span className="splash__option-dot" />
                      <span className="splash__option-text">
                        <span className="splash__option-title">{cam.label}</span>
                      </span>
                    </button>
                  );
                })}
                <button
                  type="button"
                  role="radio"
                  aria-checked={cameraIndex === "hybrid"}
                  className={`splash__option${cameraIndex === "hybrid" ? " is-active" : ""}`}
                  onClick={() => setCameraIndex("hybrid")}
                >
                  <span className="splash__option-dot" />
                  <span className="splash__option-text">
                    <span className="splash__option-title">
                      Hybrid (utilizes CPU extensively)
                    </span>
                    <span className="splash__option-detail">
                      All desk cameras at full frame rate; each frame uses
                      the front-facing camera so a blink is counted once
                    </span>
                  </span>
                </button>
              </div>
              {cameraError ? (
                <p className="splash__error" role="alert">
                  {cameraError}
                </p>
              ) : null}
            </>
          ) : step === "comfort" ? (
            <>
              <h2 className="splash__question">
                How would you describe your ocular comfort right now?
              </h2>
              <p className="splash__lead">
                A brief self-check before you begin. This stays on this device
                and is not a diagnosis.
              </p>
              <div
                className="splash__options"
                role="radiogroup"
                aria-label="Current ocular comfort"
              >
                {EYE_COMFORT_OPTIONS.map((opt) => {
                  const active = comfort === opt.id;
                  return (
                    <button
                      key={opt.id}
                      type="button"
                      role="radio"
                      aria-checked={active}
                      className={`splash__option${active ? " is-active" : ""}`}
                      onClick={() => setComfort(opt.id)}
                    >
                      <span className="splash__option-dot" />
                      <span className="splash__option-text">
                        <span className="splash__option-title">{opt.title}</span>
                        <span className="splash__option-detail">{opt.detail}</span>
                      </span>
                    </button>
                  );
                })}
              </div>
            </>
          ) : (
            <>
              <h2 className="splash__question">Before you start</h2>
              <p className="splash__lead">
                Please read these notes. You can begin now, or EyesRhythm will
                continue automatically.
              </p>
              <ol className="splash__notes">
                {NOTICES.map((note, i) => (
                  <li key={note.title} className="splash__note">
                    <span className="splash__note-n" aria-hidden>
                      {i + 1}
                    </span>
                    <div>
                      <div className="splash__note-title">{note.title}</div>
                      <p className="splash__note-body">{note.body}</p>
                    </div>
                  </li>
                ))}
              </ol>
            </>
          )}
        </div>

        <footer className="splash__foot">
          <span className="splash__hint">
            {step === "loading"
              ? "Looking for cameras"
              : step === "camera"
                ? cameraIndex !== null
                  ? "This choice is remembered for next time"
                  : "Choose a camera to continue"
                : step === "comfort"
                  ? comfort
                    ? "Continues automatically in a few minutes"
                    : "Choose an option to continue"
                  : `Auto-start in ${formatRemain(remainingMs)}`}
          </span>
          {step === "loading" ? (
            <button type="button" className="splash__cta" disabled>
              Continue
            </button>
          ) : step === "camera" ? (
            <button
              type="button"
              className="splash__cta"
              disabled={cameraIndex === null || cameraBusy}
              onClick={() => void advanceFromCamera()}
            >
              {cameraBusy ? "Opening…" : "Continue"}
            </button>
          ) : step === "comfort" ? (
            <button
              type="button"
              className="splash__cta"
              disabled={!comfort}
              onClick={() => setStep("notices")}
            >
              Continue
            </button>
          ) : (
            <button type="button" className="splash__cta" onClick={finish}>
              Start
            </button>
          )}
        </footer>
      </div>
    </div>
  );
}
