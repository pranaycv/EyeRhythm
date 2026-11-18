"""
EyesRhythm main entry
====================

Starts:
1. The processing engine (camera + blink detection + daily stats)
2. A local HTTP/WebSocket API for the React UI
3. A desktop window (pywebview) loading the UI — or the system browser
   if ``--browser`` is passed / pywebview is unavailable

Restores last session preferences (avatar, window size, Beep/Alert/On top).
"""

from __future__ import annotations

import argparse
import platform
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

# Make sure Python can find the rest of this project (`app/`, `blink_core/`)
# even when this file is launched in unusual ways (packaged .app / .exe).
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import license as app_license  # noqa: E402
from app import preferences  # noqa: E402
from app.engine import (  # noqa: E402
    EyeRhythmEngine,
    MODEL_PATH,
    describe_cameras,
    resolve_startup_camera,
)
from app.server import run_server, UI_DIST  # noqa: E402
from app.alert_overlay import bind_alert_overlay  # noqa: E402
from app.tray_icon import bind_tray_icon  # noqa: E402

# The dashboard talks to this machine only (never the internet).
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8742
WINDOW_TITLE = "EyesRhythm"
DEFAULT_WIDTH = preferences.DEFAULT_WIDTH
DEFAULT_HEIGHT = preferences.DEFAULT_HEIGHT
MIN_WIDTH = preferences.MIN_WIDTH
MIN_HEIGHT = preferences.MIN_HEIGHT


