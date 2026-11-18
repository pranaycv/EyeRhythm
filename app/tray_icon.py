"""
Live Dock / taskbar icon
=======================

Two signals that stay visible when the window is hidden:

* **Alert** (Visual or Sound) — the mark flashes orange ↔ blue.
* **Each counted blink** — the eye closes for a beat, so detection is
  obvious even while the app is minimized.

Visual mode also shows a matching translucent icon in the centre of every
display three times a minute (see ``app.alert_overlay``).

macOS updates the Dock app icon (and the minimized-window tile).
Windows updates the window icon and, while minimized, the taskbar flash.
"""

from __future__ import annotations

import platform
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from app.engine import EyeRhythmEngine

# Brand mark: same blue / orange as the in-app logo pulse.
_BLUE_BGR = (0xE0, 0x9F, 0x5B)  # #5B9FE0
_ORANGE_BGR = (0x28, 0x8C, 0xFF)  # #FF8C28
_WHITE = (255, 255, 255, 255)

ALERT_FLASH_S = 0.5
BLINK_CLOSED_S = 0.22
ICON_SIZE = 256
WIN_SMALL_SIZE = 32

_FLASHW_STOP = 0
_FLASHW_ALL = 3
_FLASHW_TIMER = 4
_WM_SETICON = 0x0080
_ICON_SMALL = 0
_ICON_BIG = 1

VARIANTS = ("blue_open", "blue_closed", "orange_open", "orange_closed")


