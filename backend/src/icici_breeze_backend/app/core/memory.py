"""How much memory this container is holding, and how much it is allowed.

**Why this exists.** The app runs as four supervised processes inside one container with a hard
`--memory` cap (1400 MiB on the stock `t4g.small` stack). When the cap is reached the kernel
kills the fattest process in the cgroup -- uvicorn -- with `SIGKILL`. There is no exception to
catch and no traceback to log: the process stops mid-line, supervisord restarts it, and the work
it was doing is only discovered as abandoned by the *next* startup. Anything long-running and
memory-hungry therefore has to look where a `try` cannot, which is what this module is for
(docs/design-decisions.md #41).

Everything here degrades to `None` rather than raising. On a dev machine there is no `/proc` and
no cgroup, and a caller that cannot read a number must carry on rather than refuse to work.
"""
from __future__ import annotations

import os
from typing import Optional

_PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096

_V2_MAX = "/sys/fs/cgroup/memory.max"
_V2_STAT = "/sys/fs/cgroup/memory.stat"
_V1_MAX = "/sys/fs/cgroup/memory/memory.limit_in_bytes"
_V1_STAT = "/sys/fs/cgroup/memory/memory.stat"

#: cgroup v1 writes "no limit" as a number near the word size rather than as a word, so a limit
#: above this is the absence of one.
_NO_LIMIT = 1 << 62


def _read(path: str) -> Optional[str]:
    try:
        with open(path, encoding="ascii") as fh:
            return fh.read()
    except OSError:
        return None


def _stat_field(path: str, field: str) -> Optional[int]:
    text = _read(path)
    if text is None:
        return None
    for line in text.splitlines():
        name, _, value = line.partition(" ")
        if name == field:
            try:
                return int(value)
            except ValueError:
                return None
    return None


def resident_bytes() -> Optional[int]:
    """This process's resident set size."""
    text = _read("/proc/self/statm")
    if text is None:
        return None
    try:
        return int(text.split()[1]) * _PAGE_SIZE
    except (IndexError, ValueError):
        return None


def in_use_bytes() -> Optional[int]:
    """What the container holds that the kernel cannot simply take back.

    Deliberately **not** cgroup v2's `memory.current`: that counts the page cache, which a
    backtest's SQLite reads inflate by hundreds of megabytes and which the kernel drops long
    before it kills anything. Tripping on it would refuse runs that were never in danger.
    `anon` is the part with nowhere to go -- every Python object in every process in here -- and
    it is what the OOM killer is actually reacting to.

    Falls back to this process's own resident set where the cgroup files are absent, which reads
    low by whatever the sibling processes hold; a caller sizing a reserve should assume that.
    """
    for path, field in ((_V2_STAT, "anon"), (_V1_STAT, "rss")):
        value = _stat_field(path, field)
        if value is not None:
            return value
    return resident_bytes()


def limit_bytes() -> Optional[int]:
    """The container's memory ceiling -- the number the kernel OOM-kills against.

    Falls back to the host's total RAM when the container is uncapped, because an uncapped
    container on a 2 GiB instance still cannot use more than the instance has.
    """
    for path in (_V2_MAX, _V1_MAX):
        raw = (_read(path) or "").strip()
        if not raw or raw == "max":
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        if 0 < value < _NO_LIMIT:
            return value
    return _total_ram()


def _total_ram() -> Optional[int]:
    text = _read("/proc/meminfo")
    if text is None:
        return None
    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            try:
                return int(line.split()[1]) * 1024
            except (IndexError, ValueError):
                return None
    return None


def usage() -> Optional[tuple[int, int]]:
    """`(bytes held, bytes allowed)`, or None when either cannot be read."""
    held, cap = in_use_bytes(), limit_bytes()
    if held is None or cap is None or cap <= 0:
        return None
    return held, cap


def describe(held: int, cap: int) -> str:
    """The reading as a sentence fragment, for a message a user will read."""
    return f"{held / 1e9:.2f} GB of the {cap / 1e9:.2f} GB this app is allowed"
