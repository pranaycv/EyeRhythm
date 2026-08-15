/**
 * Session-wide continuous-rate history for the Trace graph.
 *
 * Lives at module scope so samples keep accumulating for the last 5 minutes
 * even when the user switches away from Trace to another avatar, and even
 * when the window is minimized (uses setInterval, not rAF — rAF freezes
 * when the webview is hidden).
 *
 * Smoothing pipeline (ECG-style glide):
 *   engine bpm → triple EMA → slew limit → stored samples
 * Never snaps to the first non-zero reading (that caused a jump to 30).
 */

export interface RateSample {
  t: number;
  /** Null while no-face / off-screen — a gap in the line, not a drop to 0. */
  bpm: number | null;
  /** Regularity 0–100, or null while warming up / unavailable / away. */
  reg: number | null;
  /** False = ignore this instant (no-face or off-screen). */
  present: boolean;
}

/** Visible + retained time window (seconds). */
export const RATE_WINDOW_S = 300;
/** Dense samples make the scroll feel continuous (~12.5 Hz). */
const SAMPLE_MS = 80;
const HISTORY_PAD_S = 12;
const MAX_SAMPLES =
  Math.ceil(((RATE_WINDOW_S + HISTORY_PAD_S) * 1000) / SAMPLE_MS) + 40;
/**
 * Triple EMA alphas (per ~80ms sample step). Smaller = silkier.
 * Cascade kills the stair-steps from discrete engine updates.
 */
const EMA1 = 0.18;
const EMA2 = 0.22;
const EMA3 = 0.28;
/** Hard limit on how fast the plotted value may change (bpm per second). */
const MAX_SLEW_BPM_PER_S = 5.5;
const Y_CAP = 30;
/** How often UI subscribers are notified (ms). */
const NOTIFY_MS = 50;
/**
 * Max lag to catch up after background throttle / long sleep (ms).
 * Beyond this we just resume from "now" (older window is pruned anyway).
 */
const MAX_CATCHUP_MS = 5000;

function capGraph(bpm: number): number {
  return Math.min(Y_CAP, Math.max(0, bpm));
}

const samples: RateSample[] = [];
let ema1 = 0;
let ema2 = 0;
let ema3 = 0;
let smooth = 0;
let targetBpm = 0;
/** Latest engine regularity (0–100), held until the next reading. */
let targetReg: number | null = null;
/** On-screen (face found and not off-screen). Gaps are not plotted. */
let targetPresent = false;
let lastSampleAt = 0;
let lastNotifyAt = 0;
let running = false;
let intervalId: ReturnType<typeof setInterval> | null = null;
const listeners = new Set<() => void>();

function nowMs(): number {
  // performance.now() keeps advancing while the window is minimized;
  // Date.now() would also work but mixes poorly with existing rAF code.
  return performance.now();
}

function prune(now: number): void {
  const cutoff = now - (RATE_WINDOW_S + HISTORY_PAD_S) * 1000;
  while (samples.length > 0 && samples[0].t < cutoff) samples.shift();
  if (samples.length > MAX_SAMPLES) {
    samples.splice(0, samples.length - MAX_SAMPLES);
  }
}

/** Advance filters by one fixed sample step and push a sample. */
function advanceOneStep(sampleT: number, dtS: number): void {
  if (!targetPresent) {
    // Time still advances; do not glide the rate toward 0.
    samples.push({ t: sampleT, bpm: null, reg: null, present: false });
    lastSampleAt = sampleT;
    return;
  }

  ema1 += (targetBpm - ema1) * EMA1;
  ema2 += (ema1 - ema2) * EMA2;
  ema3 += (ema2 - ema3) * EMA3;

  const maxStep = MAX_SLEW_BPM_PER_S * dtS;
  const delta = ema3 - smooth;
  if (delta > maxStep) smooth += maxStep;
  else if (delta < -maxStep) smooth -= maxStep;
  else smooth = ema3;

  if (smooth < 0.02 && targetBpm < 0.02) smooth = 0;

  samples.push({
    t: sampleT,
    bpm: smooth,
    reg: targetReg,
    present: true,
  });
  lastSampleAt = sampleT;
}

function notify(): void {
  for (const cb of listeners) {
    try {
      cb();
    } catch {
      /* subscriber error must not stop the sampler */
    }
  }
}

