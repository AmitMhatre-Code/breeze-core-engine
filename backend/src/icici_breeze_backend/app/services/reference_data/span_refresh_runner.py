"""Run the SPAN baseline refresh in a short-lived child process (design-decisions #46).

In the API process the refresh -- a ~9 MB zip parsed into ~136k contracts, then published to
Redis -- held the GIL for tens of seconds on a t4g.small and added hundreds of MB to uvicorn.
Nearly every intraday slot dropped the breeze_connect socket ("packet queue is empty,
aborting"); on 2026-09-24 the feeds stayed silent past a minute and both scalpers closed their
positions on `stale_feed`. On 2026-09-25 uvicorn was OOM-killed seconds after a publish.

A child process has its own GIL, lowers its own priority, volunteers itself to the OOM killer,
and hands every byte back when it exits. The API process remains the only initiator, and the
lock below serialises the scheduler, the daily orchestrator and the manual refresh.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

_logger = logging.getLogger(__name__)
_run_lock = threading.Lock()

WORKER_MODULE = "icici_breeze_backend.workers.span_refresh"
# An NSE ingest takes well under a minute; this only bounds a hung download.
TIMEOUT_SECONDS = 15 * 60
# .../src/icici_breeze_backend/app/services/reference_data/<this file>
_SRC_ROOT = Path(__file__).resolve().parents[4]


def refresh_all_span_baselines(*, force: bool = False) -> dict[str, dict]:
    """Refresh both markets' SPAN baselines. Same result shape as the in-process refresh."""
    from icici_breeze_backend.app.db.redis_client import redis_using_memory_fallback
    from icici_breeze_backend.app.services.reference_data.span_baseline_store import (
        reset_local_mirror,
    )

    with _run_lock:
        if redis_using_memory_fallback():
            # The child's publish would land in its own in-memory store and die with it.
            from icici_breeze_backend.app.services.nsccl_baseline import (
                refresh_all_span_baselines_in_process,
            )

            return refresh_all_span_baselines_in_process(force=force)
        try:
            return _run_child(force=force)
        finally:
            reset_local_mirror()


def _failed(error: str) -> dict[str, dict]:
    from icici_breeze_backend.app.services.reference_data.span_sources import (
        MARKET_BSE,
        MARKET_NSE,
    )

    return {market: {"Status": 500, "Error": error, "Success": None} for market in (MARKET_NSE, MARKET_BSE)}


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    paths = [str(_SRC_ROOT)] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(paths))
    return env


def _exit_reason(returncode: int) -> str:
    if returncode == -9:
        return "SPAN refresh worker was killed (SIGKILL -- most likely out of memory)"
    if returncode < 0:
        return f"SPAN refresh worker was killed by signal {-returncode}"
    return f"SPAN refresh worker exited with status {returncode}"


def _run_child(*, force: bool) -> dict[str, dict]:
    fd, result_path = tempfile.mkstemp(prefix="span-refresh-", suffix=".json")
    os.close(fd)
    cmd = [sys.executable, "-m", WORKER_MODULE, "--result-file", result_path]
    if force:
        cmd.append("--force")
    try:
        try:
            proc = subprocess.run(
                cmd,
                env=_child_env(),
                stdin=subprocess.DEVNULL,
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            _logger.warning("SPAN refresh worker timed out after %ss", TIMEOUT_SECONDS)
            return _failed(f"SPAN refresh worker timed out after {TIMEOUT_SECONDS // 60} minutes")
        except OSError as exc:
            _logger.warning("SPAN refresh worker could not start: %s", exc)
            return _failed(f"SPAN refresh worker could not start: {exc}")
        if proc.returncode != 0:
            reason = _exit_reason(proc.returncode)
            _logger.warning("%s", reason)
            return _failed(reason)
        try:
            with open(result_path, encoding="utf-8") as fh:
                results = json.load(fh)
        except (OSError, ValueError) as exc:
            return _failed(f"SPAN refresh worker left no readable result: {exc}")
        if not isinstance(results, dict) or not all(isinstance(v, dict) for v in results.values()):
            return _failed("SPAN refresh worker returned a malformed result")
        return results
    finally:
        for path in (result_path, result_path + ".tmp"):
            try:
                os.unlink(path)
            except OSError:
                pass
