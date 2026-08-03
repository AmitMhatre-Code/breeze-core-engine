"""changelog-latest-version.mjs extracts the newest release version."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "changelog-latest-version.mjs"
_CHANGELOG = _REPO_ROOT / "frontend" / "src" / "lib" / "changelog.ts"


def test_changelog_latest_version_script():
    """The script must return the newest entry in the changelog.

    Derived from the file rather than hardcoded: the previous literal ("2.1.2") had gone
    stale three releases earlier, so the suite failed on every release for a reason that
    had nothing to do with the script being broken — which trains people to ignore it.
    """
    expected = re.search(r'version:\s*"([^"]+)"', _CHANGELOG.read_text(encoding="utf-8"))
    assert expected, "no version entry found in changelog.ts"
    out = subprocess.check_output(
        ["node", str(_SCRIPT), str(_CHANGELOG)],
        text=True,
        cwd=_REPO_ROOT,
    ).strip()
    assert out == expected.group(1)


def test_reported_version_reads_baked_file(monkeypatch, tmp_path):
    import sys

    src = _REPO_ROOT / "backend" / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from icici_breeze_backend.app.services import portal_deployment_heartbeat as hb

    version_file = tmp_path / "app_version"
    version_file.write_text("1.4.2-a\n", encoding="utf-8")
    monkeypatch.delenv("APP_VERSION", raising=False)
    monkeypatch.delenv("IMAGE_TAG", raising=False)
    monkeypatch.delenv("DEPLOYMENT_VERSION", raising=False)
    monkeypatch.setenv("APP_VERSION_FILE", str(version_file))
    assert hb._reported_version() == "1.4.2-a"
