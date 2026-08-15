# EyeRhythm — Blink companion

**Your eyes work hard for you. EyeRhythm helps them work longer.**

Every day millions of knowledge workers, students and creators spend hours staring at screens. Research from the American Academy of Ophthalmology shows that the spontaneous blink rate drops by **half or more** during computer use. Fewer blinks → dry eyes, fatigue, blurred vision, and the classic symptoms of Computer Vision Syndrome.

EyeRhythm is a lightweight, **completely offline** open-source companion that quietly watches your blink rate through the built-in webcam and gently nudges you back toward a healthy rhythm (~15–20 blinks per minute). No accounts, no cloud, no analytics, no video ever leaves your machine.

> **Privacy first.** Once installed, EyeRhythm runs 100 % offline. Nothing is uploaded. Nothing is shared. Local JSON under `~/.eyerhythm_blinkcounter/` holds today’s blink count and screen-time minutes (resets at midnight), plus an optional research log of inter-blink interval stats per screen-time hour.

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
| **Look angle** | Face/eye axis vs camera (0° = looking straight in) |
| **Sweep** | Capture eight extreme look angles (4 corners + 4 edges) |
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
./for_mac/setup.sh          # venv + Python deps + React build
./for_mac/run.sh            # launches the desktop window
```

First launch: allow **Camera** permission  
(System Settings → Privacy & Security → Camera).

### Windows

All Windows helpers live under **[`for_windows/`](for_windows/)**.

* **Non-developers** → [`for_windows/just_use_for_nondevelopers/`](for_windows/just_use_for_nondevelopers/)  
  Install Python (tick “Add to PATH”) and Node.js once, then double-click `EyeRhythm_Launch.bat`.

* **Developers** → [`for_windows/for_developers/`](for_windows/for_developers/)  
  Build a standalone `.exe` after `frontend/dist` exists.

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
| **A** | Toggle low-rate alert (beeps while rate is very low) |
| **S** | Toggle look-angle sweep (eight extreme look angles) |
| **T** | Toggle always-on-top (desktop window) |
| **C** | Expand / collapse camera preview |

Close the window to quit (or Ctrl+C in browser mode).

---

## Architecture

* **`blink_core/`** — blink detection + daily stats (platform-independent)  
* **`app/`** — camera engine, local FastAPI server, desktop window  
* **`frontend/`** — React + TypeScript + Vite UI  

The default window is small and resizable. Maximising scales typography and layout with the viewport. Works on **macOS and Windows** (Linux supported via browser or pywebview).

---

## Libraries (open source)

* **MediaPipe** Face Landmarker — Apache 2.0  
* **OpenCV** — Apache 2.0  
* **NumPy** — BSD  
* **FastAPI / Uvicorn** — MIT  
* **pywebview** — BSD  
* **React / Vite** — MIT  

The face-landmarker model is bundled under `models/` (Apache 2.0).

---

## Project layout

```
EyeRhythm/
├── app/                 # engine + local API + window
├── blink_core/          # blink detection & screen-time
├── frontend/            # React UI (source + dist/)
├── models/              # MediaPipe face landmarker
├── assets/              # logo + brand assets
├── docs/CODE_GUIDE.md   # map of the codebase
├── for_mac/             # setup / run helpers
├── for_windows/         # launchers + packaging
├── requirements.txt
└── run_app.py           # packaging entry-point
```

### Contributors

Start with **[`docs/CODE_GUIDE.md`](docs/CODE_GUIDE.md)**, then `app/engine.py` and `blink_core/detector.py`.

---

## Feedback & sharing

If the counter feels off for you, or you have ideas for the reserved metric cards, please open an issue. If EyeRhythm is useful, share it — a regular blink habit is one of the easiest gifts we can give our eyes.

*EyeRhythm is free and open-source. Use it, improve it, share it.*
