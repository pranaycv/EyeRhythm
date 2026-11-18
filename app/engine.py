"""
EyesRhythm processing engine (no UI)
===================================

Owns the camera loop, blink detector, daily stats, beeps, and low-ORI alerts.
Exposes a thread-safe snapshot of live metrics for the React UI via the API.

Business logic matches the previous OpenCV-based main loop:
  camera → BlinkDetector → ScreenTimeTracker → optional Beeper / AlertMonitor
    + reconnect after sleep/wake / stream loss.
  After 1 min with no face the webcam is released (battery); a 2 min peek
  needs 30 s of face before full live mode resumes.

The GUI is intentionally not here — only numbers, flags, and optional JPEG frames.
"""

from __future__ import annotations

import base64
import json
import math
import platform
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date as date_cls
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from blink_core import BlinkDetector, FrameResult
from blink_core.face_motion import FaceMotionTracker, empty_face_map
from blink_core.screentime import ScreenTimeTracker
from blink_core.interblink_hours import InterBlinkHourTracker
from app.beeper import Beeper
from app import audit
from app import preferences
from app.eye_score import ocular_rhythm_index
from app.ori_log import OriHistory
from app.regularity import RegularityTracker
# Blink-gated CPU pacing (30 fps + post-blink rest / 1 fps no-face).
# Easy rollback: remove this import and restore the previous FACE_FPS=17 /
# NO_FACE_FPS=3 constants + inline interval logic in the camera loop.
from app.camera_idle import CameraIdlePolicy, SLEEP_BETWEEN_PEEKS_S
from app.frame_pacer import FACE_FPS, FramePacer, NO_FACE_FPS
from app.hybrid_camera import (
    CAMERA_MODE_HYBRID,
    FusedBlinkRate,
    any_on_screen_face,
    grab_all,
    hybrid_decode_indices,
    hybrid_should_count_blink,
    is_front_facing,
    missing_camera_indices,
    next_hybrid_fill_wait,
    pick_front_facing_index,
    retrieve_frame,
    should_drop_silent_hybrid_camera,
)

# ---------------------------------------------------------------------------
# Resource root (source run + PyInstaller)
# ---------------------------------------------------------------------------


