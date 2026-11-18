"""
Persistent user preferences
===========================

Stores the last session UI state so the next launch looks the same:

* selected avatar
* compact / expanded dashboard
* window width / height (expanded size is stored; compact uses a fixed height)
* Beep / Alert / On top toggles
  (Practice is session-only and is never stored)

File (local only, never uploaded)::

    ~/.eyerhythm_blinkcounter/preferences.json
"""

from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

# Same folder as daily screen-time stats.
STORAGE_DIR = Path.home() / ".eyerhythm_blinkcounter"
PREFS_FILE = STORAGE_DIR / "preferences.json"

VALID_AVATARS = frozenset({"atlas", "lumen", "vesper", "lens", "graph", "poise"})
VALID_EYE_COMFORT = frozenset(
    {"comfortable", "mild", "moderate", "marked", "decline"}
)
VALID_ALERT_MODES = frozenset({"visual", "sound"})
VALID_CAMERA_MODES = frozenset({"single", "hybrid"})

# Canonical “default GUI”: More + Undock. Dock is exactly 2× this width.
DEFAULT_WIDTH = 480
DEFAULT_HEIGHT = 800
MIN_WIDTH = 400
# Floor for any window (compact companion). Expanded layout still prefers more.
MIN_HEIGHT = 400
MIN_EXPANDED_HEIGHT = 640
# Fallback compact window height until the UI measures the real content.
COMPACT_HEIGHT = 440
# Side dock: window becomes 2× the undocked companion width (50 / 50 split).
DOCK_WIDTH = 400  # unused for sizing; kept so older comments still resolve

# Live detection / alert knobs (also stored here so they survive relaunch).
ORI_THRESHOLD_MIN = 20.0
ORI_THRESHOLD_MAX = 80.0
WRITING_ANGLE_MIN = 10.0
WRITING_ANGLE_MAX = 40.0
TURN_ANGLE_MIN = 20.0
TURN_ANGLE_MAX = 80.0
CLOSE_DELTA_MIN = 0.10
CLOSE_DELTA_MAX = 0.30
ALERT_LOW_MINUTES_MIN = 1.0
ALERT_LOW_MINUTES_MAX = 5.0

# First-launch values if the JSON file is missing or broken.
_DEFAULTS: dict[str, Any] = {
    "avatar": "atlas",
    "compact": False,
    "window_width": DEFAULT_WIDTH,
    "window_height": DEFAULT_HEIGHT,
    "beep": False,
    "alert": True,
    "alert_mode": "visual",
    "ontop": False,
    # Last splash ocular-comfort self-check (empty = never answered).
    "eye_comfort": "",
    "dock": False,
    "ori_threshold": 50.0,
    "alert_low_minutes": 3.0,
    "writing_angle_deg": 20.0,
    "turn_angle_deg": 50.0,
    "close_delta": 0.18,
    # OpenCV index; -1 = not chosen yet (multiple webcams at launch).
    "camera_index": -1,
    # "single" = one desk webcam; "hybrid" = every desk webcam except phones.
    "camera_mode": "single",
    # Skip the comfort check and the “Before you start” notes.
    "skip_startup_screens": False,
}

# One lock + in-memory copy so the UI and engine can read/write safely.
_lock = threading.RLock()
_cache: dict[str, Any] | None = None


def _clamp_size(width: int, height: int) -> tuple[int, int]:
    """Keep a saved window size inside a sane range (not tiny, not huge)."""
    w = max(MIN_WIDTH, int(width))
    h = max(MIN_HEIGHT, int(height))
    # Soft upper bound so a huge multi-monitor size does not break layout.
    w = min(w, 4096)
    h = min(h, 4096)
    return w, h


