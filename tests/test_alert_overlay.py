"""Visual Alert multi-monitor overlay: frame timeline and alpha."""

from __future__ import annotations

import unittest

import numpy as np

from app.alert_overlay import (
    OVERLAY_ALPHA,
    OVERLAY_BLINK_AT_S,
    OVERLAY_DURATION_S,
    OVERLAY_SIZE,
    _macos_behavior_mask,
    _scale_alpha,
    macos_overlay_behavior_mask,
    macos_overlay_style_mask,
    macos_overlay_window_level,
    overlay_frame_at,
    prepare_overlay_frames,
)
from app.tray_icon import BLINK_CLOSED_S


class OverlayTimelineTests(unittest.TestCase):
    def test_hidden_before_and_after(self) -> None:
        self.assertIsNone(overlay_frame_at(-0.01))
        self.assertIsNone(overlay_frame_at(OVERLAY_DURATION_S))
        self.assertIsNone(overlay_frame_at(OVERLAY_DURATION_S + 1.0))

    def test_opens_then_blinks_once(self) -> None:
        self.assertEqual(overlay_frame_at(0.0), "orange_open")
        self.assertEqual(overlay_frame_at(OVERLAY_BLINK_AT_S - 0.01), "orange_open")
        self.assertEqual(overlay_frame_at(OVERLAY_BLINK_AT_S), "orange_closed")
        mid_closed = OVERLAY_BLINK_AT_S + BLINK_CLOSED_S / 2.0
        self.assertEqual(overlay_frame_at(mid_closed), "orange_closed")
        reopened = OVERLAY_BLINK_AT_S + BLINK_CLOSED_S
        self.assertEqual(overlay_frame_at(reopened), "orange_open")
        self.assertEqual(overlay_frame_at(OVERLAY_DURATION_S - 0.01), "orange_open")

    def test_cue_is_about_one_second(self) -> None:
        self.assertAlmostEqual(OVERLAY_DURATION_S, 1.0)


class VisualCueCadenceTests(unittest.TestCase):
    def test_center_icon_shows_on_orange_peaks_about_three_times_a_minute(self) -> None:
        from app.engine import (
            ALERT_BEEP_INTERVAL_S,
            ALERT_ORANGE_PULSE_S,
            ALERT_VISUAL_CUE_HOLD_S,
            ALERT_VISUAL_CUE_INTERVAL_S,
            AlertMonitor,
        )

        self.assertEqual(ALERT_VISUAL_CUE_INTERVAL_S, 20.0)
        self.assertEqual(ALERT_BEEP_INTERVAL_S, 60.0)
        self.assertEqual(ALERT_ORANGE_PULSE_S, 3.0)
        self.assertEqual(ALERT_VISUAL_CUE_HOLD_S, 1.5)
        monitor = AlertMonitor(None, visual_interval_s=ALERT_VISUAL_CUE_INTERVAL_S)
        # Latch just turned on: the app is still blue. No icon yet.
        self.assertFalse(monitor._maybe_fire_visual_locked(0.0, True))
        self.assertFalse(monitor._maybe_fire_visual_locked(1.4, True))
        # Orange peak (halfway through the 3s glow). Then the next peak
        # that is at least 20s later (22.5). Peaks in between stay quiet.
        self.assertTrue(monitor._maybe_fire_visual_locked(1.5, True))
        self.assertFalse(monitor._maybe_fire_visual_locked(4.5, True))
        self.assertFalse(monitor._maybe_fire_visual_locked(19.5, True))
        self.assertFalse(monitor._maybe_fire_visual_locked(21.0, True))
        self.assertTrue(monitor._maybe_fire_visual_locked(22.5, True))
        # A gap clears the orange phase and does not restart the 20s clock.
        self.assertFalse(monitor._maybe_fire_visual_locked(23.0, False))
        # New phase starts on this sample (24.5). First peak is 26.0.
        # The first peak at least 20s after 22.5 is 44.0.
        self.assertFalse(monitor._maybe_fire_visual_locked(24.5, True))
        self.assertFalse(monitor._maybe_fire_visual_locked(41.0, True))
        self.assertTrue(monitor._maybe_fire_visual_locked(44.0, True))
        # Sound stays on the one-minute clock and does not wait for orange.
        self.assertTrue(monitor._maybe_fire_alert_beep_locked(1_000.0, True))
        self.assertFalse(monitor._maybe_fire_alert_beep_locked(1_020.0, True))
        self.assertTrue(monitor._maybe_fire_alert_beep_locked(1_060.0, True))

    def test_short_blip_never_shows_the_center_icon(self) -> None:
        from app.engine import AlertMonitor

        cues: list[float] = []
        clock = {"t": 100.0}
        monitor = AlertMonitor(
            None,
            on_visual_cue=lambda: cues.append(clock["t"]),
            time_fn=lambda: clock["t"],
            low_duration_s=0.0,
            poll_s=0.5,
        )
        monitor._suppress_until = 0.0
        monitor._face_found = True
        monitor._ori = 10.0
        monitor._tick()
        self.assertTrue(monitor.is_active())
        self.assertEqual(cues, [])
        # Recover before the app reaches the orange peak.
        clock["t"] = 100.5
        monitor._ori = 90.0
        monitor._tick()
        self.assertFalse(monitor.is_active())
        self.assertEqual(cues, [])
        clock["t"] = 102.0
        monitor._tick()
        self.assertEqual(cues, [])

    def test_sustained_latch_shows_icon_only_on_orange_peaks(self) -> None:
        from app.engine import AlertMonitor

        cues: list[float] = []
        clock = {"t": 200.0}
        monitor = AlertMonitor(
            None,
            on_visual_cue=lambda: cues.append(clock["t"]),
            time_fn=lambda: clock["t"],
            low_duration_s=0.0,
            poll_s=0.5,
        )
        monitor._suppress_until = 0.0
        monitor._face_found = True
        monitor._ori = 10.0
        monitor._tick()
        self.assertEqual(cues, [])
        clock["t"] = 201.5
        monitor._tick()
        self.assertEqual(cues, [201.5])
        clock["t"] = 204.5
        monitor._tick()
        self.assertEqual(cues, [201.5])
        # Next orange peak that is at least 20s after 201.5.
        clock["t"] = 222.5
        monitor._tick()
        self.assertEqual(cues, [201.5, 222.5])

    def test_center_icon_requires_the_orange_alert_flag(self) -> None:
        from app.engine import EyeRhythmEngine

        engine = EyeRhythmEngine()
        got: list[int] = []
        engine.on_visual_alert_cue(lambda: got.append(1))
        engine._alert_mode = "visual"
        engine._alert_active = False
        engine._note_visual_cue()
        self.assertEqual(got, [])
        engine._alert_active = True
        engine._note_visual_cue()
        self.assertEqual(got, [1])
        engine._alert_mode = "sound"
        engine._note_visual_cue()
        self.assertEqual(got, [1])


