"""
Full-screen Visual Alert cue
============================

When Alert is latched and the chip is on **Visual**, show the brand mark
briefly in the centre of every connected display:

* 50% transparent so the desktop stays readable
* one eye blink (same open → closed → open as the Dock / taskbar icon)
* about one second on screen
* on the app's orange pulse (about three times a minute) while that
  low-rhythm alert is still on — not while the app is blue, and not
  for a flicker that never turns the app orange

macOS uses a borderless, nonactivating ``NSPanel``. A normal
``NSWindow`` stays behind the active app once that app is maximized,
even at a high window level. Windows uses layered, click-through
popup windows. Other platforms are a no-op.
"""

from __future__ import annotations

import platform
import sys
import threading
import time
from typing import TYPE_CHECKING, Callable

import numpy as np

from app.tray_icon import BLINK_CLOSED_S, _on_main, _png_bytes, _resize, render_frames


# Keep the latest main-thread block alive (pyobjc can drop short-lived refs).
_pending_main: list[Callable[[], None]] = []


def _on_main_sync(fn: Callable[[], None], timeout_s: float = 2.0) -> bool:
    """Run ``fn`` on the Cocoa main thread and wait until it finishes."""
    if sys.platform != "darwin":
        fn()
        return True
    try:
        from Foundation import NSThread  # type: ignore

        if bool(NSThread.isMainThread()):
            fn()
            return True
    except Exception:
        pass
    done = threading.Event()

    def wrap() -> None:
        try:
            fn()
        finally:
            done.set()

    _pending_main.append(wrap)
    try:
        _on_main(wrap)
        ok = done.wait(timeout_s)
    finally:
        try:
            _pending_main.remove(wrap)
        except ValueError:
            pass
    return bool(ok)


if TYPE_CHECKING:
    from app.engine import EyeRhythmEngine

OVERLAY_SIZE = 180
OVERLAY_ALPHA = 0.5
OVERLAY_DURATION_S = 1.0
# Close the eye once in the middle of the one-second cue.
OVERLAY_BLINK_AT_S = 0.35

# AppKit values. Tests check these bits without importing AppKit.
# Screen-saver level sits above fullscreen content and still composites
# alpha. Shielding levels (~2e9) do not: a transparent window there never
# appears. Floating (3) and status-bar (25) sit below fullscreen content.
_MAC_JOIN_ALL_SPACES = 1
_MAC_STATIONARY = 16
_MAC_IGNORES_CYCLE = 64
_MAC_FULLSCREEN_AUX = 256
_MAC_JOIN_ALL_APPS = 262144
_MAC_SCREENSAVER_LEVEL = 1000
_MAC_NONACTIVATING = 128


def macos_overlay_behavior_mask() -> int:
    """
    Spaces mask for the cue.

    ``CanJoinAllApplications`` lets the panel join another app's fullscreen
    Space and Stage Manager set. ``FullScreenAuxiliary`` is the older flag
    for the same idea. ``Stationary`` keeps it out of Mission Control.
    """
    return (
        _MAC_JOIN_ALL_SPACES
        | _MAC_STATIONARY
        | _MAC_IGNORES_CYCLE
        | _MAC_FULLSCREEN_AUX
        | _MAC_JOIN_ALL_APPS
    )


def macos_overlay_window_level() -> int:
    """Screen-saver level: above fullscreen content, alpha still composites."""
    return _MAC_SCREENSAVER_LEVEL


def macos_overlay_style_mask() -> int:
    """Borderless (0) with the nonactivating-panel bit set at init."""
    return _MAC_NONACTIVATING


def _macos_frontmost_level() -> int:
    """Live screen-saver level, or the documented value if AppKit is missing."""
    try:
        from AppKit import NSScreenSaverWindowLevel  # type: ignore

        return int(NSScreenSaverWindowLevel)
    except Exception:
        return macos_overlay_window_level()


