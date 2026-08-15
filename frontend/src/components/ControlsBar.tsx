import { useEffect, useRef, useState } from "react";
import { setToggle } from "../api";
import type { EngineState, ToggleName } from "../types";

interface Props {
  state: EngineState;
}

export function ControlsBar({ state }: Props) {
  const [busy, setBusy] = useState<ToggleName | null>(null);
  /** Orange flash on Beep when beep-on-blink + a blink is counted. */
  const [beepPulse, setBeepPulse] = useState(false);
  /** Red flash on Alert when low-rate alert emits a beep. */
  const [alertPulse, setAlertPulse] = useState(false);
  /** Orange flash on Practice when a blink is counted during practice. */
  const [practicePulse, setPracticePulse] = useState(false);

  const lastBlinkRef = useRef(0);
  const lastAlertRef = useRef(0);

  // Blink detected → orange on Beep (if on) and Practice (if on).
  useEffect(() => {
    const t = state.last_blink_at ?? 0;
    if (t > 0 && t !== lastBlinkRef.current) {
      lastBlinkRef.current = t;
      const timers: number[] = [];
      if (state.beep) {
        setBeepPulse(true);
        timers.push(window.setTimeout(() => setBeepPulse(false), 320));
      }
      if (state.practice) {
        setPracticePulse(true);
        timers.push(window.setTimeout(() => setPracticePulse(false), 320));
      }
      return () => timers.forEach((id) => window.clearTimeout(id));
    }
  }, [state.last_blink_at, state.beep, state.practice]);

  // Alert beep → red on Alert (if on).
  useEffect(() => {
    const t = state.last_alert_beep_at ?? 0;
    if (t > 0 && t !== lastAlertRef.current) {
      lastAlertRef.current = t;
      if (state.alert) {
        setAlertPulse(true);
        const id = window.setTimeout(() => setAlertPulse(false), 320);
        return () => window.clearTimeout(id);
      }
    }
  }, [state.last_alert_beep_at, state.alert]);

  const flip = async (name: ToggleName) => {
    if (busy) return;
    setBusy(name);
    try {
      await Promise.race([
        setToggle(name),
        new Promise((_, reject) =>
          window.setTimeout(() => reject(new Error("toggle timeout")), 4000),
        ),
      ]);
    } catch {
      /* engine may reconnect */
    } finally {
      setBusy(null);
    }
  };

  return (
    <footer className="controls">
      <ToggleChip
        label="Beep"
        hint="B"
        active={state.beep}
        pulse={beepPulse ? "orange" : null}
        onClick={() => void flip("beep")}
        disabled={busy === "beep"}
        title="Beep on each counted blink"
      />
      <ToggleChip
        label="Alert"
        hint="A"
        active={state.alert}
        pulse={alertPulse ? "red" : null}
        onClick={() => void flip("alert")}
        disabled={busy === "alert"}
        title="Alert when current rate is below ~10/min and that has been true for at least 2 minutes in the last 5 (zeros and off-screen time ignored). Stops as soon as current rate recovers."
      />
      <ToggleChip
        label="Sweep"
        hint="S"
        active={state.sweep}
        onClick={() => void flip("sweep")}
        disabled={busy === "sweep"}
        title="Find your extreme look angles — glance at each screen’s corners, then the centre of every monitor"
      />
      <ToggleChip
        label="Practice"
        hint="P"
        active={state.practice}
        pulse={practicePulse ? "orange" : null}
        onClick={() => void flip("practice")}
        disabled={busy === "practice"}
        title="20s to read instructions, then 2 min of full-screen ball + beeps every 3s. Regularity resets when beeps start."
      />
    </footer>
  );
}

function ToggleChip({
  label,
  hint,
  active,
  pulse,
  onClick,
  disabled,
  title,
}: {
  label: string;
  hint: string;
  active: boolean;
  pulse?: "orange" | "red" | null;
  onClick: () => void;
  disabled?: boolean;
  title?: string;
}) {
  const pulseClass =
    pulse === "orange"
      ? "pulse-orange"
      : pulse === "red"
        ? "pulse-red"
        : "";

  return (
    <button
      type="button"
      className={`toggle-chip ${active ? "active" : ""} ${pulseClass}`}
      onClick={onClick}
      disabled={disabled}
      title={title ?? `${label} (${hint})`}
    >
      <span className="toggle-chip__dot" />
      <span>{label}</span>
      <kbd>{hint}</kbd>
    </button>
  );
}
