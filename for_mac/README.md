# EyesRhythm on macOS and Linux

This folder contains the scripts for running EyesRhythm from source on macOS or Linux, and for building the macOS app.

If you only want to use EyesRhythm, download the installer from [eyesrhythm.com](https://eyesrhythm.com) instead.

| Script | Purpose |
|--------|---------|
| `setup.sh` | One-time setup: create the virtual environment and install dependencies |
| `run.sh` | Launch the app from source |
| `install_autostart.sh` | Open EyesRhythm automatically at login |
| `uninstall_autostart.sh` | Stop opening at login |
| `start_at_login.sh` | Used by the login item; do not run it directly |
| `build_mac.sh` | Build a standalone `dist/EyesRhythm.app` |
| `make_app_icon.sh` | Generate the app icons from `assets/logo.svg` |
| `build_dmg.sh` | Build the app and package it as `dist/EyesRhythm.dmg` in one step |
| `make_dmg.sh` | Wrap an existing `dist/EyesRhythm.app` in a `.dmg` |
| `run_license_test.sh` | Test the optional license gate against the payment provider's test mode |

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

### Start automatically when you turn the Mac on

```bash
./for_mac/install_autostart.sh
```

This registers a per-user Launch Agent. After you log in (or restart), the EyesRhythm window opens on its own — no Terminal needed. macOS may ask once to allow EyesRhythm in the background; choose **Allow** (System Settings → General → Login Items).

Turn it off with:

```bash
./for_mac/uninstall_autostart.sh
```

If you move the project folder, run `install_autostart.sh` again so the login item points at the new path.

Logs land in `~/Library/Logs/EyesRhythm.log`.

### Useful flags

```bash
python -m app.main --camera 1          # try a different webcam
python -m app.main --skip-checks       # skip the startup self-check
```

---

## 4. Keyboard shortcuts

| Key | Action |
|-----|--------|
| B | Beep on every counted blink |
| A | Switch the alert between Visual and Sound |
| P | Start or stop Practice |
| D | Switch between the compact and full dashboard |
| W | Show or hide the Settings and Reports panel |
| C | Switch to or from the Lens camera preview |

Close the window to quit.

---

## 5. Build a standalone .app (optional)

If you want a double-clickable Mac application (no Terminal needed):

```bash
./for_mac/build_mac.sh
```

The script builds the app icon from `assets/logo.svg`, stamps the camera permission key, and writes `dist/EyesRhythm.app`.

### After you change the code

You must rebuild. Editing Python / React files does **not** update an already-built `.app`.

1. Save your code changes.
2. If you changed the React UI (`frontend/src/…`), rebuild it first:

   ```bash
   cd frontend && npm run build && cd ..
   ```

   (`build_mac.sh` also does this if `frontend/dist` is missing, but not if it is merely stale.)
3. Rebuild the app (this replaces `dist/EyesRhythm.app`):

   ```bash
   ./for_mac/build_mac.sh
   ```
4. Double-click `dist/EyesRhythm.app` and confirm it works.
5. Right-click `EyesRhythm.app` → **Compress**, and send only that zip.

Do not send the project folder, `build/`, or the virtual environment.

Code signing and notarization are not covered by these scripts.

---

## Troubleshooting

| Symptom                         | What to try |
|---------------------------------|-------------|
| “No module named mediapipe”     | Make sure the `EyeRhythm` venv is activated and `pip install -r requirements.txt` succeeded |
| Black / no camera               | Close Zoom, Teams, browser tabs that use the webcam; check System Settings → Camera |
| “Model file not found”          | Confirm `models/face_landmarker.task` exists (≈ 3.7 MB) |
| App starts then immediately exits | Look for a crash log at `~/Library/Logs/EyesRhythm_crash.log` |
| Double-click `.app` shows Retry / Ignore / Report | The bundle is missing `NSCameraUsageDescription`. Rebuild with `./for_mac/build_mac.sh`, which adds the camera privacy key. |
