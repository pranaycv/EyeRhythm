# EyeRhythm on macOS / Linux

Everything Mac/Linux-related lives in this folder.

```
for_mac/
├── README.md        ← this file
├── setup.sh         ← one-time: create venv + install deps
├── run.sh           ← launch the app from source
└── build_mac.sh     ← optional: build a standalone .app
```

---

## 1. Install Python

You need **Python 3.10, 3.11 or 3.12**.

- Official installer: https://www.python.org/downloads/  
- or Homebrew: `brew install python@3.11`

```bash
python3 --version
# should print something like Python 3.11.x
```

---

## 2. One-time setup

Open Terminal and go to the **project root** (the folder that contains `app/`, `requirements.txt`, `run_app.py`, etc.):

```bash
cd /path/to/EyeRhythm
./for_mac/setup.sh
```

This creates a virtual environment named exactly **`EyeRhythm`** and installs the required libraries.

(You can also do it manually: `python3 -m venv EyeRhythm && source EyeRhythm/bin/activate && pip install -r requirements.txt`.)

---

## 3. Run the app

```bash
./for_mac/run.sh
```

or:

```bash
source EyeRhythm/bin/activate
python -m app.main
```

### Camera permission

The first time you launch, macOS will ask for camera access → **Allow**.  
You can manage it later under:

**System Settings → Privacy & Security → Camera**

### Useful flags

```bash
python -m app.main --camera 1          # try a different webcam
python -m app.main --skip-checks       # skip the startup self-check
```

---

## 4. Keyboard shortcuts

| Key       | Action                          |
|-----------|---------------------------------|
| Q / Esc   | Quit                            |
| B         | Beep on every blink ON/OFF      |
| A         | Low-rate alert ON/OFF           |
| S         | Look-angle sweep (workspace extrema) |
| T         | Always-on-top ON/OFF            |

---

## 5. Build a standalone .app (optional)

If you want a double-clickable Mac application (no Terminal needed):

```bash
source EyeRhythm/bin/activate
pip install pyinstaller
./for_mac/build_mac.sh
```

The result appears under `dist/`.  
(Advanced code-signing / notarization is outside the scope of this open-source edition.)

---

## Troubleshooting

| Symptom                         | What to try |
|---------------------------------|-------------|
| “No module named mediapipe”     | Make sure the `EyeRhythm` venv is activated and `pip install -r requirements.txt` succeeded |
| Black / no camera               | Close Zoom, Teams, browser tabs that use the webcam; check System Settings → Camera |
| “Model file not found”          | Confirm `models/face_landmarker.task` exists (≈ 3.7 MB) |
| App starts then immediately exits | Look for a crash log at `~/Library/Logs/EyeRhythm_crash.log` |

That’s everything required to run EyeRhythm on a Mac or Linux machine.
