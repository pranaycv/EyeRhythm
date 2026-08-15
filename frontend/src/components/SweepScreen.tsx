/**
 * Full-screen look-angle sweep.
 *
 * Physical look-targets sit on this screen's corners / edges.
 * The compass is always *your* face direction (camera-relative), so a
 * "top-left" sticker on a monitor to your right does not pretend to be
 * a left look.
 */

import { useCallback, useEffect } from "react";
import { finishSweep, setToggle } from "../api";
import {
  overlayPlace,
  setPracticeFullscreen,
  setSweepOverlays,
  type OverlayPlace,
} from "../bridge";
import type { EngineState } from "../types";
import {
  LOOK_COMPASS,
  LOOK_SLOT_META,
  LOOK_SLOTS,
  type LookSectorValues,
  type LookSlot,
  formatLookDeg,
  isLookSlotHot,
  sweepValuesFromState,
} from "../utils/lookSectors";

const PRIMARY_INSTRUCTION =
  "Look at each corner, each edge, then the centre — on this screen and every other monitor. Turning your face toward a side screen is fine. No need to blink on each point; just look at it for a few seconds until that compass cell turns green. The compass is your face direction (your left), not a side monitor’s left edge.";

const SATELLITE_INSTRUCTION: Record<OverlayPlace, string> = {
  right:
    "This display is to your right — turn your face toward it. Look at each target for a few seconds (no blink needed). That updates your right-side angles, not “left” on this monitor.",
  left:
    "This display is to your left — turn your face toward it. Look at each target for a few seconds (no blink needed). That updates your left-side angles, not “right” on this monitor.",
  above:
    "This display is above the camera screen — look up at it. Hold each target for a few seconds (no blink needed). That updates your top / corner-top angles.",
  below:
    "This display is below the camera screen — look down at it. Hold each target for a few seconds (no blink needed). That updates your bottom / corner-bottom angles.",
  other:
    "Turn toward this screen and look at each target for a few seconds (no blink needed). The compass is your face direction from the camera, not this monitor’s left/right labels.",
};

interface Props {
  state: EngineState;
  /** Extra window on a secondary display — do not spawn more overlays. */
  satellite?: boolean;
}

export function SweepScreen({ state, satellite = false }: Props) {
  const phase = state.sweep_phase ?? "off";
  const active = Boolean(state.sweep) && (phase === "active" || phase === "summary");
  const isActive = phase === "active";
  const isSummary = phase === "summary";
  const values = sweepValuesFromState(state);
  const place = satellite ? overlayPlace() : null;

  useEffect(() => {
    if (satellite) return;
    if (active) {
      void setPracticeFullscreen(true);
      void setSweepOverlays(true);
      return () => {
        void setPracticeFullscreen(false);
        void setSweepOverlays(false);
      };
    }
    void setPracticeFullscreen(false);
    void setSweepOverlays(false);
  }, [active, satellite]);

  const closeSweep = useCallback(() => {
    void setToggle("sweep", false);
  }, []);

  const onFinish = useCallback(() => {
    void finishSweep().catch(() => {
      /* engine may reconnect — overlay stays until a later click */
    });
  }, []);

  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        closeSweep();
      } else if (isActive && (e.key === "Enter" || e.key === "f" || e.key === "F")) {
        e.preventDefault();
        onFinish();
      } else if (isSummary && e.key === "Enter") {
        e.preventDefault();
        closeSweep();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, isActive, isSummary, closeSweep, onFinish]);

  if (!active) return null;

  const dir = state.face_found ? state.face_direction : null;
  const liveAngle =
    state.face_found && state.face_angle_deg != null
      ? Math.round(state.face_angle_deg)
      : null;
  const instruction = satellite
    ? SATELLITE_INSTRUCTION[place ?? "other"]
    : PRIMARY_INSTRUCTION;

  return (
    <div
      className={`practice-screen sweep-screen ${
        satellite ? "sweep-screen--satellite" : ""
      }`}
      role="dialog"
      aria-modal="true"
      aria-label="Look-angle sweep"
    >
      <div className="practice-screen__chrome">
        <div className="practice-screen__brand">
          {satellite
            ? `EyeRhythm Sweep · ${placeLabel(place)}`
            : "EyeRhythm Sweep"}
        </div>
        <div className="practice-screen__meta">
          {isSummary ? (
            <span>Your extreme look angles</span>
          ) : (
            <span>Capturing extremes</span>
          )}
        </div>
        {isActive ? (
          <div className="sweep-screen__actions">
            <button
              type="button"
              className="practice-screen__end"
              onClick={closeSweep}
              title="Cancel sweep (Esc)"
            >
              Cancel
              <kbd>Esc</kbd>
            </button>
            <button
              type="button"
              className="practice-screen__end sweep-screen__finish"
              onClick={onFinish}
              title="Finish capture (Enter)"
            >
              Finish
              <kbd>↵</kbd>
            </button>
          </div>
        ) : (
          <button
            type="button"
            className="practice-screen__end"
            onClick={closeSweep}
            title="Back to dashboard (Esc)"
          >
            Close
            <kbd>Esc</kbd>
          </button>
        )}
      </div>

      <div className="practice-screen__stage sweep-screen__stage">
        {isSummary ? (
          <div className="practice-screen__summary sweep-screen__summary" role="status">
            <h2 className="practice-screen__summary-title">Extreme look angles</h2>
            <p className="practice-screen__summary-sub">
              From your face, not from each monitor’s printed corners
            </p>
            <UserCompass values={values} dir={null} large />
            <p className="practice-screen__summary-footer">
              Close to save these eight limits and return to the dashboard
            </p>
          </div>
        ) : (
          <>
            {LOOK_SLOTS.map((slot) =>
              satellite ? (
                <LookHereMark key={slot} slot={slot} />
              ) : (
                <SectorBadge
                  key={slot}
                  slot={slot}
                  value={values[slot]}
                  hot={isLookSlotHot(dir, slot)}
                />
              ),
            )}

            <div className="sweep-screen__center">
              <p className="sweep-screen__instruction">{instruction}</p>
              <div className="sweep-screen__live" aria-live="polite">
                <span className="sweep-screen__live-angle">
                  {liveAngle != null ? `${liveAngle}°` : "—"}
                </span>
                <span className="sweep-screen__live-dir">
                  {state.face_found
                    ? `your face · ${dir || "straight"}`
                    : "no face — face the camera"}
                </span>
              </div>
              <UserCompass values={values} dir={dir} />
            </div>
          </>
        )}
      </div>

      {isActive ? (
        <div className="practice-screen__hint">
          {satellite
            ? "Hold each target a few seconds · no blink needed · compass = your face"
            : "Hold each point a few seconds · no blink needed · turn your face for other screens"}
        </div>
      ) : null}
    </div>
  );
}

