"""Per-session audit log for Strategy Builder (New) propose-trades runs."""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import icici_breeze_backend.app.core.config as cfg

_logger = logging.getLogger(__name__)

_AUDIT_SUBDIR = "strategy-builder-audit"


def audit_log_dir() -> str:
    path = os.path.join(cfg.DATA_PATH, _AUDIT_SUBDIR)
    os.makedirs(path, exist_ok=True)
    return path


def _safe_token(value: str, max_len: int = 32) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", (value or "unknown").strip())
    return cleaned[:max_len] or "unknown"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    if hasattr(obj, "__dataclass_fields__"):
        return {k: getattr(obj, k) for k in obj.__dataclass_fields__}
    return str(obj)


class StrategyBuilderAuditSession:
    """Collects a full decision trail for one propose-trades build."""

    def __init__(
        self,
        *,
        user_id: str,
        request: dict[str, Any],
        request_id: str | None = None,
    ) -> None:
        self.session_id = str(uuid.uuid4())
        self.user_id = user_id
        self.request_id = request_id
        self.started_at = _utc_now()
        self._started_mono = datetime.now(timezone.utc)
        self.request = request
        self.events: list[dict[str, Any]] = []
        self._seq = 0
        self._api_call_stats: dict[str, dict[str, int]] = {}
        self._temp_liquid_cache: dict[str, Any] | None = None
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        fname = (
            f"{ts}_{_safe_token(user_id, 16)}_{_safe_token(request.get('stock_code', ''), 12)}"
            f"_{self.session_id[:8]}.json"
        )
        self.file_path = os.path.join(audit_log_dir(), fname)

    def record(
        self,
        category: str,
        message: str,
        data: dict[str, Any] | None = None,
        *,
        rationale: str | None = None,
    ) -> None:
        self._seq += 1
        entry: dict[str, Any] = {
            "seq": self._seq,
            "ts": _utc_now(),
            "category": category,
            "message": message,
        }
        if rationale:
            entry["rationale"] = rationale
        if data:
            entry["data"] = data
        self.events.append(entry)

    def _bump_api_stat(self, api: str, *, success: bool) -> None:
        stats = self._api_call_stats.setdefault(
            api, {"total": 0, "success": 0, "failed": 0}
        )
        stats["total"] += 1
        if success:
            stats["success"] += 1
        else:
            stats["failed"] += 1

    def record_icici_api_call(
        self,
        api: str,
        request: dict[str, Any],
        response: dict[str, Any] | None,
        *,
        rationale: str | None = None,
        error: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Record one breeze-connect SDK method invocation (e.g. get_option_chain_quotes)."""
        ok = (response or {}).get("Status") == 200
        self._bump_api_stat(api, success=ok)
        payload: dict[str, Any] = {
            "api": api,
            "success": ok,
            "request": request,
            "response_status": (response or {}).get("Status"),
            "response": response,
        }
        if context:
            payload["context"] = context
        if error:
            payload["error"] = error
        self.record("api_call", f"API {api}", payload, rationale=rationale)

    def record_api_call(
        self,
        api: str,
        request: dict[str, Any],
        response: dict[str, Any] | None,
        *,
        rationale: str | None = None,
        error: str | None = None,
    ) -> None:
        """Backward-compatible alias for record_icici_api_call."""
        self.record_icici_api_call(
            api, request, response, rationale=rationale, error=error
        )

    def set_temp_liquid_cache(self, snapshot: dict[str, Any]) -> None:
        """Snapshot of the in-memory quote cache used for strategy construction."""
        self._temp_liquid_cache = snapshot

    def record_calculation(
        self,
        name: str,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
        *,
        formula: str | None = None,
        rationale: str | None = None,
    ) -> None:
        data: dict[str, Any] = {"inputs": inputs, "outputs": outputs}
        if formula:
            data["formula"] = formula
        self.record("calculation", name, data, rationale=rationale)

    def record_decision(
        self,
        decision: str,
        outcome: str,
        *,
        rationale: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        data: dict[str, Any] = {"decision": decision, "outcome": outcome}
        if details:
            data["details"] = details
        self.record("decision", decision, data, rationale=rationale)

    def record_strike(
        self,
        strike: int,
        right: str,
        *,
        included: bool,
        reason: str,
        quote: dict[str, Any] | None = None,
        context: str | None = None,
    ) -> None:
        data: dict[str, Any] = {
            "strike": strike,
            "right": right,
            "included": included,
            "reason": reason,
        }
        if quote:
            data["quote"] = quote
        if context:
            data["context"] = context
        category = "strike_selected" if included else "strike_excluded"
        self.record(category, f"{right} {strike}", data, rationale=reason)

    def record_strategy_phase(self, strategy_id: str, strategy_name: str, phase: str, **data: Any) -> None:
        payload = {"strategy_id": strategy_id, "strategy_name": strategy_name, "phase": phase, **data}
        self.record("strategy", f"{strategy_name}: {phase}", payload)

    @property
    def icici_api_call_stats(self) -> dict[str, Any]:
        by_api = dict(sorted(self._api_call_stats.items()))
        total = sum(s["total"] for s in by_api.values())
        total_success = sum(s["success"] for s in by_api.values())
        total_failed = sum(s["failed"] for s in by_api.values())
        return {
            "total": total,
            "total_success": total_success,
            "total_failed": total_failed,
            "by_api": by_api,
        }

    def finalize(self, summary: dict[str, Any]) -> str:
        finished = datetime.now(timezone.utc)
        duration_ms = int((finished - self._started_mono).total_seconds() * 1000)
        document = {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "request_id": self.request_id,
            "source": "strategy_builder_new",
            "started_at": self.started_at,
            "finished_at": finished.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "duration_ms": duration_ms,
            "audit_file": self.file_path,
            "request": self.request,
            "icici_api_calls": self.icici_api_call_stats,
            "temp_liquid_cache": self._temp_liquid_cache,
            "event_count": len(self.events),
            "events": self.events,
            "summary": summary,
        }
        try:
            with open(self.file_path, "w", encoding="utf-8") as fh:
                json.dump(document, fh, indent=2, default=_json_default, ensure_ascii=False)
                fh.write("\n")
            _logger.info(
                "Strategy builder audit written: session=%s path=%s events=%d",
                self.session_id,
                self.file_path,
                len(self.events),
            )
        except OSError as exc:
            _logger.warning("Failed to write strategy builder audit %s: %s", self.file_path, exc)
        return self.file_path


def resolve_audit_file_for_user(session_id: str, user_id: str) -> str | None:
    """Return audit file path when session_id belongs to user_id."""
    if not session_id or not user_id:
        return None
    root = audit_log_dir()
    suffix = f"_{session_id[:8]}.json"
    for fname in os.listdir(root):
        if not fname.endswith(suffix):
            continue
        path = os.path.join(root, fname)
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if doc.get("session_id") == session_id and doc.get("user_id") == user_id:
            return path
    return None


def quote_row_to_audit(q: Any) -> dict[str, Any]:
    return {
        "strike": q.strike,
        "right": q.right,
        "ltp": q.ltp,
        "best_bid_price": q.best_bid_price,
        "best_offer_price": q.best_offer_price,
        "total_buy_qty": q.total_buy_qty,
        "total_sell_qty": q.total_sell_qty,
        "buy_sell_ratio": q.buy_sell_ratio,
        "spot_price": q.spot_price,
        "liquid": q.liquid,
    }
