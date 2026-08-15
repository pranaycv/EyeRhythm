/**
 * Live continuous-rate chart for the “Trace” avatar option.
 *
 * Renders the last 5 minutes from the session-wide rate history store
 * (so switching avatars does not reset the plot).
 * Thin blue stroke; multi-stage smoothing for a continuous glide.
 * Left Y-axis: blink rate 0–30 (marks every 10; red dotted line at 10).
 * Right Y-axis: regularity 0–100 (plotted simultaneously, orange stroke).
 */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  RATE_WINDOW_S,
  capRateForGraph,
  getRateEma,
  getRateReg,
  getRateSamples,
  subscribeRateHistory,
  type RateSample,
} from "../hooks/rateHistory";

const AVG_HALF_S = 2.4;
const DRAW_POINTS = 240;
const GAUSS_RADIUS = 4;
const TENSION = 0.14;
const Y_MAX = 30;
const Y_TICKS = [10, 20, 30] as const;
const Y_REG_MAX = 100;
const Y_REG_TICKS = [25, 50, 75, 100] as const;
const LOW_RATE_LINE = 10;
const PAD = { top: 28, right: 28, bottom: 20, left: 28 };

export interface RateGraphProps {
  faceFound: boolean;
  lastBlinkAt: number;
}

function isLiveSample(s: RateSample | undefined): s is RateSample & { bpm: number } {
  return Boolean(s && s.present && s.bpm !== null && Number.isFinite(s.bpm));
}

function bpmAt(arr: readonly RateSample[], t: number): number | null {
  if (arr.length === 0) return null;
  if (t <= arr[0].t) return isLiveSample(arr[0]) ? arr[0].bpm : null;
  if (t >= arr[arr.length - 1].t) {
    return isLiveSample(arr[arr.length - 1]) ? arr[arr.length - 1].bpm : null;
  }

  let lo = 0;
  let hi = arr.length - 1;
  while (lo + 1 < hi) {
    const mid = (lo + hi) >> 1;
    if (arr[mid].t <= t) lo = mid;
    else hi = mid;
  }
  const a = arr[lo];
  const b = arr[hi];
  // Do not draw across a no-face / off-screen hole.
  if (!isLiveSample(a) || !isLiveSample(b)) return null;
  const span = b.t - a.t;
  if (span <= 0) return b.bpm;
  let u = (t - a.t) / span;
  u = u * u * (3 - 2 * u);
  return a.bpm + (b.bpm - a.bpm) * u;
}

/** Interpolate regularity; returns null when no sample near t has a score. */
function regAt(arr: readonly RateSample[], t: number): number | null {
  if (arr.length === 0) return null;
  if (t <= arr[0].t) return isLiveSample(arr[0]) ? arr[0].reg : null;
  if (t >= arr[arr.length - 1].t) {
    return isLiveSample(arr[arr.length - 1]) ? arr[arr.length - 1].reg : null;
  }

  let lo = 0;
  let hi = arr.length - 1;
  while (lo + 1 < hi) {
    const mid = (lo + hi) >> 1;
    if (arr[mid].t <= t) lo = mid;
    else hi = mid;
  }
  const a = arr[lo];
  const b = arr[hi];
  if (!isLiveSample(a) || !isLiveSample(b)) return null;
  if (a.reg === null && b.reg === null) return null;
  if (a.reg === null) return b.reg;
  if (b.reg === null) return a.reg;
  const span = b.t - a.t;
  if (span <= 0) return b.reg;
  let u = (t - a.t) / span;
  u = u * u * (3 - 2 * u);
  return a.reg + (b.reg - a.reg) * u;
}

function averagedBpm(
  arr: readonly RateSample[],
  t: number,
  halfMs: number,
): number | null {
  if (arr.length === 0) return null;
  // Same gap rule as regularity: do not borrow nearby on-screen values
  // into a no-face / off-screen hole (that made the rate line linger).
  if (bpmAt(arr, t) === null) return null;
  const t0 = t - halfMs;
  const t1 = t + halfMs;
  const sigma = halfMs / 2.2;
  const twoSigma2 = 2 * sigma * sigma;
  let sum = 0;
  let wsum = 0;
  for (const s of arr) {
    if (s.t < t0) continue;
    if (s.t > t1) break;
    if (!isLiveSample(s)) continue;
    const d = s.t - t;
    const w = Math.exp(-(d * d) / twoSigma2);
    sum += s.bpm * w;
    wsum += w;
  }
  if (wsum > 0) return sum / wsum;
  return bpmAt(arr, t);
}