def _macos_behavior_mask() -> int:
    """Live collection-behavior bits, falling back to the documented mask."""
    try:
        from AppKit import (  # type: ignore
            NSWindowCollectionBehaviorCanJoinAllApplications,
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorIgnoresCycle,
            NSWindowCollectionBehaviorStationary,
        )

        return (
            int(NSWindowCollectionBehaviorCanJoinAllSpaces)
            | int(NSWindowCollectionBehaviorStationary)
            | int(NSWindowCollectionBehaviorIgnoresCycle)
            | int(NSWindowCollectionBehaviorFullScreenAuxiliary)
            | int(NSWindowCollectionBehaviorCanJoinAllApplications)
        )
    except Exception:
        return macos_overlay_behavior_mask()


def _macos_other_app_is_fullscreen() -> bool:
    """
    True when another app owns a fullscreen Space.

    A zoomed window that merely fills the display does not set this.
    The panel's level already draws above that case. A fullscreen Space
    is separate, and macOS only lets an accessory process join it.
    """
    try:
        from AppKit import (  # type: ignore
            NSApplication,
            NSApplicationPresentationFullScreen,
        )

        app = NSApplication.sharedApplication()
        # A menu bar set to auto-hide is not a fullscreen Space. Only the
        # fullscreen presentation flag means another app took over the Space.
        if bool(app.isActive()):
            return False
        opts = int(app.currentSystemPresentationOptions())
        return bool(opts & int(NSApplicationPresentationFullScreen))
    except Exception:
        return False


def _log(msg: str) -> None:
    print(f"[EyesRhythm] {msg}", flush=True)


def overlay_frame_at(elapsed_s: float) -> str | None:
    """
    Which orange tray frame to show at ``elapsed_s``, or ``None`` to hide.

    Timeline (default): open → closed for ``BLINK_CLOSED_S`` → open → gone.
    """
    if elapsed_s < 0.0 or elapsed_s >= OVERLAY_DURATION_S:
        return None
    close_end = OVERLAY_BLINK_AT_S + BLINK_CLOSED_S
    if OVERLAY_BLINK_AT_S <= elapsed_s < close_end:
        return "orange_closed"
    return "orange_open"


def _scale_alpha(bgra: np.ndarray, alpha: float) -> np.ndarray:
    """Return a copy with the alpha channel multiplied by ``alpha`` (0–1)."""
    out = bgra.copy()
    factor = max(0.0, min(1.0, float(alpha)))
    out[:, :, 3] = np.clip(
        out[:, :, 3].astype(np.float32) * factor, 0, 255
    ).astype(np.uint8)
    return out


def prepare_overlay_frames(
    size: int = OVERLAY_SIZE, alpha: float = OVERLAY_ALPHA
) -> dict[str, np.ndarray]:
    """Orange open/closed frames sized and faded for the screen cue."""
    frames = render_frames(256)
    out: dict[str, np.ndarray] = {}
    for name in ("orange_open", "orange_closed"):
        out[name] = _scale_alpha(_resize(frames[name], size), alpha)
    return out


