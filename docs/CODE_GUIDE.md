# EyesRhythm code guide

An overview of how the engine, the local API, and the React interface fit together. Start here before reading `app/engine.py` and `blink_core/detector.py`.

---

## What the product does

1. Opens the **webcam** in the background  
2. Detects **face + blinks** (MediaPipe)  
3. Shows a **launch splash** (ocular-comfort self-check + notices), then a
   **React dashboard** in a compact desktop window. The license gate is off
   unless you turn it on
4. Optionally **beeps** on blinks or when ORI stays below the threshold for the Alert duration (default 3 min in the last 5 min)  
5. Saves **today’s** blink count and face-time to a local JSON file  

Blink data is not uploaded. Privacy is by design. Camera preview is collapsed by default.

---

## Big picture

```
┌─────────────────────────────────────────────────────────────┐
│  frontend/          React (Vite + TypeScript)               │
│  - dashboard, cards, camera panel, toggles                  │
│  - talks to local API over WebSocket / HTTP                 │
└──────────────────────────────┬──────────────────────────────┘
                               │  ws://127.0.0.1:8742
                               ▼
┌─────────────────────────────────────────────────────────────┐
│  app/               “shell + hands”                         │
│  - main.py      process entry, pywebview window             │
│  - server.py    FastAPI REST + WebSocket                    │
│  - engine.py    camera loop, toggles, snapshot              │
│  - regularity.py  CV-based regularity score                 │
│  - beeper.py    OS-native short sounds                      │
└──────────────────────────────┬──────────────────────────────┘
                               │ uses
                               ▼
┌─────────────────────────────────────────────────────────────┐
│  blink_core/        “brain” (unchanged science)             │
│  - detector.py      blink state machine                     │
│  - screentime.py    daily diary on disk                     │
│  - interblink_hours.py  research: IBI stats per screen hour │
└─────────────────────────────────────────────────────────────┘
```

| Path | Role |
|------|------|
| `app/version.py` | Product SemVer (`1.6.0`) |
| `app/main.py` | Start engine + HTTP + desktop window |
| `app/engine.py` | Camera, detector, stats, beeps/alerts |
| `app/camera_idle.py` | Battery: 1 min no-face → camera off; peek every 2 min; 30 s confirm |
| `app/frame_pacer.py` | Adaptive FPS: 30 face / post-blink sleep / 1 no-face |
| `app/license.py` | Dodo Payments activate + local license file |
| `app/server.py` | Local API for the UI |
| `app/regularity.py` | Inter-blink CV → score 0–100 |
| `app/beeper.py` | Sounds |
| `blink_core/detector.py` | Blink detection |
| `blink_core/screentime.py` | Daily totals |
| `blink_core/interblink_hours.py` | Per screen-hour inter-blink research JSON |
| `frontend/` | React source |
| `frontend/dist/` | Built UI (served in production) |
| `run_app.py` | Packaging entry + crash log |

---

## Life of one frame

```
Webcam frame
    → EyeRhythmEngine (background thread)
    → BlinkDetector.process_frame()
    → ScreenTimeTracker + RegularityTracker
    → EngineSnapshot (thread-safe)
    → WebSocket /api clients (~10 Hz)
    → React re-renders metric cards
```

Optional camera panel: engine encodes low-res JPEG only while the panel is open.

---

## Camera battery idle

1 fps with no face still leaves the webcam open. After **60 s** with no
MediaPipe face, the engine **releases** the camera. Every **2 minutes** it
reopens for a peek: if nobody appears within a few seconds, it parks again;
if a face stays for **30 s** (1 s blink grace), live tracking resumes.
Practice or Lens wakes the camera immediately (no 30 s wait). Peeks do not
count blinks or screen time.

---

## Regularity score

```
CV = stdev(inter-blink gaps) / mean(gaps)
Score = max(0, 100 − 100 × CV)
```

Needs a few recent blinks before the UI shows a number.

---

## Ocular Rhythm Index (ORI)

Composite 0–100 = 70 % rate score + 30 % regularity. Trace plots this
single series (not rate and regularity as separate lines). Alert beeps
when ORI has been below the **ORI threshold** (default 50) for the
**Alert duration** (default 3 minutes, 1–5 in Settings) in total out of
the last **5 minutes** (the Trace window). Beeps stop the instant current
ORI reaches the threshold.

---

## Look angle (face axis vs camera)

Every face frame, `blink_core.detector.estimate_face_camera_pose` builds a 3D
face plane from outer eye corners + forehead + chin, takes the outward normal
(face/eye axis), and measures the angle to the camera optical axis.

* **0°** → axes parallel → looking straight into the camera  
* **yaw / pitch** → direction label: `straight`, `left`, `right`, `top`,
  `bottom`, or combinations like `right-top`  
* Published on `EngineSnapshot` and shown in the **Look angle** metric card
  beside Regularity

Looking **down** (``bottom`` / ``left-bottom`` / ``right-bottom``) when the
Look-angle card is past the writing-angle setting (default **20°**) is
**off-screen**. Looking **left or right** when yaw is past the turn-angle
setting (default **50°**) is also off-screen, with the same treatment.
Off-screen blinks are not counted, regularity holds, Alert does not
sample, and Trace **cuts the line immediately**. Both limits are live
Settings-dock knobs.

