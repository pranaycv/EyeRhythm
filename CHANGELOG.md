# Changelog

All notable changes to EyesRhythm are documented in this file. Versions follow [Semantic Versioning](https://semver.org/), and the version number lives in `app/version.py`.

## 1.6.0 — 7 November 2026

### Changed

- EyesRhythm’s source code is now licensed under the PolyForm Noncommercial License 1.0.0. Noncommercial use is allowed; commercial use is not included.
- Required Notice: Copyright Pranay Soni (https://github.com/pranaycv)

### Files

The official downloads are the 1.6.0 installers attached to the GitHub release.

| Platform | Installer |
|----------|-----------|
| macOS | `EyesRhythm-1.6.0.dmg` |
| Windows | `EyesRhythm-Setup-1.6.0.exe` |

---

## 1.5.0 — 2 November 2026

The Poise dial shows which way the head turned today, in 30° slices.

### Poise dial

- Twelve directions. A slice turns green the more the head goes that way, and stays red where it rarely goes.
- Tiles light from the center outward. The least-used direction pulses with an arrow.
- The center shows a balance score. The ball follows the head and leaves a short trail.
- Looking up lights the top of the dial and looking down lights the bottom.
- The dial starts all red each calendar day. The face wireframe stays.

### Files

| Platform | Installer |
|----------|-----------|
| macOS | `EyesRhythm-1.5.0.dmg` |
| Windows | `EyesRhythm-Setup-1.5.0.exe` |

Drag the Mac app into Applications. On Windows, run Setup (per-user, no administrator account). Allow the camera when the system asks.

---

## 1.4.0 — 2 November 2026

EyesRhythm is free. Packaged Mac and Windows copies open with no license key.

### License

- The activation gate stays in the app and is off. First launch goes straight to the splash.
- Turn it back on with `--require-license` or `EYERHYTHM_REQUIRE_LICENSE=1`.

### Files

| Platform | Installer |
|----------|-----------|
| macOS | `EyesRhythm-1.4.0.dmg` |
| Windows | `EyesRhythm-Setup-1.4.0.exe` |

Drag the Mac app into Applications. On Windows, run Setup (per-user, no administrator account). Allow the camera when the system asks.

---

## 1.3.0 — 17 September 2026

Screen-time reports in the dock, and Sound-mode alerts that stay on a one-minute cadence.

### Screen time reports

- Reports splits into **Ocular rhythm** and **Screen time**. Both use the same Daily / Weekly / Monthly picker.
- Daily screen time is minutes at the screen in each 5-minute slot. Weekly and monthly show hours per day, with total, daily average, and days present.
- These charts read the screen-time seconds already stored in the local ORI history bins. No extra diary file.

### Sound alert

- After a low-ORI Sound cue plays, the next cue waits **one minute**, even if the alert latch flickers (brief face loss or ORI bouncing around the line).
- Beeps still stop as soon as ORI is back at or above the threshold.

### Files

| Platform | Installer |
|----------|-----------|
| macOS | `EyesRhythm-1.3.0.dmg` |
| Windows | `EyesRhythm-Setup-1.3.0.exe` |

Drag the Mac app into Applications. On Windows, run Setup (per-user, no administrator account). Allow the camera when the system asks.

---

## 1.2.1 — 13 September 2026

Camera picker is visible again in Settings after 1.2.0 hid it on machines with more than one desk webcam.

### Camera picker

- Settings shows **Camera** whenever a desk webcam is found. A black first frame no longer empties the list for the whole session.
- **Hybrid** still appears when there are two or more desk cameras (built-in plus USB). Phone, iPad, and Continuity cameras stay out of the list.

### Files

| Platform | Installer |
|----------|-----------|
| macOS | `EyesRhythm-1.2.1.dmg` |
| Windows | `EyesRhythm-Setup-1.2.1.exe` |

Drag the Mac app into Applications. On Windows, run Setup (per-user, no administrator account). Allow the camera when the system asks.

---

## 1.2.0 — 13 September 2026

Hybrid multi-camera support. If you have more than one desk webcam (built-in plus USB), you can use them together instead of picking a single camera.

### Hybrid camera

- New **Hybrid** choice on the launch splash and in Settings. It uses every desk camera on the computer at once. Phone, iPad, and Continuity cameras stay out of the list.
- Each frame is grabbed from all desk cameras at the same instant, then blink detection runs on the camera that has a **front-facing** face, so one physical blink is counted once.
- Hybrid does not use the single-camera FPS cap (no 30 / 1 fps pacing and no post-blink pause). Detection stays on the active camera so the live FPS stays close to a single-camera session; the other cameras are checked when you turn toward them.
- If the built-in camera fails to open while the USB camera is already running, Hybrid retries the full set instead of staying on one camera.

### Also in this build

- Camera picker and Settings still let you use a single desk camera, same as 1.1.0.
- Packaged copies remain a one-time Dodo license, on-device only after activation.

### Files

| Platform | Installer |
|----------|-----------|
| macOS | `EyesRhythm-1.2.0.dmg` |
| Windows | `EyesRhythm-Setup-1.2.0.exe` |

Drag the Mac app into Applications. On Windows, run Setup (per-user, no administrator account). Allow the camera when the system asks.

---

## 1.1.0 — 7 September 2026

Quieter custom alert tone. Sound mode plays the chime; Visual mode pulses the in-app mark and blinks the minimized Windows taskbar.