class AlertOverlayCue:
    """Show a one-second translucent icon cue on every monitor."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._frames: dict[str, np.ndarray] = {}
        self._ready = False
        self._show_token = 0
        self._visible = False
        self._system = platform.system()
        # Platform handles kept alive for the duration of a cue.
        self._mac_windows: list = []
        self._mac_views: list = []
        self._mac_images: dict[str, object] = {}
        # While another app is in a fullscreen Space, this process briefly
        # becomes an accessory so the panel is allowed onto that Space.
        # Depth is only touched on the Cocoa main thread.
        self._mac_policy_depth = 0
        self._mac_policy_prev: int | None = None
        self._mac_was_active = False
        self._win_hwnds: list[int] = []
        self._win_class = "EyesRhythmAlertOverlay"

    def start(self) -> None:
        try:
            self._frames = prepare_overlay_frames()
            self._prepare_os()
            self._ready = True
        except Exception:
            self._ready = False

    def stop(self) -> None:
        self._stop.set()
        self.hide()
        self._destroy_os()

    def hide(self) -> None:
        """Cancel any in-flight cue and remove overlay windows."""
        with self._lock:
            self._show_token += 1
            self._visible = False
        self._close_windows()

    def pulse(self) -> None:
        """Start (or restart) a one-second cue on every display."""
        if not self._ready or self._stop.is_set():
            return
        with self._lock:
            self._show_token += 1
            token = self._show_token
            self._visible = True
        _log("visual alert cue")
        if self._system == "Darwin":
            self._pulse_macos(token)
            return
        threading.Thread(
            target=self._run_cue,
            args=(token,),
            name="alert-overlay",
            daemon=True,
        ).start()

    def _alive(self, token: int) -> bool:
        with self._lock:
            return (
                not self._stop.is_set()
                and token == self._show_token
                and self._visible
            )

    def _pulse_macos(self, token: int) -> None:
        """
        Schedule the cue on the Cocoa main thread (open → blink → hide).

        Avoids blocking a worker on ``_on_main_sync``; pywebview's run loop
        owns window creation, same pattern as the Dock icon animator.
        """
        close_at = OVERLAY_BLINK_AT_S
        reopen_at = OVERLAY_BLINK_AT_S + BLINK_CLOSED_S
        hide_at = OVERLAY_DURATION_S

        def open_cue() -> None:
            if not self._alive(token):
                return
            self._close_macos_now()
            self._open_macos_now("orange_open")
            if not self._mac_windows:
                _log("visual alert cue: no overlay windows created")

        def blink_closed() -> None:
            if not self._alive(token):
                return
            self._set_macos_frame_now("orange_closed")
            self._raise_macos_now()

        def blink_open() -> None:
            if not self._alive(token):
                return
            self._set_macos_frame_now("orange_open")
            self._raise_macos_now()

        def hide_cue() -> None:
            with self._lock:
                if token == self._show_token:
                    self._visible = False
            self._close_macos_now()

        self._mac_after(0.0, open_cue)
        self._mac_after(close_at, blink_closed)
        self._mac_after(reopen_at, blink_open)
        self._mac_after(hide_at, hide_cue)

    def _mac_after(self, delay_s: float, fn: Callable[[], None]) -> None:
        """Run ``fn`` on the Cocoa main thread after ``delay_s`` seconds."""

        def worker() -> None:
            if delay_s > 0:
                time.sleep(delay_s)
            if self._stop.is_set():
                return

            def wrap() -> None:
                try:
                    fn()
                except Exception as exc:
                    _log(f"visual alert cue failed: {exc}")

            _pending_main.append(wrap)
            try:
                _on_main(wrap)
            finally:
                # Keep wrap alive briefly so the queue can drain it.
                def _drop() -> None:
                    time.sleep(2.0)
                    try:
                        _pending_main.remove(wrap)
                    except ValueError:
                        pass

                threading.Thread(target=_drop, daemon=True).start()

        threading.Thread(target=worker, name="alert-overlay-mac", daemon=True).start()

    def _run_cue(self, token: int) -> None:
        """Windows (and fallback) worker-thread animation."""
        try:
            self._open_windows("orange_open")
            with self._lock:
                if token != self._show_token:
                    return
            started = time.monotonic()
            last = "orange_open"
            while not self._stop.is_set():
                with self._lock:
                    if token != self._show_token:
                        return
                elapsed = time.monotonic() - started
                frame = overlay_frame_at(elapsed)
                if frame is None:
                    break
                if frame != last:
                    self._set_frame(frame)
                    last = frame
                elif self._system == "Windows":
                    # A maximized foreground window can bury a topmost
                    # popup until the next z-order pass. Keep reclaiming
                    # it for the whole second the cue is up.
                    for hwnd in list(self._win_hwnds):
                        self._raise_windows_hwnd(hwnd)
                    _pump_windows_messages()
                time.sleep(0.03)
        except Exception as exc:
            _log(f"visual alert cue failed: {exc}")
        finally:
            with self._lock:
                if token == self._show_token:
                    self._visible = False
            self._close_windows()

    # ---- OS prep -------------------------------------------------------

    def _prepare_os(self) -> None:
        if self._system == "Darwin":
            self._prepare_macos()
        elif self._system == "Windows":
            self._prepare_windows()

    def _destroy_os(self) -> None:
        self._close_windows()
        if self._system == "Windows":
            self._unregister_windows_class()
        self._mac_images.clear()

    def _prepare_macos(self) -> None:
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
                self._mac_images[name] = image

    def _prepare_windows(self) -> None:
        try:
            import ctypes
            from ctypes import wintypes

            WNDPROC = ctypes.WINFUNCTYPE(
                ctypes.c_long,
                wintypes.HWND,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )

            class WNDCLASSW(ctypes.Structure):
                _fields_ = [
                    ("style", wintypes.UINT),
                    ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE),
                    ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HANDLE),
                    ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR),
                    ("lpszClassName", wintypes.LPCWSTR),
                ]

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            # Keep the callback alive for the process lifetime.
            self._win_wndproc = WNDPROC(user32.DefWindowProcW)
            wc = WNDCLASSW()
            wc.style = 0
            wc.lpfnWndProc = self._win_wndproc
            wc.cbClsExtra = 0
            wc.cbWndExtra = 0
            wc.hInstance = kernel32.GetModuleHandleW(None)
            wc.hIcon = None
            wc.hCursor = None
            wc.hbrBackground = None
            wc.lpszMenuName = None
            wc.lpszClassName = self._win_class
            atom = user32.RegisterClassW(ctypes.byref(wc))
            if not atom and ctypes.GetLastError() not in (0, 1410):
                # 1410 = ERROR_CLASS_ALREADY_EXISTS
                pass
        except Exception:
            pass

    def _unregister_windows_class(self) -> None:
        try:
            import ctypes

            ctypes.windll.user32.UnregisterClassW(
                self._win_class, ctypes.windll.kernel32.GetModuleHandleW(None)
            )
        except Exception:
            pass

    # ---- show / update / hide ------------------------------------------

    def _open_windows(self, variant: str) -> None:
        self._close_windows()
        if self._system == "Darwin":
            self._open_macos(variant)
        elif self._system == "Windows":
            self._open_windows_win(variant)

    def _set_frame(self, variant: str) -> None:
        if self._system == "Darwin":
            self._set_macos_frame(variant)
        elif self._system == "Windows":
            self._set_windows_frame(variant)

    def _close_windows(self) -> None:
        if self._system == "Darwin":
            self._close_macos()
        elif self._system == "Windows":
            self._close_windows_win()

    # ---- macOS ---------------------------------------------------------

    def _macos_begin_overlay_policy(self) -> None:
        """
        Become an accessory app so the panel may enter another app's
        fullscreen Space. Restored when the cue closes. No-op unless that
        Space is actually in front, so the Dock icon stays put otherwise.
        """
        if self._mac_policy_depth > 0:
            self._mac_policy_depth += 1
            return
        self._mac_policy_depth = 1
        self._mac_policy_prev = None
        self._mac_was_active = False
        if not _macos_other_app_is_fullscreen():
            return
        try:
            from AppKit import (  # type: ignore
                NSApplication,
                NSApplicationActivationPolicyAccessory,
            )

            app = NSApplication.sharedApplication()
            prev = int(app.activationPolicy())
            self._mac_policy_prev = prev
            self._mac_was_active = bool(app.isActive())
            if prev == int(NSApplicationActivationPolicyAccessory):
                return
            if not bool(app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)):
                _log("visual alert cue: fullscreen space join was declined")
                return
            if bool(app.isHidden()):
                app.unhideWithoutActivation()
        except Exception as exc:
            _log(f"visual alert cue: fullscreen space join skipped ({exc})")

    def _macos_end_overlay_policy(self) -> None:
        """Restore the activation policy captured by the matching begin."""
        if self._mac_policy_depth <= 0:
            return
        self._mac_policy_depth -= 1
        if self._mac_policy_depth > 0:
            return
        prev = self._mac_policy_prev
        was_active = self._mac_was_active
        self._mac_policy_prev = None
        self._mac_was_active = False
        if prev is None:
            return
        try:
            from AppKit import (  # type: ignore
                NSApplication,
                NSApplicationActivationPolicyAccessory,
            )

            if int(prev) == int(NSApplicationActivationPolicyAccessory):
                return
            app = NSApplication.sharedApplication()
            app.setActivationPolicy_(int(prev))
            if was_active:
                app.activateIgnoringOtherApps_(True)
            elif bool(app.isHidden()):
                app.unhideWithoutActivation()
        except Exception as exc:
            _log(f"visual alert cue: policy restore failed ({exc})")

    def _open_macos(self, variant: str) -> None:
        _on_main(lambda: self._open_macos_now(variant))

    def _open_macos_now(self, variant: str) -> None:
        """Create overlay panels. Must run on the Cocoa main thread."""
        image = self._mac_images.get(variant)
        if image is None:
            return
        # Drop any previous panels before taking the fullscreen policy,
        # so a restart does not restore the Dock icon between the two.
        self._close_macos_now()
        self._macos_begin_overlay_policy()
        try:
            from AppKit import (  # type: ignore
                NSBackingStoreBuffered,
                NSColor,
                NSImageView,
                NSPanel,
                NSScreen,
                NSWindowStyleMaskBorderless,
                NSWindowStyleMaskNonactivatingPanel,
            )
            from Foundation import NSMakeRect  # type: ignore
        except Exception:
            self._macos_end_overlay_policy()
            return

        # Nonactivating is read once, at init. Setting it later does not
        # update the window-server tag, and the panel stays behind the
        # active app.
        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        size = float(OVERLAY_SIZE)
        windows = []
        views = []
        try:
            screens = list(NSScreen.screens() or [])
        except Exception:
            screens = []
        for screen in screens:
            try:
                frame = screen.frame()
                x = float(frame.origin.x) + (float(frame.size.width) - size) / 2.0
                y = float(frame.origin.y) + (float(frame.size.height) - size) / 2.0
                rect = NSMakeRect(x, y, size, size)
                win = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                    rect,
                    style,
                    NSBackingStoreBuffered,
                    False,
                )
                win.setReleasedWhenClosed_(False)
                win.setOpaque_(False)
                win.setBackgroundColor_(NSColor.clearColor())
                win.setHasShadow_(False)
                win.setIgnoresMouseEvents_(True)
                win.setHidesOnDeactivate_(False)
                try:
                    win.setCanHide_(False)
                except Exception:
                    pass
                try:
                    win.setBecomesKeyOnlyIfNeeded_(True)
                except Exception:
                    pass
                try:
                    from AppKit import NSWindowAnimationBehaviorNone  # type: ignore

                    win.setAnimationBehavior_(NSWindowAnimationBehaviorNone)
                except Exception:
                    pass
                # Floating-panel status resets the level; set the level after.
                win.setFloatingPanel_(True)
                view = NSImageView.alloc().initWithFrame_(
                    NSMakeRect(0, 0, size, size)
                )
                view.setImage_(image)
                try:
                    from AppKit import NSImageScaleProportionallyUpOrDown  # type: ignore

                    view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
                except Exception:
                    view.setImageScaling_(2)
                win.setContentView_(view)
                self._present_macos_panel(win)
                windows.append(win)
                views.append(view)
            except Exception as exc:
                _log(f"visual alert window failed: {exc}")
        self._mac_windows = windows
        self._mac_views = views
        if not windows:
            self._macos_end_overlay_policy()

    def _present_macos_panel(self, win) -> None:
        """Put one panel above the active app, including a fullscreen Space."""
        level = _macos_frontmost_level()
        win.setLevel_(level)
        win.setCollectionBehavior_(_macos_behavior_mask())
        win.setLevel_(level)
        win.setAlphaValue_(1.0)
        win.orderFrontRegardless()

    def _raise_macos_now(self) -> None:
        for win in list(self._mac_windows):
            try:
                self._present_macos_panel(win)
            except Exception:
                pass

    def _set_macos_frame(self, variant: str) -> None:
        _on_main(lambda: self._set_macos_frame_now(variant))

    def _set_macos_frame_now(self, variant: str) -> None:
        image = self._mac_images.get(variant)
        if image is None:
            return
        for view in list(self._mac_views):
            try:
                view.setImage_(image)
            except Exception:
                pass

    def _close_macos(self) -> None:
        _on_main_sync(self._close_macos_now)

    def _close_macos_now(self) -> None:
        windows = list(self._mac_windows)
        self._mac_windows = []
        self._mac_views = []
        for win in windows:
            try:
                win.orderOut_(None)
                win.close()
            except Exception:
                pass
        self._macos_end_overlay_policy()

    # ---- Windows -------------------------------------------------------

    def _monitor_rects(self) -> list[tuple[int, int, int, int]]:
        """Return (left, top, width, height) for each monitor."""
        try:
            import ctypes
            from ctypes import wintypes

            rects: list[tuple[int, int, int, int]] = []

            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", wintypes.LONG),
                    ("top", wintypes.LONG),
                    ("right", wintypes.LONG),
                    ("bottom", wintypes.LONG),
                ]

            MonitorEnumProc = ctypes.WINFUNCTYPE(
                wintypes.BOOL,
                wintypes.HMONITOR,
                wintypes.HDC,
                ctypes.POINTER(RECT),
                wintypes.LPARAM,
            )

            def _enum(hmon, hdc, lprect, lparam):  # noqa: ARG001
                r = lprect.contents
                rects.append(
                    (
                        int(r.left),
                        int(r.top),
                        int(r.right - r.left),
                        int(r.bottom - r.top),
                    )
                )
                return True

            cb = MonitorEnumProc(_enum)
            ctypes.windll.user32.EnumDisplayMonitors(0, 0, cb, 0)
            if rects:
                return rects
        except Exception:
            pass
        return [(0, 0, 800, 600)]

    def _open_windows_win(self, variant: str) -> None:
        frame = self._frames.get(variant)
        if frame is None:
            return
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            WS_POPUP = 0x80000000
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_TOPMOST = 0x00000008
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_NOACTIVATE = 0x08000000
            HWND_TOPMOST = -1
            SWP_NOACTIVATE = 0x0010
            SWP_SHOWWINDOW = 0x0040
            size = OVERLAY_SIZE
            hwnds: list[int] = []
            for left, top, width, height in self._monitor_rects():
                x = int(left + (width - size) / 2)
                y = int(top + (height - size) / 2)
                hwnd = user32.CreateWindowExW(
                    WS_EX_LAYERED
                    | WS_EX_TRANSPARENT
                    | WS_EX_TOPMOST
                    | WS_EX_TOOLWINDOW
                    | WS_EX_NOACTIVATE,
                    self._win_class,
                    "EyesRhythm Alert",
                    WS_POPUP,
                    x,
                    y,
                    size,
                    size,
                    0,
                    0,
                    kernel32.GetModuleHandleW(None),
                    None,
                )
                if not hwnd:
                    continue
                hwnd = int(hwnd)
                self._paint_layered(hwnd, frame)
                self._raise_windows_hwnd(hwnd, x, y, size, size)
                hwnds.append(hwnd)
            self._win_hwnds = hwnds
        except Exception:
            self._win_hwnds = []

    def _raise_windows_hwnd(
        self,
        hwnd: int,
        x: int | None = None,
        y: int | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> None:
        """Force a layered popup above every other top-level window."""
        try:
            import ctypes

            user32 = ctypes.windll.user32
            HWND_TOPMOST = -1
            SWP_NOACTIVATE = 0x0010
            SWP_SHOWWINDOW = 0x0040
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            flags = SWP_NOACTIVATE | SWP_SHOWWINDOW
            if x is None or y is None or width is None or height is None:
                flags |= SWP_NOMOVE | SWP_NOSIZE
                x = y = width = height = 0
            # Show without activating, then pin above maximized windows.
            # SW_SHOWNOACTIVATE is 4. A popup left hidden until the next
            # focus change is exactly the "only visible after switching" bug.
            user32.ShowWindow(hwnd, 4)
            user32.SetWindowPos(
                hwnd, HWND_TOPMOST, int(x), int(y), int(width), int(height), flags
            )
            user32.SetWindowPos(
                hwnd,
                HWND_TOPMOST,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
            )
        except Exception:
            pass

    def _set_windows_frame(self, variant: str) -> None:
        frame = self._frames.get(variant)
        if frame is None:
            return
        for hwnd in list(self._win_hwnds):
            try:
                self._paint_layered(hwnd, frame)
                self._raise_windows_hwnd(hwnd)
            except Exception:
                pass

    def _close_windows_win(self) -> None:
        hwnds = list(self._win_hwnds)
        self._win_hwnds = []
        if not hwnds:
            return
        try:
            import ctypes

            for hwnd in hwnds:
                try:
                    ctypes.windll.user32.DestroyWindow(hwnd)
                except Exception:
                    pass
        except Exception:
            pass

    def _paint_layered(self, hwnd: int, bgra: np.ndarray) -> None:
        """Blit a BGRA image onto a layered window (per-pixel alpha)."""
        import ctypes
        from ctypes import wintypes

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

        class POINT(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

        class SIZE(ctypes.Structure):
            _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]

        class BLENDFUNCTION(ctypes.Structure):
            _fields_ = [
                ("BlendOp", wintypes.BYTE),
                ("BlendFlags", wintypes.BYTE),
                ("SourceConstantAlpha", wintypes.BYTE),
                ("AlphaFormat", wintypes.BYTE),
            ]

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        gdi32.CreateCompatibleDC.restype = wintypes.HDC

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = h
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0

        bits = ctypes.c_void_p()
        hdc_screen = user32.GetDC(None)
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        hbm = gdi32.CreateDIBSection(
            hdc_screen, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0
        )
        if not hbm or not bits:
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(None, hdc_screen)
            return
        ctypes.memmove(bits, pixels.ctypes.data, int(pixels.nbytes))
        old = gdi32.SelectObject(hdc_mem, hbm)

        blend = BLENDFUNCTION()
        blend.BlendOp = 0  # AC_SRC_OVER
        blend.BlendFlags = 0
        blend.SourceConstantAlpha = 255
        blend.AlphaFormat = 1  # AC_SRC_ALPHA

        size = SIZE(w, h)
        src = POINT(0, 0)
        # Keep current window position; only refresh pixels.
        pt = POINT(0, 0)
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        pt.x = rect.left
        pt.y = rect.top

        user32.UpdateLayeredWindow(
            hwnd,
            hdc_screen,
            ctypes.byref(pt),
            ctypes.byref(size),
            hdc_mem,
            ctypes.byref(src),
            0,
            ctypes.byref(blend),
            0x00000002,  # ULW_ALPHA
        )

        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteObject(hbm)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)


def _pump_windows_messages() -> None:
    """Let a layered popup created on this thread actually map on screen."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        msg = wintypes.MSG()
        PM_REMOVE = 0x0001
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    except Exception:
        pass


def bind_alert_overlay(engine: EyeRhythmEngine) -> AlertOverlayCue:
    """Drive the multi-monitor Visual cue from the Alert metronome."""
    cue = AlertOverlayCue()
    cue.start()

    def on_visual(active: bool) -> None:
        # The orange glow is the latch. Hide the icon the moment that ends.
        # Shows come from the orange-peak metronome, not from this edge:
        # a one-poll flicker turns the flag on before the app looks orange
        # and used to flash the icon anyway.
        if not active:
            cue.hide()

    engine.on_visual_alert_change(on_visual)
    engine.on_visual_alert_cue(cue.pulse)
    return cue