class OverlayWindowTests(unittest.TestCase):
    def test_panel_mask_joins_other_apps_fullscreen_space(self) -> None:
        mask = macos_overlay_behavior_mask()
        self.assertEqual(mask & 1, 1)  # every Space
        self.assertEqual(mask & 16, 16)  # stationary
        self.assertEqual(mask & 64, 64)  # not in the window cycle
        self.assertEqual(mask & 256, 256)  # fullscreen auxiliary
        self.assertEqual(mask & 262144, 262144)  # other apps' fullscreen
        # Full-screen primary (128) would make this the fullscreen window.
        self.assertEqual(mask & 128, 0)

    def test_level_composites_above_fullscreen_content(self) -> None:
        # Screen-saver level. Floating (3) and the menu bar (24) sit below
        # fullscreen content. Shielding levels do not composite alpha.
        level = macos_overlay_window_level()
        self.assertEqual(level, 1000)
        self.assertLess(level, 10_000)

    def test_style_is_nonactivating_panel(self) -> None:
        # Bit 128 is NSWindowStyleMaskNonactivatingPanel. It has to be
        # passed to init; borderless itself is 0.
        self.assertEqual(macos_overlay_style_mask(), 128)

    def test_live_mask_matches_documented_bits(self) -> None:
        self.assertEqual(_macos_behavior_mask(), macos_overlay_behavior_mask())


class OverlayFrameTests(unittest.TestCase):
    def test_scale_alpha_halves_channel(self) -> None:
        img = np.zeros((4, 4, 4), dtype=np.uint8)
        img[:, :, 3] = 200
        out = _scale_alpha(img, 0.5)
        self.assertEqual(int(out[0, 0, 3]), 100)
        self.assertEqual(int(img[0, 0, 3]), 200)

    def test_prepare_frames_size_and_alpha(self) -> None:
        frames = prepare_overlay_frames(size=OVERLAY_SIZE, alpha=OVERLAY_ALPHA)
        self.assertEqual(set(frames), {"orange_open", "orange_closed"})
        for name, img in frames.items():
            self.assertEqual(img.shape, (OVERLAY_SIZE, OVERLAY_SIZE, 4), name)
            # Brand mark pixels should be faded (~50%), not fully opaque.
            opaque = img[:, :, 3] > 0
            self.assertTrue(bool(opaque.any()), name)
            self.assertLessEqual(int(img[:, :, 3].max()), 140, name)


if __name__ == "__main__":
    unittest.main()
