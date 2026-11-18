"""
app — desktop shell + local API for EyesRhythm
=============================================

Modules
-------
* ``version``      – product SemVer (currently 1.7.0)
* ``main``         – process entry: engine + HTTP server + window
* ``engine``       – camera loop, blink pipeline, beeps/alerts (business logic)
* ``camera_idle``  – battery: 1 min no-face → camera off; peek every 2 min
* ``frame_pacer``  – adaptive FPS (30 face / post-blink rest / 1 no-face)
* ``server``       – FastAPI REST + WebSocket API for the React UI
* ``preferences``  – last avatar, window size, Beep/Alert/On top
* ``regularity``   – inter-blink CV → regularity score 0–100
* ``beeper``       – short OS-native sounds
* ``tray_icon``    – Dock / taskbar: orange Alert flash + eye blink
* ``alert_overlay``– Visual Alert: translucent icon cue on every screen

The React UI lives in ``frontend/`` and is served from ``frontend/dist`` after build.
Blink science stays in ``blink_core`` (unchanged).
"""

from app.version import VERSION as __version__

__all__ = ["__version__"]