class _WindowApi:
    """
    JS bridge methods exposed to the React UI via ``window.pywebview.api``.
    """

    def __init__(self) -> None:
        # The real desktop window is attached later, after pywebview creates it.
        self._window = None
        self._practice_fullscreen = False
        # Remember whether we used true fullscreen vs. “maximize”, so Exit
        # can undo the same action (toggle_fullscreen vs. just resizing).
        self._used_native_fullscreen = False
        # Window size before Practice, so we can restore it afterwards.
        self._pre_practice_geometry: tuple[int, int] | None = None
        # Splash may grow a compact window; do not persist that size.
        self._splash_active = False
        self._pre_splash_geometry: tuple[int, int] | None = None
        # Side dock is session-only; every launch is More + Undock.
        self._dock_active = False
        self._undocked_width = preferences.DEFAULT_WIDTH

    def bind(self, window) -> None:
        """Remember the live window object so later JS calls can resize it."""
        self._window = window

    def set_camera_layout(self, expanded: bool) -> None:
        # No-op: Lens avatar replaces the expandable camera panel.
        return None

    def set_compact_layout(self, compact: bool, height=None) -> None:
        """
        Less / More only change height. Left-panel width stays the default
        GUI width (or 2× that width if the dock is open).
        """
        window = self._window
        want = bool(compact)
        try:
            preferences.set_compact(want)
        except Exception:
            pass
        if window is None:
            return
        target_w = (
            preferences.DEFAULT_WIDTH * 2
            if self._dock_active
            else preferences.DEFAULT_WIDTH
        )
        fit_h = None
        if height is not None:
            try:
                fit_h = int(round(float(height)))
            except (TypeError, ValueError):
                fit_h = None
        try:
            if want:
                target_h = (
                    fit_h
                    if fit_h is not None and fit_h >= preferences.MIN_HEIGHT
                    else preferences.COMPACT_HEIGHT
                )
            else:
                target_h = preferences.DEFAULT_HEIGHT
                if fit_h is not None:
                    target_h = max(target_h, fit_h)
            self._resize_if_needed(target_w, min(int(target_h), 1400))
        except Exception as exc:
            _log(f"compact layout toggle failed: {exc}")

    def set_splash_layout(self, enabled: bool) -> None:
        """Keep the splash on the default GUI size (More + Undock)."""
        window = self._window
        self._splash_active = bool(enabled)
        if window is None:
            return
        try:
            w, h = preferences.default_gui_size()
            self._resize_if_needed(w, h)
        except Exception as splash_exc:
            _log(f"splash layout failed: {splash_exc}")

    def _resize_if_needed(self, width: int, height: int) -> None:
        """
        Resize the native window only when the size actually changes.

        pywebview's Cocoa backend redraws WKWebView from scratch on
        ``resize`` — a no-op resize is a blank-white flash.
        """
        window = self._window
        if window is None:
            return
        width, height = int(width), int(height)
        try:
            cur_w = int(getattr(window, "width", 0) or 0)
            cur_h = int(getattr(window, "height", 0) or 0)
            if cur_w == width and cur_h == height:
                return
        except Exception:
            pass
        window.resize(width, height)

    def set_dock_layout(self, docked: bool, height=None) -> None:
        """
        Dock → 2× default GUI width; height grows if the left column needs it.
        Undock → default GUI width and height.
        """
        window = self._window
        want = bool(docked)
        try:
            preferences.save(dock=want)
            if want:
                preferences.set_compact(False)
        except Exception:
            pass
        self._dock_active = want
        if window is None:
            return
        fit_h = None
        if height is not None:
            try:
                fit_h = int(round(float(height)))
            except (TypeError, ValueError):
                fit_h = None
        try:
            if want:
                w, h = preferences.docked_gui_size()
                if fit_h is not None:
                    h = max(h, fit_h)
            else:
                w, h = preferences.default_gui_size()
                if fit_h is not None:
                    h = max(h, fit_h)
            self._resize_if_needed(w, min(int(h), 1400))
        except Exception as exc:
            _log(f"dock layout failed: {exc}")

    def set_practice_fullscreen(self, enabled: bool) -> None:
        """
        Expand the desktop window for Practice mode; restore on exit.

        Prefer native fullscreen when available; otherwise maximize.
        Idempotent — safe to call repeatedly from the React overlay.
        """
        window = self._window
        if window is None:
            return
        want = bool(enabled)
        if want == self._practice_fullscreen:
            return
        try:
            if want:
                # Remember the current size so leaving Practice can restore it.
                try:
                    w = int(getattr(window, "width", 0) or 0)
                    h = int(getattr(window, "height", 0) or 0)
                    if w > 0 and h > 0:
                        self._pre_practice_geometry = (w, h)
                except Exception:
                    self._pre_practice_geometry = None
                self._used_native_fullscreen = False
                try:
                    window.toggle_fullscreen()
                    self._used_native_fullscreen = True
                except Exception:
                    # Some platforms have no true fullscreen — fill the screen.
                    try:
                        window.maximize()
                    except Exception:
                        pass
                self._practice_fullscreen = True
            else:
                if self._used_native_fullscreen:
                    try:
                        window.toggle_fullscreen()
                    except Exception:
                        pass
                geom = self._pre_practice_geometry
                if geom is not None:
                    try:
                        window.resize(geom[0], geom[1])
                    except Exception:
                        pass
                self._pre_practice_geometry = None
                self._used_native_fullscreen = False
                self._practice_fullscreen = False
        except Exception as exc:
            _log(f"practice fullscreen toggle failed: {exc}")


def _log(msg: str) -> None:
    """Print a short diagnostic line. Never crash if the terminal is gone."""
    try:
        print(f"[EyesRhythm] {msg}", file=sys.stderr, flush=True)
    except Exception:
        pass


def _alert(title: str, text: str) -> None:
    """Windows MessageBox so a frozen exe is not silent. No-op elsewhere."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)
    except Exception:
        pass


def _fatal(msg: str, code: int = 1) -> None:
    """Log, tell the user, then exit. Used when the window never appears."""
    _log(msg)
    _alert("EyesRhythm", msg)
    sys.exit(code)


def _port_free(host: str, port: int) -> bool:
    """True if nothing else is already using this local address + port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _find_port(host: str, preferred: int) -> int:
    """
    Use the preferred port if free; otherwise try the next few numbers.

    Two EyesRhythm windows (or another app) may already own 8742 — we still
    want to launch instead of failing.
    """
    if _port_free(host, preferred):
        return preferred
    for p in range(preferred + 1, preferred + 40):
        if _port_free(host, p):
            return p
    raise RuntimeError("No free localhost port for EyesRhythm UI server")