/**
 * Interval tick: keep sampling even when the window is minimized.
 * If the timer was throttled, catch up a few steps so the trail stays continuous.
 */
function tick(): void {
  const now = nowMs();

  if (lastSampleAt <= 0) {
    // First sample of the session — start the trail immediately.
    advanceOneStep(now, SAMPLE_MS / 1000);
    prune(now);
    lastNotifyAt = now;
    notify();
    return;
  }

  const lag = now - lastSampleAt;
  if (lag < SAMPLE_MS * 0.5) {
    // Too soon (spurious double-fire); just refresh the head.
    if (samples.length > 0) {
      samples[samples.length - 1] = targetPresent
        ? { t: now, bpm: smooth, reg: targetReg, present: true }
        : { t: now, bpm: null, reg: null, present: false };
    }
    return;
  }

  // Catch up missed steps (background throttle), capped so a long sleep
  // does not burn a huge loop — the 5-minute prune handles the rest.
  const catchUp = Math.min(lag, MAX_CATCHUP_MS);
  const steps = Math.max(1, Math.round(catchUp / SAMPLE_MS));
  const dtS = SAMPLE_MS / 1000;
  // Place samples ending at `now` so the head stays on the right edge.
  const startT = now - (steps - 1) * SAMPLE_MS;
  for (let i = 0; i < steps; i++) {
    advanceOneStep(startT + i * SAMPLE_MS, dtS);
  }
  // Pin the latest sample exactly to now.
  if (samples.length > 0) {
    samples[samples.length - 1] = targetPresent
      ? { t: now, bpm: smooth, reg: targetReg, present: true }
      : { t: now, bpm: null, reg: null, present: false };
    lastSampleAt = now;
  }

  prune(now);

  if (now - lastNotifyAt >= NOTIFY_MS) {
    lastNotifyAt = now;
    notify();
  }
}

/**
 * Feed the latest engine reading. Safe to call every websocket tick.
 * When ``present`` is false (no-face / off-screen) the trail records a
 * gap — time still advances, but that stretch is not plotted and is not
 * a decaying “low rate”.
 */
export function setRateHistoryInput(
  continuousBpm: number,
  present = true,
  regularity: number | null = null,
): void {
  targetPresent = Boolean(present);
  if (targetPresent) {
    targetBpm = capGraph(Math.max(0, continuousBpm));
    targetReg =
      regularity !== null && Number.isFinite(regularity)
        ? Math.min(100, Math.max(0, regularity))
        : null;
  }
  // Ensure the clock is running as soon as the engine feeds us data.
  ensureRateHistoryRunning();
}

/** Start the background sampler (idempotent). Keeps running for the session. */
export function ensureRateHistoryRunning(): void {
  if (running) return;
  running = true;
  lastSampleAt = 0;
  // Seed an immediate zero sample so Trace has a trail from t=0.
  const t0 = nowMs();
  samples.push({ t: t0, bpm: null, reg: null, present: false });
  lastSampleAt = t0;
  // setInterval survives minimize/hide; rAF does not.
  intervalId = setInterval(tick, SAMPLE_MS);
  // Run one tick soon so the first non-zero engine values are not delayed.
  tick();
}

export function getRateSamples(): readonly RateSample[] {
  return samples;
}

export function getRateEma(): number {
  return smooth;
}

/** Latest regularity target held by the sampler (0–100 or null). */
export function getRateReg(): number | null {
  return targetReg;
}

/** Subscribe to sample ticks (for Trace redraw). Returns unsubscribe. */
export function subscribeRateHistory(cb: () => void): () => void {
  listeners.add(cb);
  // Ensure sampling even if only the graph subscribed (e.g. hot reload).
  ensureRateHistoryRunning();
  return () => {
    listeners.delete(cb);
  };
}

export function capRateForGraph(bpm: number): number {
  return capGraph(bpm);
}

/** Test / hot-reload helper — not used in production UI. */
export function _resetRateHistoryForTests(): void {
  if (intervalId !== null) {
    clearInterval(intervalId);
    intervalId = null;
  }
  running = false;
  samples.length = 0;
  ema1 = ema2 = ema3 = smooth = targetBpm = 0;
  targetReg = null;
  targetPresent = false;
  lastSampleAt = lastNotifyAt = 0;
  listeners.clear();
}
