/** Eight look-angle sectors: 4 corners + 4 edge midpoints. */

export type LookSlot = "tl" | "t" | "tr" | "l" | "r" | "bl" | "b" | "br";

export const LOOK_SLOTS: LookSlot[] = [
  "tl",
  "t",
  "tr",
  "l",
  "r",
  "bl",
  "b",
  "br",
];

/** 3×3 compass order including a center hole. */
export const LOOK_COMPASS: Array<LookSlot | "center"> = [
  "tl",
  "t",
  "tr",
  "l",
  "center",
  "r",
  "bl",
  "b",
  "br",
];

export const LOOK_SLOT_META: Record<
  LookSlot,
  { label: string; short: string }
> = {
  tl: { label: "Top left", short: "TL" },
  t: { label: "Top", short: "T" },
  tr: { label: "Top right", short: "TR" },
  l: { label: "Left", short: "L" },
  r: { label: "Right", short: "R" },
  bl: { label: "Bottom left", short: "BL" },
  b: { label: "Bottom", short: "B" },
  br: { label: "Bottom right", short: "BR" },
};

export type LookSectorValues = Record<LookSlot, number | null | undefined>;

export function formatLookDeg(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${Math.round(v)}°`;
}

export function hasAnyLookLimit(values: LookSectorValues): boolean {
  return LOOK_SLOTS.some((k) => {
    const v = values[k];
    return v != null && Number.isFinite(v);
  });
}

export function isLookSlotHot(
  direction: string | null | undefined,
  slot: LookSlot,
): boolean {
  if (!direction || direction === "straight") return false;
  const left = direction.includes("left");
  const right = direction.includes("right");
  const top = direction.includes("top");
  const bottom = direction.includes("bottom");
  switch (slot) {
    case "tl":
      return left && top;
    case "t":
      return top && !left && !right;
    case "tr":
      return right && top;
    case "l":
      return left && !top && !bottom;
    case "r":
      return right && !top && !bottom;
    case "bl":
      return left && bottom;
    case "b":
      return bottom && !left && !right;
    case "br":
      return right && bottom;
    default:
      return false;
  }
}

export function sweepValuesFromState(state: {
  sweep_tl_deg: number | null;
  sweep_t_deg: number | null;
  sweep_tr_deg: number | null;
  sweep_l_deg: number | null;
  sweep_r_deg: number | null;
  sweep_bl_deg: number | null;
  sweep_b_deg: number | null;
  sweep_br_deg: number | null;
}): LookSectorValues {
  return {
    tl: state.sweep_tl_deg,
    t: state.sweep_t_deg,
    tr: state.sweep_tr_deg,
    l: state.sweep_l_deg,
    r: state.sweep_r_deg,
    bl: state.sweep_bl_deg,
    b: state.sweep_b_deg,
    br: state.sweep_br_deg,
  };
}

export function lookValuesFromState(state: {
  look_tl_deg: number | null;
  look_t_deg: number | null;
  look_tr_deg: number | null;
  look_l_deg: number | null;
  look_r_deg: number | null;
  look_bl_deg: number | null;
  look_b_deg: number | null;
  look_br_deg: number | null;
}): LookSectorValues {
  return {
    tl: state.look_tl_deg,
    t: state.look_t_deg,
    tr: state.look_tr_deg,
    l: state.look_l_deg,
    r: state.look_r_deg,
    bl: state.look_bl_deg,
    b: state.look_b_deg,
    br: state.look_br_deg,
  };
}