def _wait_for_server(host: str, port: int, timeout: float = 15.0) -> bool:
    """Keep poking the local API until it answers, or we give up."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.4):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _startup_console_checks(camera_index: int) -> list[str]:
    """Lightweight checks printed to stderr (no OpenCV window)."""
    issues: list[str] = []
    if sys.version_info < (3, 10):
        issues.append("Python 3.10+ is required")
    for mod in ("cv2", "numpy", "mediapipe", "fastapi", "uvicorn"):
        try:
            __import__(mod)
        except Exception:
            issues.append(f"Missing dependency: {mod}")
    if not Path(MODEL_PATH).is_file():
        issues.append(f"Model not found: {MODEL_PATH}")
    if not (UI_DIST / "index.html").is_file():
        issues.append(
            "React UI not built. Run:  cd frontend && npm install && npm run build"
        )
    return issues


def _window_size_from_prefs() -> tuple[int, int]:
    """Always the default GUI (More + Undock)."""
    return preferences.default_gui_size()


def _save_window_size(window) -> None:
    """Layout size is fixed (default GUI / 2× dock). Do not persist resizes."""
    return None


def _app_icon_path() -> Path | None:
    """Brand mark used as the Dock / taskbar icon when running from source."""
    for name in ("app_icon.png", "EyeRhythm.icns", "EyeRhythm.ico", "logo.svg"):
        path = ROOT / "assets" / name
        if path.is_file():
            return path
    return None


def _apply_app_icon() -> None:
    """
    Running from source is a Python process, so macOS shows the Python icon
    in the Dock unless we set the application image ourselves. The packaged
    .app already has this icon in Info.plist.
    """
    icon = _app_icon_path()
    if icon is None:
        return
    if platform.system() != "Darwin":
        return
    try:
        from AppKit import NSApplication, NSImage  # type: ignore

        image = NSImage.alloc().initWithContentsOfFile_(str(icon))
        if image is None:
            return
        NSApplication.sharedApplication().setApplicationIconImage_(image)
    except Exception as exc:
        _log(f"could not set app icon: {exc}")


def _open_webview(
    url: str, engine: EyeRhythmEngine, *, start_engine: bool = True
) -> None:
    """Native desktop window via pywebview (macOS + Windows + Linux)."""
    import webview

    _apply_app_icon()

    width, height = _window_size_from_prefs()
    prefs = preferences.load()
    ontop = bool(prefs.get("ontop", False))
    _log(f"restoring window {width}x{height} ontop={ontop}")

    # js_api lets the React UI call back into Python (resize / fullscreen).
    api = _WindowApi()
    window = webview.create_window(
        WINDOW_TITLE,
        url,
        width=width,
        height=height,
        min_size=(MIN_WIDTH, MIN_HEIGHT),
        resizable=True,
        confirm_close=False,
        background_color="#E8F0F8",
        js_api=api,
        on_top=ontop,
    )
    api.bind(window)

    def apply_ontop(flag: bool) -> None:
        """Keep the window above others when the user turns On top on."""
        try:
            window.on_top = flag
        except Exception:
            pass

    engine.on_ontop_change(apply_ontop)
    tray = bind_tray_icon(window, engine)
    overlay = bind_alert_overlay(engine)

    def on_resized():
        if api._splash_active:
            return
        _save_window_size(window)

    def on_closing():
        # Remember size and switches so the next launch looks the same.
        _save_window_size(window)
        # Keep toggles in sync with engine (already auto-saved on change).
        try:
            preferences.set_toggles(**engine.get_toggles())
        except Exception:
            pass
        try:
            tray.stop()
        except Exception:
            pass
        try:
            overlay.stop()
        except Exception:
            pass

    # pywebview event hooks (versions differ slightly — guard each).
    try:
        window.events.resized += on_resized
    except Exception:
        pass
    try:
        window.events.closing += on_closing
    except Exception:
        try:
            window.events.closed += on_closing
        except Exception:
            pass

    def _boot() -> None:
        # Start the camera only after the native window has painted.
        # Opening AVFoundation / creating MediaPipe's OpenGL context on
        # the Cocoa thread before WKWebView's first frame leaves a blank
        # white window (worse at login, when the GPU is still coming up).
        if not start_engine:
            return
        if app_license.is_required() and not app_license.is_activated():
            return
        if engine.is_awaiting_camera_pick():
            return
        try:
            engine.start()
        except Exception as exc:
            _log(f"engine start failed: {exc}")

    started = {"done": False}
    fallback = {"timer": None}

    def _start_once() -> None:
        if started["done"]:
            return
        started["done"] = True
        timer = fallback["timer"]
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass
        _boot()

    try:
        window.events.loaded += _start_once
    except Exception:
        pass

    def _gui_initialized() -> None:
        # `loaded` is the right moment (page has painted). If that event
        # is missing, wait a beat so WKWebView can draw before MediaPipe
        # creates its OpenGL context.
        if started["done"]:
            return
        timer = threading.Timer(1.0, _start_once)
        timer.daemon = True
        fallback["timer"] = timer
        timer.start()

    # Block until the window is closed.
    start_kwargs = {"debug": False}
    if sys.platform == "win32":
        start_kwargs["gui"] = "edgechromium"
    webview.start(_gui_initialized, **start_kwargs)
    # Final save after start() returns (window gone).
    _save_window_size(window)


def main() -> None:
    """
    Boot sequence (plain English):

    1. Check Python, libraries, the face model, and the built dashboard.
    2. Pick a free localhost port.
    3. Start the camera/blink engine in the background.
    4. Start the tiny local web server the dashboard talks to.
    5. Open a desktop window (or the browser) pointing at that server.
    6. On quit: stop the camera loop and save today’s stats.
    """
    parser = argparse.ArgumentParser(
        description="EyesRhythm — blink rhythm companion (React UI)")
    parser.add_argument("--camera", type=int, default=None,
                        help="webcam index (skip picker; default: ask if several)")
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help="local API host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"local API port (default {DEFAULT_PORT})")
    parser.add_argument("--browser", action="store_true",
                        help="open system browser instead of desktop window")
    parser.add_argument("--skip-checks", action="store_true",
                        help="skip dependency / build checks")
    parser.add_argument("--no-engine", action="store_true",
                        help="UI only (dev; no camera) — not for normal use")
    parser.add_argument(
        "--require-license",
        action="store_true",
        help="ask for a Dodo Payments key even when running from source",
    )
    parser.add_argument(
        "--skip-license",
        action="store_true",
        help="skip license activation even in a packaged build",
    )
    args = parser.parse_args()
    if args.skip_license:
        app_license.set_enforcement(False)
    elif args.require_license:
        app_license.set_enforcement(True)

    if not args.skip_checks:
        issues = _startup_console_checks(args.camera)
        for msg in issues:
            _log(f"CHECK: {msg}")
        hard = [m for m in issues if m.startswith("Python")
                or m.startswith("Model") or m.startswith("React")]
        if hard:
            _fatal("\n".join(hard))

    port = args.port
    try:
        if not _port_free(args.host, port):
            _log(
                f"port {port} is already in use — another EyesRhythm window "
                f"may still be running. Quit every copy, then run again."
            )
            port = _find_port(args.host, port)
            _log(f"using port {port} instead")
    except RuntimeError as exc:
        _fatal(str(exc))

    # The engine owns the webcam and all blink numbers. --no-engine is a
    # developer shortcut: show the dashboard without opening the camera.
    # Probe cameras *before* the native window exists (AVFoundation would
    # otherwise stall WKWebView). Ask in the splash only when more than one
    # desk camera exists and none has been chosen yet.
    prefs = preferences.load()
    cameras = describe_cameras()
    avail = [int(c["index"]) for c in cameras]
    try:
        saved = int(prefs.get("camera_index", -1))
    except (TypeError, ValueError):
        saved = -1

    initial, pick_required, hybrid = resolve_startup_camera(
        avail, saved, args.camera, str(prefs.get("camera_mode", "single"))
    )

    engine = EyeRhythmEngine(camera_index=initial, hybrid=hybrid)
    if pick_required:
        engine.set_awaiting_camera_pick(True)
        _log(f"multiple cameras detected {avail} — pick one in the app")
    elif hybrid:
        preferences.save(camera_index=initial, camera_mode="hybrid")
        _log(f"hybrid cameras {avail} ready")
    elif avail:
        preferences.save(camera_index=initial, camera_mode="single")
        _log(f"camera {initial} ready ({len(avail)} found)")

    # Local HTTP + WebSocket for the React UI (same machine only).
    run_server(engine, host=args.host, port=port)
    url = f"http://{args.host}:{port}/"
    _log(f"UI at {url}")
    _log(f"OS={platform.system()}  model={MODEL_PATH}")
    _log(f"prefs: {preferences.load()}")

    if not _wait_for_server(args.host, port):
        engine.stop()
        _fatal(
            "EyesRhythm’s local UI server did not start.\n\n"
            "If this zip came from email or a browser download, right-click "
            "the zip → Properties → Unblock, extract again, and retry."
        )

    use_browser = args.browser
    if not use_browser:
        try:
            import webview  # noqa: F401
        except ImportError:
            _log("pywebview not installed — falling back to browser")
            use_browser = True

    def _run_browser() -> None:
        # Developer / fallback path: the OS browser, not a native window.
        webbrowser.open(url)
        _log("running in browser mode — press Ctrl+C to quit")
        while True:
            time.sleep(0.5)

    def _start_engine_if_licensed() -> None:
        if args.no_engine:
            return
        if app_license.is_required() and not app_license.is_activated():
            _log("camera waiting — activate this copy in the app first")
            return
        if engine.is_awaiting_camera_pick():
            return
        try:
            engine.start()
        except Exception as exc:
            _log(f"engine start failed: {exc}")

    try:
        if use_browser:
            _start_engine_if_licensed()
            _run_browser()
        else:
            # Normal path: a compact desktop window wrapping the same UI.
            # On Windows a freeze can miss WebView2 / pythonnet; keep the
            # dashboard reachable in the system browser instead of dying.
            try:
                # Do not open the webcam until the native window has painted.
                # Camera + MediaPipe GL on the Cocoa thread before first
                # paint is the blank-white-window bug at login.
                _open_webview(url, engine, start_engine=not args.no_engine)
            except Exception as exc:
                _log(f"desktop window failed ({exc}) — falling back to browser")
                _alert(
                    "EyesRhythm",
                    "The EyesRhythm desktop window could not open, so the "
                    "dashboard will open in your browser instead.\n\n"
                    "If this copy came from email, WhatsApp, or a download, "
                    "Windows may have blocked the files. Double-click "
                    "Unblock-and-run.bat in the same folder as EyesRhythm.exe "
                    "(or right-click the zip → Properties → Unblock, extract "
                    "again). Then install Microsoft Edge WebView2 Runtime if "
                    "Windows still cannot open a window.\n\n"
                    f"({exc})",
                )
                _start_engine_if_licensed()
                _run_browser()
    except KeyboardInterrupt:
        _log("interrupted")
    finally:
        # Always stop the camera loop, even if the window closed abruptly.
        engine.stop()
        _log("shutdown complete")


if __name__ == "__main__":
    main()