---

## Daily stats file

Path: `~/.eyerhythm_blinkcounter/screentime.json`

```json
{ "date": "2026-08-01", "seconds": 3600.0, "blinks": 420 }
```

Resets at local midnight.

---

## Inter-blink research log (per screen-time hour)

Path: `~/.eyerhythm_blinkcounter/interblink_hours.json`

Each **hour of face-present screen time** (not wall-clock hour) gets one
entry with inter-blink interval stats. Example: **6 h 45 m** screen time →
**7 entries** for that date (6 complete hours + 1 partial).

```json
{
  "version": 1,
  "records": [
    {
      "date": "2026-04-05",
      "hour_index": 1,
      "start_time": "2026-04-05T09:15:22",
      "end_time": "2026-04-05T10:40:11",
      "screen_seconds": 3600.0,
      "blink_count": 420,
      "gap_count": 419,
      "min_s": 0.42,
      "max_s": 18.3,
      "mean_s": 3.15,
      "median_s": 2.88,
      "complete": true
    }
  ]
}
```

Gaps longer than 30 s (or under 0.15 s) are ignored as breaks/noise.
History is kept across days in the same file.

---

## Settings / Reports dock

**Settings** (or **W**) grows the window sideways to show Settings and Reports. **More / Less** still owns height.

* **Settings** — live ORI threshold (Trace red line + Alert), writing angle
  (10–40°, off-screen look-down), turn angle (20–80°, default 50°, off-screen
  left/right), blink close sensitivity. Changes apply immediately and are
  appended to `~/.eyerhythm_blinkcounter/audit.json`.
* **Reports** — ORI history in `ori_history.json` (5-minute bins). Daily bars
  (24 h, 5-min bins), weekly bars (Mon–Sun), monthly daily bars. Choose the
  period in the panel.

---

## License activation (kept, off)

The Dodo Payments gate stays in the app and is **off**, including packaged
Mac and Windows builds. Copies open with no key. Blink data still never
leaves the machine.

Turn the gate on only when you mean to:

* `--require-license` / `EYERHYTHM_REQUIRE_LICENSE=1` — ask for a key
* `--skip-license` / `EYERHYTHM_SKIP_LICENSE=1` — skip even if the gate was forced
* `EYERHYTHM_DODO_TEST=1` — talk to Dodo test mode instead of live

When the gate is on, the key plus a hashed machine id go to Dodo and the
result is stored in `~/.eyerhythm_blinkcounter/license.json`. The dashboard
UI talks only to localhost. Python is the process that calls
`https://live.dodopayments.com/licenses/activate`.

UI: `frontend/src/components/LicenseScreen.tsx`.

---

## Launch splash

On every start, a same-theme overlay covers the dashboard until **Start** or
**3 minutes**, whichever is first.

1. Ocular-comfort self-check (comfortable / mild / moderate / marked /
   prefer not to say). Stored only in local `preferences.json` as
   `eye_comfort`.
2. Three notices: on-device privacy, not a medical device, how ORI and
   Alert work.

Enter continues / starts. Dashboard shortcuts (B/A/P/D/W/C) are ignored until
the splash closes. UI: `frontend/src/components/SplashScreen.tsx`.

---

## Practice mode (full-screen coach)

1. User toggles **Practice** → engine enters `countdown` for **20 s**.
2. React opens a dark full-screen overlay with instructions + countdown.
3. Desktop shell may expand the window (`set_practice_fullscreen` bridge).
4. Countdown ends → `active` for **120 s**: beeps every **3 s** (at the *end*
   of each interval) and a ball+string animation that arrives on each beep.
5. Path cycle: center → top-left → bottom-left → bottom-right → top-right →
   spiral back to center (then repeat).
6. Esc or **End practice** stops early; natural end clears the toggle.

Tunables in `app/engine.py`: `PRACTICE_COUNTDOWN_S`, `PRACTICE_INTERVAL_S`,
`PRACTICE_DURATION_S`. Animation lives in `frontend/src/components/PracticeScreen.tsx`.

---

## UI shortcuts

| Key | Effect |
|-----|--------|
| B | Toggle beep on each counted blink |
| A | Switch the low-ORI alert between Visual and Sound (the alert is always on) |
| P | Practice |
| D | More / Less |
| W | Settings / Hide settings |
| C | Switch Lens camera preview |

---

## How to run (developers)

```bash
# Python
./for_mac/setup.sh          # or venv + pip install -r requirements.txt
./for_mac/run.sh

# UI only (hot reload) — terminal 1:
python -m app.main --browser
# terminal 2:
cd frontend && npm install && npm run dev
```

Useful flags:

* `--camera 1` — second webcam  
* `--browser` — system browser instead of pywebview  
* `--port 8742` — local API port  
* `--skip-checks` — skip startup dependency checks
* `--require-license` — Dodo Payments gate even from source
* `--skip-license` — skip the license gate  

## Tests

```bash
python -m unittest discover -s tests
```

---

See [`CHANGELOG.md`](../CHANGELOG.md) for the release history and [`CONTRIBUTING.md`](../CONTRIBUTING.md) for how to submit changes. EyesRhythm’s source is licensed under the PolyForm Noncommercial License 1.0.0; third-party terms are in [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md).
