# EyesRhythm — Blink companion

**Your eyes work hard for you. EyesRhythm helps them work longer.**

<p align="center">
  <img src="website/public/images/guide/dashboard.jpg" alt="EyesRhythm dashboard with today’s blink count, ocular rhythm, screen time, and the face companion" width="720">
</p>

Every day millions of knowledge workers, students and creators spend hours staring at screens. Research from the American Academy of Ophthalmology shows that the spontaneous blink rate drops by **half or more** during computer use. Fewer blinks → dry eyes, fatigue, blurred vision, and the classic symptoms of Computer Vision Syndrome.

EyesRhythm is a lightweight, **on-device** companion that quietly watches your blink rate through the built-in webcam and gently nudges you back toward a healthy rhythm (~15–20 blinks per minute). No accounts, no cloud, no analytics, no video ever leaves your machine.

Official **macOS and Windows** copies are free to download at [eyesrhythm.com](https://eyesrhythm.com). There is no account and no license key. The app stays on your computer.

The source in this tree is under the [PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0). You may use, modify, and share it for a noncommercial purpose, provided you keep `LICENSE`, the required notice below, and the third-party notices in `THIRD_PARTY_NOTICES.md`. Commercial use is not included. Third-party libraries keep their own licenses.

Required Notice: Copyright Pranay Soni (https://github.com/pranaycv)

> **Privacy first.** EyesRhythm runs on this computer. Camera frames are not uploaded. Local JSON under `~/.eyerhythm_blinkcounter/` holds today’s blink count and screen-time minutes (resets at midnight), plus an optional research log of inter-blink interval stats per screen-time hour.

---

## What you see

A compact **React dashboard** (not full-screen by default):

| Element | Meaning |
|--------|---------|
| **Live blink rate** | Continuous blinks/min over the recent window |
| **Average rate** | Daily average = total blinks ÷ face-time minutes |
| **Screen time** | How long a face has been in front of the screen today |
| **FPS** | Camera pipeline throughput |
| **Regularity** | How steady your inter-blink gaps are (0–100) |
| **Reserved cards** | Placeholders for future metrics |
| **Camera panel** | Collapsed by default; expand when you want a preview |

Regularity is computed as:

```text
CV = (std of inter-blink gaps) ÷ (mean gap)
Score = max(0, 100 − 100 × CV)
```

---

## Quick start

### Prerequisites

* **Python 3.10–3.12** (3.11 recommended)  
* **Node.js 18+** (to build the UI once)  
* Webcam + OS camera permission  

### macOS / Linux

```bash
./for_mac/setup.sh                 # venv + Python deps + React build
./for_mac/run.sh                   # launches the desktop window
./for_mac/install_autostart.sh     # optional: open at every login
```

First launch: allow **Camera** permission  
(System Settings → Privacy & Security → Camera).

To stop opening at login: `./for_mac/uninstall_autostart.sh`.

### Windows

Share **`EyesRhythm-Windows.zip`** (built by GitHub Actions or `for_windows/for_developers/build_exe.bat`). Recipients unzip and double-click `EyesRhythm.exe` — no Python or Node.

All Windows helpers live under **[`for_windows/`](for_windows/)**.

* **Share / use the app** → unzip `EyesRhythm-Windows.zip` and run `EyesRhythm.exe`. Details in [`for_windows/`](for_windows/).
* **From source** → [`for_windows/just_use_for_nondevelopers/`](for_windows/just_use_for_nondevelopers/) (needs Python once).
* **Rebuild the zip / installer** → [`for_windows/for_developers/`](for_windows/for_developers/) or **Actions → Build Windows app**.

### Developer (UI hot reload)

```bash
# Terminal 1 — engine + API
source EyeRhythm/bin/activate   # or your venv
python -m app.main --browser

# Terminal 2 — Vite
cd frontend && npm install && npm run dev
```

Open the Vite URL (proxies `/api` and `/ws` to the Python server).

---

## Keyboard shortcuts (dashboard focused)

| Key | Action |
|-----|--------|
| **B** | Toggle beep on every counted blink |
| **A** | Toggle alert (beeps after ORI is below 50 for 3 min in the last 5 min) |
| **D** | More / Less (compact companion vs full dashboard) |
| **W** | Settings / Hide settings (Reports beside the companion) |
| **C** | Switch Lens camera preview |

Close the window to quit (or Ctrl+C in browser mode).

---

## Architecture

* **`blink_core/`** — blink detection + daily stats (platform-independent)  
* **`app/`** — camera engine, local FastAPI server, desktop window  
* **`frontend/`** — React + TypeScript + Vite UI  

The default window is small and resizable. Maximising scales typography and layout with the viewport. Works on **macOS and Windows** (Linux supported via browser or pywebview).

---

## Libraries and licenses

EyesRhythm’s own code is under the PolyForm Noncommercial License 1.0.0.
Third-party libraries keep their own licenses. Those terms are what allow
the packaged app to include them. None of them re-license EyesRhythm’s code.

See **[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)** (also in
Settings → About, and inside every freeze). Full texts live in `licenses/`.

| Component | License | In the app |
|-----------|---------|------------|
| EyesRhythm source | PolyForm Noncommercial 1.0.0 | Yes |
| MediaPipe + Face Landmarker model | Apache 2.0 | Yes |
| OpenCV | Apache 2.0 | Yes |
| FFmpeg (inside the OpenCV wheel) | LGPLv2.1 | Yes — replaceable library, notice required |
| NumPy | BSD-3-Clause | Yes |
| FastAPI, Pydantic, React, Vite | MIT | Yes |
| Uvicorn, pywebview | BSD-3-Clause | Yes |
| Inter font | SIL OFL 1.1 | Bundled with the app, not sold alone |
| PyInstaller | GPL-2.0 with freeze exception | Build tool only |

Packaged copies include `LICENSE`, `THIRD_PARTY_NOTICES.md`, and `licenses/`.
FFmpeg remains a separate shared library (typically
`opencv_videoio_ffmpeg*.dll` on Windows) so it can be substituted, as LGPL
asks.

The alert tone (`alert.mp3`) is the Sound-mode cue shipped with EyesRhythm. Practice
quotations (`quotes.json`) are from the original list, limited to
public-domain authors.

---

## Project layout

```
EyeRhythm/
├── app/                 # engine + local API + window
├── blink_core/          # blink detection & screen-time
├── frontend/            # React UI (source + dist/)
├── models/              # MediaPipe face landmarker
├── assets/              # logo + brand assets + Inter (OFL)
├── licenses/            # Apache 2.0, LGPLv2.1, SIL OFL texts
├── THIRD_PARTY_NOTICES.md
├── docs/CODE_GUIDE.md   # map of the codebase
├── for_mac/             # setup / run / login-start helpers
├── for_windows/         # launchers + packaging
├── requirements.txt
└── run_app.py           # packaging entry-point
```

### Contributors

Start with **[`docs/CODE_GUIDE.md`](docs/CODE_GUIDE.md)**, then `app/engine.py` and `blink_core/detector.py`.

---

## Feedback & sharing

If the counter feels off for you, or you have ideas for the reserved metric cards, please write to [hello@eyesrhythm.com](mailto:hello@eyesrhythm.com). If EyesRhythm is useful, share it — a regular blink habit is one of the easiest gifts we can give our eyes.

EyesRhythm is a wellness companion, not a medical device. It is not intended to diagnose, treat, cure, or prevent disease.

Official copies: [eyesrhythm.com](https://eyesrhythm.com). Source: [github.com/pranaycv/EyeRhythm](https://github.com/pranaycv/EyeRhythm). Source terms: [`LICENSE`](LICENSE). Third-party terms: [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
