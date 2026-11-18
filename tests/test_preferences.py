"""Startup-screen preference is off until the user turns it on."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app import preferences


class SkipStartupScreenTests(unittest.TestCase):
    def test_missing_key_stays_off(self) -> None:
        data = preferences._sanitize({"avatar": "atlas"})
        self.assertFalse(data["skip_startup_screens"])

    def test_round_trip(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "preferences.json"
            with (
                patch.object(preferences, "PREFS_FILE", path),
                patch.object(preferences, "_cache", None),
            ):
                self.assertFalse(preferences.load()["skip_startup_screens"])
                saved = preferences.save(skip_startup_screens=True)
                self.assertTrue(saved["skip_startup_screens"])
                preferences._cache = None
                self.assertTrue(preferences.load()["skip_startup_screens"])
                kept = preferences.save(dock=True)
                self.assertTrue(kept["skip_startup_screens"])
                self.assertTrue(kept["dock"])
                cleared = preferences.save(skip_startup_screens=False)
                self.assertFalse(cleared["skip_startup_screens"])