def resource_root() -> Path:
    """
    Folder that contains `models/` and `assets/`.

    From source that is the project root. Inside a packaged .app / .exe,
    PyInstaller unpacks those files into a temp folder (`sys._MEIPASS`).
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[1]


ROOT = resource_root()
MODEL_PATH = str(ROOT / "models" / "face_landmarker.task")

# ORI alert: beep after enough time below this; stop at/above it.
ALERT_ORI_THRESHOLD = 50.0
ALERT_POLL_S = 0.5
# Once a Sound-mode cue has played, wait this long before the next one while
# ORI is still below the threshold. Latch flicker must not restart the timer.
ALERT_BEEP_INTERVAL_S = 60.0
# Visual center icon: about 3 shows per minute while the low-ORI latch holds.
# Separate from the sound metronome so a faster icon does not beep more often.
# Shows land on the in-app orange peak (see ALERT_ORANGE_PULSE_S), so the
# gap is the first orange peak that is at least this many seconds later.
ALERT_VISUAL_CUE_INTERVAL_S = 20.0
# In-app glow and logo pulse (frontend alert-glow-pulse / er-logo-alert-pulse).
# The center icon is shown on this cycle's orange peak, not while the logo is blue.
ALERT_ORANGE_PULSE_S = 3.0
# Halfway through the cycle the app is orange. A shorter latch never gets there.
ALERT_VISUAL_CUE_HOLD_S = ALERT_ORANGE_PULSE_S / 2.0
# After a fresh start (or face return after a long break), rate (and therefore
# ORI) is still filling its 60 s window and would look artificially low.
# Suppress alert beeps for this long so we act on real data only.
ALERT_WARMUP_S = 60.0
# Face absence longer than this restarts the alert warmup on re-detection.
FACE_BREAK_S = 60.0
# Same span as Trace: judge low-ORI *duration* over the last 5 minutes.
ALERT_TREND_WINDOW_S = 300.0
# Beep only after this much of that window has been below threshold (in total).
ALERT_LOW_DURATION_S = 180.0
# Do not store a sample more often than this (keeps the deque small).
ALERT_TREND_SAMPLE_S = 1.0
ALERT_MODES = ("visual", "sound")
ALERT_MODE_DEFAULT = "visual"

# Looking down this far (bottom / left-bottom / right-bottom) is off-screen.
# Pitch is negative when looking down. Change this one number to retune.
LOOK_DOWN_OFFSCREEN_DEG = 20.0
# Looking left/right this far (yaw) is off-screen, same treatment as looking down.
LOOK_SIDE_OFFSCREEN_DEG = 50.0
# Stay off-screen until pitch rises this many degrees back toward level.
LOOK_DOWN_OFFSCREEN_HYSTERESIS_DEG = 1.5

# Practice coach: metronome for target blink rhythm (~20 blinks/min)
PRACTICE_INTERVAL_S = 3.0
# Read instructions (and full-screen visual cue intro) first, then beep.
PRACTICE_COUNTDOWN_S = 20.0
PRACTICE_DURATION_S = 120.0
# After active practice, hold results (rate + regularity) on screen.
PRACTICE_SUMMARY_S = 20.0

# Camera resilience
READ_FAILS_BEFORE_REOPEN = 8
REOPEN_INITIAL_WAIT_S = 0.4
REOPEN_BACKOFF_MAX_S = 5.0
REOPEN_WARMUP_READS = 10
# macOS AVFoundation often refuses a second open while another session is
# live, and needs a short pause after release before the next open.
CAMERA_OPEN_GAP_S = 0.12
CAMERA_OPEN_RETRIES = 3
CAMERA_OPEN_RETRY_WAIT_S = 0.35
CAMERA_RELEASE_SETTLE_S = 0.15

# Low-power capture profile (target ~20% of one core on typical laptops).
# MediaPipe Face Landmarker dominates CPU — process a small frame; display
# uses the full-res camera crop with landmarks remapped after inference.
#
# Frame *rate* policy (30 fps face / post-blink sleep / 1 fps no-face) lives
# entirely in ``app.frame_pacer`` so it can be rolled back without touching
# detection, screen-time, regularity, or research logging.
PROCESS_MAX_WIDTH = 480          # MediaPipe input width; height keeps aspect
DISPLAY_CAPTURE_MAX_WIDTH = 1280 # camera hint for a sharper Lens crop

# Preview encoding (only when UI requests camera)
PREVIEW_MAX_WIDTH = 640          # displayed crop / full-frame width
PREVIEW_JPEG_QUALITY = 82
PREVIEW_MIN_INTERVAL_S = 0.12  # ~8 fps ceiling for the stream
# Legacy alias (logs / older call sites)
CAPTURE_MAX_WIDTH = PROCESS_MAX_WIDTH


def look_down_offscreen(
    pitch_deg: float | None,
    *,
    currently_off: bool,
    angle_deg: float | None = None,
    direction: str | None = None,
    threshold_deg: float = LOOK_DOWN_OFFSCREEN_DEG,
    hysteresis_deg: float = LOOK_DOWN_OFFSCREEN_HYSTERESIS_DEG,
) -> bool:
    """
    True when the user is looking down and the Look-angle card is past 20°.

    The card shows the combined face-to-camera angle (yaw + pitch). A
    down-left / down-right glance can be 25° on the card while pitch is
    only −12°, which used to miss off-screen. We now use that same card
    angle once the face is aimed downward (``bottom`` / ``left-bottom`` /
    ``right-bottom``). Pure left/right uses ``look_side_offscreen``.
    """
    # “bottom” in the direction label means the face is aimed downward.
    looking_down = isinstance(direction, str) and direction.endswith("bottom")
    if not looking_down:
        try:
            looking_down = (
                pitch_deg is not None and float(pitch_deg) <= -8.0
            )
        except (TypeError, ValueError):
            looking_down = False
    if not looking_down:
        return False

    # Prefer the same combined angle the Look-angle card shows.
    measure: float | None = None
    for raw in (angle_deg, pitch_deg):
        if raw is None:
            continue
        try:
            value = abs(float(raw))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            measure = value
            break
    if measure is None:
        return False

    limit = float(threshold_deg)
    if currently_off:
        # Once off-screen, stay off until the angle comes a bit back up
        # (stops the status flipping on every tiny head nod).
        limit = max(0.0, limit - float(hysteresis_deg))
    return measure >= limit


def look_side_offscreen(
    yaw_deg: float | None,
    *,
    currently_off: bool,
    direction: str | None = None,
    threshold_deg: float = LOOK_SIDE_OFFSCREEN_DEG,
    hysteresis_deg: float = LOOK_DOWN_OFFSCREEN_HYSTERESIS_DEG,
) -> bool:
    """
    True when the face is turned left or right past ``threshold_deg`` (50°).

    Uses yaw (left/right), not the combined Look-angle card. A 55° glance
    at a side monitor counts even if pitch is near zero. Same hysteresis
    as looking down so the status does not flicker on the boundary.
    """
    looking_side = False
    if isinstance(direction, str):
        looking_side = direction.startswith("left") or direction.startswith("right")
    if not looking_side:
        try:
            looking_side = yaw_deg is not None and abs(float(yaw_deg)) >= 8.0
        except (TypeError, ValueError):
            looking_side = False
    if not looking_side:
        return False
    try:
        measure = abs(float(yaw_deg))
    except (TypeError, ValueError):
        return False
    if not math.isfinite(measure):
        return False
    limit = float(threshold_deg)
    if currently_off:
        limit = max(0.0, limit - float(hysteresis_deg))
    return measure >= limit


def _log(msg: str) -> None:
    """Write a diagnostic line. Silent if the terminal is already gone."""
    try:
        print(f"[EyesRhythm] {msg}", file=sys.stderr, flush=True)
    except Exception:
        pass


def _frame_is_black(frame) -> bool:
    """True if the camera handed us an empty / nearly-all-black picture."""
    return frame is None or float(frame.mean()) < 2.0


# Ignore Continuity / parked cameras that open but only return a dark static frame.
MIN_USABLE_FRAME_MEAN = 12.0
MIN_USABLE_FRAME_STD = 6.0


def _frame_is_usable(frame) -> bool:
    """True when the frame looks like a live camera, not a dead virtual device."""
    if frame is None or _frame_is_black(frame):
        return False
    try:
        mean = float(frame.mean())
        std = float(frame.std())
    except (TypeError, ValueError):
        return False
    return mean >= MIN_USABLE_FRAME_MEAN and std >= MIN_USABLE_FRAME_STD


def _is_mobile_camera_name(name: str) -> bool:
    """True for phone/tablet cameras and phone-as-webcam apps."""
    lowered = str(name or "").strip().lower()
    if not lowered:
        return False
    markers = (
        "iphone",
        "ipad",
        "ipod",
        "continuity",
        "ios device",
        "apple mobile",
        "android",
        "galaxy",
        "pixel",
        "phone camera",
        "mobile camera",
        "droidcam",
        "epoccam",
        "iriun",
        "camo studio",
        "camo camera",
    )
    return any(m in lowered for m in markers)


def _is_mobile_camera_model(model_id: str) -> bool:
    """True for Continuity Camera hardware ids (iPhone14,2 / iPad13,1 …)."""
    mid = str(model_id or "").strip().lower()
    if not mid:
        return False
    return mid.startswith(("iphone", "ipad", "ipod"))


def _is_desk_camera(name: str, model_id: str = "") -> bool:
    """
    True only for a Mac built-in camera or a USB webcam on the computer.

    Phone / iPad Continuity cameras and phone-as-webcam apps are excluded.
    """
    if _is_mobile_camera_name(name) or _is_mobile_camera_model(model_id):
        return False

    mid = str(model_id or "").strip()
    if platform.system() == "Darwin" and mid:
        # macOS hardware id from system_profiler when available.
        return mid.lower().startswith("uvc camera")

    # Name-only path (AVFoundation / Windows): allow if not mobile.
    return True


def _release_camera(cap: cv2.VideoCapture | None) -> None:
    """Politely close a webcam handle. Safe to call with None."""
    if cap is None:
        return
    try:
        cap.release()
    except Exception:
        pass


def _configure_capture(
    cap: cv2.VideoCapture,
    width_hint: int = DISPLAY_CAPTURE_MAX_WIDTH,
) -> tuple[int, int, float]:
    """
    Hint the driver toward a display-friendly capture size and FACE_FPS.

    Inference always downscales to PROCESS_MAX_WIDTH in the loop, so CPU
    savings do not depend on the driver honoring these hints. Height is left
    unset so the camera keeps its native aspect ratio.
    """
    try:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(width_hint))
        cap.set(cv2.CAP_PROP_FPS, FACE_FPS)
        # Prefer a small buffer so we do not chew through a backlog after sleeps.
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        # MJPEG can cut USB bandwidth on Windows webcams; skip on macOS —
        # AVFoundation often ignores / mishandles FOURCC.
        if platform.system() == "Windows":
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    except Exception as exc:
        _log(f"capture property set failed (continuing): {exc}")

    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    return w, h, fps


def _downscale_for_process(frame: np.ndarray) -> np.ndarray:
    """Cap width at PROCESS_MAX_WIDTH for MediaPipe; preserve aspect ratio."""
    h, w = frame.shape[:2]
    if w <= PROCESS_MAX_WIDTH:
        return frame
    scale = PROCESS_MAX_WIDTH / float(w)
    nw = PROCESS_MAX_WIDTH
    nh = max(1, int(round(h * scale)))
    return cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)


def _resize_to_preview_width(frame: np.ndarray) -> tuple[np.ndarray, float]:
    """
    Scale ``frame`` so its width is PREVIEW_MAX_WIDTH.

    Returns ``(resized, scale)`` where ``scale`` maps original-crop pixels →
    preview pixels (landmarks multiply by this after crop-local conversion).
    """
    h, w = frame.shape[:2]
    if w <= 0:
        return frame, 1.0
    if w == PREVIEW_MAX_WIDTH:
        return frame, 1.0
    scale = PREVIEW_MAX_WIDTH / float(w)
    nw = PREVIEW_MAX_WIDTH
    nh = max(1, int(round(h * scale)))
    interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    return cv2.resize(frame, (nw, nh), interpolation=interp), scale


def open_cameras(
    indices: list[int],
    *,
    quiet: bool = False,
    width_hint: int | None = None,
) -> dict[int, cv2.VideoCapture]:
    """
    Open each desk-camera index; skip ones that still fail after retries.

    Hybrid needs every camera from a clean slate. If one index fails while
    another is already open (common for the Mac built-in next to a USB
    webcam), release the whole set and try again so we do not run hybrid
    on a single leftover camera.
    """
    wanted = [int(raw) for raw in indices]
    if not wanted:
        return {}
    out: dict[int, cv2.VideoCapture] = {}
    for attempt in range(max(1, int(CAMERA_OPEN_RETRIES))):
        if out:
            _release_cameras(out)
        out = {}
        failed: list[int] = []
        for i, idx in enumerate(wanted):
            if i and CAMERA_OPEN_GAP_S > 0:
                time.sleep(CAMERA_OPEN_GAP_S)
            cap = open_camera(idx, quiet=quiet, width_hint=width_hint)
            if cap is not None:
                out[idx] = cap
            else:
                failed.append(idx)
        if not failed:
            return out
        _log(
            "camera open incomplete "
            f"(have {sorted(out)}; missing {failed}; "
            f"attempt {attempt + 1}/{CAMERA_OPEN_RETRIES})"
        )
        if attempt + 1 < CAMERA_OPEN_RETRIES:
            time.sleep(CAMERA_OPEN_RETRY_WAIT_S)
    return out


def _release_cameras(caps: dict[int, cv2.VideoCapture] | None) -> None:
    """Close every webcam handle in ``caps`` and clear the dict."""
    if not caps:
        return
    for cap in list(caps.values()):
        _release_camera(cap)
    caps.clear()
    if platform.system() == "Darwin" and CAMERA_RELEASE_SETTLE_S > 0:
        time.sleep(CAMERA_RELEASE_SETTLE_S)


def open_camera(
    index: int,
    *,
    quiet: bool = False,
    width_hint: int | None = None,
) -> cv2.VideoCapture | None:
    """Open webcam with OS-appropriate backends; verify non-black frames."""
    system = platform.system()
    if system == "Windows":
        backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
    elif system == "Darwin":
        backends = [cv2.CAP_AVFOUNDATION, cv2.CAP_ANY]
    else:
        backends = [cv2.CAP_ANY]

    for backend in backends:
        cap = cv2.VideoCapture(index, backend)
        if not cap.isOpened():
            _release_camera(cap)
            continue

        w, h, cam_fps = _configure_capture(
            cap, width_hint or DISPLAY_CAPTURE_MAX_WIDTH
        )

        good = False
        actual_w, actual_h = w, h
        for _ in range(15):
            ok, frame = cap.read()
            if ok and _frame_is_usable(frame):
                good = True
                actual_h, actual_w = frame.shape[:2]
                break
            time.sleep(0.05)

        if good:
            if not quiet:
                _log(
                    f"camera {index} opened (backend={backend}) "
                    f"capture={actual_w}x{actual_h} "
                    f"(process max width {PROCESS_MAX_WIDTH}, "
                    f"preview max width {PREVIEW_MAX_WIDTH}, "
                    f"adaptive {NO_FACE_FPS:.0f}/{FACE_FPS:.0f} fps no-face/face, "
                    f"driver fps≈{cam_fps:.1f})"
                )
            return cap
        _release_camera(cap)

    return None


def _capture_backend() -> int:
    """OpenCV backend that matches how we enumerate cameras on this OS."""
    system = platform.system()
    if system == "Darwin":
        return cv2.CAP_AVFOUNDATION
    if system == "Windows":
        return cv2.CAP_DSHOW
    return cv2.CAP_ANY


def _opencv_avfoundation_order(
    devices: list[dict[str, str]],
) -> list[dict[str, str]]:
    """
    Match OpenCV's AVFoundation backend.

    OpenCV concatenates video + muxed devices, then sorts by uniqueID
    (NSString compare) so index 0 is stable. Listing order from
    ``devicesWithMediaType:`` is *not* OpenCV order — using it labels
    FaceTime as the USB webcam and vice versa.
    """
    return sorted(devices, key=lambda d: str(d.get("unique_id") or ""))


def _macos_profiler_camera_devices() -> list[dict[str, str]]:
    """system_profiler names (slow; order does not match OpenCV)."""
    try:
        raw = subprocess.check_output(
            ["system_profiler", "SPCameraDataType", "-json"],
            text=True,
            timeout=8,
        )
        data = json.loads(raw)
        out: list[dict[str, str]] = []
        for item in data.get("SPCameraDataType") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("_name") or "").strip()
            if name:
                out.append({
                    "name": name,
                    "model_id": str(item.get("spcamera_model-id") or "").strip(),
                    "unique_id": str(item.get("spcamera_unique-id") or "").strip(),
                })
        return out
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return []


def _avfoundation_capture_devices() -> list[object]:
    """
    Raw AVCaptureDevice objects: video first, then muxed (OpenCV's concat).

    ``pyobjc-framework-AVFoundation`` is not a dependency, so load the
    system framework through objc / Cocoa when the dedicated module is
    missing. Media types are the four-char codes AVMediaTypeVideo /
    AVMediaTypeMuxed ("vide" / "muxx").
    """
    device_cls = None
    video_type: object = "vide"
    muxed_type: object = "muxx"
    try:
        import AVFoundation  # type: ignore

        device_cls = AVFoundation.AVCaptureDevice
        video_type = AVFoundation.AVMediaTypeVideo
        muxed_type = AVFoundation.AVMediaTypeMuxed
    except Exception:
        try:
            import objc
            from Foundation import NSBundle

            bundle = NSBundle.bundleWithPath_(
                "/System/Library/Frameworks/AVFoundation.framework"
            )
            if bundle is not None:
                bundle.load()
            device_cls = objc.lookUpClass("AVCaptureDevice")
        except Exception:
            device_cls = None
    if device_cls is None:
        return []
    try:
        video = device_cls.devicesWithMediaType_(video_type) or []
        muxed = device_cls.devicesWithMediaType_(muxed_type) or []
        return list(video) + list(muxed)
    except Exception:
        return []


def _macos_avfoundation_camera_devices() -> list[dict[str, str]]:
    """
    macOS cameras in the same order OpenCV uses for CAP_AVFOUNDATION indices.
    """
    out: list[dict[str, str]] = []
    for dev in _avfoundation_capture_devices():
        try:
            name = str(dev.localizedName()).strip()
        except Exception:
            name = ""
        if not name:
            continue
        try:
            uid = str(dev.uniqueID()).strip()
        except Exception:
            uid = ""
        out.append({
            "name": name,
            "model_id": "",
            "unique_id": uid,
        })
    if not out:
        return []
    return _opencv_avfoundation_order(out)


def _macos_camera_devices() -> list[dict[str, str]]:
    """
    macOS cameras in OpenCV capture-index order.

    Prefers AVFoundation (uniqueID-sorted, same as OpenCV). Falls back to
    system_profiler only when AVFoundation is unavailable.
    """
    av = _macos_avfoundation_camera_devices()
    if av:
        return av
    return _macos_profiler_camera_devices()


def _windows_camera_devices() -> list[dict[str, str]]:
    """Windows cameras in PnP enumeration order (DirectShow index order)."""
    try:
        script = (
            "Get-PnpDevice -Class Camera -Status OK | "
            "ForEach-Object { $_.FriendlyName }"
        )
        raw = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", script],
            text=True,
            timeout=12,
            errors="replace",
        )
        names = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        return [{"name": name, "model_id": ""} for name in names]
    except (OSError, subprocess.SubprocessError, ValueError):
        return []


def _camera_devices() -> list[dict[str, str]]:
    """
    Cameras in OpenCV capture-index order.

    List position ``i`` is the OpenCV capture index on this machine.
    Each entry: ``{"name": str, "model_id": str, "unique_id": str}``.
    """
    system = platform.system()
    if system == "Darwin":
        return _macos_camera_devices()
    if system == "Windows":
        return _windows_camera_devices()
    return []


def _probe_camera_index(
    index: int,
    backend: int,
) -> tuple[bool, int, int, float, float]:
    """Open briefly; return usable flag and width, height, mean, std."""
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        _release_camera(cap)
        return False, 0, 0, 0.0, 0.0
    ok, frame = False, None
    # First frames are often black on USB / FaceTime; match open_camera warmup.
    for _ in range(8):
        ok, frame = cap.read()
        if ok and _frame_is_usable(frame):
            break
        time.sleep(0.05)
    _release_camera(cap)
    if platform.system() == "Darwin" and CAMERA_RELEASE_SETTLE_S > 0:
        time.sleep(CAMERA_RELEASE_SETTLE_S)
    if not ok or not _frame_is_usable(frame):
        return False, 0, 0, 0.0, 0.0
    h, w = frame.shape[:2]
    return True, int(w), int(h), float(frame.mean()), float(frame.std())


def _describe_from_os_devices(
    devices: list[dict[str, str]],
    *,
    max_index: int,
    backend: int,
) -> list[dict[str, object]]:
    """
    Build the picker list: OpenCV index ``i`` + the name of that device.

    When devices include ``unique_id`` (macOS AVFoundation), they are sorted
    the same way OpenCV sorts them. Phone / Continuity entries are omitted
    from the picker but do not shift the index numbers of the cameras that
    remain.

    A failed probe must not hide a named desk camera. Opening each index
    to confirm a live frame is best-effort for the resolution label only —
    first frames are often black, and a camera already in use will not
    reopen. An empty picker then hides Settings → Camera for the session.
    """
    work = list(devices) if devices else []
    if any(str(d.get("unique_id") or "") for d in work):
        work = _opencv_avfoundation_order(work)

    out: list[dict[str, object]] = []
    limit = len(work) if work else int(max_index)
    for index in range(min(max(0, int(max_index)), limit)):
        if work:
            dev = work[index]
            name = str(dev.get("name") or f"Camera {index}")
            model_id = str(dev.get("model_id") or "")
            if not _is_desk_camera(name, model_id):
                continue
            ok, w, h, _mean, _std = _probe_camera_index(index, backend)
            out.append(
                {
                    "index": index,
                    "label": f"{name} ({w}×{h})" if ok and w and h else name,
                    "name": name,
                    "width": w if ok else 0,
                    "height": h if ok else 0,
                }
            )
            continue

        name = f"Camera {index}"
        ok, w, h, _mean, _std = _probe_camera_index(index, backend)
        if not ok:
            continue
        out.append(
            {
                "index": index,
                "label": f"{name} ({w}×{h})",
                "name": name,
                "width": w,
                "height": h,
            }
        )
    return out


# Opening cameras via AVFoundation hops onto the Cocoa main thread. After
# the pywebview window exists that thread is also painting WKWebView, so a
# second probe makes the GUI go blank-white. Probe once, then reuse.
_cameras_cache: list[dict[str, object]] | None = None
_cameras_cache_lock = threading.Lock()


def _clear_camera_cache() -> None:
    """Test helper: drop the process-wide camera list."""
    global _cameras_cache
    with _cameras_cache_lock:
        _cameras_cache = None


def describe_cameras(
    max_index: int = 6, *, refresh: bool = False
) -> list[dict[str, object]]:
    """
    List desk webcams only: built-in or USB cameras on this computer.

    Names come from the OS. OpenCV index follows AVFoundation uniqueID order
    on macOS (same as OpenCV). Phone / iPad Continuity cameras are omitted
    from the picker.

    The first call may open each index for a resolution label. Later calls
    reuse that list unless ``refresh=True`` — re-opening cameras after the
    desktop window is up stalls WKWebView (blank white window). Named desk
    cameras stay in the picker even if that first open fails.
    """
    global _cameras_cache
    with _cameras_cache_lock:
        if not refresh and _cameras_cache is not None:
            return [dict(c) for c in _cameras_cache]
        devices = _camera_devices()
        result = _describe_from_os_devices(
            devices, max_index=max_index, backend=_capture_backend()
        )
        _cameras_cache = [dict(c) for c in result]
        return [dict(c) for c in _cameras_cache]


def resolve_startup_camera(
    available: list[int],
    saved: int,
    cli: int | None,
    saved_mode: str = "single",
) -> tuple[int, bool, bool]:
    """
    Pick which webcam(s) to open at launch.

    Returns ``(index, pick_required, hybrid)``. Ask in the splash only when
    more than one desk camera exists *and* nothing has been chosen yet (no
    ``--camera``, no saved hybrid/single index that still exists). Re-asking
    every login re-opens every camera while the window is on screen.
    """
    avail = [int(i) for i in available]
    mode = str(saved_mode or "single").strip().lower()
    if cli is not None:
        return int(cli), False, False
    if mode == CAMERA_MODE_HYBRID and len(avail) >= 2:
        return int(avail[0]), False, True
    if len(avail) == 1:
        return avail[0], False, False
    if saved in avail:
        return int(saved), False, False
    if avail:
        return avail[0], True, False
    return 0, False, False


def list_camera_indices(max_index: int = 6) -> list[int]:
    """Return OpenCV indices that deliver a usable live frame."""
    return [int(c["index"]) for c in describe_cameras(max_index)]


def _read_frame(cap: cv2.VideoCapture | None, *, drain: int = 0):
    """
    Read one frame. If ``drain`` > 0, grab and drop that many buffered frames
    first so long sleeps (no-face mode) do not process a stale backlog.
    """
    if cap is None:
        return False, None
    try:
        for _ in range(max(0, int(drain))):
            if not cap.grab():
                break
        ok, frame = cap.read()
        if not ok or frame is None:
            return False, None
        return True, frame
    except Exception as exc:
        _log(f"camera read error: {exc}")
        return False, None


def _reopen_camera(cap: cv2.VideoCapture | None, index: int,
                   attempt: int) -> cv2.VideoCapture | None:
    """
    Close a dead webcam and try again after a short wait.

    Common after the laptop lid closes, the display sleeps, or Windows
    steals the camera. Each attempt waits a little longer (backoff).
    """
    _release_camera(cap)
    wait = min(REOPEN_BACKOFF_MAX_S,
               REOPEN_INITIAL_WAIT_S * (1.5 ** min(attempt, 8)))
    _log(f"camera lost — reconnect attempt {attempt + 1} "
         f"(waiting {wait:.1f}s; common after display/system sleep)")
    time.sleep(wait)

    new_cap = open_camera(index, quiet=True)
    if new_cap is not None:
        for _ in range(REOPEN_WARMUP_READS):
            ok, frame = _read_frame(new_cap)
            if ok and _frame_is_usable(frame):
                break
            time.sleep(0.03)
        _log("camera reconnected")
    return new_cap


def _reopen_cameras(
    caps: dict[int, cv2.VideoCapture],
    indices: list[int],
    attempt: int,
    *,
    width_hint: int | None = None,
) -> dict[int, cv2.VideoCapture]:
    """Release every webcam, wait with backoff, then reopen the index list."""
    _release_cameras(caps)
    wait = min(
        REOPEN_BACKOFF_MAX_S,
        REOPEN_INITIAL_WAIT_S * (1.5 ** min(attempt, 8)),
    )
    label = "+".join(str(i) for i in indices) or "?"
    _log(
        f"camera(s) {label} lost — reconnect attempt {attempt + 1} "
        f"(waiting {wait:.1f}s; common after display/system sleep)"
    )
    time.sleep(wait)
    new_caps = open_cameras(indices, quiet=True, width_hint=width_hint)
    if new_caps:
        _log("camera(s) reconnected: " + "+".join(str(i) for i in new_caps))
    return new_caps


# ===========================================================================
# Alert monitor (same idea as previous main.py)
# ===========================================================================

class AlertMonitor:
    """
    Background beeps while face present and ORI has been low enough, long enough.

    Beep only when **all** of these hold:
      * face is present (and on-screen),
      * face-break / first-face warmup is finished,
      * **current** Ocular Rhythm Index is below ``ALERT_ORI_THRESHOLD`` (50)
        (None means no usable score yet — ignored, not treated as low),
      * of the last ``ALERT_TREND_WINDOW_S`` (5 minutes, same as Trace),
        ORI was below 50 for at least ``ALERT_LOW_DURATION_S`` (3 minutes)
        **in total** (not necessarily consecutive). Gaps (no-face / looking
        down / no usable ORI) do not count as low time.

    As soon as the **current** ORI rises to ≥ threshold, beeps stop
    immediately — even if the 5-minute window still has 3 minutes of low
    history. They can resume if ORI dips below 50 again while that history
    still qualifies.

    Sound cues fire at most once every ``ALERT_BEEP_INTERVAL_S`` (60 s) after
    the first beep. The Visual center icon waits until the app's orange pulse
    is actually up (``ALERT_ORANGE_PULSE_S``, peak at halfway), then repeats on
    later orange peaks at least ``ALERT_VISUAL_CUE_INTERVAL_S`` apart. A blip
    that ends before the app turns orange never shows the icon. Brief face /
    ORI flicker does not reset the sound gap or the visual gap.

    Alert beeps are also suppressed for ``ALERT_WARMUP_S`` (default 60 s) after:
      * construction / first face of the session, and
      * face re-detection after an absence of ``FACE_BREAK_S`` or more.
    """

    def __init__(
        self,
        beeper: Beeper,
        threshold: float = ALERT_ORI_THRESHOLD,
        low_duration_s: float = ALERT_LOW_DURATION_S,
        poll_s: float = ALERT_POLL_S,
        on_alert_beep=None,
        on_visual_cue=None,
        on_active=None,
        visual_mode_on=None,
        visual_interval_s: float = ALERT_VISUAL_CUE_INTERVAL_S,
        time_fn=time.monotonic,
    ):
        self._beeper = beeper
        self._threshold = threshold
        self._low_duration_s = float(low_duration_s)
        self._poll_s = poll_s
        self._on_alert_beep = on_alert_beep
        self._on_visual_cue = on_visual_cue
        self._on_active = on_active
        # () -> bool. True while the chip is on Visual, so the icon phase
        # matches the orange glow. None means "always visual" (tests).
        self._visual_mode_on = visual_mode_on
        self._visual_interval_s = float(visual_interval_s)
        self._time = time_fn
        self._lock = threading.Lock()
        self._ori: float | None = None
        self._face_found = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Fresh start: do not beep until one minute of data has accumulated.
        self._suppress_until = self._time() + ALERT_WARMUP_S
        # True while the latest update reported a face.
        self._face_present = False
        # Monotonic time when the current no-face stretch began (None if face
        # is present or we have not yet observed a loss after a face).
        self._absent_since: float | None = self._time()
        # Have we ever seen a face this process lifetime?
        self._ever_had_face = False
        # Rolling 5-minute face-present ORI history: (monotonic_t, ori).
        self._trend: deque[tuple[float, float]] = deque()
        self._last_trend_sample_at: float | None = None
        self._active = False
        # 0 = never fired this process. Each interval is measured from its last cue.
        self._last_beep_mono = 0.0
        self._last_visual_mono: float | None = None
        # Monotonic time when this orange stretch started. None when the app
        # is not in the orange alert. Cleared as soon as the latch drops.
        self._visual_hold_since: float | None = None

    def update(self, ori: float | None, face_found: bool) -> None:
        """
        Feed latest ORI. Safe to call every camera frame whether or not
        the alert thread is running (state is kept warm for face-break logic).
        """
        with self._lock:
            now = self._time()
            value: float | None = None
            if ori is not None:
                try:
                    parsed = float(ori)
                except (TypeError, ValueError):
                    parsed = None
                if parsed is not None and math.isfinite(parsed):
                    value = parsed
            if face_found:
                if not self._face_present:
                    # Rising edge: face just (re)appeared.
                    self._on_face_reappeared(now)
                self._face_present = True
                self._ever_had_face = True
                self._absent_since = None
                # Genuine ORI only (including healthy values). None is a gap
                # in the trail, not a low score — must not inflate low time.
                if value is not None:
                    self._record_trend_sample(now, value)
            else:
                if self._face_present:
                    # Falling edge: start the absence clock exactly once.
                    self._absent_since = now
                elif self._absent_since is None:
                    # Never saw a face yet, or state was reset — count as absent.
                    self._absent_since = now
                self._face_present = False
            self._ori = value
            self._face_found = bool(face_found)
            self._prune_trend(now)

    def _record_trend_sample(self, now: float, ori: float) -> None:
        """
        Append a genuine face-present ORI sample (throttled); caller holds lock.

        ``ori`` must be a real score — None/gaps are no-face / empty-window
        noise and must not enter the 5-minute low-ORI duration.
        """
        if (
            self._last_trend_sample_at is not None
            and (now - self._last_trend_sample_at) < ALERT_TREND_SAMPLE_S
        ):
            # Refresh the latest sample's ori so the head stays current.
            if self._trend:
                t0, _ = self._trend[-1]
                if now - t0 < ALERT_TREND_SAMPLE_S * 1.5:
                    self._trend[-1] = (t0, ori)
                    return
            return
        self._trend.append((now, ori))
        self._last_trend_sample_at = now

    def _prune_trend(self, now: float) -> None:
        """Drop samples older than the 5-minute window; caller holds lock."""
        cutoff = now - ALERT_TREND_WINDOW_S
        while self._trend and self._trend[0][0] < cutoff:
            self._trend.popleft()

    def _low_ori_duration_s(self, now: float) -> float:
        """
        Seconds in the last 5 minutes when ORI was below threshold.

        Time-weights consecutive genuine samples. Face-absent / unusable-ORI
        holes are not filled — those gaps are not “low ORI”. Caller holds lock.
        """
        self._prune_trend(now)
        pts = self._trend
        if not pts:
            return 0.0
        cutoff = now - ALERT_TREND_WINDOW_S
        # ~3 s between samples is still “continuous”; larger is a trail gap.
        max_bridge = ALERT_TREND_SAMPLE_S * 3.0
        total = 0.0
        n = len(pts)
        for i, (t, v) in enumerate(pts):
            t_clamped = t if t >= cutoff else cutoff
            if i + 1 < n:
                dt = max(0.0, pts[i + 1][0] - t_clamped)
                if dt > max_bridge:
                    dt = ALERT_TREND_SAMPLE_S
            else:
                # Last sample: only extend a short tail to ``now`` if still live.
                dt = min(max(0.0, now - t_clamped), ALERT_TREND_SAMPLE_S)
            if dt > 0 and v < self._threshold:
                total += dt
        return total

    def _should_beep_locked(self, now: float) -> bool:
        """
        Beep gate (caller holds lock):
          current ORI is a genuine score below threshold
          AND at least 3 minutes of the last 5 were also below threshold
          AND face present  AND  not in face-break warmup.
        Current ORI ≥ threshold → False immediately (stop beeps).
        Current ORI is None → False (not a genuine low-ORI reading).
        """
        if not self._face_found:
            return False
        if now < self._suppress_until:
            return False
        if self._ori is None:
            return False
        # Immediate stop when current ORI is at/above the threshold again.
        if self._ori >= self._threshold:
            return False
        return self._low_ori_duration_s(now) >= self._low_duration_s

    def _maybe_fire_alert_beep_locked(self, now: float, should: bool) -> bool:
        """
        True once per ``ALERT_BEEP_INTERVAL_S`` while the low-ORI latch holds.

        The timer is **not** cleared when ``should`` briefly goes false, so a
        flickering face or ORI around the line cannot re-trigger immediately.
        Caller holds the lock.
        """
        if not should:
            return False
        if (
            self._last_beep_mono > 0.0
            and (now - self._last_beep_mono) < ALERT_BEEP_INTERVAL_S
        ):
            return False
        self._last_beep_mono = now
        return True

    def anchor_visual_orange(self) -> None:
        """
        Line the center icon up with the moment the app starts turning orange.

        Called when Visual alert becomes true in the UI. Does not move an
        anchor that is already running, so a repeated emit cannot restart
        the orange phase.
        """
        with self._lock:
            if self._visual_hold_since is None:
                self._visual_hold_since = self._time()

    def _maybe_fire_visual_locked(self, now: float, orange: bool) -> bool:
        """
        True on an orange peak, and only while the app is actually orange.

        ``orange`` is the low-ORI latch with Visual mode on — the same
        condition that adds the in-app orange glow. The first show is the
        halfway peak of that pulse. Later shows are the next peaks that are
        at least ``visual_interval_s`` after the last one. A latch that drops
        before the peak never shows the icon. Dropping it clears the phase
        but does not restart the gap, so flicker cannot re-trigger early.
        Caller holds the lock.
        """
        if not orange:
            self._visual_hold_since = None
            return False
        if self._visual_hold_since is None:
            self._visual_hold_since = now
        first_peak = self._visual_hold_since + ALERT_VISUAL_CUE_HOLD_S
        if now < first_peak:
            return False
        cycle = ALERT_ORANGE_PULSE_S
        if cycle <= 0:
            return False
        # Round half away from zero (not bankers' round) so a sample exactly
        # between two peaks does not snap to the earlier one.
        k = int((now - first_peak) / cycle + 0.5)
        if k < 0:
            return False
        peak = first_peak + cycle * k
        # Ticks are ``_poll_s`` apart. A half-poll window hits each peak once.
        if abs(now - peak) > (self._poll_s * 0.5 + 1e-9):
            return False
        last = self._last_visual_mono
        if last is not None and (peak - last) < (self._visual_interval_s - 0.05):
            return False
        self._last_visual_mono = peak
        return True

    def _on_face_reappeared(self, now: float) -> None:
        """Apply warmup after first face or after a long absence (caller holds lock)."""
        if not self._ever_had_face:
            self._suppress_until = max(self._suppress_until, now + ALERT_WARMUP_S)
            _log(
                f"alert warmup {int(ALERT_WARMUP_S)}s "
                "(fresh start / first face)"
            )
            return

        if self._absent_since is None:
            # Should not happen on a rising edge; be safe.
            return

        gap = now - self._absent_since
        if gap >= FACE_BREAK_S:
            # Long break: wait a full minute of face-present time before
            # low-ORI alerts can fire (rate window was empty / stale).
            self._suppress_until = now + ALERT_WARMUP_S
            _log(
                f"alert warmup {int(ALERT_WARMUP_S)}s "
                f"(face back after {gap:.0f}s break)"
            )

    def start(self) -> None:
        """
        Start the beep thread. Does **not** clear ``_suppress_until`` or
        absence tracking — face-break warmup must survive toggle off/on.
        """
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="AlertMonitor", daemon=True)
        self._thread.start()
        with self._lock:
            remaining = max(0.0, self._suppress_until - self._time())
        if remaining > 1.0:
            _log(f"alert started — warmup {remaining:.0f}s still active")

    def set_threshold(self, value: float) -> None:
        """Live-update the low-ORI line (caller already clamped)."""
        with self._lock:
            self._threshold = float(value)

    def set_low_duration_s(self, value: float) -> None:
        """Live-update how much of the 5-minute window must be low (caller clamped)."""
        with self._lock:
            self._low_duration_s = float(value)

    def stop(self) -> None:
        """Ask the beep thread to finish and wait a moment for it."""
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=2.0)
        self._thread = None

    @property
    def running(self) -> bool:
        """True while the background alert thread is still alive."""
        return self._thread is not None and self._thread.is_alive()

    def is_active(self) -> bool:
        """True while the low-ORI condition currently qualifies for Alert."""
        with self._lock:
            return bool(self._active)

    def _run(self) -> None:
        """Wake every half-second: keep Alert latched while ORI is still low."""
        while not self._stop.is_set():
            self._tick()
            self._stop.wait(self._poll_s)

    def _tick(self) -> None:
        """One alert poll: latch, sound metronome, and orange-peak icon."""
        visual_on = True
        if self._visual_mode_on is not None:
            try:
                visual_on = bool(self._visual_mode_on())
            except Exception:
                visual_on = False
        fire_beep = False
        fire_visual = False
        changed = False
        should = False
        with self._lock:
            now = self._time()
            # Keep trend head fresh even between sparse camera frames.
            if self._face_found and self._ori is not None:
                self._record_trend_sample(now, self._ori)
            should = self._should_beep_locked(now)
            changed = should != self._active
            self._active = should
            fire_beep = self._maybe_fire_alert_beep_locked(now, should)
            # Icon phase follows the orange glow, which is Visual-only.
            fire_visual = self._maybe_fire_visual_locked(
                now, bool(should) and visual_on
            )
        if changed and self._on_active is not None:
            try:
                self._on_active(should)
            except Exception:
                pass
        if fire_beep and self._on_alert_beep is not None:
            try:
                self._on_alert_beep()
            except Exception:
                pass
        if fire_visual and self._on_visual_cue is not None:
            try:
                self._on_visual_cue()
            except Exception:
                pass


class PracticeCoach:
    """
    Metronome for blink practice: one short beep every 3 seconds.

    Runs for a fixed duration (default 120s) then stops and notifies the engine
    via ``on_finished``. Cadence ≈ 20 blinks/min (healthy target range).
    """

    def __init__(
        self,
        beeper: Beeper,
        interval_s: float = PRACTICE_INTERVAL_S,
        duration_s: float = PRACTICE_DURATION_S,
        on_finished=None,
    ):
        self._beeper = beeper
        self._interval_s = max(0.5, float(interval_s))
        self._duration_s = max(1.0, float(duration_s))
        self._on_finished = on_finished
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_at: float | None = None
        self._ends_at: float | None = None
        self._started_wall: float = 0.0

    def start(self) -> None:
        # Restart cleanly if already running (new practice session).
        self.stop()
        self._stop.clear()
        now = time.monotonic()
        self._started_at = now
        self._ends_at = now + self._duration_s
        # Wall clock of active start — UI keys ball disappear to this + n×interval.
        self._started_wall = time.time()
        self._thread = threading.Thread(
            target=self._run, name="PracticeCoach", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Cancel the metronome immediately (user ended Practice early)."""
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=2.0)
        self._thread = None
        self._started_at = None
        self._ends_at = None
        self._started_wall = 0.0

    @property
    def started_wall(self) -> float:
        """Wall-clock epoch when the coach started (0 if idle)."""
        return float(getattr(self, "_started_wall", 0.0) or 0.0)

    @property
    def running(self) -> bool:
        """True while the 3-second beep metronome is still running."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def remaining_s(self) -> float:
        """Seconds left in the 2-minute practice (0 if not running)."""
        if self._ends_at is None:
            return 0.0
        return max(0.0, self._ends_at - time.monotonic())

    @property
    def ends_at_wall(self) -> float | None:
        """Wall-clock epoch when practice should end (for UI countdown)."""
        if self._ends_at is None or self._started_at is None:
            return None
        # Convert monotonic remaining → wall time.
        return time.time() + self.remaining_s

    def _run(self) -> None:
        """
        Beep at the *end* of each interval (not immediately).

        That lines up with the full-screen ball animation: the ball travels
        for ``interval_s`` and arrives at the next waypoint on the beep.
        """
        finished_naturally = False
        try:
            while not self._stop.is_set():
                remaining = self.remaining_s
                if remaining <= 0:
                    finished_naturally = True
                    break
                wait = min(self._interval_s, remaining)
                if self._stop.wait(wait):
                    break
                # Beep only after a full interval so arrivals stay on cadence.
                # A truncated final wait (session end) does not get a beep.
                if wait + 1e-3 >= self._interval_s:
                    self._beeper.beep()
                if self.remaining_s <= 0:
                    finished_naturally = True
                    break
        finally:
            self._started_at = None
            self._ends_at = None
            self._thread = None
            if finished_naturally and self._on_finished is not None:
                try:
                    self._on_finished()
                except Exception:
                    pass


# ===========================================================================
# Public snapshot for the UI
# ===========================================================================

@dataclass
class EngineSnapshot:
    """JSON-serialisable live state for the React dashboard."""

    # Core metrics
    continuous_bpm: float = 0.0
    average_bpm: float = 0.0
    # Ocular Rhythm Index — 70 % rate + 30 % regularity (0–100).
    ocular_rhythm_index: float | None = None
    ocular_rhythm_full: bool = False
    screen_time: str = "0m"
    screen_time_seconds: float = 0.0
    fps: float = 0.0
    regularity: float | None = None
    regularity_ready: bool = False
    regularity_gaps: int = 0

    # Context
    blinks_today: int = 0
    session_blinks: int = 0
    eyes_closed: bool = False
    face_found: bool = False
    blink_score: float = 0.0

    # Status / diagnostics
    camera_ok: bool = False
    camera_message: str = "starting"
    is_frontal: bool = True
    has_glasses: bool = False
    low_light: bool = False
    # Face/eye axis vs camera optical axis (None when no face).
    face_angle_deg: float | None = None
    face_yaw_deg: float | None = None
    face_pitch_deg: float | None = None
    # "straight" | "left" | "right-top" | …  (None when no face)
    face_direction: str | None = None
    # Looking down past LOOK_DOWN_OFFSCREEN_DEG (bottom / left-bottom / right-bottom).
    off_screen: bool = False
    # Live Settings dock (also persisted). Frontend Trace uses ori_threshold.
    ori_threshold: float = ALERT_ORI_THRESHOLD
    # Minutes of low ORI in the last 5 required before Alert beeps (default 3).
    alert_low_minutes: float = 3.0
    writing_angle_deg: float = LOOK_DOWN_OFFSCREEN_DEG
    turn_angle_deg: float = LOOK_SIDE_OFFSCREEN_DEG
    close_delta: float = 0.18

    # Toggles
    beep: bool = False
    alert: bool = False
    # "visual" | "sound" — Alert is always armed; this is the channel.
    alert_mode: str = ALERT_MODE_DEFAULT
    # True while low-ORI Alert is currently latched.
    alert_active: bool = False
    ontop: bool = False
    practice: bool = False
    # "off" | "countdown" | "active" | "summary"
    practice_phase: str = "off"
    # Seconds left in the pre-start countdown (read instructions).
    practice_countdown_s: float = 0.0
    # Total countdown length (UI progress / full-screen intro).
    practice_countdown_total_s: float = PRACTICE_COUNTDOWN_S
    # Seconds left in the active beeping practice (0 when not active).
    practice_remaining_s: float = 0.0
    # Beep / ball travel interval (seconds).
    practice_interval_s: float = PRACTICE_INTERVAL_S
    # Wall-clock epoch when active practice (beeps + ball) began; 0 if idle.
    practice_active_started_at: float = 0.0
    # Post-practice results screen (seconds left; 0 when not in summary).
    practice_summary_s: float = 0.0
    # Frozen results shown on the summary screen.
    practice_result_blinks: int = 0
    practice_result_bpm: float = 0.0
    practice_result_regularity: float | None = None
    # UI pulse markers (wall-clock). Separate signals for each control dot.
    last_blink_at: float = 0.0       # counted blink → Beep/Practice orange
    last_alert_beep_at: float = 0.0  # alert metronome → Alert red

    # Poise: centered face mesh + movement heat. Empty until the still
    # pose has been learned. The mesh itself never follows the head.
    face_map: dict = field(default_factory=empty_face_map)

    # Placeholders for future cards (always null for now)
    future_a: None = None
    future_b: None = None

    # Meta
    running: bool = False
    timestamp: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Turn this snapshot into plain JSON for the WebSocket / REST API."""
        return asdict(self)


