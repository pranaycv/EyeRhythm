"""Product version is SemVer and packaging files stay in lockstep."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from app.version import VERSION, version_tuple

ROOT = Path(__file__).resolve().parents[1]


class VersionTests(unittest.TestCase):
    def test_release_is_semver_1_6_0(self) -> None:
        self.assertEqual(VERSION, "1.6.0")
        self.assertRegex(VERSION, r"^\d+\.\d+\.\d+$")
        self.assertEqual(version_tuple(), (1, 6, 0, 0))

    def test_package_dunder_version(self) -> None:
        import app

        self.assertEqual(app.__version__, VERSION)

    def test_windows_installer_matches(self) -> None:
        text = (ROOT / "for_windows" / "for_developers" / "EyeRhythm.iss").read_text(
            encoding="utf-8"
        )
        match = re.search(r'#define MyAppVersion "([^"]+)"', text)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), VERSION)
        self.assertIn("VersionInfoVersion={#MyAppVersion}", text)
        self.assertIn("AppVerName={#MyAppName} {#MyAppVersion}", text)

    def test_pyinstaller_spec_reads_the_module(self) -> None:
        spec = (ROOT / "EyeRhythm.spec").read_text(encoding="utf-8")
        self.assertIn("from app.version import", spec)
        self.assertIn("CFBundleShortVersionString", spec)
        self.assertIn("CFBundleVersion", spec)
        self.assertIn("WIN_VERSION", spec)

    def test_frontend_package_json_matches(self) -> None:
        data = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(data["version"], VERSION)


if __name__ == "__main__":
    unittest.main()