def _sanitize(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Copy only values we understand from a preference file.

    Unknown keys, bad types, or an old “practice” flag are ignored so a
    hand-edited or outdated file cannot crash the next launch.
    """
    data = deepcopy(_DEFAULTS)
    if not isinstance(raw, dict):
        return data

    avatar = raw.get("avatar", data["avatar"])
    if isinstance(avatar, str) and avatar in VALID_AVATARS:
        data["avatar"] = avatar

    try:
        w = int(raw.get("window_width", data["window_width"]))
        h = int(raw.get("window_height", data["window_height"]))
        data["window_width"], data["window_height"] = _clamp_size(w, h)
    except (TypeError, ValueError):
        pass

    if "compact" in raw:
        data["compact"] = bool(raw["compact"])

    if "dock" in raw:
        data["dock"] = bool(raw["dock"])

    for key in ("beep", "alert", "ontop"):
        if key in raw:
            data[key] = bool(raw[key])
    # Ignore legacy "practice" key if present in old preference files.
    data["alert"] = True
    mode = raw.get("alert_mode", data["alert_mode"])
    if isinstance(mode, str) and mode.strip().lower() in VALID_ALERT_MODES:
        data["alert_mode"] = mode.strip().lower()
    else:
        data["alert_mode"] = "visual"

    comfort = raw.get("eye_comfort", data["eye_comfort"])
    if isinstance(comfort, str) and comfort in VALID_EYE_COMFORT:
        data["eye_comfort"] = comfort
    elif comfort == "":
        data["eye_comfort"] = ""

    data["ori_threshold"] = _clamp_float(
        raw.get("ori_threshold", data["ori_threshold"]),
        ORI_THRESHOLD_MIN,
        ORI_THRESHOLD_MAX,
        50.0,
    )
    data["alert_low_minutes"] = float(
        int(
            round(
                _clamp_float(
                    raw.get("alert_low_minutes", data["alert_low_minutes"]),
                    ALERT_LOW_MINUTES_MIN,
                    ALERT_LOW_MINUTES_MAX,
                    3.0,
                )
            )
        )
    )
    data["writing_angle_deg"] = _clamp_float(
        raw.get("writing_angle_deg", data["writing_angle_deg"]),
        WRITING_ANGLE_MIN,
        WRITING_ANGLE_MAX,
        20.0,
    )
    data["turn_angle_deg"] = _clamp_float(
        raw.get("turn_angle_deg", data["turn_angle_deg"]),
        TURN_ANGLE_MIN,
        TURN_ANGLE_MAX,
        50.0,
    )
    data["close_delta"] = _clamp_float(
        raw.get("close_delta", data["close_delta"]),
        CLOSE_DELTA_MIN,
        CLOSE_DELTA_MAX,
        0.18,
    )

    if "camera_index" in raw:
        try:
            data["camera_index"] = max(-1, int(raw["camera_index"]))
        except (TypeError, ValueError):
            pass

    mode = raw.get("camera_mode", data["camera_mode"])
    if isinstance(mode, str) and mode.strip().lower() in VALID_CAMERA_MODES:
        data["camera_mode"] = mode.strip().lower()
    else:
        data["camera_mode"] = "single"

    if "skip_startup_screens" in raw:
        data["skip_startup_screens"] = bool(raw["skip_startup_screens"])

    return data


def _clamp_float(value: Any, lo: float, hi: float, fallback: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    if v != v:  # NaN
        return float(fallback)
    return max(float(lo), min(float(hi), v))


def load() -> dict[str, Any]:
    """Load preferences from disk (or defaults). Thread-safe, cached."""
    global _cache
    with _lock:
        if _cache is not None:
            return deepcopy(_cache)
        try:
            raw = json.loads(PREFS_FILE.read_text(encoding="utf-8"))
            _cache = _sanitize(raw)
        except (OSError, ValueError, TypeError):
            _cache = deepcopy(_DEFAULTS)
        return deepcopy(_cache)


def save(partial: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    """
    Merge updates into preferences and write to disk.

    Accepts either a dict or keyword args (avatar=, beep=, …).
    Returns the full saved preferences.
    """
    global _cache
    updates: dict[str, Any] = {}
    if partial:
        updates.update(partial)
    updates.update(kwargs)

    with _lock:
        current = load() if _cache is None else deepcopy(_cache)
        current.update(updates)
        current = _sanitize(current)
        _cache = current
        try:
            _write_prefs(current)
        except OSError:
            # Never crash the app over preferences I/O.
            pass
        return deepcopy(current)


def _write_prefs(current: dict[str, Any]) -> None:
    """Replace the prefs file and flush it so a quit cannot drop the last click."""
    PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(current, indent=2, sort_keys=True) + "\n"
    tmp = PREFS_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, PREFS_FILE)


def get_avatar() -> str:
    """Which face / Trace view the user last picked (atlas, lumen, …)."""
    return str(load()["avatar"])


def set_avatar(avatar: str) -> dict[str, Any]:
    """Remember the chosen avatar for the next launch."""
    return save(avatar=avatar)


def get_compact() -> bool:
    """True = companion window (avatar + buttons only, no metric cards)."""
    return bool(load().get("compact", True))


def set_compact(compact: bool) -> dict[str, Any]:
    """Remember whether More (full dashboard) or Less (compact) was last used."""
    return save(compact=bool(compact))


def get_window_size() -> tuple[int, int]:
    """Last saved expanded width × height (pixels)."""
    p = load()
    return int(p["window_width"]), int(p["window_height"])


def default_gui_size() -> tuple[int, int]:
    """More + Undock — the window every launch and splash use."""
    return DEFAULT_WIDTH, DEFAULT_HEIGHT


def docked_gui_size() -> tuple[int, int]:
    """More + Dock — twice the default width, same default height."""
    return DEFAULT_WIDTH * 2, DEFAULT_HEIGHT


def launch_window_geometry() -> tuple[int, int]:
    """Always open as the default GUI (More, undocked)."""
    return default_gui_size()


def set_window_size(width: int, height: int) -> dict[str, Any]:
    w, h = _clamp_size(width, height)
    return save(window_width=w, window_height=h)


def get_dock() -> bool:
    """True when the Settings / Reports side dock is open."""
    return bool(load().get("dock", False))


def docked_window_width(_content_w: int | None = None) -> int:
    """Outer width when the Settings/Reports dock is open (2× default GUI)."""
    return DEFAULT_WIDTH * 2


def undocked_window_width(_outer_w: int | None = None) -> int:
    """Companion / left-panel width (the default GUI width)."""
    return DEFAULT_WIDTH


def content_width(window_width: int) -> int:
    """Left-panel width. Dock never changes this — it is always default GUI."""
    return DEFAULT_WIDTH


def get_toggles() -> dict[str, bool]:
    """Last session’s Beep / Alert / On top (Practice is never stored)."""
    p = load()
    return {
        "beep": bool(p["beep"]),
        "alert": bool(p["alert"]),
        "ontop": bool(p["ontop"]),
    }


def set_toggles(**flags: bool) -> dict[str, Any]:
    """Save only the durable switches; unknown names are ignored."""
    allowed = {
        k: bool(v)
        for k, v in flags.items()
        if k in ("beep", "alert", "ontop")
    }
    return save(allowed)
