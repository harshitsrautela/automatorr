"""Locates the bundled test sites (`<repo>/testsites/<name>`); set TESTSITES_DIR to use another copy."""

import os

SITES_DIR = os.environ.get("TESTSITES_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "testsites")


def site_path(name: str, *parts: str) -> str:
    """The path of a file or folder inside one test site, e.g. site_path("ap-desk", "data/seed.json")."""
    return os.path.join(SITES_DIR, name, *parts)
