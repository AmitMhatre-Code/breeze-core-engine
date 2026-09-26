"""Short-lived OS worker: fetch, ingest and publish the newest SPAN files, then exit.

Spawned by the API process (`reference_data/span_refresh_runner.py`), never by supervisord --
the API process stays the only thing that starts a refresh, so two ingests never race on
`exchange_margin_baseline`. Why this is a process and not a thread: design-decisions #46.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from icici_breeze_backend.workers._env import load_env

_logger = logging.getLogger(__name__)


def _deprioritise() -> None:
    """Yield the CPU to the trading process, and be the first thing the OOM killer takes.

    Raising our own `oom_score_adj` needs no privilege. If the container runs out of memory
    mid-ingest, losing this refresh costs one SPAN revision; losing uvicorn costs the live
    feeds, the bots' stops and every open screen.
    """
    try:
        os.nice(10)
    except (AttributeError, OSError):
        pass
    try:
        with open("/proc/self/oom_score_adj", "w", encoding="ascii") as fh:
            fh.write("1000")
    except OSError:
        pass  # not Linux (local dev on macOS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-file", required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    load_env()
    from icici_breeze_backend.app.core.logging import configure_logging

    configure_logging(level=os.environ.get("LOG_LEVEL", "INFO"), process_name="span-refresh")
    _deprioritise()

    from icici_breeze_backend.app.services.nsccl_baseline import (
        refresh_all_span_baselines_in_process,
    )

    results = refresh_all_span_baselines_in_process(force=args.force)
    tmp_path = args.result_file + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, default=str)
    os.replace(tmp_path, args.result_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
