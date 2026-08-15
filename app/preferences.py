"""
Persistent user preferences
===========================

Stores the last session UI state so the next launch looks the same:

* selected avatar
* window width / height (if the user resized; else defaults)
* Beep / Alert / On top toggles
  (Practice is session-only and is never stored)
* Look-angle workspace extrema (after Sweep → Finish → Close)

File (local only, never uploaded)::

    ~/.eyerhythm_blinkcounter/preferences.json
"""

from __future__ import annotations

import json
import math
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

# Same folder as daily screen-time stats.
STORAGE_DIR = Path.home() / ".eyerhythm_blinkcounter"
PREFS_FILE = STORAGE_DIR / "preferences.json"

VALID_AVATARS = frozenset({"atlas", "lumen", "vesper", "lens", "graph"})

# Defaults match app.main compact window.
DEFAULT_WIDTH = 480
DEFAULT_HEIGHT = 840
MIN_WIDTH = 400
MIN_HEIGHT = 640

_DEFAULTS: dict[str, Any] = {
    "avatar": "atlas",
    "window_width": DEFAULT_WIDTH,
    "window_height": DEFAULT_HEIGHT,
    "beep": False,
    "alert": False,
    "ontop": False,
    # Registered look-angle workspace (None until first finished sweep).
    "look_tl_deg": None,
    "look_t_deg": None,
    "look_tr_deg": None,
    "look_l_deg": None,
    "look_r_deg": None,
    "look_bl_deg": None,
    "look_b_deg": None,
    "look_br_deg": None,
}

_lock = threading.RLock()
_cache: dict[str, Any] | None = None


def _clamp_size(width: int, height: int) -> tuple[int, int]:
    w = max(MIN_WIDTH, int(width))
    h = max(MIN_HEIGHT, int(height))
    # Soft upper bound so a huge multi-monitor size does not break layout.
    w = min(w, 4096)
    h = min(h, 4096)
    return w, h


def _sanitize(raw: dict[str, Any]) -> dict[str, Any]:
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

    for key in ("beep", "alert", "ontop"):
        if key in raw:
            data[key] = bool(raw[key])
    # Ignore legacy "practice" key if present in old preference files.

    for key in (
        "look_tl_deg",
        "look_t_deg",
        "look_tr_deg",
        "look_l_deg",
        "look_r_deg",
        "look_bl_deg",
        "look_b_deg",
        "look_br_deg",
    ):
        data[key] = _as_opt_angle(raw.get(key, data[key]))

    return data


def _as_opt_angle(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        angle = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(angle) or angle < 0.0 or angle > 90.0:
        return None
    return round(angle, 1)


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
            PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
            PREFS_FILE.write_text(
                json.dumps(current, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError:
            # Never crash the app over preferences I/O.
            pass
        return deepcopy(current)


def get_avatar() -> str:
    return str(load()["avatar"])


def set_avatar(avatar: str) -> dict[str, Any]:
    return save(avatar=avatar)


def get_window_size() -> tuple[int, int]:
    p = load()
    return int(p["window_width"]), int(p["window_height"])


def set_window_size(width: int, height: int) -> dict[str, Any]:
    w, h = _clamp_size(width, height)
    return save(window_width=w, window_height=h)


def get_toggles() -> dict[str, bool]:
    p = load()
    return {
        "beep": bool(p["beep"]),
        "alert": bool(p["alert"]),
        "ontop": bool(p["ontop"]),
    }


def set_toggles(**flags: bool) -> dict[str, Any]:
    allowed = {
        k: bool(v)
        for k, v in flags.items()
        if k in ("beep", "alert", "ontop")
    }
    return save(allowed)
