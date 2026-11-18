"""
Product version
===============

Semantic Versioning 2.0.0 (https://semver.org): MAJOR.MINOR.PATCH.

Current release is ``1.6.0``. Bump:

* PATCH (1.0.x) — fixes that do not change how people use the app
* MINOR (1.x.0) — new features that stay compatible
* MAJOR (x.0.0) — breaking changes

Packaging (Mac Info.plist, Windows exe/Setup) and Settings → About all
read this module. Change the number here, then match ``MyAppVersion`` in
``for_windows/for_developers/EyeRhythm.iss`` (a unit test checks that).
"""

from __future__ import annotations

APP_NAME = "EyesRhythm"
VERSION = "1.6.0"


def version_tuple() -> tuple[int, int, int, int]:
    """Windows FileVersion is a 4-part tuple; unused parts are 0."""
    parts = [int(piece) for piece in VERSION.split(".")]
    while len(parts) < 4:
        parts.append(0)
    return (parts[0], parts[1], parts[2], parts[3])