# ===========================================================================
# Engine
# ===========================================================================

class EyeRhythmEngine:
    """
    Background camera + detection loop.

    Start with ``start()``, stop with ``stop()``.
    UI reads ``snapshot()`` and optionally ``latest_preview_jpeg()``.
    """

    def __init__(
        self,
        camera_index: int = 0,
        model_path: str = MODEL_PATH,
        *,
        hybrid: bool = False,
    ):
        self.camera_index = camera_index
        self.hybrid = bool(hybrid)
        self.model_path = model_path

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake_camera = threading.Event()
        self._thread: threading.Thread | None = None
        self._idle: CameraIdlePolicy | None = None
        self._awaiting_camera_pick = False

        saved_prefs = preferences.load()
        self._ori_threshold = float(
            saved_prefs.get("ori_threshold", ALERT_ORI_THRESHOLD)
        )
        self._alert_low_minutes = max(
            int(preferences.ALERT_LOW_MINUTES_MIN),
            min(
                int(preferences.ALERT_LOW_MINUTES_MAX),
                int(round(float(saved_prefs.get("alert_low_minutes", 3.0)))),
            ),
        )
        self._writing_angle_deg = float(
            saved_prefs.get("writing_angle_deg", LOOK_DOWN_OFFSCREEN_DEG)
        )
        self._turn_angle_deg = float(
            saved_prefs.get("turn_angle_deg", LOOK_SIDE_OFFSCREEN_DEG)
        )
        self._close_delta = float(saved_prefs.get("close_delta", 0.18))

        self._beeper = Beeper()
        self._alert = AlertMonitor(
            self._beeper,
            threshold=self._ori_threshold,
            low_duration_s=float(self._alert_low_minutes) * 60.0,
            on_alert_beep=self._note_alert_beep,
            on_visual_cue=self._note_visual_cue,
            on_active=self._note_alert_active,
            visual_mode_on=self._visual_mode_is_on,
        )
        self._ori_log = OriHistory()
        self._practice = PracticeCoach(
            self._beeper,
            on_finished=self._on_practice_finished,
        )
        self._screen_time = ScreenTimeTracker()
        # Research log: inter-blink min/max/mean/median per screen-time hour.
        self._interblink_hours = InterBlinkHourTracker()
        self._interblink_hours.sync_screen_seconds(self._screen_time.seconds_today)
        self._regularity = RegularityTracker()
        # Poise: local face motion around a center that stays at the origin.
        self._face_motion = FaceMotionTracker()
        self._detector: BlinkDetector | None = None
        self._detectors: dict[int, BlinkDetector] = {}
        self._fused_rate = FusedBlinkRate()
        self._look_off_by_cam: dict[int, bool] = {}
        self._sticky_cam: int | None = None
        self._hybrid_hunt = True
        self._capture_width: int | None = None
        self._hybrid_pool: ThreadPoolExecutor | None = None
        self._detector_init_lock = threading.Lock()
        self._last_blink_at = 0.0
        self._last_alert_beep_at = 0.0
        # Lens: red eye outlines for 500 ms after a counted blink (not while closed).
        self._eye_flash_until = 0.0
        self._look_off_screen = False
        # Freeze rate + regularity while looking down / no-face.
        self._rate_present = False
        self._rate_absent_mono: float | None = None
        self._held_bpm = 0.0
        self._held_regularity: float | None = None
        # Practice session: countdown → active → summary.
        self._practice_phase = "off"  # off | countdown | active | summary
        self._countdown_stop = threading.Event()
        self._countdown_thread: threading.Thread | None = None
        self._countdown_ends: float | None = None
        # Wall-clock epoch when active practice began (for UI animation sync).
        self._practice_active_started_at: float = 0.0
        # Blinks counted only while practice_phase == "active".
        self._practice_blinks = 0
        # Summary hold after natural finish.
        self._summary_stop = threading.Event()
        self._summary_thread: threading.Thread | None = None
        self._summary_ends: float | None = None
        self._practice_result_blinks = 0
        self._practice_result_bpm = 0.0
        self._practice_result_regularity: float | None = None

        # Restore last session toggles (Beep / Alert / On top only).
        # Practice is always session-local and never restored.
        saved = preferences.get_toggles()
        mode = str(saved_prefs.get("alert_mode") or ALERT_MODE_DEFAULT).strip().lower()
        if mode not in ALERT_MODES:
            mode = ALERT_MODE_DEFAULT
        self._alert_mode = mode
        self._alert_active = False
        self._states = {
            "beep": bool(saved.get("beep", False)),
            # Alert stays armed; the chip switches Visual / Sound.
            "alert": True,
            "ontop": bool(saved.get("ontop", False)),
            "practice": False,
        }
        self._snap = EngineSnapshot(
            beep=self._states["beep"],
            alert=True,
            alert_mode=self._alert_mode,
            alert_active=False,
            ontop=self._states["ontop"],
            ori_threshold=self._ori_threshold,
            alert_low_minutes=float(self._alert_low_minutes),
            writing_angle_deg=self._writing_angle_deg,
            turn_angle_deg=self._turn_angle_deg,
            close_delta=self._close_delta,
            practice=False,
            practice_phase="off",
            practice_countdown_s=0.0,
            practice_countdown_total_s=float(PRACTICE_COUNTDOWN_S),
            practice_remaining_s=0.0,
            practice_interval_s=float(PRACTICE_INTERVAL_S),
            practice_active_started_at=0.0,
            practice_summary_s=0.0,
            practice_result_blinks=0,
            practice_result_bpm=0.0,
            practice_result_regularity=None,
        )
        self._preview_jpeg: bytes | None = None
        self._preview_wanted = False
        self._last_preview_at = 0.0
        self._ontop_listeners: list = []
        self._visual_alert_listeners: list = []
        self._visual_alert_cue_listeners: list = []
        self._alert_active_listeners: list = []
        self._blink_pulse_listeners: list = []

    # ---- public control -----------------------------------------------

    def start(self) -> None:
        """Begin the background camera loop (no-op if already running)."""
        if self._thread is not None and self._thread.is_alive():
            return
        # Alert is always armed (Visual or Sound); the chip only picks the channel.
        self._alert.start()
        # Practice is never auto-started from preferences.
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="EyeRhythmEngine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Shut down camera, alerts, and Practice; save today’s diary files."""
        self._stop.set()
        self._alert.stop()
        self._stop_practice_session()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=5.0)
        self._thread = None
        try:
            self._screen_time.save()
        except Exception:
            pass
        try:
            self._interblink_hours.save()
        except Exception:
            pass
        try:
            self._ori_log.save(force=True)
        except Exception:
            pass
        # Persist durable toggles only (not practice).
        try:
            tgl = self.get_toggles()
            preferences.set_toggles(
                beep=tgl["beep"],
                alert=tgl["alert"],
                ontop=tgl["ontop"],
            )
        except Exception:
            pass

    def snapshot(self) -> EngineSnapshot:
        """Thread-safe copy of the latest numbers for the dashboard."""
        with self._lock:
            s = EngineSnapshot(**asdict(self._snap))
        t = self._thread
        if t is not None and not t.is_alive() and s.running:
            s.running = False
            s.camera_ok = False
            s.camera_message = "engine stopped"
        return s

    def latest_preview_jpeg(self) -> bytes | None:
        """Most recent Lens-avatar camera still, or None if not encoding."""
        with self._lock:
            return self._preview_jpeg

    def reset_face_map(self) -> None:
        """Clear Poise's face grid and the direction histogram."""
        self._face_motion.reset()
        self._publish()

    def _poise_source(
        self,
        chosen: FrameResult,
        views: list[tuple[int, np.ndarray, FrameResult, bool]],
    ) -> FrameResult | None:
        """
        Landmarks for the live face grid.

        Prefer the primary camera so a head turn is not cancelled when the
        hybrid picker hops to whichever camera sees a more frontal face.
        """
        primary = int(self.camera_index)
        for idx, _frame, cam_res, _off in views:
            if int(idx) == primary and cam_res.face_found and cam_res.landmarks_xyz is not None:
                return cam_res
        if chosen.face_found and chosen.landmarks_xyz is not None:
            return chosen
        return None

    def _observe_face_motion(
        self,
        res: FrameResult | None,
        *,
        counting: bool,
        off_screen: bool,
    ) -> None:
        """
        Feed Poise one mesh, or freeze the grids when this frame has no face.

        A turn past the off-screen angle is still the pose the grid follows.
        A missing face, or a parked camera, leaves the grids where they are.
        """
        del off_screen
        try:
            xyz = None
            if (
                counting
                and res is not None
                and res.face_found
                and res.landmarks_xyz is not None
            ):
                xyz = res.landmarks_xyz
            self._face_motion.observe(xyz, time.monotonic(), frontal=True)
        except Exception:
            pass

    def set_preview_wanted(self, wanted: bool) -> None:
        """Turn camera-preview encoding on only while Lens is visible."""
        with self._lock:
            self._preview_wanted = bool(wanted)
            if not wanted:
                self._preview_jpeg = None

    def wake_camera(self) -> None:
        """
        User finished the launch splash (or otherwise is ready).

        Reopen the webcam if the idle policy parked it while they were
        reading on-screen notices. Start the loop if it has not begun yet.
        """
        with self._lock:
            if self._idle is not None:
                self._idle.reset_to_live()
        self._wake_camera.set()
        if self._awaiting_camera_pick:
            return
        if self._thread is None or not self._thread.is_alive():
            self.start()

    def set_awaiting_camera_pick(self, waiting: bool) -> None:
        """Hold the engine off until the user picks a webcam in the UI."""
        self._awaiting_camera_pick = bool(waiting)

    def is_awaiting_camera_pick(self) -> bool:
        return self._awaiting_camera_pick

    def is_hybrid(self) -> bool:
        return bool(self.hybrid)

    def reselect_camera(self, index: int) -> None:
        """Switch to one webcam (startup picker or Settings). Restarts the loop."""
        self.reselect_source(index=int(index), hybrid=False)

    def reselect_source(self, index: int | None = None, *, hybrid: bool = False) -> None:
        """Switch to one webcam or hybrid (all desk cameras). Restarts the loop."""
        running = self._thread is not None and self._thread.is_alive()
        if hybrid:
            if running:
                self.stop()
            self.hybrid = True
            idxs = list_camera_indices()
            self.camera_index = int(idxs[0]) if idxs else int(index or 0)
            self._awaiting_camera_pick = False
            self._stop.clear()
            self.start()
            _log(f"using hybrid cameras {idxs or [self.camera_index]}")
            return
        index = int(index if index is not None else self.camera_index)
        if running and (not self.hybrid) and index == self.camera_index:
            self.wake_camera()
            return
        if running:
            self.stop()
        self.hybrid = False
        self.camera_index = index
        self._awaiting_camera_pick = False
        self._stop.clear()
        self.start()
        _log(f"using camera index {index}")

    def get_settings(self) -> dict[str, float]:
        """Live detection / alert knobs (ORI line, writing angle, blink close)."""
        with self._lock:
            return {
                "ori_threshold": float(self._ori_threshold),
                "alert_low_minutes": float(self._alert_low_minutes),
                "writing_angle_deg": float(self._writing_angle_deg),
                "turn_angle_deg": float(self._turn_angle_deg),
                "close_delta": float(self._close_delta),
            }

    def apply_settings(self, patch: dict[str, Any]) -> dict[str, float]:
        """
        Apply Settings-dock changes immediately, persist, and audit.

        Unknown keys are ignored. Values are clamped to the documented ranges.
        """
        labels = {
            "ori_threshold": "ORI threshold",
            "alert_low_minutes": "Poor-rhythm minutes",
            "writing_angle_deg": "Writing angle",
            "turn_angle_deg": "Turn angle",
            "close_delta": "Blink sensitivity",
        }
        changed: list[tuple[str, float, float]] = []
        with self._lock:
            if patch.get("ori_threshold") is not None:
                v = max(
                    preferences.ORI_THRESHOLD_MIN,
                    min(preferences.ORI_THRESHOLD_MAX, float(patch["ori_threshold"])),
                )
                old = float(self._ori_threshold)
                if abs(v - old) >= 0.05:
                    self._ori_threshold = v
                    self._alert.set_threshold(v)
                    changed.append(("ori_threshold", old, v))
            if patch.get("alert_low_minutes") is not None:
                v = max(
                    int(preferences.ALERT_LOW_MINUTES_MIN),
                    min(
                        int(preferences.ALERT_LOW_MINUTES_MAX),
                        int(round(float(patch["alert_low_minutes"]))),
                    ),
                )
                old = float(self._alert_low_minutes)
                if abs(float(v) - old) >= 0.05:
                    self._alert_low_minutes = v
                    self._alert.set_low_duration_s(float(v) * 60.0)
                    changed.append(("alert_low_minutes", old, float(v)))
            if patch.get("writing_angle_deg") is not None:
                v = max(
                    preferences.WRITING_ANGLE_MIN,
                    min(preferences.WRITING_ANGLE_MAX, float(patch["writing_angle_deg"])),
                )
                old = float(self._writing_angle_deg)
                if abs(v - old) >= 0.05:
                    self._writing_angle_deg = v
                    changed.append(("writing_angle_deg", old, v))
            if patch.get("turn_angle_deg") is not None:
                v = max(
                    preferences.TURN_ANGLE_MIN,
                    min(preferences.TURN_ANGLE_MAX, float(patch["turn_angle_deg"])),
                )
                old = float(self._turn_angle_deg)
                if abs(v - old) >= 0.05:
                    self._turn_angle_deg = v
                    changed.append(("turn_angle_deg", old, v))
            if patch.get("close_delta") is not None:
                v = max(
                    preferences.CLOSE_DELTA_MIN,
                    min(preferences.CLOSE_DELTA_MAX, float(patch["close_delta"])),
                )
                old = float(self._close_delta)
                if abs(v - old) >= 0.0005:
                    self._close_delta = v
                    self._apply_close_delta_locked()
                    changed.append(("close_delta", old, v))
            if changed:
                preferences.save(
                    ori_threshold=self._ori_threshold,
                    alert_low_minutes=float(self._alert_low_minutes),
                    writing_angle_deg=self._writing_angle_deg,
                    turn_angle_deg=self._turn_angle_deg,
                    close_delta=self._close_delta,
                )
                self._snap.ori_threshold = float(self._ori_threshold)
                self._snap.alert_low_minutes = float(self._alert_low_minutes)
                self._snap.writing_angle_deg = float(self._writing_angle_deg)
                self._snap.turn_angle_deg = float(self._turn_angle_deg)
                self._snap.close_delta = float(self._close_delta)
        for key, old, new in changed:
            try:
                audit.record(key, old, new, label=labels.get(key, key))
            except Exception:
                pass
        return self.get_settings()

    def _apply_close_delta_locked(self) -> None:
        """Push blink close/open deltas onto every live detector (caller holds lock)."""
        d = float(self._close_delta)
        seen: set[int] = set()
        dets: list[BlinkDetector] = list(self._detectors.values())
        if self._detector is not None:
            dets.append(self._detector)
        for det in dets:
            ident = id(det)
            if ident in seen:
                continue
            seen.add(ident)
            det.config.close_delta = d
            det.config.open_delta = max(0.06, d - 0.06)

    def reports_index(self) -> dict[str, Any]:
        return {
            "today": date_cls.today().isoformat(),
            "days": self._ori_log.available_days(),
            "ori_threshold": self.get_settings()["ori_threshold"],
        }

    def report_daily(self, day: str) -> dict[str, Any]:
        pack = self._ori_log.daily(day)
        pack["ori_threshold"] = self.get_settings()["ori_threshold"]
        return pack

    def report_weekly(self, day: str) -> dict[str, Any]:
        pack = self._ori_log.weekly(day)
        pack["ori_threshold"] = self.get_settings()["ori_threshold"]
        return pack

    def report_monthly(self, year: int, month: int) -> dict[str, Any]:
        pack = self._ori_log.monthly(year, month)
        pack["ori_threshold"] = self.get_settings()["ori_threshold"]
        return pack

    def set_toggle(self, name: str, value: bool | None = None) -> dict[str, bool]:
        """
        Set or flip a toggle: beep | alert | ontop | practice.

        If ``value`` is None, flip current state.
        """
        if name not in self._states:
            raise ValueError(f"unknown toggle: {name}")
        with self._lock:
            if name == "alert":
                if value is None:
                    self._alert_mode = (
                        "sound" if self._alert_mode == "visual" else "visual"
                    )
                    try:
                        preferences.save(alert_mode=self._alert_mode)
                    except Exception:
                        pass
                self._states["alert"] = True
                new_val = True
            elif value is None:
                self._states[name] = not self._states[name]
                new_val = self._states[name]
            else:
                self._states[name] = bool(value)
                new_val = self._states[name]

            if name == "practice":
                if new_val:
                    # 10 s instruction countdown, then 2 min of beeps.
                    # (Must release lock before starting threads that may join.)
                    pass
                else:
                    pass

            if name == "ontop":
                # Tell the desktop window to stay above (or stop staying above).
                for cb in list(self._ontop_listeners):
                    try:
                        cb(new_val)
                    except Exception:
                        pass

            self._snap.beep = self._states["beep"]
            self._snap.alert = True
            self._snap.alert_mode = self._alert_mode
            self._snap.ontop = self._states["ontop"]
            self._snap.practice = self._states["practice"]
            # Persist durable toggles only — practice is session-local.
            try:
                preferences.set_toggles(
                    beep=self._states["beep"],
                    alert=True,
                    ontop=self._states["ontop"],
                )
            except Exception:
                pass
            practice_on = self._states["practice"] and name == "practice"
            practice_off = (not self._states["practice"]) and name == "practice"
            alert_mode_changed = name == "alert"

        # Start/stop practice outside the lock to avoid deadlocks with join().
        if practice_on:
            self._begin_practice_session()
        elif practice_off:
            self._stop_practice_session()
        if alert_mode_changed:
            self._emit_visual_alert()

        with self._lock:
            out = dict(self._states)
            out["alert"] = True
            out["alert_mode"] = self._alert_mode
            return out

    def _begin_practice_session(self) -> None:
        """Start instruction countdown, then active beeping + visual practice."""
        self._stop_practice_session(clear_toggle=False)
        self._countdown_stop.clear()
        self._countdown_ends = time.monotonic() + PRACTICE_COUNTDOWN_S
        self._practice_active_started_at = 0.0
        self._practice_blinks = 0
        self._practice_result_blinks = 0
        self._practice_result_bpm = 0.0
        self._practice_result_regularity = None
        with self._lock:
            self._states["practice"] = True
            self._practice_phase = "countdown"
            self._snap.practice = True
            self._snap.practice_phase = "countdown"
            self._snap.practice_countdown_s = float(PRACTICE_COUNTDOWN_S)
            self._snap.practice_countdown_total_s = float(PRACTICE_COUNTDOWN_S)
            self._snap.practice_remaining_s = 0.0
            self._snap.practice_interval_s = float(PRACTICE_INTERVAL_S)
            self._snap.practice_active_started_at = 0.0
            self._snap.practice_summary_s = 0.0
            self._snap.practice_result_blinks = 0
            self._snap.practice_result_bpm = 0.0
            self._snap.practice_result_regularity = None
            # Clear regularity now so the card shows “warming up” during prep.
            self._regularity.reset()
            self._snap.regularity = None
            self._snap.regularity_ready = False
            self._snap.regularity_gaps = 0
        self._countdown_thread = threading.Thread(
            target=self._countdown_run, name="PracticeCountdown", daemon=True)
        self._countdown_thread.start()
        _log(f"practice countdown {int(PRACTICE_COUNTDOWN_S)}s — read instructions")

    def _countdown_run(self) -> None:
        """Tick the “read the instructions” clock, then start the 2-min coach."""
        while not self._countdown_stop.is_set():
            left = 0.0
            if self._countdown_ends is not None:
                left = max(0.0, self._countdown_ends - time.monotonic())
            with self._lock:
                self._snap.practice_countdown_s = round(left, 1)
                self._snap.practice_phase = "countdown"
            if left <= 0:
                break
            if self._countdown_stop.wait(0.1):
                return
        if self._countdown_stop.is_set():
            return
        # Countdown done → active practice with fresh regularity.
        self._regularity.reset()
        self._practice_blinks = 0
        # Start coach first, then publish its wall clock so UI beeps/disappear match.
        self._practice.start()
        started_wall = self._practice.started_wall or time.time()
        self._practice_active_started_at = started_wall
        with self._lock:
            self._practice_phase = "active"
            self._snap.practice_phase = "active"
            self._snap.practice_countdown_s = 0.0
            self._snap.practice_remaining_s = float(PRACTICE_DURATION_S)
            self._snap.practice_interval_s = float(PRACTICE_INTERVAL_S)
            self._snap.practice_active_started_at = started_wall
            self._snap.practice_summary_s = 0.0
            self._snap.regularity = None
            self._snap.regularity_ready = False
            self._snap.regularity_gaps = 0
        _log(f"practice active for {int(PRACTICE_DURATION_S)}s")

    def _stop_summary_thread(self) -> None:
        """Cancel the 20-second results hold if it is still running."""
        self._summary_stop.set()
        t = self._summary_thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=2.0)
        self._summary_thread = None
        self._summary_ends = None

    def _stop_practice_session(self, *, clear_toggle: bool = True) -> None:
        """Cancel countdown + coach + summary; optionally clear the practice toggle."""
        self._countdown_stop.set()
        t = self._countdown_thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=2.0)
        self._countdown_thread = None
        self._countdown_ends = None
        self._practice.stop()
        self._stop_summary_thread()
        self._practice_active_started_at = 0.0
        with self._lock:
            self._practice_phase = "off"
            if clear_toggle:
                self._states["practice"] = False
            self._snap.practice = self._states.get("practice", False) and not clear_toggle
            if clear_toggle:
                self._snap.practice = False
            self._snap.practice_phase = "off"
            self._snap.practice_countdown_s = 0.0
            self._snap.practice_remaining_s = 0.0
            self._snap.practice_active_started_at = 0.0
            self._snap.practice_summary_s = 0.0

    def _on_practice_finished(self) -> None:
        """
        Active coach finished naturally → freeze results and hold summary
        for PRACTICE_SUMMARY_S, then fully close practice.
        """
        self._practice_active_started_at = 0.0
        # Session blink rate: blinks during active practice ÷ practice minutes.
        minutes = max(1e-6, PRACTICE_DURATION_S / 60.0)
        blinks = int(self._practice_blinks)
        bpm = round(blinks / minutes, 1)
        reg = self._regularity.score
        self._practice_result_blinks = blinks
        self._practice_result_bpm = bpm
        self._practice_result_regularity = reg

        self._stop_summary_thread()
        self._summary_stop.clear()
        self._summary_ends = time.monotonic() + PRACTICE_SUMMARY_S
        with self._lock:
            self._states["practice"] = True
            self._practice_phase = "summary"
            self._snap.practice = True
            self._snap.practice_phase = "summary"
            self._snap.practice_countdown_s = 0.0
            self._snap.practice_remaining_s = 0.0
            self._snap.practice_active_started_at = 0.0
            self._snap.practice_summary_s = float(PRACTICE_SUMMARY_S)
            self._snap.practice_result_blinks = blinks
            self._snap.practice_result_bpm = bpm
            self._snap.practice_result_regularity = reg
        self._summary_thread = threading.Thread(
            target=self._summary_run, name="PracticeSummary", daemon=True)
        self._summary_thread.start()
        _log(
            f"practice summary {int(PRACTICE_SUMMARY_S)}s — "
            f"blinks={blinks} bpm={bpm} reg={reg}"
        )

    def _summary_run(self) -> None:
        """Hold results on screen, then end practice and close the overlay."""
        while not self._summary_stop.is_set():
            left = 0.0
            if self._summary_ends is not None:
                left = max(0.0, self._summary_ends - time.monotonic())
            with self._lock:
                self._snap.practice_summary_s = round(left, 1)
                self._snap.practice_phase = "summary"
                self._snap.practice = True
                self._snap.practice_result_blinks = self._practice_result_blinks
                self._snap.practice_result_bpm = self._practice_result_bpm
                self._snap.practice_result_regularity = (
                    self._practice_result_regularity
                )
            if left <= 0:
                break
            if self._summary_stop.wait(0.1):
                return
        if self._summary_stop.is_set():
            return
        # Summary done → fully close practice (window returns to normal).
        with self._lock:
            self._states["practice"] = False
            self._practice_phase = "off"
            self._snap.practice = False
            self._snap.practice_phase = "off"
            self._snap.practice_summary_s = 0.0
            self._snap.practice_remaining_s = 0.0
            self._snap.practice_active_started_at = 0.0
        self._summary_ends = None
        self._summary_thread = None
        _log("practice summary finished — session closed")

    def _note_blink_pulse(self) -> None:
        """UI: orange pulse on Beep (if on) / Practice (if on) dots."""
        with self._lock:
            self._last_blink_at = time.time()
            self._snap.last_blink_at = self._last_blink_at
            listeners = list(self._blink_pulse_listeners)
        for cb in listeners:
            try:
                cb()
            except Exception:
                pass

    def _note_alert_beep(self) -> None:
        """UI: red pulse on Alert dot when the Sound metronome fires."""
        with self._lock:
            self._last_alert_beep_at = time.time()
            self._snap.last_alert_beep_at = self._last_alert_beep_at

    def _visual_mode_is_on(self) -> bool:
        """True while the Alert chip is on Visual (the orange glow)."""
        with self._lock:
            return self._alert_mode == "visual"

    def _note_visual_cue(self) -> None:
        """
        Center-icon cue. Only while the app is in the orange Visual alert.

        The monitor already waits for an orange peak. This check is the
        last gate so a cue cannot land after the glow has turned off.
        """
        with self._lock:
            if self._alert_mode != "visual" or not self._alert_active:
                return
            cue_listeners = list(self._visual_alert_cue_listeners)
        for cb in cue_listeners:
            try:
                cb()
            except Exception:
                pass

    def _note_alert_active(self, active: bool) -> None:
        """Latch / clear the live Alert flag for Visual glow and Sound."""
        with self._lock:
            self._alert_active = bool(active)
            self._snap.alert_active = self._alert_active
            listeners = list(self._alert_active_listeners)
        self._emit_visual_alert()
        for cb in listeners:
            try:
                cb(bool(active))
            except Exception:
                pass

    def get_toggles(self) -> dict[str, bool]:
        """Copy of Beep / Alert / On top / Practice (safe to read from the UI)."""
        with self._lock:
            out = dict(self._states)
            out["alert"] = True
            out["alert_mode"] = self._alert_mode
            return out

    def on_ontop_change(self, callback) -> None:
        """Register a callback(bool) when always-on-top changes (for pywebview)."""
        self._ontop_listeners.append(callback)

    def on_visual_alert_change(self, callback) -> None:
        """
        Register a callback(bool) when Visual alert should draw attention.

        True while Alert is latched *and* the chip is on Visual (not Sound).
        """
        self._visual_alert_listeners.append(callback)

    def on_visual_alert_cue(self, callback) -> None:
        """
        Register a callback() for each Visual-mode screen cue.

        Fires on the in-app orange peak (about three times a minute) while
        the low-ORI latch holds and the chip is on Visual. Used for the
        multi-monitor icon overlay. It does not fire while the app is blue.
        """
        self._visual_alert_cue_listeners.append(callback)

    def on_alert_active_change(self, callback) -> None:
        """
        Register a callback(bool) when the low-ORI Alert latch turns on or off.

        Fires for both Visual and Sound. The Dock / taskbar icon uses this
        to flash orange while the latch is on.
        """
        self._alert_active_listeners.append(callback)

    def on_blink_pulse(self, callback) -> None:
        """
        Register a callback() for each counted blink (eye-open after close).

        The Dock / taskbar icon uses this to close the eye for a beat.
        """
        self._blink_pulse_listeners.append(callback)

    def _emit_visual_alert(self) -> None:
        with self._lock:
            wanted = bool(self._alert_active) and self._alert_mode == "visual"
            listeners = list(self._visual_alert_listeners)
        if wanted:
            # Same instant the UI adds the orange glow, so the icon peak
            # lines up with the logo turning orange.
            self._alert.anchor_visual_orange()
        for cb in listeners:
            try:
                cb(wanted)
            except Exception:
                pass

    def _sync_presence_hold(
        self,
        present: bool,
        res: FrameResult,
        ts_ms: int,
    ) -> None:
        """
        Freeze blink rate and regularity while off-screen / no-face.

        On return, shift only the 60s rate window so BPM continues from the
        last on-screen value. Regularity keeps its last score (the pause is
        not an inter-blink gap).
        """
        now = time.monotonic()
        if present:
            if not self._rate_present and self._rate_absent_mono is not None:
                # Face just came back — slide the 60s window so BPM does not
                # drop just because the person looked away for a bit.
                gap_ms = (now - self._rate_absent_mono) * 1000.0
                if gap_ms > 250.0:
                    if self.hybrid:
                        self._fused_rate.hold_rate_across_gap(gap_ms)
                        res.blinks_per_minute = self._fused_rate.bpm(ts_ms)
                    elif self._detector is not None:
                        self._detector.hold_rate_across_gap(gap_ms)
                        res.blinks_per_minute = self._detector.blinks_per_minute_at(
                            ts_ms)
            self._rate_present = True
            self._rate_absent_mono = None
            self._held_bpm = float(res.blinks_per_minute or 0.0)
            if self._regularity.score is not None:
                self._held_regularity = self._regularity.score
            return
        if self._rate_present:
            # Just left the screen: remember the last good numbers.
            self._rate_absent_mono = now
            self._regularity.note_break()
        self._rate_present = False

    def _update_look_down_offscreen(
        self,
        res: FrameResult,
        *,
        cam_index: int | None = None,
    ) -> bool:
        """Off-screen: looking down past writing angle, or left/right past turn angle."""
        key = int(self.camera_index if cam_index is None else cam_index)
        if not res.face_found:
            self._look_off_by_cam[key] = False
            if cam_index is None:
                self._look_off_screen = False
            return False
        with self._lock:
            down_lim = float(self._writing_angle_deg)
            side_lim = float(self._turn_angle_deg)
        currently = bool(self._look_off_by_cam.get(key, False))
        looking_down = look_down_offscreen(
            res.face_pitch_deg,
            currently_off=currently,
            angle_deg=res.face_angle_deg,
            direction=res.face_direction,
            threshold_deg=down_lim,
        )
        looking_side = look_side_offscreen(
            res.face_yaw_deg,
            currently_off=currently,
            direction=res.face_direction,
            threshold_deg=side_lim,
        )
        off = looking_down or looking_side
        self._look_off_by_cam[key] = off
        if cam_index is None:
            self._look_off_screen = off
        return off

    @staticmethod
    def _ori_fields(bpm: float, regularity: float | None) -> dict:
        """Ocular Rhythm Index for the snapshot (full once regularity is ready)."""
        reg = regularity
        ori = ocular_rhythm_index(bpm, reg)
        return {
            "ocular_rhythm_index": ori,
            "ocular_rhythm_full": (
                ori is not None and reg is not None
            ),
        }

    # ---- internals ----------------------------------------------------

    def _camera_is_demanded(self) -> bool:
        """True when Practice or Lens needs the webcam immediately."""
        with self._lock:
            if self._preview_wanted:
                return True
            if self._states.get("practice"):
                return True
            if self._practice_phase != "off":
                return True
        return False

    def _publish_camera_idle(self, message: str, *, camera_ok: bool = False) -> None:
        """Hold last numbers while the webcam is parked to save battery."""
        held_reg = (
            self._held_regularity
            if self._held_regularity is not None
            else self._regularity.score
        )
        self._publish(
            face_found=False,
            camera_ok=camera_ok,
            camera_message=message,
            continuous_bpm=round(self._held_bpm, 1),
            average_bpm=self._screen_time.avg_blinks_per_minute,
            **self._ori_fields(self._held_bpm, held_reg),
            screen_time=self._screen_time.formatted(),
            screen_time_seconds=self._screen_time.seconds_today,
            blinks_today=self._screen_time.blinks_today,
            fps=0.0,
            regularity=held_reg,
            regularity_ready=held_reg is not None,
            regularity_gaps=self._regularity.gap_count,
            face_angle_deg=None,
            face_yaw_deg=None,
            face_pitch_deg=None,
            face_direction=None,
            off_screen=False,
            eyes_closed=False,
        )

    def _active_indices(self) -> list[int]:
        """OpenCV indices this session should use (hybrid = every desk cam)."""
        if self.hybrid:
            idxs = list_camera_indices()
            return idxs if idxs else [int(self.camera_index)]
        return [int(self.camera_index)]

    def _ensure_detector(self, index: int) -> BlinkDetector | None:
        """Return the per-camera detector, creating it if needed."""
        key = int(index)
        with self._detector_init_lock:
            existing = self._detectors.get(key)
            if existing is not None:
                return existing
            try:
                det = BlinkDetector(self.model_path)
                with self._lock:
                    d = float(self._close_delta)
                    det.config.close_delta = d
                    det.config.open_delta = max(0.06, d - 0.06)
                self._detectors[key] = det
                if self._detector is None:
                    self._detector = det
                return det
            except Exception as exc:
                _log(f"detector init failed for camera {index}: {exc}")
                return None

    def _hybrid_adopt_caps(self, caps: dict[int, cv2.VideoCapture]) -> None:
        """Ensure detectors and the parallel pool cover every open hybrid camera."""
        for idx in list(caps):
            self._ensure_detector(int(idx))
        if self._hybrid_pool is None:
            self._hybrid_pool = ThreadPoolExecutor(
                max_workers=max(2, len(caps) or 2),
                thread_name_prefix="hybrid-mp",
            )

    def _close_all_detectors(self) -> None:
        seen: set[int] = set()
        dets = list(self._detectors.values())
        if self._detector is not None:
            dets.append(self._detector)
        for det in dets:
            ident = id(det)
            if ident in seen:
                continue
            seen.add(ident)
            try:
                det.close()
            except Exception:
                pass
        self._detectors.clear()
        self._detector = None

    def _infer_frame(
        self,
        index: int,
        frame: np.ndarray,
        ts_ms: int,
        *,
        want_preview: bool,
    ) -> FrameResult | None:
        """Run MediaPipe on one frame. Safe for one detector per camera thread."""
        det = self._ensure_detector(index)
        if det is None:
            return None
        process_frame = _downscale_for_process(frame)
        return det.process_frame(
            process_frame, ts_ms, include_mesh=want_preview
        )

    def _finish_chosen_view(
        self,
        index: int,
        frame: np.ndarray,
        res: FrameResult,
        off_screen: bool,
        *,
        counting: bool,
        ts_ms: int,
        now_mono: float,
        extras: list[tuple[int, FrameResult, bool]],
    ) -> tuple[np.ndarray, FrameResult, bool]:
        """
        Apply off-screen discard and hybrid fused counting to the chosen view.

        ``extras`` are other cameras processed this tick:
        ``(index, result, off_screen)``. Their detector totals are cleared so
        the same physical blink cannot sit on two counters; if an extra view
        actually finished the blink, hybrid still counts it once on the
        shared rate (ORI uses that rate).
        """
        self._sticky_cam = int(index)
        self._detector = self._detectors.get(int(index), self._detector)
        self._look_off_screen = bool(off_screen) if counting else False

        extra_events: list[tuple[bool, bool]] = []
        extra_dets: list[Any] = []
        for extra_idx, extra_res, extra_off in extras:
            extra_events.append(
                (bool(extra_res.blink_just_counted), bool(extra_off))
            )
            det = self._detectors.get(int(extra_idx))
            if det is None:
                continue
            extra_dets.append(det)
            if extra_res.blink_just_counted:
                det.discard_last_blink()
                extra_res.blink_just_counted = False

        if not self.hybrid:
            if (
                counting
                and off_screen
                and res.blink_just_counted
                and self._detector is not None
            ):
                self._detector.discard_last_blink()
                res.blink_just_counted = False
                res.blink_count = self._detector._blink_count
                res.blinks_per_minute = self._detector.blinks_per_minute_at(ts_ms)
            return frame, res, self._look_off_screen

        fuse = hybrid_should_count_blink(
            counting=counting,
            chosen_counted=bool(res.blink_just_counted),
            chosen_off_screen=bool(off_screen),
            extra_counts=extra_events,
        )
        det = self._detectors.get(int(index), self._detector)
        if res.blink_just_counted and det is not None:
            det.discard_last_blink()
            res.blink_just_counted = False
        accepted = bool(fuse and self._fused_rate.allow(now_mono))
        if accepted:
            self._fused_rate.on_blink(ts_ms, now_mono)
            res.blink_just_counted = True
        if fuse:
            # Only after a real fused blink: freeze extras so a delayed OPEN
            # cannot recount. Do not align every tick — that aborts in-progress
            # blinks on the cameras we are not displaying.
            if det is not None:
                det.align_after_shared_blink(ts_ms)
            for extra_det in extra_dets:
                extra_det.align_after_shared_blink(ts_ms)
        res.blink_count = self._fused_rate.count
        res.blinks_per_minute = self._fused_rate.bpm(ts_ms)
        return frame, res, self._look_off_screen

    def _wait_parked_then_peek(
        self,
        caps: dict[int, cv2.VideoCapture],
        idle: CameraIdlePolicy,
        indices: list[int],
    ) -> dict[int, cv2.VideoCapture]:
        """
        Release the webcam(s), wait 2 minutes (or until Practice/Lens), then
        reopen for a presence peek. Returns the new captures (possibly empty).
        """
        _release_cameras(caps)
        self._look_off_screen = False
        self._look_off_by_cam.clear()
        self._screen_time.tick(False)
        self._alert.update(None, False)
        self._publish_camera_idle("Idle")
        _log(
            "camera off (no face for 1 min); next peek in "
            f"{SLEEP_BETWEEN_PEEKS_S:.0f}s"
        )

        deadline = time.monotonic() + SLEEP_BETWEEN_PEEKS_S
        while not self._stop.is_set():
            if self._camera_is_demanded() or self._wake_camera.is_set():
                self._wake_camera.clear()
                idle.reset_to_live()
                new_caps = open_cameras(indices, quiet=True, width_hint=self._capture_width)
                if new_caps:
                    self._publish(camera_ok=True, camera_message="live")
                    _log("camera on — user ready")
                return new_caps
            left = deadline - time.monotonic()
            if left <= 0:
                break
            self._stop.wait(min(0.5, max(0.05, left)))

        if self._stop.is_set():
            return {}

        self._publish_camera_idle("Checking…")
        new_caps = open_cameras(indices, quiet=True, width_hint=self._capture_width)
        if not new_caps:
            _log("camera peek failed — will sleep again")
            idle.begin_sleep()
            return {}
        idle.begin_peek(time.monotonic())
        self._publish(camera_ok=True, camera_message="Checking…")
        _log("camera peek (need 30 s of face to resume)")
        return new_caps

    def _publish(self, **kwargs) -> None:
        """
        Copy the latest metrics into `_snap` so the WebSocket can send them.

        Extra kwargs overwrite matching snapshot fields. Practice timers and
        toggle flags are always refreshed from the live session state.
        """
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self._snap, k):
                    setattr(self._snap, k, v)
            self._snap.beep = self._states["beep"]
            self._snap.alert = True
            self._snap.alert_mode = self._alert_mode
            self._alert_active = bool(self._alert.is_active())
            self._snap.alert_active = self._alert_active
            self._snap.ontop = self._states["ontop"]
            self._snap.practice = self._states["practice"]
            self._snap.practice_phase = self._practice_phase
            self._snap.practice_interval_s = float(PRACTICE_INTERVAL_S)
            self._snap.practice_countdown_total_s = float(PRACTICE_COUNTDOWN_S)
            self._snap.practice_active_started_at = (
                self._practice_active_started_at
                if self._practice_phase == "active"
                else 0.0
            )
            self._snap.practice_result_blinks = self._practice_result_blinks
            self._snap.practice_result_bpm = self._practice_result_bpm
            self._snap.practice_result_regularity = self._practice_result_regularity
            self._snap.last_blink_at = self._last_blink_at
            self._snap.last_alert_beep_at = self._last_alert_beep_at
            self._snap.off_screen = self._look_off_screen
            self._snap.face_map = self._face_motion.to_public()
            self._snap.ori_threshold = float(self._ori_threshold)
            self._snap.alert_low_minutes = float(self._alert_low_minutes)
            self._snap.writing_angle_deg = float(self._writing_angle_deg)
            self._snap.turn_angle_deg = float(self._turn_angle_deg)
            self._snap.close_delta = float(self._close_delta)
            if self._practice_phase == "countdown" and self._countdown_ends is not None:
                self._snap.practice_countdown_s = round(
                    max(0.0, self._countdown_ends - time.monotonic()), 1)
                self._snap.practice_remaining_s = 0.0
                self._snap.practice_summary_s = 0.0
            elif self._practice_phase == "active":
                self._snap.practice_countdown_s = 0.0
                self._snap.practice_remaining_s = round(
                    self._practice.remaining_s, 1)
                self._snap.practice_summary_s = 0.0
            elif self._practice_phase == "summary" and self._summary_ends is not None:
                self._snap.practice_countdown_s = 0.0
                self._snap.practice_remaining_s = 0.0
                self._snap.practice_summary_s = round(
                    max(0.0, self._summary_ends - time.monotonic()), 1)
            else:
                self._snap.practice_countdown_s = 0.0
                self._snap.practice_remaining_s = 0.0
                self._snap.practice_summary_s = 0.0
            self._snap.timestamp = time.time()

    def _face_roi_xyxy(
        self,
        frame: np.ndarray,
        res: FrameResult,
        *,
        pad: float = 0.35,
    ) -> tuple[int, int, int, int] | None:
        """
        Bounding box around the face mesh, with padding, clamped to the frame.

        Returns ``(x1, y1, x2, y2)`` in pixel coords, or ``None`` if no face.
        Uses a square crop (fits the Lens stage better).
        """
        if not res.face_found or not res.all_points:
            return None
        h, w = frame.shape[:2]
        xs = [float(p[0]) * w for p in res.all_points]
        ys = [float(p[1]) * h for p in res.all_points]
        if not xs or not ys:
            return None

        x1, x2 = min(xs), max(xs)
        y1, y2 = min(ys), max(ys)
        bw = max(1.0, x2 - x1)
        bh = max(1.0, y2 - y1)

        # Expand by padding.
        x1 -= bw * pad
        x2 += bw * pad
        y1 -= bh * pad
        y2 += bh * pad

        # Square crop centred on the face (stable look in the avatar stage).
        cx = 0.5 * (x1 + x2)
        cy = 0.5 * (y1 + y2)
        side = max(x2 - x1, y2 - y1)
        half = 0.5 * side
        x1, x2 = cx - half, cx + half
        y1, y2 = cy - half, cy + half

        ix1 = int(max(0, min(w - 1, x1)))
        iy1 = int(max(0, min(h - 1, y1)))
        ix2 = int(max(0, min(w, x2)))
        iy2 = int(max(0, min(h, y2)))
        if ix2 - ix1 < 24 or iy2 - iy1 < 24:
            return None
        return ix1, iy1, ix2, iy2

    def _draw_eye_outlines(
        self,
        frame: np.ndarray,
        res: FrameResult,
        *,
        origin_xy: tuple[int, int] = (0, 0),
        src_wh: tuple[int, int] | None = None,
        display_scale: float = 1.0,
    ) -> None:
        """
        Draw hollow red eye outlines for 500 ms after a counted blink.

        Eyes closed without a counted blink, or no face → leave the stream raw.
        Landmarks are normalized (0…1) on the *source* camera frame (same
        geometry as the process frame). ``origin_xy`` / ``src_wh`` map into
        the crop; ``display_scale`` remaps crop pixels after the crop is
        resized to the preview display size.
        """
        if not res.face_found or time.monotonic() >= self._eye_flash_until:
            return
        fh, fw = frame.shape[:2]
        if src_wh is None:
            src_w, src_h = fw / max(display_scale, 1e-6), fh / max(display_scale, 1e-6)
            ox, oy = 0.0, 0.0
        else:
            src_w, src_h = float(src_wh[0]), float(src_wh[1])
            ox, oy = float(origin_xy[0]), float(origin_xy[1])
        scale = float(display_scale) if display_scale > 0 else 1.0

        def to_px(nx: float, ny: float) -> tuple[int, int]:
            x = int(round((float(nx) * src_w - ox) * scale))
            y = int(round((float(ny) * src_h - oy) * scale))
            return x, y

        # Hollow outline only — lid/iris stay visible under the stroke.
        outline_color = (40, 40, 230)  # BGR red
        thickness = max(1, int(round(2 * scale))) if scale >= 0.75 else 2

        for pts in (res.left_eye_points, res.right_eye_points):
            if not pts or len(pts) < 3:
                continue
            poly: list[tuple[int, int]] = []
            for nx, ny in pts:
                x, y = to_px(nx, ny)
                # Clamp lightly so a point just outside still contributes to the curve
                x = max(-2, min(fw + 1, x))
                y = max(-2, min(fh + 1, y))
                poly.append((x, y))
            arr = np.asarray(poly, dtype=np.int32).reshape(-1, 1, 2)
            cv2.polylines(
                frame,
                [arr],
                isClosed=True,
                color=outline_color,
                thickness=thickness,
                lineType=cv2.LINE_AA,
            )

    def _encode_preview(self, display_frame: np.ndarray, res: FrameResult) -> None:
        """
        Build a JPEG from the *full-resolution* camera frame.

        Face ROI is computed in display-frame pixels from normalized landmarks
        (valid because process frames are a uniform scale of the same image).
        The crop is then resized to PREVIEW_MAX_WIDTH; eye outlines are drawn
        with coordinates remapped by the same scale.
        """
        now = time.monotonic()
        if now - self._last_preview_at < PREVIEW_MIN_INTERVAL_S:
            return
        self._last_preview_at = now

        h, w = display_frame.shape[:2]
        roi = self._face_roi_xyxy(display_frame, res) if res.face_found else None

        if roi is not None:
            x1, y1, x2, y2 = roi
            out = display_frame[y1:y2, x1:x2].copy()
            out, disp_scale = _resize_to_preview_width(out)
            self._draw_eye_outlines(
                out,
                res,
                origin_xy=(x1, y1),
                src_wh=(w, h),
                display_scale=disp_scale,
            )
        else:
            # No face → full camera frame, scaled to preview width.
            out = display_frame.copy()
            out, disp_scale = _resize_to_preview_width(out)
            self._draw_eye_outlines(
                out, res, display_scale=disp_scale)

        ok, buf = cv2.imencode(
            ".jpg", out,
            [int(cv2.IMWRITE_JPEG_QUALITY), PREVIEW_JPEG_QUALITY])
        if ok:
            with self._lock:
                self._preview_jpeg = buf.tobytes()

    def _run(self) -> None:
        _log("engine starting")
        self._publish(running=True, camera_message="opening camera",
                      camera_ok=False)
        self._fused_rate.reset()
        self._look_off_by_cam.clear()
        self._detectors.clear()

        # Detector may fail if model missing — surface that to UI.
        try:
            self._detector = BlinkDetector(self.model_path)
            with self._lock:
                self._apply_close_delta_locked()
        except Exception as exc:
            _log(f"detector init failed: {exc}")
            self._publish(
                running=False,
                camera_ok=False,
                camera_message=f"model/detector error: {exc}",
            )
            return

        indices = self._active_indices()
        self._capture_width = None
        caps = open_cameras(indices, width_hint=self._capture_width)
        if not caps:
            label = "+".join(str(i) for i in indices) or str(self.camera_index)
            self._publish(
                camera_ok=False,
                camera_message=f"camera {label} not usable",
            )
        else:
            first_idx = next(iter(caps))
            self._detectors[first_idx] = self._detector
            self._sticky_cam = first_idx
            if self.hybrid:
                self._hybrid_adopt_caps(caps)
            self._publish(camera_ok=True, camera_message="live")
            if self.hybrid:
                missing = missing_camera_indices(indices, list(caps))
                if missing:
                    _log(
                        "hybrid started with "
                        + "+".join(str(i) for i in caps)
                        + f" — will retry {missing}"
                    )
                else:
                    _log(
                        "hybrid cameras "
                        + "+".join(str(i) for i in caps)
                        + " — synced grab, parallel detect at full fps "
                        "(uses CPU extensively)"
                    )

        t0 = time.monotonic()
        fps, frames = 0.0, 0
        fps_t = t0
        last_ts_ms = -1
        read_fails = 0
        reopen_attempt = 0
        last_fill_at = 0.0
        fill_attempt = 0
        silent_miss: dict[int, int] = {}
        last_res = FrameResult()
        # Adaptive FPS / post-blink rest — see app/frame_pacer.py.
        # Hybrid stays uncapped (no 30/1 fps cap, no post-blink pause).
        pacer = FramePacer(
            post_blink_sleep=not bool(self.hybrid),
            uncapped=bool(self.hybrid),
        )
        # Battery: release the webcam after 1 min with no face; peek every 2 min.
        idle = CameraIdlePolicy(started_at=time.monotonic())
        with self._lock:
            self._idle = idle
        _log(
            f"low-power profile: process_width={PROCESS_MAX_WIDTH} "
            f"preview_width={PREVIEW_MAX_WIDTH} "
            f"{pacer.describe()}"
        )

        try:
            # Forever, until stop() is called: grab a frame, detect blinks,
            # update daily stats, then sleep according to the CPU pacer.
            while not self._stop.is_set():
                if idle.phase == "sleep":
                    caps = self._wait_parked_then_peek(caps, idle, indices)
                    if self.hybrid:
                        indices = self._active_indices()
                        if caps:
                            self._hybrid_adopt_caps(caps)
                    pacer = FramePacer(
                        post_blink_sleep=not bool(self.hybrid),
                        uncapped=bool(self.hybrid),
                    )
                    read_fails = 0
                    reopen_attempt = 0
                    fill_attempt = 0
                    silent_miss.clear()
                    self._hybrid_hunt = True
                    continue

                loop_start = time.monotonic()
                # In no-face mode the camera may buffer many frames; drain a few.
                drain = 3 if pacer.should_drain_buffer() else 0
                decoded: dict[int, np.ndarray] = {}
                hybrid_multi = bool(self.hybrid) and len(caps) > 1
                if hybrid_multi:
                    ready = grab_all(caps, drain=drain)
                    decode_idxs = hybrid_decode_indices(
                        list(ready),
                        self._sticky_cam,
                        hunt=self._hybrid_hunt,
                    )
                    for idx in decode_idxs:
                        cap = ready.get(idx)
                        if cap is None:
                            continue
                        got = retrieve_frame(cap)
                        if got is not None and not _frame_is_black(got):
                            decoded[int(idx)] = got
                elif caps:
                    only_idx, only_cap = next(iter(caps.items()))
                    ok, frame = _read_frame(only_cap, drain=drain)
                    if ok and frame is not None and not _frame_is_black(frame):
                        decoded[only_idx] = frame
                    ready = {only_idx: only_cap} if decoded else {}
                else:
                    ready = {}

                if self.hybrid and caps:
                    others_live = bool(ready)
                    for idx in list(caps):
                        if idx in ready:
                            silent_miss[idx] = 0
                            continue
                        silent_miss[idx] = silent_miss.get(idx, 0) + 1
                        if should_drop_silent_hybrid_camera(
                            silent_miss[idx], others_live=others_live
                        ):
                            _log(
                                f"hybrid camera {idx} went silent — "
                                "releasing so the full set can reopen"
                            )
                            _release_camera(caps.pop(idx, None))
                            silent_miss.pop(idx, None)
                            self._hybrid_hunt = True

                if not decoded:
                    read_fails += 1
                    pacer.note_no_signal()
                    self._screen_time.tick(False)
                    self._sync_presence_hold(False, last_res, 0)
                    self._alert.update(None, False)
                    self._look_off_screen = False
                    self._observe_face_motion(None, counting=False, off_screen=False)
                    held_reg = (
                        self._held_regularity
                        if self._held_regularity is not None
                        else self._regularity.score
                    )
                    self._publish(
                        face_found=False,
                        camera_ok=False,
                        camera_message="reconnecting…" if read_fails >= 3
                        else "no signal",
                        continuous_bpm=round(self._held_bpm, 1),
                        average_bpm=self._screen_time.avg_blinks_per_minute,
                        **self._ori_fields(self._held_bpm, held_reg),
                        screen_time=self._screen_time.formatted(),
                        screen_time_seconds=self._screen_time.seconds_today,
                        blinks_today=self._screen_time.blinks_today,
                        fps=round(fps, 1),
                        regularity=self._held_regularity
                        if self._held_regularity is not None
                        else self._regularity.score,
                        regularity_ready=(
                            self._held_regularity is not None
                            or self._regularity.score is not None
                        ),
                        regularity_gaps=self._regularity.gap_count,
                        face_angle_deg=None,
                        face_yaw_deg=None,
                        face_pitch_deg=None,
                        face_direction=None,
                    )

                    if read_fails >= READ_FAILS_BEFORE_REOPEN:
                        caps = _reopen_cameras(
                            caps, indices, reopen_attempt,
                            width_hint=self._capture_width,
                        )
                        reopen_attempt += 1
                        read_fails = 0
                        silent_miss.clear()
                        if self.hybrid and caps:
                            self._hybrid_adopt_caps(caps)
                        if caps:
                            msg = (
                                "Checking…"
                                if idle.phase == "peek"
                                else "live"
                            )
                            self._publish(camera_ok=True, camera_message=msg)
                    time.sleep(0.02)
                    continue

                if read_fails or reopen_attempt:
                    if reopen_attempt:
                        _log("camera stream healthy again")
                    read_fails = 0
                    reopen_attempt = 0
                    msg = "Checking…" if idle.phase == "peek" else "live"
                    self._publish(camera_ok=True, camera_message=msg)

                if self.hybrid and (loop_start - last_fill_at) >= next_hybrid_fill_wait(fill_attempt):
                    last_fill_at = loop_start
                    indices = self._active_indices()
                    missing = missing_camera_indices(indices, list(caps))
                    if missing:
                        for idx in missing:
                            extra = open_camera(
                                idx, quiet=True, width_hint=self._capture_width
                            )
                            if extra is not None:
                                caps[idx] = extra
                                silent_miss[idx] = 0
                                self._hybrid_hunt = True
                                _log(f"hybrid added camera {idx}")
                        still = missing_camera_indices(indices, list(caps))
                        if still:
                            # Hot-add often fails on macOS while another
                            # AVFoundation session is live. Reopen the full
                            # set the same way switching away from hybrid
                            # and back recovers both cameras.
                            _log(
                                "hybrid recovering cameras "
                                f"{still} by reopening all desk cameras"
                            )
                            caps = _reopen_cameras(
                                caps,
                                indices,
                                fill_attempt,
                                width_hint=self._capture_width,
                            )
                            fill_attempt += 1
                            silent_miss.clear()
                            self._hybrid_hunt = True
                        else:
                            fill_attempt = 0
                        if caps:
                            self._hybrid_adopt_caps(caps)
                    else:
                        fill_attempt = 0

                ts_ms = max(int((time.monotonic() - t0) * 1000), last_ts_ms + 1)
                last_ts_ms = ts_ms

                with self._lock:
                    want_preview = self._preview_wanted
                preview_idx = (
                    self._sticky_cam
                    if self._sticky_cam in decoded
                    else (next(iter(decoded)) if decoded else None)
                )

                process_error = False
                inferred: list[tuple[int, np.ndarray, FrameResult]] = []
                items = list(decoded.items())
                pool = self._hybrid_pool if hybrid_multi else None
                if pool is not None and len(items) > 1:
                    futs = [
                        pool.submit(
                            self._infer_frame,
                            idx,
                            frame,
                            ts_ms,
                            want_preview=bool(want_preview and idx == preview_idx),
                        )
                        for idx, frame in items
                    ]
                    for (idx, frame), fut in zip(items, futs):
                        try:
                            cam_res = fut.result()
                        except Exception as exc:
                            _log(f"frame processing error camera {idx}: {exc}")
                            process_error = True
                            continue
                        if cam_res is None:
                            continue
                        inferred.append((idx, frame, cam_res))
                else:
                    for idx, frame in items:
                        try:
                            cam_res = self._infer_frame(
                                idx,
                                frame,
                                ts_ms,
                                want_preview=bool(want_preview and idx == preview_idx),
                            )
                        except Exception as exc:
                            _log(f"frame processing error camera {idx}: {exc}")
                            process_error = True
                            continue
                        if cam_res is None:
                            continue
                        inferred.append((idx, frame, cam_res))

                if hybrid_multi:
                    # Skipped cameras must not finish a stale CLOSED→OPEN
                    # blink the next time we hunt them.
                    for idx in caps:
                        if int(idx) in decoded:
                            continue
                        det = self._detectors.get(int(idx))
                        if det is not None:
                            det.align_after_shared_blink(ts_ms)

                processed: list[tuple[int, np.ndarray, FrameResult, bool]] = []
                for idx, frame, cam_res in inferred:
                    off = self._update_look_down_offscreen(
                        cam_res, cam_index=idx
                    )
                    processed.append((idx, frame, cam_res, off))

                if not processed:
                    self._screen_time.tick(False)
                    self._sync_presence_hold(False, last_res, ts_ms)
                    self._alert.update(None, False)
                    if process_error:
                        pacer.sleep_for_frame(loop_start)
                    continue

                scored = [
                    (i, r.face_found, off, r.is_frontal, r.face_angle_deg)
                    for i, _f, r, off in processed
                ]
                best_idx = pick_front_facing_index(
                    scored, sticky=self._sticky_cam
                )
                chosen = next(
                    (p for p in processed if p[0] == best_idx),
                    processed[0],
                )
                ch_idx, display_frame, res, off_screen = chosen
                if self.hybrid:
                    self._hybrid_hunt = not is_front_facing(
                        face_found=bool(res.face_found),
                        off_screen=bool(off_screen),
                        is_frontal=bool(res.is_frontal),
                    )
                extras = [
                    (i, r, o) for i, _f, r, o in processed if i != ch_idx
                ]
                face_any = any(
                    r.face_found for _i, _f, r, _o in processed
                )
                on_screen_any = any_on_screen_face(
                    [(r.face_found, off) for _i, _f, r, off in processed]
                )
                if self._camera_is_demanded():
                    idle.reset_to_live()
                    next_phase = "live"
                else:
                    next_phase = idle.note_face(bool(face_any), time.monotonic())
                counting = next_phase == "live"
                if not counting:
                    off_screen = False

                display_frame, res, off_screen = self._finish_chosen_view(
                    ch_idx,
                    display_frame,
                    res,
                    off_screen,
                    counting=counting,
                    ts_ms=ts_ms,
                    now_mono=time.monotonic(),
                    extras=extras,
                )
                last_res = res

                # Hybrid: present if ANY desk camera still sees an on-screen
                # face. Using only the chosen view made ORI freeze whenever
                # pick hopped to a profile / look-away camera for a tick.
                if self.hybrid:
                    present = counting and on_screen_any
                    off_screen = bool(face_any) and not on_screen_any
                    res.face_found = bool(face_any)
                    self._look_off_screen = off_screen if counting else False
                else:
                    present = (
                        counting and bool(res.face_found) and not off_screen
                    )
                self._sync_presence_hold(present, res, ts_ms)
                self._screen_time.tick(present)
                # Keep research hour buckets aligned with face-present time.
                try:
                    self._interblink_hours.on_screen_seconds(
                        self._screen_time.seconds_today)
                except Exception:
                    pass

                # A complete blink just finished (eyes opened again).
                if counting and res.blink_just_counted:
                    self._screen_time.add_blink()
                    blink_mono = time.monotonic()
                    # Research: inter-blink gaps for every counted blink
                    # (including practice — useful for cadence studies).
                    try:
                        self._interblink_hours.on_blink(blink_mono)
                    except Exception:
                        pass
                    # During practice countdown, ignore gaps (user is reading).
                    # During active practice, score against the 3 s beep cadence.
                    with self._lock:
                        phase = self._practice_phase
                    if phase == "active":
                        # Practice-only blink tally for the end-of-session rate.
                        self._practice_blinks += 1
                        self._regularity.record_blink(
                            blink_mono,
                            target_interval=PRACTICE_INTERVAL_S,
                        )
                    elif phase == "off":
                        self._regularity.record_blink(blink_mono)
                    # phase == "countdown" | "summary": do not update regularity
                    self._note_blink_pulse()
                    # During the 10s instruction countdown, skip blink-beeps.
                    if self._states["beep"] and phase != "countdown":
                        self._beeper.beep()
                    self._eye_flash_until = time.monotonic() + 0.5
                    # Encode the flash on this frame, not the next preview tick.
                    self._last_preview_at = 0.0

                # Adaptive FPS only (face 30 / no-face 1 + post-blink rest).
                # All other business logic above is unchanged.
                pacer.observe(
                    face_found=face_any if self.hybrid else res.face_found,
                    blink_just_counted=res.blink_just_counted,
                )

                live_bpm = (
                    float(res.blinks_per_minute)
                    if present
                    else float(self._held_bpm)
                )
                live_reg = self._regularity.score
                if not present and self._held_regularity is not None:
                    live_reg = self._held_regularity
                live_ori = ocular_rhythm_index(live_bpm, live_reg)
                self._alert.update(
                    live_ori if present else None, present and counting
                )
                try:
                    self._ori_log.sample(
                        live_ori if present else None,
                        present,
                        blinks=1 if counting and res.blink_just_counted else 0,
                    )
                except Exception:
                    pass

                frames += 1
                now = time.monotonic()
                if now - fps_t >= 1.0:
                    fps = frames / (now - fps_t)
                    frames, fps_t = 0, now

                if want_preview:
                    self._encode_preview(display_frame, res)

                self._observe_face_motion(
                    self._poise_source(res, processed),
                    counting=counting,
                    off_screen=bool(off_screen),
                )
                self._publish(
                    continuous_bpm=round(live_bpm, 1),
                    average_bpm=round(self._screen_time.avg_blinks_per_minute, 1),
                    **self._ori_fields(live_bpm, live_reg),
                    screen_time=self._screen_time.formatted(),
                    screen_time_seconds=self._screen_time.seconds_today,
                    fps=round(fps, 1),
                    regularity=live_reg,
                    regularity_ready=live_reg is not None,
                    regularity_gaps=self._regularity.gap_count,
                    blinks_today=self._screen_time.blinks_today,
                    session_blinks=res.blink_count,
                    eyes_closed=res.eyes_closed,
                    face_found=res.face_found,
                    blink_score=round(res.blink_score, 3),
                    camera_ok=True,
                    camera_message=(
                        "Checking…" if next_phase == "peek" else "live"
                    ),
                    is_frontal=res.is_frontal,
                    has_glasses=res.has_glasses,
                    low_light=res.low_light,
                    face_angle_deg=res.face_angle_deg,
                    face_yaw_deg=res.face_yaw_deg,
                    face_pitch_deg=res.face_pitch_deg,
                    face_direction=res.face_direction,
                    off_screen=off_screen,
                    running=True,
                )

                if next_phase == "sleep":
                    continue

                # Pace: full fps, or POST_BLINK_SLEEP_S after a blink, or 1 fps.
                pacer.sleep_for_frame(loop_start)

        finally:
            _log("engine stopping")
            with self._lock:
                self._idle = None
            self._alert.stop()
            self._stop_practice_session()
            try:
                self._screen_time.save()
            except Exception:
                pass
            try:
                self._interblink_hours.save()
            except Exception:
                pass
            try:
                self._ori_log.save(force=True)
            except Exception:
                pass
            self._close_all_detectors()
            pool = self._hybrid_pool
            self._hybrid_pool = None
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)
            _release_cameras(caps)
            self._publish(running=False, camera_message="stopped", camera_ok=False)


def preview_data_url(jpeg_bytes: bytes | None) -> str | None:
    """Return a data-URL for the React <img>, or None."""
    if not jpeg_bytes:
        return None
    b64 = base64.b64encode(jpeg_bytes).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"