function capRegForGraph(reg: number): number {
  return Math.min(Y_REG_MAX, Math.max(0, reg));
}

function blurYs(ys: number[], radius: number): number[] {
  if (ys.length < 3 || radius < 1) return ys;
  const out = new Array<number>(ys.length);
  const sigma = radius / 1.8;
  const twoSigma2 = 2 * sigma * sigma;
  for (let i = 0; i < ys.length; i++) {
    let sum = 0;
    let wsum = 0;
    const a = Math.max(0, i - radius);
    const b = Math.min(ys.length - 1, i + radius);
    for (let j = a; j <= b; j++) {
      const d = j - i;
      const w = Math.exp(-(d * d) / twoSigma2);
      sum += ys[j] * w;
      wsum += w;
    }
    out[i] = sum / wsum;
  }
  out[out.length - 1] = ys[ys.length - 1];
  if (out.length > 1) {
    out[out.length - 2] =
      ys[ys.length - 2] * 0.4 + out[out.length - 2] * 0.6;
  }
  return out;
}

/** Build a smooth path; falls back to polyline if needed. */
function smoothPath(pts: { x: number; y: number }[]): string {
  if (pts.length === 0) return "";
  if (pts.length === 1) {
    // Degenerate: tiny horizontal tick so a lone point is still visible.
    const p = pts[0];
    return `M ${(p.x - 2).toFixed(2)} ${p.y.toFixed(2)} L ${(p.x + 2).toFixed(2)} ${p.y.toFixed(2)}`;
  }
  if (pts.length === 2) {
    return `M ${pts[0].x.toFixed(2)} ${pts[0].y.toFixed(2)} L ${pts[1].x.toFixed(2)} ${pts[1].y.toFixed(2)}`;
  }

  // Prefer a reliable polyline when the x-span is tiny (early trail).
  const xSpan = pts[pts.length - 1].x - pts[0].x;
  if (xSpan < 4) {
    let d = `M ${pts[0].x.toFixed(2)} ${pts[0].y.toFixed(2)}`;
    for (let i = 1; i < pts.length; i++) {
      d += ` L ${pts[i].x.toFixed(2)} ${pts[i].y.toFixed(2)}`;
    }
    return d;
  }

  const k = TENSION;
  let d = `M ${pts[0].x.toFixed(2)} ${pts[0].y.toFixed(2)}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(0, i - 1)];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[Math.min(pts.length - 1, i + 2)];
    const c1x = p1.x + (p2.x - p0.x) * k;
    const c1y = p1.y + (p2.y - p0.y) * k;
    const c2x = p2.x - (p3.x - p1.x) * k;
    const c2y = p2.y - (p3.y - p1.y) * k;
    d += ` C ${c1x.toFixed(2)} ${c1y.toFixed(2)}, ${c2x.toFixed(2)} ${c2y.toFixed(2)}, ${p2.x.toFixed(2)} ${p2.y.toFixed(2)}`;
  }
  return d;
}

export function RateGraph({ faceFound, lastBlinkAt }: RateGraphProps) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 320, h: 200 });
  const [frame, setFrame] = useState(0);
  const [blinkFlash, setBlinkFlash] = useState(false);
  const prevBlinkAt = useRef(0);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const r = entries[0]?.contentRect;
      if (!r) return;
      const w = Math.max(120, Math.round(r.width));
      const h = Math.max(90, Math.round(r.height));
      setSize((s) => (s.w === w && s.h === h ? s : { w, h }));
    });
    ro.observe(el);
    // Immediate measure (ResizeObserver can lag one frame in webviews).
    const rect = el.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) {
      setSize({
        w: Math.max(120, Math.round(rect.width)),
        h: Math.max(90, Math.round(rect.height)),
      });
    }
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    return subscribeRateHistory(() => {
      setFrame((n) => (n + 1) % 1_000_000);
    });
  }, []);

  useEffect(() => {
    if (!lastBlinkAt || lastBlinkAt === prevBlinkAt.current) return;
    prevBlinkAt.current = lastBlinkAt;
    setBlinkFlash(true);
    const id = window.setTimeout(() => setBlinkFlash(false), 420);
    return () => window.clearTimeout(id);
  }, [lastBlinkAt]);

  const { pathD, regPathD, nowBpm, nowReg } = useMemo(() => {
    void frame;
    const now = performance.now();
    const arr = getRateSamples();
    const halfMs = AVG_HALF_S * 1000;
    const tStart = now - RATE_WINDOW_S * 1000;

    const { w, h } = size;
    const plotW = Math.max(1, w - PAD.left - PAD.right);
    const plotH = Math.max(1, h - PAD.top - PAD.bottom);

    const xOf = (t: number) =>
      PAD.left + ((t - tStart) / (RATE_WINDOW_S * 1000)) * plotW;
    const yOf = (bpm: number) =>
      PAD.top + plotH - (capRateForGraph(bpm) / Y_MAX) * plotH;
    const yOfReg = (reg: number) =>
      PAD.top + plotH - (capRegForGraph(reg) / Y_REG_MAX) * plotH;

    const rateSegs: { x: number; y: number }[][] = [];
    const regSegs: { x: number; y: number }[][] = [];

    if (arr.length > 0) {
      // First sample time (never left of −5m).
      const dataStart = Math.max(tStart, arr[0].t);
      const dataEnd = now;

      // Dense series from first sample → now; holes stay holes.
      const spanMs = Math.max(1, dataEnd - dataStart);
      const n = Math.max(
        8,
        Math.min(DRAW_POINTS, Math.ceil(spanMs / 50) + 2),
      );

      let rateRun: { x: number; y: number }[] = [];
      let regRun: { x: number; y: number }[] = [];
      const flushRate = () => {
        if (rateRun.length > 0) {
          const xs = rateRun.map((p) => p.x);
          const ys = blurYs(
            rateRun.map((p) => p.y),
            GAUSS_RADIUS,
          );
          rateSegs.push(xs.map((x, i) => ({ x, y: ys[i] })));
          rateRun = [];
        }
      };
      const flushReg = () => {
        if (regRun.length > 0) {
          const xs = regRun.map((p) => p.x);
          const ys = blurYs(
            regRun.map((p) => p.y),
            Math.min(GAUSS_RADIUS, 2),
          );
          regSegs.push(xs.map((x, i) => ({ x, y: ys[i] })));
          regRun = [];
        }
      };

      for (let i = 0; i < n; i++) {
        const u = n === 1 ? 1 : i / (n - 1);
        const t = dataStart + u * spanMs;
        const x = Math.max(PAD.left, Math.min(PAD.left + plotW, xOf(t)));
        const at = bpmAt(arr, t);
        const v = at === null ? null : (averagedBpm(arr, t, halfMs) ?? at);
        if (v === null) {
          flushRate();
          flushReg();
          continue;
        }
        rateRun.push({ x, y: yOf(v) });
        const reg = regAt(arr, t);
        if (reg !== null) {
          regRun.push({ x, y: yOfReg(reg) });
        } else {
          flushReg();
        }
      }
      flushRate();
      flushReg();

      // Pin the live head only while on-screen (do not stretch a gap to “now”).
      if (faceFound && rateSegs.length > 0) {
        const last = rateSegs[rateSegs.length - 1];
        if (last.length > 0) {
          last[last.length - 1] = {
            x: PAD.left + plotW,
            y: yOf(getRateEma()),
          };
        }
      }
      if (faceFound && regSegs.length > 0) {
        const liveReg = getRateReg();
        const last = regSegs[regSegs.length - 1];
        if (liveReg !== null && last.length > 0) {
          last[last.length - 1] = {
            x: PAD.left + plotW,
            y: yOfReg(liveReg),
          };
        }
      }
    }

    return {
      pathD: rateSegs.map(smoothPath).filter(Boolean).join(" "),
      regPathD: regSegs.map(smoothPath).filter(Boolean).join(" "),
      nowBpm: getRateEma(),
      nowReg: getRateReg(),
    };
  }, [frame, size, faceFound]);

  const { w, h } = size;
  const plotW = Math.max(1, w - PAD.left - PAD.right);
  const plotH = Math.max(1, h - PAD.top - PAD.bottom);
  const yOf = (bpm: number) =>
    PAD.top + plotH - (capRateForGraph(bpm) / Y_MAX) * plotH;
  const yOfReg = (reg: number) =>
    PAD.top + plotH - (capRegForGraph(reg) / Y_REG_MAX) * plotH;

  const bandTop = yOf(20);
  const bandBot = yOf(15);
  const lowY = yOf(LOW_RATE_LINE);
  const headX = PAD.left + plotW;
  const headY = yOf(nowBpm);
  const regHeadY = nowReg !== null ? yOfReg(nowReg) : null;
  const displayBpm = capRateForGraph(nowBpm);

  return (
    <div
      className="rate-graph"
      ref={wrapRef}
      aria-label="Continuous blink rate and regularity, last 5 minutes"
    >
      <div
        className={`rate-graph__blink-dot ${blinkFlash ? "is-flash" : ""} ${
          faceFound ? "" : "is-idle"
        }`}
        title="Blink indicator"
        aria-hidden
      />

      <div className="rate-graph__legend" aria-hidden>
        <span className="rate-graph__legend-item rate-graph__legend-item--rate">
          Rate
        </span>
        <span className="rate-graph__legend-item rate-graph__legend-item--reg">
          Regularity
        </span>
      </div>

      <div className="rate-graph__live" aria-live="polite">
        <span className="rate-graph__live-value">
          {faceFound ? displayBpm.toFixed(1) : "—"}
        </span>
        <span className="rate-graph__live-unit">/min</span>
      </div>

      <svg
        className="rate-graph__svg"
        width={w}
        height={h}
        viewBox={`0 0 ${w} ${h}`}
        preserveAspectRatio="none"
      >
        <rect
          x={PAD.left}
          y={bandTop}
          width={plotW}
          height={Math.max(0, bandBot - bandTop)}
          className="rate-graph__target-band"
        />

        {Y_TICKS.map((v) => {
          const y = yOf(v);
          return (
            <g key={v}>
              <line
                x1={PAD.left}
                y1={y}
                x2={PAD.left + plotW}
                y2={y}
                className="rate-graph__grid"
              />
              <text
                x={PAD.left - 5}
                y={y + 3}
                textAnchor="end"
                className="rate-graph__axis-label rate-graph__axis-label--rate"
              >
                {v}
              </text>
            </g>
          );
        })}
        <text
          x={PAD.left - 5}
          y={PAD.top + plotH + 3}
          textAnchor="end"
          className="rate-graph__axis-label rate-graph__axis-label--rate"
        >
          0
        </text>

        {/* Right axis: regularity 0–100 */}
        {Y_REG_TICKS.map((v) => {
          const y = yOfReg(v);
          return (
            <text
              key={`reg-${v}`}
              x={PAD.left + plotW + 5}
              y={y + 3}
              textAnchor="start"
              className="rate-graph__axis-label rate-graph__axis-label--reg"
            >
              {v}
            </text>
          );
        })}
        <text
          x={PAD.left + plotW + 5}
          y={PAD.top + plotH + 3}
          textAnchor="start"
          className="rate-graph__axis-label rate-graph__axis-label--reg"
        >
          0
        </text>

        <line
          x1={PAD.left}
          y1={lowY}
          x2={PAD.left + plotW}
          y2={lowY}
          className="rate-graph__low-line"
        />

        <line
          x1={PAD.left}
          y1={PAD.top + plotH}
          x2={PAD.left + plotW}
          y2={PAD.top + plotH}
          className="rate-graph__axis"
        />

        {/* No clipPath — some desktop webviews hide clipped strokes. */}
        {pathD ? (
          <>
            <path
              d={pathD}
              fill="none"
              className="rate-graph__line rate-graph__line--glow"
            />
            <path
              d={pathD}
              fill="none"
              className="rate-graph__line rate-graph__line--rate"
            />
          </>
        ) : null}

        {regPathD ? (
          <path
            d={regPathD}
            fill="none"
            className="rate-graph__line rate-graph__line--regularity"
          />
        ) : null}

        {faceFound ? (
          <circle cx={headX} cy={headY} r={3} className="rate-graph__head" />
        ) : null}
        {faceFound && regHeadY !== null ? (
          <circle
            cx={headX}
            cy={regHeadY}
            r={2.5}
            className="rate-graph__head rate-graph__head--reg"
          />
        ) : null}

        <text
          x={PAD.left}
          y={h - 5}
          className="rate-graph__axis-label"
          textAnchor="start"
        >
          −5m
        </text>
        <text
          x={PAD.left + plotW}
          y={h - 5}
          className="rate-graph__axis-label"
          textAnchor="end"
        >
          now
        </text>
      </svg>

      {/* Only show the prompt before any real trail exists — not on brief face loss. */}
      {!faceFound && getRateSamples().length < 8 && (
        <div className="rate-graph__empty">
          Face the camera to start the trace
        </div>
      )}
    </div>
  );
}
