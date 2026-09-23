"""Reading how full the container is (docs/design-decisions.md #41).

What these pin: the limit is read from whichever cgroup version is mounted; "no limit" is
recognised in both of its spellings and falls back to the host's RAM; the reading is the
container's *anonymous* memory, never `memory.current`, whose page cache a backtest's SQLite
reads inflate by hundreds of megabytes that the kernel would simply reclaim; and a host with
none of these files reads as unknown rather than as full.
"""
from __future__ import annotations

import pytest

from icici_breeze_backend.app.core import memory


@pytest.fixture
def fs(tmp_path, monkeypatch):
    """Point the module at files under tmp_path instead of /sys and /proc."""
    written: dict[str, str] = {}

    def write(key: str, text: str) -> str:
        """Stand `key` -- a real path, or a name to point a module constant at -- on disk."""
        path = tmp_path / key.strip("/").replace("/", "_")
        path.write_text(text, encoding="ascii")
        written[key] = str(path)
        return str(path)

    def read(path: str):
        try:
            with open(written.get(path, path), encoding="ascii") as fh:
                return fh.read()
        except OSError:
            return None

    monkeypatch.setattr(memory, "_read", read)
    return write


class TestTheLimit:
    def test_cgroup_v2(self, fs, monkeypatch):
        monkeypatch.setattr(memory, "_V2_MAX", fs("v2max", "1468006400\n"))
        assert memory.limit_bytes() == 1_468_006_400

    def test_cgroup_v1(self, fs, monkeypatch):
        monkeypatch.setattr(memory, "_V1_MAX", fs("v1max", "1468006400\n"))
        assert memory.limit_bytes() == 1_468_006_400

    def test_an_uncapped_container_falls_back_to_the_host(self, fs, monkeypatch):
        monkeypatch.setattr(memory, "_V2_MAX", fs("v2max", "max\n"))
        monkeypatch.setattr(memory, "_V1_MAX", fs("v1max", str(1 << 63)))
        fs("/proc/meminfo", "MemTotal:        2027520 kB\nMemFree:  100 kB\n")
        assert memory.limit_bytes() == 2_027_520 * 1024

    def test_nothing_readable_is_unknown_not_zero(self, fs):
        assert memory.limit_bytes() is None


class TestWhatIsHeld:
    def test_cgroup_v2_anon_not_current(self, fs, monkeypatch):
        # `anon` is what has nowhere to go; `file` is page cache the kernel drops under
        # pressure, and counting it would refuse runs that were never in danger.
        monkeypatch.setattr(
            memory, "_V2_STAT", fs("v2stat", "anon 734003200\nfile 900000000\nkernel 1000\n")
        )
        assert memory.in_use_bytes() == 734_003_200

    def test_cgroup_v1_rss(self, fs, monkeypatch):
        monkeypatch.setattr(memory, "_V1_STAT", fs("v1stat", "cache 900000000\nrss 734003200\n"))
        assert memory.in_use_bytes() == 734_003_200

    def test_without_a_cgroup_it_falls_back_to_this_process(self, fs, monkeypatch):
        monkeypatch.setattr(memory, "resident_bytes", lambda: 123)
        assert memory.in_use_bytes() == 123

    def test_a_malformed_field_is_unknown(self, fs, monkeypatch):
        monkeypatch.setattr(memory, "_V2_STAT", fs("v2stat", "anon not-a-number\n"))
        monkeypatch.setattr(memory, "resident_bytes", lambda: None)
        assert memory.in_use_bytes() is None


class TestUsage:
    def test_both_halves_or_nothing(self, fs, monkeypatch):
        monkeypatch.setattr(memory, "in_use_bytes", lambda: 100)
        monkeypatch.setattr(memory, "limit_bytes", lambda: None)
        assert memory.usage() is None

        monkeypatch.setattr(memory, "limit_bytes", lambda: 0)
        assert memory.usage() is None, "a zero ceiling is not a container that is always full"

        monkeypatch.setattr(memory, "limit_bytes", lambda: 1_000)
        assert memory.usage() == (100, 1_000)

    def test_it_reads_as_gigabytes_a_person_can_act_on(self):
        assert memory.describe(1_240_000_000, 1_468_006_400) == "1.24 GB of the 1.47 GB this app is allowed"
