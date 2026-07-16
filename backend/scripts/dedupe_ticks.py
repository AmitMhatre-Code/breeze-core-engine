#!/usr/bin/env python3
"""Dev helper: collapse a raw WS tick capture down to one example per unique shape.

Usage:
    BWS_TICK_DEBUG_LOG_PATH=/tmp/ticks.jsonl ./dev.sh   # capture a session, then Ctrl+C
    python3 backend/scripts/dedupe_ticks.py /tmp/ticks.jsonl

Reads a JSONL file of {"ts": <float>, "tick": {...}} lines (as written by
BWS_TICK_DEBUG_LOG_PATH in breeze_websocket_manager.py) and prints one
example per distinct tick "shape" -- shape meaning the set of keys present,
since ICICI sends structurally different dicts for quote ticks, order
notifications, and OHLC ticks on the same on_ticks callback.
"""
from __future__ import annotations

import json
import sys
from collections import Counter


def main() -> None:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <path-to-jsonl>", file=sys.stderr)
        raise SystemExit(1)

    shapes: dict[tuple[str, ...], dict] = {}
    counts: Counter[tuple[str, ...]] = Counter()
    total = 0
    malformed = 0

    with open(sys.argv[1]) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                row = json.loads(line)
                tick = row["tick"]
            except (json.JSONDecodeError, KeyError, TypeError):
                malformed += 1
                continue
            if not isinstance(tick, dict):
                shape = ("<non-dict>",)
            else:
                shape = tuple(sorted(tick.keys()))
            counts[shape] += 1
            shapes.setdefault(shape, tick)

    print(f"# {total} ticks read ({malformed} malformed), {len(shapes)} distinct shapes\n")
    for shape, count in counts.most_common():
        print(f"--- shape ({count} ticks) ---")
        print(f"keys: {list(shape)}")
        print(json.dumps(shapes[shape], indent=2, default=str))
        print()


if __name__ == "__main__":
    main()