function placeLabel(place: OverlayPlace | null): string {
  if (place === "left") return "screen to your left";
  if (place === "right") return "screen to your right";
  if (place === "above") return "screen above";
  if (place === "below") return "screen below";
  return "other screen";
}

function LookHereMark({ slot }: { slot: LookSlot }) {
  return (
    <div className={`sweep-target sweep-target--${slot}`} aria-hidden>
      <span className="sweep-target__dot" />
      <span className="sweep-target__caption">Look here</span>
    </div>
  );
}

function SectorBadge({
  slot,
  value,
  hot,
}: {
  slot: LookSlot;
  value: number | null | undefined;
  hot: boolean;
}) {
  const captured = value != null && Number.isFinite(value);
  return (
    <div
      className={`sweep-corner sweep-corner--${slot} ${hot ? "is-hot" : ""} ${
        captured ? "is-captured" : ""
      }`}
    >
      <span className="sweep-corner__label">{LOOK_SLOT_META[slot].label}</span>
      <span className="sweep-corner__value">{formatLookDeg(value)}</span>
    </div>
  );
}

function UserCompass({
  values,
  dir,
  large,
}: {
  values: LookSectorValues;
  dir: string | null;
  large?: boolean;
}) {
  return (
    <div
      className={`sweep-hud-compass ${large ? "sweep-hud-compass--large" : ""}`}
      aria-label="Your face directions"
    >
      {LOOK_COMPASS.map((slot) => {
        if (slot === "center") {
          return (
            <div
              key="center"
              className="sweep-hud-compass__cell sweep-hud-compass__cell--center"
              aria-hidden
            >
              you
            </div>
          );
        }
        const captured = values[slot] != null && Number.isFinite(values[slot]);
        const hot = isLookSlotHot(dir, slot);
        return (
          <div
            key={slot}
            className={`sweep-hud-compass__cell ${
              captured ? "is-captured" : "is-pending"
            } ${hot ? "is-hot" : ""}`}
            title={LOOK_SLOT_META[slot].label}
          >
            <span className="sweep-hud-compass__name">
              {LOOK_SLOT_META[slot].short}
            </span>
            <span className="sweep-hud-compass__val">
              {formatLookDeg(values[slot])}
            </span>
          </div>
        );
      })}
    </div>
  );
}
