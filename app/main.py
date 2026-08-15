"""
EyeRhythm main entry
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
import time
import webbrowser
from pathlib import Path

# Project root on import path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import preferences  # noqa: E402
from app.engine import EyeRhythmEngine, MODEL_PATH  # noqa: E402
from app.server import run_server, UI_DIST  # noqa: E402

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8742
WINDOW_TITLE = "EyeRhythm"
DEFAULT_WIDTH = preferences.DEFAULT_WIDTH
DEFAULT_HEIGHT = preferences.DEFAULT_HEIGHT
MIN_WIDTH = preferences.MIN_WIDTH
MIN_HEIGHT = preferences.MIN_HEIGHT


class _WindowApi:
    """
    JS bridge methods exposed to the React UI via ``window.pywebview.api``.
    """

    def __init__(self, ui_url: str = "") -> None:
        self._window = None
        self._ui_url = ui_url.rstrip("/") + "/"
        self._practice_fullscreen = False
        self._used_native_fullscreen = False
        self._pre_practice_geometry: tuple[int, int] | None = None
        self._sweep_overlays: list = []
        self._sweep_overlays_on = False

    def bind(self, window) -> None:
        self._window = window

    def set_ui_url(self, url: str) -> None:
        self._ui_url = str(url).rstrip("/") + "/"

    def set_camera_layout(self, expanded: bool) -> None:
        # No-op: Lens avatar replaces the expandable camera panel.
        return None

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

    def set_sweep_overlays(self, enabled: bool) -> None:
        """
        Open a fullscreen sweep dialog on every attached display.

        The main window already covers the display it sits on; extra
        frameless windows cover the remaining screens so the user can
        look at each monitor's corners / edges.
        """
        want = bool(enabled)
        if want == self._sweep_overlays_on and (want is False or self._sweep_overlays):
            if want:
                return
        if not want:
            self._close_sweep_overlays()
            return
        self._open_sweep_overlays()

    def _open_sweep_overlays(self) -> None:
        self._close_sweep_overlays()
        try:
            import webview
        except ImportError:
            return
        try:
            screens = list(webview.screens or [])
        except Exception as exc:
            _log(f"could not list displays for sweep: {exc}")
            return
        if len(screens) <= 1:
            self._sweep_overlays_on = True
            _log("sweep overlays: single display — main window only")
            return

        main_screen = _screen_for_window(self._window, screens)
        created = 0
        for screen in screens:
            if main_screen is not None and _same_screen(screen, main_screen):
                continue
            place = _place_vs_main(screen, main_screen)
            overlay_url = f"{self._ui_url}?sweepOverlay=1&place={place}"
            try:
                win = webview.create_window(
                    "EyeRhythm Sweep",
                    overlay_url,
                    screen=screen,
                    width=int(getattr(screen, "width", 800) or 800),
                    height=int(getattr(screen, "height", 600) or 600),
                    fullscreen=True,
                    frameless=True,
                    easy_drag=False,
                    on_top=True,
                    focus=False,
                    resizable=False,
                    confirm_close=False,
                    background_color="#2B2E33",
                )
                if win is not None:
                    self._sweep_overlays.append(win)
                    created += 1
            except Exception as exc:
                _log(f"sweep overlay on {screen} failed: {exc}")
        self._sweep_overlays_on = True
        _log(f"sweep overlays: {created} extra display(s) of {len(screens)}")

    def _close_sweep_overlays(self) -> None:
        for win in list(self._sweep_overlays):
            try:
                win.destroy()
            except Exception:
                try:
                    win.hide()
                except Exception:
                    pass
        self._sweep_overlays.clear()
        self._sweep_overlays_on = False


def _place_vs_main(screen, main) -> str:
    """
    Which side of the main/camera display this screen sits on.

    Left/right use x (same on every OS). Above/below flips on macOS
    because Cocoa screen y grows upward.
    """
    if main is None:
        return "other"
    try:
        scx = float(screen.x) + float(screen.width) / 2.0
        scy = float(screen.y) + float(screen.height) / 2.0
        mcx = float(main.x) + float(main.width) / 2.0
        mcy = float(main.y) + float(main.height) / 2.0
    except Exception:
        return "other"
    dx = scx - mcx
    dy = scy - mcy
    if abs(dx) < 8 and abs(dy) < 8:
        return "other"
    if abs(dx) >= abs(dy):
        return "right" if dx > 0 else "left"
    # Windows / most platforms: y grows down. Cocoa: y grows up.
    if sys.platform == "darwin":
        return "above" if dy > 0 else "below"
    return "below" if dy > 0 else "above"


def _same_screen(a, b) -> bool:
    if a is b:
        return True
    try:
        return (
            int(a.x) == int(b.x)
            and int(a.y) == int(b.y)
            and int(a.width) == int(b.width)
            and int(a.height) == int(b.height)
        )
    except Exception:
        return False


def _screen_for_window(window, screens: list):
    """Which attached display currently holds the main window."""
    if not screens:
        return None
    if window is None:
        return screens[0]
    attached = getattr(window, "screen", None)
    if attached is not None:
        for s in screens:
            if _same_screen(s, attached):
                return s
        return attached
    try:
        wx = getattr(window, "x", None)
        wy = getattr(window, "y", None)
        ww = int(getattr(window, "width", 0) or 0)
        wh = int(getattr(window, "height", 0) or 0)
        if wx is None or wy is None:
            return screens[0]
        cx = int(wx) + max(1, ww) // 2
        cy = int(wy) + max(1, wh) // 2
    except Exception:
        return screens[0]
    for s in screens:
        try:
            if (int(s.x) <= cx < int(s.x) + int(s.width)
                    and int(s.y) <= cy < int(s.y) + int(s.height)):
                return s
        except Exception:
            continue
    return screens[0]


def _log(msg: str) -> None:
    try:
        print(f"[EyeRhythm] {msg}", file=sys.stderr, flush=True)
    except Exception:
        pass


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _find_port(host: str, preferred: int) -> int:
    if _port_free(host, preferred):
        return preferred
    for p in range(preferred + 1, preferred + 40):
        if _port_free(host, p):
            return p
    raise RuntimeError("No free localhost port for EyeRhythm UI server")


def _wait_for_server(host: str, port: int, timeout: float = 15.0) -> bool:
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
    try:
        return preferences.get_window_size()
    except Exception:
        return DEFAULT_WIDTH, DEFAULT_HEIGHT


def _save_window_size(window) -> None:
    """Best-effort persist of current window dimensions."""
    try:
        w = int(getattr(window, "width", 0) or 0)
        h = int(getattr(window, "height", 0) or 0)
        if w >= MIN_WIDTH and h >= MIN_HEIGHT:
            preferences.set_window_size(w, h)
            _log(f"saved window size {w}x{h}")
    except Exception as exc:
        _log(f"could not save window size: {exc}")


def _open_webview(url: str, engine: EyeRhythmEngine) -> None:
    """Native desktop window via pywebview (macOS + Windows + Linux)."""
    import webview

    width, height = _window_size_from_prefs()
    prefs = preferences.load()
    ontop = bool(prefs.get("ontop", False))
    _log(f"restoring window {width}x{height} ontop={ontop}")

    api = _WindowApi(url)
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
        try:
            window.on_top = flag
        except Exception:
            pass

    engine.on_ontop_change(apply_ontop)

    def on_resized():
        _save_window_size(window)

    def on_closing():
        _save_window_size(window)
        try:
            api.set_sweep_overlays(False)
        except Exception:
            pass
        # Keep toggles in sync with engine (already auto-saved on change).
        try:
            preferences.set_toggles(**engine.get_toggles())
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

    # Block until the window is closed.
    webview.start(debug=False)
    # Final save after start() returns (window gone).
    _save_window_size(window)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="EyeRhythm — blink rhythm companion (React UI)")
    parser.add_argument("--camera", type=int, default=0,
                        help="webcam index (default 0)")
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
    args = parser.parse_args()

    if not args.skip_checks:
        issues = _startup_console_checks(args.camera)
        for msg in issues:
            _log(f"CHECK: {msg}")
        hard = [m for m in issues if m.startswith("Python")
                or m.startswith("Model") or m.startswith("React")]
        if hard:
            for m in hard:
                print(m, file=sys.stderr)
            sys.exit(1)

    port = args.port
    try:
        if not _port_free(args.host, port):
            port = _find_port(args.host, port)
            _log(f"port {args.port} busy — using {port}")
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)

    engine = EyeRhythmEngine(camera_index=args.camera)
    if not args.no_engine:
        engine.start()

    run_server(engine, host=args.host, port=port)
    url = f"http://{args.host}:{port}/"
    _log(f"UI at {url}")
    _log(f"OS={platform.system()}  model={MODEL_PATH}")
    _log(f"prefs: {preferences.load()}")

    if not _wait_for_server(args.host, port):
        _log("server failed to start in time")
        engine.stop()
        sys.exit(1)

    use_browser = args.browser
    if not use_browser:
        try:
            import webview  # noqa: F401
        except ImportError:
            _log("pywebview not installed — falling back to browser")
            use_browser = True

    try:
        if use_browser:
            webbrowser.open(url)
            _log("running in browser mode — press Ctrl+C to quit")
            while True:
                time.sleep(0.5)
        else:
            _open_webview(url, engine)
    except KeyboardInterrupt:
        _log("interrupted")
    finally:
        engine.stop()
        _log("shutdown complete")


if __name__ == "__main__":
    main()
