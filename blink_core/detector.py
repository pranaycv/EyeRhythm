"""
blink_core.detector — eye-blink detection engine
================================================

Plain-language summary
----------------------
This file is the scientific heart of EyeRhythm. It answers one question
over and over, for every camera frame:

    “Did the person just complete a blink?”

It does **not** open the webcam, draw windows, or play sounds. Those are
handled by ``app/main.py`` and friends. That separation means this same
code works on Windows, macOS, and Linux.

How a blink is detected (big picture)
-------------------------------------
1. **Find the face.** Google’s MediaPipe Face Landmarker looks at the image
   and returns up to 478 points on the face (eyes, nose, mouth, etc.) plus
   “blendshape” scores that say things like “how closed is the left eye?”

2. **Measure eye closure two ways** (redundancy = reliability):
   a) **Blendshapes** ``eyeBlinkLeft`` / ``eyeBlinkRight`` — learned by the
      model; usually best, and fairly robust with glasses.
   b) **EAR (Eye Aspect Ratio)** — a simple geometry formula from research
      papers: open eyes are “tall”, closed eyes are “flat”. Used as fallback
      if blendshapes are missing.

3. **Turn a noisy number into discrete blinks** with a small state machine:
      eyes OPEN → eyes CLOSED → eyes OPEN again  =  **1 blink counted**
   Extra rules stop double-counting, ignore head turns, and adapt thresholds
   to *this person’s* open-eye baseline (not one magic number for everyone).

Extra robustness tricks
-----------------------
* **Frontal-face gate** — if the head is turned a lot, skip counting
  (landmarks get unreliable when you look sideways).
* **Low-light boost** — if the room is dark, gently brighten the image
  before analysis (CLAHE), only when needed.
* **Glasses helper** — if frames are detected on the nose bridge, we make
  the “eyes closed” threshold a bit easier so weak signals still count.

Dependencies (all open source)
------------------------------
MediaPipe, OpenCV, NumPy. Model file: ``models/face_landmarker.task``.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision


# ==========================================================================
# Landmark indices (MediaPipe 478-point face mesh)
# --------------------------------------------------------------------------
# MediaPipe numbers every point on the face mesh. We only need a few groups:
#
# EAR (classic 6 points per eye), in this order:
#   [outer corner, upper1, upper2, inner corner, lower2, lower1]
# Those six points let us measure “how open is the eye?” with geometry.
# ==========================================================================
LEFT_EYE_EAR_IDX = [33, 160, 158, 133, 153, 144]
RIGHT_EYE_EAR_IDX = [362, 385, 387, 263, 373, 380]

# Fuller outline of each eye — used only for hollow red outlines on the Lens
# preview while eyes are closed, not for the blink decision itself.
LEFT_EYE_OUTLINE_IDX = [33, 7, 163, 144, 145, 153, 154, 155, 133,
                        173, 157, 158, 159, 160, 161, 246]
RIGHT_EYE_OUTLINE_IDX = [362, 382, 381, 380, 374, 373, 390, 249, 263,
                         466, 388, 387, 386, 385, 384, 398]

# Upper and lower lip midpoints — used to find the mouth centre for the
# “is the face looking straight at the camera?” check.
MOUTH_UPPER_IDX = 13
MOUTH_LOWER_IDX = 14

# Points along the nose bridge — used when estimating “are glasses present?”
NOSE_BRIDGE_IDX = [6, 168, 197, 195, 5]

# Face / eye axis vs camera (look angle)
# Forehead + chin + outer eye corners define the face plane (more frontal-stable
# than eyes+nose alone, which always tilts the normal downward).
FOREHEAD_IDX = 10
CHIN_IDX = 152
LEFT_EYE_OUTER_IDX = 33
RIGHT_EYE_OUTER_IDX = 263
# Dead-zone (degrees): |yaw| and |pitch| below this → direction "straight".
FACE_STRAIGHT_DEG = 8.0
# Flip yaw sign so left/right are from the *user's* perspective (they look
# left → "left"), not the unmirrored camera image. +1 = image-right; −1 = user-left.
FACE_YAW_SIGN = -1.0


@dataclass
class BlinkConfig:
    """
    Tunable knobs for the blink state machine.

    You normally do **not** need to change these. Defaults are tuned for
    typical webcams and faces. Advanced users / researchers can pass a
    custom ``BlinkConfig(...)`` into ``BlinkDetector``.

    Why “adaptive” thresholds?
    --------------------------
    A fixed “score > 0.5 means closed” fails for many people (different eye
    shapes, makeup, glasses, lighting). Instead we learn each person’s
    *open-eye baseline* over recent frames and place close/open thresholds
    a little above that baseline.
    """

    # How far the blink score must rise above the open-eye baseline
    # before we say “eyes are closing / closed”.
    close_delta: float = 0.18

    # How far the score must fall back toward baseline before we say
    # “eyes are open again”. Smaller than close_delta → hysteresis
    # (prevents flickering open/closed on noise).
    open_delta: float = 0.12

    # Hard limits so adaptive thresholds cannot drift to nonsense values.
    close_min: float = 0.22
    close_max: float = 0.60
    open_min: float = 0.20

    # EAR fallback (only if blendshapes are unavailable):
    # eye counts as closed when EAR drops below this fraction of the
    # open-eye EAR baseline (e.g. 0.78 = 78% of normal open height).
    ear_close_ratio: float = 0.78
    ear_open_ratio: float = 0.85

    # How many recent frames to keep when estimating the open-eye baseline.
    # ~300 frames ≈ 10 seconds at 30 fps.
    baseline_window: int = 300

    # Closures shorter than this (seconds) are treated as landmark noise,
    # not real blinks.
    min_blink_duration_s: float = 0.03

    # Closures longer than this are “eyes deliberately shut” (not a blink
    # flap). We still count once when the eyes reopen (user finished the
    # long close). Kept for documentation / future tuning of behaviour.
    long_closure_s: float = 0.8

    # After counting a blink, ignore new closures for this many seconds
    # (debounce so one physical blink is not counted twice).
    refractory_s: float = 0.08

    # ---- anti false-positive / false-negative knobs -------------------

    # Compare left-eye→mouth distance vs right-eye→mouth distance.
    # If one side is much longer than the other, the head is turned;
    # we refuse to count a blink (landmarks are less trustworthy).
    face_turn_max_ratio: float = 1.25

    # When glasses are detected, subtract this from close_delta so weaker
    # signals can still cross the “closed” line.
    glasses_close_delta_reduction: float = 0.12

    # Average gray brightness below this → apply low-light enhancement.
    low_light_mean: float = 70.0

    # Horizontal edge energy on the nose bridge above this → “glasses on”.
    glasses_edge_threshold: float = 9.0

    # Smooth the glasses yes/no decision over this many recent frames
    # (avoids flickering on every tiny edge change).
    glasses_smooth_window: int = 9

    # Run the glasses Sobel heuristic only every N face frames (reuse last
    # decision in between). 1 = every frame; 5 ≈ once per ~0.3 s at 17 fps.
    glasses_eval_every_n: int = 5


@dataclass
class FrameResult:
    """
    Everything the UI needs to know about **one** processed frame.

    Think of this as a status report the detector hands back to the main loop:

    * Was a face found?
    * Are the eyes currently open or closed?
    * How many blinks so far this session?
    * Did we just count a new blink on *this* frame?
    * What is the recent blinks-per-minute rate?
    * Optional drawing data (eye outlines, all landmarks) for debug mode.
    """

    # True if MediaPipe found at least one face in this frame.
    face_found: bool = False

    # Session blink total (resets only if detector.reset() is called).
    blink_count: int = 0

    # True while the state machine thinks eyes are in the CLOSED state.
    eyes_closed: bool = False

    # Fused eye-closure score from blendshapes, roughly 0 (open) … 1 (closed).
    blink_score: float = 0.0

    # Eye Aspect Ratio for left / right / average (geometry path).
    ear_left: float = 0.0
    ear_right: float = 0.0
    ear_avg: float = 0.0

    # Raw blendshape blink scores for each eye (0…1 style).
    blend_left: float = 0.0
    blend_right: float = 0.0

    # Blinks per minute over the last ~60 seconds (for the big rate number).
    blinks_per_minute: float = 0.0

    # True only on the single frame where a blink was just accepted.
    # The main loop uses this to update daily totals and optional beep.
    blink_just_counted: bool = False

    # Eye outline points as normalized coordinates (0…1), for drawing.
    left_eye_points: list = field(default_factory=list)
    right_eye_points: list = field(default_factory=list)

    # All 478 mesh points (normalized), for optional debug drawing.
    all_points: list = field(default_factory=list)

    # Diagnostic flags (handy for debugging or future UI badges).
    is_frontal: bool = True
    has_glasses: bool = False
    low_light: bool = False

    # Angle between face/eye axis and camera optical axis (degrees).
    # 0 ≈ looking straight into the camera. None when no face.
    face_angle_deg: float | None = None
    face_yaw_deg: float | None = None
    face_pitch_deg: float | None = None
    # "straight" | "left" | "right" | "top" | "bottom" | "right-top" | …
    face_direction: str | None = None


# ==========================================================================
# Small math helpers (no MediaPipe needed)
# ==========================================================================

def _dist(a, b) -> float:
    """
    Straight-line distance between two points that have ``.x`` and ``.y``.

    MediaPipe landmarks are normalized (roughly 0…1 across the image), so
    this distance is in “face-relative” units, not pixels.
    """
    return math.hypot(a.x - b.x, a.y - b.y)


def _percentile(values, pct: float) -> float:
    """
    Approximate a percentile (e.g. 25th) of a small list of numbers.

    Used to estimate the “typical open-eye” score without needing NumPy
    for this tiny calculation. ``pct`` is 0…100.
    """
    data = sorted(values)
    if not data:
        return 0.0
    # Linear interpolation between neighbouring sorted values.
    k = (len(data) - 1) * pct / 100.0
    lo, hi = int(k), min(int(k) + 1, len(data) - 1)
    frac = k - lo
    return data[lo] * (1.0 - frac) + data[hi] * frac


def eye_aspect_ratio(landmarks, idx) -> float:
    """
    Classic Eye Aspect Ratio (EAR) for one eye.

    Formula (Soukupová & Čech, 2016)::

        EAR = (|p2-p6| + |p3-p5|) / (2 * |p1-p4|)

    * Horizontal part (p1–p4) = eye width (corner to corner).
    * Vertical parts = how “tall” the opening is.

    When the eye closes, the vertical distances shrink → EAR drops.
    """
    # Unpack the six landmark indices for this eye into named points.
    p1, p2, p3, p4, p5, p6 = (landmarks[i] for i in idx)
    horizontal = _dist(p1, p4)
    # Guard against division by zero if landmarks collapse (rare).
    if horizontal <= 1e-6:
        return 0.0
    return (_dist(p2, p6) + _dist(p3, p5)) / (2.0 * horizontal)


def _landmark_center(landmarks, idxs):
    """
    Average position of several landmarks → a simple point with ``.x`` / ``.y``.

    Used to find the “centre” of an eye from its outline points.
    """
    n = len(idxs)
    xs = sum(landmarks[i].x for i in idxs) / n
    ys = sum(landmarks[i].y for i in idxs) / n
    # Tiny throwaway object so callers can use the same .x/.y API as landmarks.
    return type("Pt", (), {"x": xs, "y": ys})()


def is_face_frontal(landmarks, max_ratio: float = 1.15) -> bool:
    """
    Rough check: is the person looking mostly straight at the camera?

    Idea
    ----
    Measure left-eye-centre → mouth-centre and right-eye-centre → mouth-centre.
    If those two distances are similar, the face is fairly frontal.
    If one is much larger, the head is turned (yaw) or moving oddly.

    Returns
    -------
    True  – OK to trust a blink candidate
    False – discard candidate blinks this frame
    """
    left_c = _landmark_center(landmarks, LEFT_EYE_EAR_IDX)
    right_c = _landmark_center(landmarks, RIGHT_EYE_EAR_IDX)

    # Mouth centre = midpoint between upper and lower lip landmarks.
    mouth = type("Pt", (), {
        "x": (landmarks[MOUTH_UPPER_IDX].x + landmarks[MOUTH_LOWER_IDX].x) / 2.0,
        "y": (landmarks[MOUTH_UPPER_IDX].y + landmarks[MOUTH_LOWER_IDX].y) / 2.0,
    })()

    dL = _dist(left_c, mouth)
    dR = _dist(right_c, mouth)

    # Degenerate geometry → not frontal.
    if min(dL, dR) < 1e-6:
        return False

    # How much longer is the longer side vs the shorter side?
    ratio = max(dL, dR) / min(dL, dR)
    return ratio <= max_ratio


def estimate_face_camera_pose(
    landmarks,
) -> tuple[float, float, float] | None:
    """
    Angle between the face/eye axis and the camera optical axis.

    Idea
    ----
    Build a 3D face plane from the outer eye corners, forehead, and chin
    (MediaPipe landmarks already carry a relative depth ``z``). The plane
    normal is the direction the face / eyes point. The camera axis in this
    coordinate system is ``(0, 0, −1)`` (toward the camera: smaller ``z``
    is closer to the lens).

    Returns
    -------
    (angle_deg, yaw_deg, pitch_deg) or None if geometry is degenerate.

    * **angle_deg** — 0 means face axis ‖ camera axis (looking straight in).
    * **yaw_deg** — positive → face points to the user's **right**
      (``FACE_YAW_SIGN`` flips camera-image left/right).
    * **pitch_deg** — positive → face points **up** (looking toward top).
    """
    try:
        le = landmarks[LEFT_EYE_OUTER_IDX]
        re = landmarks[RIGHT_EYE_OUTER_IDX]
        forehead = landmarks[FOREHEAD_IDX]
        chin = landmarks[CHIN_IDX]
    except (IndexError, TypeError, KeyError, AttributeError):
        return None

    # Horizontal: left eye → right eye; vertical: forehead → chin.
    v1x = re.x - le.x
    v1y = re.y - le.y
    v1z = re.z - le.z
    v2x = chin.x - forehead.x
    v2y = chin.y - forehead.y
    v2z = chin.z - forehead.z

    # Face normal = v1 × v2 (right-hand rule).
    cx = v1y * v2z - v1z * v2y
    cy = v1z * v2x - v1x * v2z
    cz = v1x * v2y - v1y * v2x
    norm = math.sqrt(cx * cx + cy * cy + cz * cz)
    if norm < 1e-9:
        return None
    inv = 1.0 / norm
    cx, cy, cz = cx * inv, cy * inv, cz * inv

    # Outward normal should point toward the camera (negative z). Flip if not.
    if cz > 0.0:
        cx, cy, cz = -cx, -cy, -cz

    # Camera optical axis in MediaPipe face space: toward camera = (0, 0, -1).
    # angle = arccos( face_forward · camera_axis ) = arccos( -cz )
    dot = max(-1.0, min(1.0, -cz))
    angle_deg = math.degrees(math.acos(dot))

    # Yaw / pitch of the face-forward vector relative to the camera axis.
    # atan2 keeps the full −180…180 range for clear left/right, up/down.
    yaw_deg = FACE_YAW_SIGN * math.degrees(math.atan2(cx, -cz))
    # Image y grows downward → negate cy so positive pitch = looking up / top.
    pitch_deg = math.degrees(math.atan2(-cy, -cz))

    return angle_deg, yaw_deg, pitch_deg


def face_direction_label(
    yaw_deg: float,
    pitch_deg: float,
    straight_deg: float = FACE_STRAIGHT_DEG,
) -> str:
    """
    Map yaw/pitch into a short direction string for the UI.

    Examples: ``straight``, ``left``, ``right``, ``top``, ``bottom``,
    ``right-top``, ``left-bottom``.
    """
    thr = max(0.0, float(straight_deg))
    parts: list[str] = []

    if abs(yaw_deg) >= thr:
        parts.append("right" if yaw_deg > 0.0 else "left")
    if abs(pitch_deg) >= thr:
        # "top" / "bottom" matches combinations like "right-top".
        parts.append("top" if pitch_deg > 0.0 else "bottom")

    if not parts:
        return "straight"
    return "-".join(parts)


def _enhance_low_light(bgr: np.ndarray, mean_thr: float) -> tuple[np.ndarray, bool]:
    """
    If the image is dim, gently boost contrast in the brightness channel.

    Details
    -------
    * Sample brightness on a tiny downscale (cheap) instead of full-frame gray.
    * If bright enough, return the original frame unchanged.
    * If dark, convert BGR → LAB, run CLAHE on the L (lightness) channel,
      then convert back. CLAHE is a standard “local contrast” method that
      helps landmarks survive dim rooms without blowing out bright spots.

    Returns
    -------
    (frame, was_low_light)
        frame may be the original or the enhanced copy.
    """
    # Tiny probe image — mean brightness tracks full-frame closely enough.
    h, w = bgr.shape[:2]
    if w > 80 or h > 60:
        probe = cv2.resize(bgr, (80, 60), interpolation=cv2.INTER_AREA)
    else:
        probe = bgr
    mean_br = float(cv2.cvtColor(probe, cv2.COLOR_BGR2GRAY).mean())

    # Room is bright enough — leave the image alone (faster + more natural).
    if mean_br >= mean_thr:
        return bgr, False

    # Split into Lightness + colour channels; enhance only Lightness.
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    l2 = clahe.apply(l)
    enhanced = cv2.cvtColor(cv2.merge([l2, a, b]), cv2.COLOR_LAB2BGR)
    return enhanced, True


def _glasses_edge_score(bgr: np.ndarray, landmarks) -> float:
    """
    Cheap “are there glasses?” score based on edges on the nose bridge.

    Idea
    ----
    Spectacle frames create strong horizontal lines across the nose.
    Bare skin is smoother. We crop a small rectangle on the bridge,
    blur slightly, run a horizontal edge filter (Sobel-X), and return
    the average edge strength. Higher ⇒ more likely glasses.

    This is intentionally simple — not a full glasses classifier.
    """
    h, w = bgr.shape[:2]

    def px(i):
        """Convert one normalized landmark into integer pixel (x, y)."""
        p = landmarks[i]
        return int(p.x * w), int(p.y * h)

    # Inner corners of the eyes — the bridge sits between them.
    le = px(133)   # left eye inner corner
    re = px(362)   # right eye inner corner
    eye_dist = max(abs(re[0] - le[0]), 1)

    mid_x = (le[0] + re[0]) // 2
    # Bridge is a bit above the line connecting the inner eye corners.
    mid_y = (le[1] + re[1]) // 2 - max(4, int(0.04 * h))

    # Size the region of interest relative to eye spacing (works at different
    # face sizes / distances from the camera).
    roi_w = max(12, int(eye_dist * 0.38))
    roi_h = max(8, int(eye_dist * 0.14))
    x1 = max(0, mid_x - roi_w // 2)
    y1 = max(0, mid_y - roi_h // 2)
    x2 = min(w, mid_x + roi_w // 2)
    y2 = min(h, mid_y + roi_h // 2)

    # ROI too small to be meaningful.
    if x2 - x1 < 6 or y2 - y1 < 4:
        return 0.0

    roi = bgr[y1:y2, x1:x2]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    # Light blur reduces camera noise before we look for edges.
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    # Sobel in X direction = “how much do things change left↔right?”
    # Frame arms create strong horizontal structure → strong response.
    sobelx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    return float(np.mean(np.abs(sobelx)))


class BlinkDetector:
    """
    Feed webcam frames in; get blink counts and status out.

    Minimal usage
    -------------
        det = BlinkDetector("models/face_landmarker.task")
        result = det.process_frame(bgr_frame, timestamp_ms)
        if result.blink_just_counted:
            print("Blink!")
        det.close()

    Important
    ---------
    ``timestamp_ms`` must **strictly increase** every call. MediaPipe’s video
    mode requires that. The main app enforces this even when two frames
    arrive in the same millisecond.
    """

    # Two possible states for the eyes in our simple machine.
    STATE_OPEN = "OPEN"
    STATE_CLOSED = "CLOSED"

    def __init__(self, model_path: str, config: BlinkConfig | None = None):
        """
        Load the MediaPipe face model and reset all counters.

        Parameters
        ----------
        model_path:
            Filesystem path to ``face_landmarker.task``.
        config:
            Optional custom thresholds; defaults are usually fine.
        """
        self.config = config or BlinkConfig()

        # --- create the MediaPipe Face Landmarker -----------------------
        # BaseOptions points at the model file on disk.
        base_options = mp_python.BaseOptions(model_asset_path=model_path)
        options = vision.FaceLandmarkerOptions(
            base_options=base_options,
            # VIDEO mode expects a stream of frames with rising timestamps
            # (as opposed to still IMAGE mode).
            running_mode=vision.RunningMode.VIDEO,
            # We need blendshape scores (eyeBlinkLeft / eyeBlinkRight).
            output_face_blendshapes=True,
            # One face is enough for a personal blink counter.
            num_faces=1,
        )
        self._landmarker = vision.FaceLandmarker.create_from_options(options)

        # --- blink state machine memory ---------------------------------
        self._state = self.STATE_OPEN          # assume eyes start open
        self._blink_count = 0                  # session total
        self._closed_since_ms: float | None = None  # when CLOSED began
        self._last_blink_ms: float = -1e12     # for refractory debounce
        # Timestamps of recent blinks — used to compute blinks/minute.
        self._blink_times_ms: deque[float] = deque(maxlen=300)
        # First frame’s timestamp — so early BPM uses elapsed time, not 60s.
        self._start_ms: float | None = None

        # Rolling history of blink scores / EAR for adaptive baselines.
        self._score_hist: deque[float] = deque(
            maxlen=self.config.baseline_window)
        self._ear_hist: deque[float] = deque(
            maxlen=self.config.baseline_window)

        # Recent glasses edge scores — smoothed so the decision does not flicker.
        self._glasses_votes: deque[float] = deque(
            maxlen=self.config.glasses_smooth_window)
        self._glasses_frame_i = 0
        self._last_has_glasses = False

    # ------------------------------------------------------------------ API

    def reset(self) -> None:
        """
        Zero the session counter and clear adaptive history.

        Does **not** touch the daily totals in ``ScreenTimeTracker`` —
        those are a separate diary.
        """
        self._state = self.STATE_OPEN
        self._blink_count = 0
        self._closed_since_ms = None
        self._last_blink_ms = -1e12
        self._blink_times_ms.clear()
        self._start_ms = None
        self._score_hist.clear()
        self._ear_hist.clear()
        self._glasses_votes.clear()
        self._glasses_frame_i = 0
        self._last_has_glasses = False

    def discard_last_blink(self) -> bool:
        """
        Undo the most recently counted blink.

        Used when the blink happened while looking off-screen (beyond the
        swept workspace). Returns True if a blink was removed.
        """
        if not self._blink_times_ms:
            return False
        self._blink_times_ms.pop()
        self._blink_count = max(0, int(self._blink_count) - 1)
        if self._blink_times_ms:
            self._last_blink_ms = float(self._blink_times_ms[-1])
        else:
            self._last_blink_ms = -1e12
        return True

    def blinks_per_minute_at(self, timestamp_ms: float) -> float:
        """Session blink rate after a possible discard."""
        return self._bpm(timestamp_ms)

    def hold_rate_across_gap(self, gap_ms: float) -> None:
        """
        Treat no-face / off-screen time as if the 60s *rate window* paused.

        Only the BPM history timestamps move. The open/closed state machine
        (``_last_blink_ms``, ``_closed_since_ms``) stays put so a gap cannot
        invent extra refractory time or shrink a blink's duration.
        """
        try:
            shift = float(gap_ms)
        except (TypeError, ValueError):
            return
        if not math.isfinite(shift) or shift <= 0.0:
            return
        shifted: deque[float] = deque(maxlen=self._blink_times_ms.maxlen)
        for t in self._blink_times_ms:
            shifted.append(float(t) + shift)
        self._blink_times_ms = shifted

    def close(self) -> None:
        """Release MediaPipe resources (call on app quit)."""
        self._landmarker.close()

    def process_frame(
        self,
        bgr_frame,
        timestamp_ms: int,
        *,
        include_mesh: bool = False,
    ) -> FrameResult:
        """
        Analyse one OpenCV BGR image and update blink state.

        Parameters
        ----------
        bgr_frame:
            Image from ``cv2.VideoCapture.read()`` (Blue-Green-Red channel order).
        timestamp_ms:
            Monotonic-ish time in milliseconds for this frame. Must be
            greater than the previous call’s value.
        include_mesh:
            If True, fill eye outlines + full mesh for the camera preview.
            Leave False in the normal low-power path (blink logic does not need it).

        Returns
        -------
        FrameResult
            Status for the UI and for daily tracking.
        """
        # Start with last known blink count; we may increment it below.
        result = FrameResult(blink_count=self._blink_count)

        # Remember when the session started (first successful call).
        if self._start_ms is None:
            self._start_ms = timestamp_ms

        cfg = self.config

        # ---- Step 1: maybe brighten a dark frame ----------------------
        frame, was_low = _enhance_low_light(bgr_frame, cfg.low_light_mean)
        result.low_light = was_low

        # ---- Step 2: run MediaPipe face landmarker --------------------
        # OpenCV BGR → RGB (contiguous buffer required by MediaPipe).
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        detection = self._landmarker.detect_for_video(mp_image,
                                                      int(timestamp_ms))

        # ---- Step 3: no face? report empty result and stop ------------
        if not detection.face_landmarks:
            # We keep the state machine as-is (do not force OPEN/CLOSED)
            # so a brief face loss mid-blink does not invent events.
            result.face_found = False
            result.blinks_per_minute = self._bpm(timestamp_ms)
            return result

        # First (and only) face’s landmark list.
        landmarks = detection.face_landmarks[0]
        result.face_found = True

        # ---- Step 3b: face axis vs camera (look angle + direction) ----
        # Computed every face frame; independent of blink gating.
        pose = estimate_face_camera_pose(landmarks)
        if pose is not None:
            angle_deg, yaw_deg, pitch_deg = pose
            result.face_angle_deg = round(angle_deg, 1)
            result.face_yaw_deg = round(yaw_deg, 1)
            result.face_pitch_deg = round(pitch_deg, 1)
            result.face_direction = face_direction_label(yaw_deg, pitch_deg)

        # ---- Step 4: frontal gate (head-turn filter) ------------------
        frontal = is_face_frontal(landmarks, cfg.face_turn_max_ratio)
        result.is_frontal = frontal

        # ---- Step 5: glasses heuristic (throttled — expensive Sobel) ---
        self._glasses_frame_i += 1
        every_n = max(1, int(cfg.glasses_eval_every_n))
        if self._glasses_frame_i % every_n == 1 or every_n == 1:
            edge = _glasses_edge_score(bgr_frame, landmarks)
            self._glasses_votes.append(edge)
            avg_edge = sum(self._glasses_votes) / len(self._glasses_votes)
            self._last_has_glasses = avg_edge >= cfg.glasses_edge_threshold
        has_glasses = self._last_has_glasses
        result.has_glasses = has_glasses

        # ---- Step 6: geometric EAR for both eyes ----------------------
        result.ear_left = eye_aspect_ratio(landmarks, LEFT_EYE_EAR_IDX)
        result.ear_right = eye_aspect_ratio(landmarks, RIGHT_EYE_EAR_IDX)
        result.ear_avg = (result.ear_left + result.ear_right) / 2.0

        # ---- Step 7: blendshape blink scores (preferred signal) -------
        have_blend = bool(detection.face_blendshapes)
        if have_blend:
            # Only pull the two blink channels — skip building a full name map.
            bl = br = 0.0
            for c in detection.face_blendshapes[0]:
                name = c.category_name
                if name == "eyeBlinkLeft":
                    bl = c.score
                elif name == "eyeBlinkRight":
                    br = c.score
            result.blend_left = bl
            result.blend_right = br
            # Require BOTH eyes to show closure (min of the two).
            # That rejects most one-eyed winks and half-noise spikes.
            result.blink_score = min(bl, br)

        # ---- Step 8: adaptive open/closed thresholds ------------------
        # Glasses often weaken the signal → make “closed” easier to reach.
        close_delta = cfg.close_delta
        if has_glasses:
            close_delta = max(0.12, close_delta - cfg.glasses_close_delta_reduction)

        if have_blend:
            # Track recent scores; 25th percentile ≈ typical open-eye level
            # (most of the time eyes are open, so low percentiles sit near open).
            self._score_hist.append(result.blink_score)
            baseline = _percentile(self._score_hist, 25)

            # Closed when score is clearly above baseline; open when back near it.
            close_thr = min(cfg.close_max,
                            max(cfg.close_min, baseline + close_delta))
            open_thr = max(cfg.open_min,
                           min(close_thr - 0.08, baseline + cfg.open_delta))

            is_closed_signal = result.blink_score >= close_thr
            is_open_signal = result.blink_score <= open_thr
        else:
            # Fallback path: use EAR only (lower EAR = more closed).
            self._ear_hist.append(result.ear_avg)
            # 75th percentile of EAR ≈ open-eye height (higher EAR when open).
            ear_base = min(0.45, max(0.18,
                                     _percentile(self._ear_hist, 75)))
            # Slightly looser close ratio when glasses are on.
            close_r = cfg.ear_close_ratio + (0.05 if has_glasses else 0.0)
            open_r = cfg.ear_open_ratio
            is_closed_signal = result.ear_avg <= ear_base * close_r
            is_open_signal = result.ear_avg >= ear_base * open_r

        # ---- Step 9: hysteresis state machine → discrete blinks -------
        # OPEN + closed signal  → enter CLOSED, remember the time
        # CLOSED + open signal  → if duration OK, not too soon after last
        #                         blink, and face frontal → count +1
        counted = False
        if self._state == self.STATE_OPEN:
            if is_closed_signal:
                self._state = self.STATE_CLOSED
                self._closed_since_ms = timestamp_ms
        else:  # currently CLOSED
            if is_open_signal:
                # How long were the eyes closed?
                closure_ms = timestamp_ms - (self._closed_since_ms
                                             or timestamp_ms)
                # How long since the previous counted blink?
                since_last = timestamp_ms - self._last_blink_ms

                if (closure_ms >= cfg.min_blink_duration_s * 1000.0
                        and since_last >= cfg.refractory_s * 1000.0
                        and frontal):  # discard if head was turned
                    self._blink_count += 1
                    self._last_blink_ms = timestamp_ms
                    self._blink_times_ms.append(timestamp_ms)
                    counted = True

                # Always return to OPEN once we saw a clear open signal.
                self._state = self.STATE_OPEN
                self._closed_since_ms = None

        # ---- Step 10: pack results for the UI -------------------------
        result.eyes_closed = self._state == self.STATE_CLOSED
        result.blink_count = self._blink_count
        result.blink_just_counted = counted
        result.blinks_per_minute = self._bpm(timestamp_ms)

        # Mesh packing is only for the Lens preview — skip in the normal path.
        if include_mesh:
            result.left_eye_points = [(landmarks[i].x, landmarks[i].y)
                                      for i in LEFT_EYE_OUTLINE_IDX]
            result.right_eye_points = [(landmarks[i].x, landmarks[i].y)
                                       for i in RIGHT_EYE_OUTLINE_IDX]
            result.all_points = [(p.x, p.y) for p in landmarks]
        return result

    # ------------------------------------------------------------ internals

    def _bpm(self, now_ms: float) -> float:
        """
        Continuous blink rate = number of blinks in the last 60 seconds.

        Always uses a fixed 60s window (not “elapsed so far”). Scaling by a
        short early window used to inflate the first blink to ~30–60 bpm
        (e.g. 1 blink in 2s → 30). With a fixed minute window:

            1 blink  → 1 /min
            15 blinks → 15 /min

        so the rate starts low and rises as real blinks accumulate.
        """
        if not self._blink_times_ms:
            return 0.0

        window_ms = 60_000.0
        recent = sum(1 for t in self._blink_times_ms if now_ms - t <= window_ms)
        # count in last minute is already “blinks per minute”
        return float(recent)
