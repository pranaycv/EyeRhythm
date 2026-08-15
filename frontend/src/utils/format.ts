/** Colour band for continuous blink rate (healthy ~15–20). */
export function rateTone(bpm: number, faceFound: boolean): "good" | "warn" | "bad" | "idle" {
  if (!faceFound) return "idle";
  if (bpm >= 12 && bpm <= 25) return "good";
  if (bpm >= 8) return "warn";
  return "bad";
}

export function regularityTone(score: number | null): "good" | "warn" | "bad" | "idle" {
  if (score === null) return "idle";
  if (score >= 70) return "good";
  if (score >= 40) return "warn";
  return "bad";
}

/** Colour band for face–camera look angle (0° = looking straight in). */
export function lookAngleTone(
  angleDeg: number | null,
  faceFound: boolean,
  offScreen = false,
): "good" | "warn" | "bad" | "idle" {
  if (!faceFound || angleDeg === null || !Number.isFinite(angleDeg)) return "idle";
  if (offScreen) return "bad";
  if (angleDeg <= 12) return "good";
  if (angleDeg <= 25) return "warn";
  return "bad";
}

export function formatBpm(n: number): string {
  if (!Number.isFinite(n)) return "—";
  return n < 10 ? n.toFixed(1) : Math.round(n).toString();
}

export function eyesLabel(
  face: boolean,
  closed: boolean,
  offScreen = false,
): string {
  if (!face) return "No face";
  if (offScreen) return "Off-screen";
  return closed ? "Eyes closed" : "Eyes open";
}
