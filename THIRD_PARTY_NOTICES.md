# Third-party notices

EyesRhythm’s own code is under the PolyForm Noncommercial License 1.0.0
(https://polyformproject.org/licenses/noncommercial/1.0.0). See `LICENSE`.

Required Notice: Copyright Pranay Soni (https://github.com/pranaycv)

Third-party software included in Mac and Windows copies keeps its own
license. This file is the attribution Apache 2.0, BSD, MIT, LGPLv2.1,
and the SIL Open Font License require you to keep with the copy.

Full license texts:

| File | License |
|------|---------|
| `licenses/Apache-2.0.txt` | Apache License 2.0 |
| `licenses/LGPLv2.1.txt` | GNU Lesser General Public License 2.1 |
| `licenses/SIL-OFL-1.1.txt` | SIL Open Font License 1.1 |
| `assets/fonts/INTER-LICENSE.txt` | same OFL text, next to the font files |

Settings → About shows this notice in the app.

---

## Apache License 2.0

Used by: **MediaPipe** (Google), the bundled **Face Landmarker** model
(`models/face_landmarker.task`), and **OpenCV**.

You may use, modify, and distribute these in a commercial product.
You must keep copyright notices and a copy of the Apache 2.0 license
(`licenses/Apache-2.0.txt`).

MediaPipe: https://github.com/google-ai-edge/mediapipe  
OpenCV: https://opencv.org/

---

## GNU Lesser General Public License 2.1

**FFmpeg** (libavcodec, libavformat, libavutil, libswscale, and related
libraries) is shipped inside the `opencv-python` wheel that EyesRhythm
freezes. FFmpeg is LGPLv2.1. OpenCV itself is Apache 2.0; FFmpeg is not.

LGPL does not require EyesRhythm source to be published. It does require:

1. This notice, and the text in `licenses/LGPLv2.1.txt`.
2. That FFmpeg remain a replaceable library. In the Windows freeze it
   typically appears as `opencv_videoio_ffmpeg*.dll` under `_internal`.
   On macOS it appears as shared libraries next to `cv2`. You may
   substitute a compatible LGPL build of those files.
3. If someone modifies FFmpeg, those modifications stay under LGPL.

FFmpeg source: https://ffmpeg.org/  
opencv-python third-party list: https://github.com/opencv/opencv-python

EyesRhythm does not modify FFmpeg.

Non-headless Linux wheels of `opencv-python` can also include Qt under
LGPL. Official EyesRhythm packages are macOS and Windows only.

---

## BSD-3-Clause / BSD-style

- **NumPy** — Copyright NumPy Developers
- **Uvicorn** — Copyright Encode OSS Ltd
- **pywebview** — Copyright Roman Sirokov
- **websockets** — Copyright Aymeric Augustin
- **Starlette** (also BSD) — Copyright Encode OSS Ltd
- **Matplotlib** — Copyright Matplotlib Development Team (PSF / BSD-style)

Redistribution in binary form is allowed with this acknowledgment.

---

## MIT (and MIT-CMU)

- **FastAPI**, **Pydantic**, **anyio**, **h11**, **typing_extensions** (PSF-2.0)
- **React**, **React DOM**, **Vite**, **TypeScript** (UI build)
- **Pillow** (MIT-CMU)
- **pythonnet** and **clr_loader** (Windows desktop window)
- **proxy_tools**, **bottle** (pywebview helpers)
- **Astro** (marketing site)

Keep the copyright notice and MIT permission notice with the copy
(`LICENSE` covers EyesRhythm; this file covers the rest).

---

## SIL Open Font License 1.1

**Inter** (The Inter Project Authors, https://github.com/rsms/inter).

The font may be bundled and sold **with** EyesRhythm. The font files
must not be sold on their own. Reserved font name: Inter. Full text:
`licenses/SIL-OFL-1.1.txt` and `assets/fonts/INTER-LICENSE.txt`.

---

## Build tools (not part of the product license)

- **PyInstaller** is GPL-2.0 with an explicit exception that allows
  freezing commercial, closed-source applications. EyesRhythm does not
  modify the PyInstaller bootloader. See
  https://pyinstaller.org/en/stable/license.html
- **Inno Setup** (Windows installer) is under a modified BSD license.
  Using it to build a paid installer is allowed.

---

## Original assets in this copy

- `alert.mp3` — original Sound-mode alert cue shipped with EyesRhythm.
  `alert.wav` is kept as a fallback only.
- Practice quotations in `quotes.json` — taken from the original
  EyesRhythm list, limited to public-domain authors (ancient writers, or
  authors who died in 1925 or earlier). Living and still-in-copyright
  authors were removed.

---

## Medical disclaimer

EyesRhythm is a wellness companion, not a medical device. It is not
intended to diagnose, treat, cure, or prevent disease.