def resource_root() -> Path:
    """Folder that contains ``assets/`` (source checkout or PyInstaller)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[1]


def icon_variant(*, alert_flash: bool, eyes_closed: bool) -> str:
    """Pick one of the four pre-rendered frames."""
    tone = "orange" if alert_flash else "blue"
    lid = "closed" if eyes_closed else "open"
    return f"{tone}_{lid}"


def alert_flash_on(now: float, period_s: float = ALERT_FLASH_S) -> bool:
    """True on the orange half of a 1 s orange↔blue cycle."""
    if period_s <= 0:
        return True
    return int(now / period_s) % 2 == 1


def _load_brand_icon() -> np.ndarray | None:
    for name in ("app_icon.png", "logo.png"):
        path = resource_root() / "assets" / name
        if not path.is_file():
            continue
        try:
            import cv2

            img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        except Exception:
            return None
        if img is None:
            continue
        if img.ndim == 2:
            img = np.dstack(
                [img, img, img, np.full(img.shape, 255, dtype=np.uint8)]
            )
        elif img.shape[2] == 3:
            alpha = np.full(img.shape[:2], 255, dtype=np.uint8)
            img = np.dstack([img, alpha])
        return img
    return None


def _rounded_rect(img: np.ndarray, color: tuple[int, int, int, int], margin: int) -> None:
    """Fill a rounded square; used only if the brand PNG is missing."""
    import cv2

    h, w = img.shape[:2]
    radius = max(8, int(min(h, w) * 0.28))
    x0, y0 = margin, margin
    x1, y1 = w - 1 - margin, h - 1 - margin
    cv2.rectangle(img, (x0 + radius, y0), (x1 - radius, y1), color, -1, cv2.LINE_AA)
    cv2.rectangle(img, (x0, y0 + radius), (x1, y1 - radius), color, -1, cv2.LINE_AA)
    for cx, cy in (
        (x0 + radius, y0 + radius),
        (x1 - radius, y0 + radius),
        (x0 + radius, y1 - radius),
        (x1 - radius, y1 - radius),
    ):
        cv2.circle(img, (cx, cy), radius, color, -1, cv2.LINE_AA)


def _draw_fallback(size: int, *, orange: bool, closed: bool) -> np.ndarray:
    import cv2

    img = np.zeros((size, size, 4), dtype=np.uint8)
    bg = (*(_ORANGE_BGR if orange else _BLUE_BGR), 255)
    _rounded_rect(img, bg, max(2, size // 32))
    cx = cy = size // 2
    if closed:
        rx, ry = int(size * 0.32), max(2, int(size * 0.07))
        cv2.ellipse(img, (cx, cy), (rx, ry), 0, 0, 360, _WHITE, -1, cv2.LINE_AA)
        return img
    cv2.ellipse(
        img,
        (cx, cy),
        (int(size * 0.32), int(size * 0.20)),
        0,
        0,
        360,
        _WHITE,
        -1,
        cv2.LINE_AA,
    )
    cv2.circle(img, (cx, cy), int(size * 0.115), (0xFA, 0xF2, 0xE8, 255), -1, cv2.LINE_AA)
    cv2.circle(img, (cx, cy), int(size * 0.05), (0x38, 0x2A, 0x1A, 255), -1, cv2.LINE_AA)
    hx, hy = cx + int(size * 0.03), cy - int(size * 0.03)
    cv2.circle(img, (hx, hy), max(1, int(size * 0.02)), _WHITE, -1, cv2.LINE_AA)
    return img


def _recolor_orange(img: np.ndarray) -> np.ndarray:
    """Turn the blue rounded square orange; leave the eye (white / pupil) alone."""
    import cv2

    out = img.copy()
    hsv = cv2.cvtColor(out[:, :, :3], cv2.COLOR_BGR2HSV)
    hue, sat, val = cv2.split(hsv)
    alpha = out[:, :, 3]
    # Brand square only: bright, saturated blue. Skip pupil (dark) and sclera.
    mark = (
        (hue >= 90)
        & (hue <= 120)
        & (sat > 60)
        & (val > 140)
        & (alpha > 200)
    )
    hue2 = hue.copy()
    sat2 = sat.copy()
    hue2[mark] = 14
    sat2[mark] = np.clip(sat[mark].astype(np.int16) + 40, 0, 255).astype(np.uint8)
    bgr = cv2.cvtColor(cv2.merge([hue2, sat2, val]), cv2.COLOR_HSV2BGR)
    out[:, :, :3] = bgr
    return out


def _close_eye(img: np.ndarray) -> np.ndarray:
    """Cover the open eye with a white lid slit (readable at 32 px)."""
    import cv2

    out = img.copy()
    h, w = out.shape[:2]
    cx, cy = w // 2, h // 2
    bg = tuple(int(ch) for ch in out[int(h * 0.18), cx])
    cv2.ellipse(
        out,
        (cx, cy),
        (int(w * 0.40), int(h * 0.24)),
        0,
        0,
        360,
        bg,
        -1,
        cv2.LINE_AA,
    )
    rx, ry = int(w * 0.32), max(4, int(h * 0.07))
    cv2.ellipse(out, (cx, cy), (rx, ry), 0, 0, 360, _WHITE, -1, cv2.LINE_AA)
    return out


def _resize(img: np.ndarray, size: int) -> np.ndarray:
    import cv2

    if img.shape[0] == size and img.shape[1] == size:
        return img
    return cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)


def render_frames(size: int = ICON_SIZE) -> dict[str, np.ndarray]:
    """Four BGRA frames: blue/orange × open/closed."""
    base = _load_brand_icon()
    if base is None:
        return {
            "blue_open": _draw_fallback(size, orange=False, closed=False),
            "blue_closed": _draw_fallback(size, orange=False, closed=True),
            "orange_open": _draw_fallback(size, orange=True, closed=False),
            "orange_closed": _draw_fallback(size, orange=True, closed=True),
        }
    orange = _recolor_orange(base)
    frames = {
        "blue_open": base,
        "blue_closed": _close_eye(base),
        "orange_open": orange,
        "orange_closed": _close_eye(orange),
    }
    return {name: _resize(img, size) for name, img in frames.items()}


def _png_bytes(bgra: np.ndarray) -> bytes:
    import cv2

    ok, buf = cv2.imencode(".png", bgra)
    if not ok:
        raise RuntimeError("png encode failed")
    return buf.tobytes()


def _on_main(fn) -> None:
    """Run ``fn`` on the Cocoa main thread when we can; otherwise call it here."""
    if sys.platform != "darwin":
        fn()
        return
    try:
        from Foundation import NSOperationQueue, NSThread  # type: ignore

        if bool(NSThread.isMainThread()):
            fn()
            return
        NSOperationQueue.mainQueue().addOperationWithBlock_(fn)
    except Exception:
        try:
            fn()
        except Exception:
            pass


def _windows_hwnd(window) -> int | None:
    native = getattr(window, "native", None)
    if native is not None:
        handle = getattr(native, "Handle", None)
        if handle is not None:
            try:
                return int(handle.ToInt64())
            except Exception:
                try:
                    return int(handle)
                except Exception:
                    pass
    try:
        import ctypes

        title = str(getattr(window, "title", "") or "EyesRhythm")
        hwnd = int(ctypes.windll.user32.FindWindowW(None, title) or 0)
        return hwnd or None
    except Exception:
        return None


def _windows_is_minimized(hwnd: int | None) -> bool:
    if not hwnd:
        return False
    try:
        import ctypes

        return bool(ctypes.windll.user32.IsIconic(hwnd))
    except Exception:
        return False


def _flash_windows_taskbar(hwnd: int | None, enable: bool) -> None:
    if not hwnd:
        return
    try:
        import ctypes
        from ctypes import wintypes

        class FLASHWINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.UINT),
                ("hwnd", wintypes.HWND),
                ("dwFlags", wintypes.DWORD),
                ("uCount", wintypes.UINT),
                ("dwTimeout", wintypes.DWORD),
            ]

        info = FLASHWINFO()
        info.cbSize = ctypes.sizeof(FLASHWINFO)
        info.hwnd = hwnd
        if enable:
            info.dwFlags = _FLASHW_ALL | _FLASHW_TIMER
            info.uCount = 0
            info.dwTimeout = int(ALERT_FLASH_S * 1000)
        else:
            info.dwFlags = _FLASHW_STOP
            info.uCount = 0
            info.dwTimeout = 0
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
    except Exception:
        pass


def _hicon_from_bgra(bgra: np.ndarray) -> int | None:
    """Win32 HICON with per-pixel alpha, or None if GDI is unavailable."""
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None

    pixels = np.ascontiguousarray(np.flipud(bgra))
    h, w = int(pixels.shape[0]), int(pixels.shape[1])

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [
            ("bmiHeader", BITMAPINFOHEADER),
            ("bmiColors", wintypes.DWORD * 3),
        ]

    class ICONINFO(ctypes.Structure):
        _fields_ = [
            ("fIcon", wintypes.BOOL),
            ("xHotspot", wintypes.DWORD),
            ("yHotspot", wintypes.DWORD),
            ("hbmMask", wintypes.HBITMAP),
            ("hbmColor", wintypes.HBITMAP),
        ]

    gdi32 = ctypes.windll.gdi32
    user32 = ctypes.windll.user32
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.CreateBitmap.restype = wintypes.HBITMAP
    user32.CreateIconIndirect.restype = wintypes.HICON

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = h
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0

    bits = ctypes.c_void_p()
    hdc = user32.GetDC(None)
    hbm_color = gdi32.CreateDIBSection(
        hdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0
    )
    user32.ReleaseDC(None, hdc)
    if not hbm_color or not bits:
        return None
    ctypes.memmove(bits, pixels.ctypes.data, int(pixels.nbytes))
    hbm_mask = gdi32.CreateBitmap(w, h, 1, 1, None)
    info = ICONINFO()
    info.fIcon = True
    info.xHotspot = 0
    info.yHotspot = 0
    info.hbmMask = hbm_mask
    info.hbmColor = hbm_color
    hicon = user32.CreateIconIndirect(ctypes.byref(info))
    if hbm_mask:
        gdi32.DeleteObject(hbm_mask)
    if hbm_color:
        gdi32.DeleteObject(hbm_color)
    return int(hicon) if hicon else None


class TrayIconAnimator:
    """Swap Dock / taskbar frames from engine blink + alert callbacks."""

    def __init__(self, window) -> None:
        self._window = window
        self._alert = False
        self._blink_until = 0.0
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._applied = ""
        self._flashing_taskbar = False
        self._frames: dict[str, np.ndarray] = {}
        self._nsimages: dict[str, object] = {}
        self._hicons_big: dict[str, int] = {}
        self._hicons_small: dict[str, int] = {}
        self._ready = False

    def start(self) -> None:
        try:
            self._frames = render_frames(ICON_SIZE)
            self._prepare_os_images()
            self._ready = True
        except Exception:
            self._ready = False
            return
        self._apply(icon_variant(alert_flash=False, eyes_closed=False))
        self._thread = threading.Thread(
            target=self._run, name="tray-icon", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=1.0)
        self._thread = None
        if self._ready:
            try:
                self._apply(icon_variant(alert_flash=False, eyes_closed=False))
            except Exception:
                pass
        self._flash_taskbar(False)
        self._destroy_hicons()

    def set_alert(self, active: bool) -> None:
        with self._lock:
            self._alert = bool(active)
        if not active:
            self._flash_taskbar(False)
        self._wake.set()

    def pulse_blink(self) -> None:
        with self._lock:
            self._blink_until = time.monotonic() + BLINK_CLOSED_S
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                pass
            with self._lock:
                alert = self._alert
                remaining = self._blink_until - time.monotonic()
            timeout = 0.05 if (alert or remaining > 0) else 0.4
            self._wake.wait(timeout)
            self._wake.clear()

    def _tick(self) -> None:
        now = time.monotonic()
        with self._lock:
            alert = self._alert
            closed = now < self._blink_until
        flash = alert and alert_flash_on(now)
        self._apply(icon_variant(alert_flash=flash, eyes_closed=closed))
        if platform.system() == "Windows":
            hwnd = _windows_hwnd(self._window)
            should = bool(alert and _windows_is_minimized(hwnd))
            self._flash_taskbar(should)

    def _prepare_os_images(self) -> None:
        system = platform.system()
        if system == "Darwin":
            try:
                from AppKit import NSImage  # type: ignore
                from Foundation import NSData  # type: ignore
            except Exception:
                return
            for name, frame in self._frames.items():
                png = _png_bytes(frame)
                data = NSData.dataWithBytes_length_(png, len(png))
                image = NSImage.alloc().initWithData_(data)
                if image is not None:
                    self._nsimages[name] = image
            return
        if system == "Windows":
            for name, frame in self._frames.items():
                big = _hicon_from_bgra(frame)
                small = _hicon_from_bgra(_resize(frame, WIN_SMALL_SIZE))
                if big:
                    self._hicons_big[name] = big
                if small:
                    self._hicons_small[name] = small

    def _apply(self, variant: str) -> None:
        if not self._ready or variant == self._applied:
            return
        self._applied = variant
        system = platform.system()
        if system == "Darwin":
            self._apply_macos(variant)
        elif system == "Windows":
            self._apply_windows(variant)

    def _apply_macos(self, variant: str) -> None:
        image = self._nsimages.get(variant)
        if image is None:
            return
        window = self._window

        def go() -> None:
            try:
                from AppKit import NSApplication  # type: ignore

                NSApplication.sharedApplication().setApplicationIconImage_(image)
            except Exception:
                pass
            native = getattr(window, "native", None)
            if native is None:
                return
            try:
                setter = getattr(native, "setMiniwindowImage_", None)
                if callable(setter):
                    setter(image)
            except Exception:
                pass

        self._macos_apply = go
        _on_main(go)

    def _apply_windows(self, variant: str) -> None:
        hwnd = _windows_hwnd(self._window)
        if not hwnd:
            self._applied = ""
            return
        try:
            import ctypes

            big = self._hicons_big.get(variant) or 0
            small = self._hicons_small.get(variant) or big
            if small:
                ctypes.windll.user32.SendMessageW(
                    hwnd, _WM_SETICON, _ICON_SMALL, small
                )
            if big:
                ctypes.windll.user32.SendMessageW(
                    hwnd, _WM_SETICON, _ICON_BIG, big
                )
        except Exception:
            self._applied = ""

    def _flash_taskbar(self, enable: bool) -> None:
        if enable == self._flashing_taskbar:
            return
        self._flashing_taskbar = enable
        _flash_windows_taskbar(_windows_hwnd(self._window), enable)

    def _destroy_hicons(self) -> None:
        if platform.system() != "Windows":
            return
        try:
            import ctypes

            seen: set[int] = set()
            for table in (self._hicons_big, self._hicons_small):
                for handle in table.values():
                    if handle and handle not in seen:
                        ctypes.windll.user32.DestroyIcon(handle)
                        seen.add(handle)
        except Exception:
            pass
        self._hicons_big.clear()
        self._hicons_small.clear()


def bind_tray_icon(window, engine: EyeRhythmEngine) -> TrayIconAnimator:
    """Start live Dock / taskbar frames driven by blinks and Alert."""
    animator = TrayIconAnimator(window)
    animator.start()
    engine.on_alert_active_change(animator.set_alert)
    engine.on_blink_pulse(animator.pulse_blink)
    for name in ("minimized", "restored", "shown", "maximized"):
        try:
            evt = getattr(window.events, name)
            evt += animator._wake.set
        except Exception:
            pass
    return animator
