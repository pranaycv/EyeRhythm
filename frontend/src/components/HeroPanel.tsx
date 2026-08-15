import { useEffect, useState } from "react";
import { startPreviewPolling } from "../api";
import type { EngineState } from "../types";
import { eyesLabel, rateTone } from "../utils/format";
import { Avatar, AvatarPicker, type AvatarId } from "./Avatar";
import { RateGraph } from "./RateGraph";

interface Props {
  state: EngineState;
  avatarId: AvatarId;
  onAvatarChange: (id: AvatarId) => void;
}

/**
 * Hero: single avatar stage (fixed height for every mode).
 * - Eyes status pill: centered on the top of the head (hidden for Trace)
 * - Blinks today: top-right (avatar modes only — hidden in Trace)
 * - Picker: Atlas / Lumen / Vesper / Lens / Trace
 * - Rate history is owned at App root (useRateHistory) so Trace always
 *   has the last 5 minutes, even after avatar switches / minimize.
 */
export function HeroPanel({ state, avatarId, onAvatarChange }: Props) {
  const present = state.face_found && !state.off_screen;
  const tone = rateTone(state.continuous_bpm, present);
  const label = eyesLabel(state.face_found, state.eyes_closed, state.off_screen);
  const isLens = avatarId === "lens";
  const isGraph = avatarId === "graph";
  const [frameUrl, setFrameUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!isLens) {
      setFrameUrl(null);
      return;
    }
    return startPreviewPolling(setFrameUrl);
  }, [isLens]);

  const heroMod =
    (isLens ? " hero--lens" : "") + (isGraph ? " hero--graph" : "");

  return (
    <section className={`hero${heroMod}`}>
      {!isGraph && (
        <div className="hero__stat-chip">
          <span className="hero__stat-value">{state.blinks_today}</span>
          <span className="hero__stat-label">blinks today</span>
        </div>
      )}

      <div className="hero__stage-wrap">
        <div className="hero__avatar-stage">
          {isGraph ? (
            <RateGraph
              faceFound={present}
              lastBlinkAt={state.last_blink_at}
            />
          ) : (
            <Avatar
              id={avatarId}
              eyesClosed={state.eyes_closed}
              faceFound={state.face_found}
              blinkScore={state.blink_score}
              cameraFrameUrl={frameUrl}
              cameraOk={state.camera_ok}
              cameraMessage={state.camera_message}
            />
          )}
          {!isGraph && (
            <div className={`hero__status-pill pill tone-${tone}`}>
              <span className="pill__dot" />
              {label}
            </div>
          )}
        </div>
      </div>

      <AvatarPicker value={avatarId} onChange={onAvatarChange} />
    </section>
  );
}
