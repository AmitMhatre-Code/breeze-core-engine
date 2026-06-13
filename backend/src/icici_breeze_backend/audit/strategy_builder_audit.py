"""Per-session audit log for Strategy Builder (New) propose-trades runs."""
from __future__ import annotations

import io
import json
import logging
import os
import re
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Any, Literal

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.audit.strategy_evaluation_audit import (
    AUDIT_SCHEMA_VERSION,
    AuditDetailLevel,
    SessionTelemetry,
    StrategyAuditCollector,
    build_configuration_snapshot,
)

_logger = logging.getLogger(__name__)

_AUDIT_SUBDIR = "strategy-builder-audit"
_MAX_AUDIT_LOGS_PER_USER = 10


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
        audit_detail_level: AuditDetailLevel = "summary",
        strategy_ids: list[str] | None = None,
    ) -> None:
        self.session_id = str(uuid.uuid4())
        self.user_id = user_id
        self.request_id = request_id
        self.audit_detail_level: AuditDetailLevel = audit_detail_level
        self.started_at = _utc_now()
        self._started_mono = datetime.now(timezone.utc)
        self.request = request
        self.events: list[dict[str, Any]] = []
        self._seq = 0
        self._api_call_stats: dict[str, dict[str, int]] = {}
        self._temp_liquid_cache: dict[str, Any] | None = None
        self.telemetry = SessionTelemetry()
        self.strategy_evaluations: dict[str, dict[str, Any]] = {}
        self._configuration_snapshot = build_configuration_snapshot(
            request,
            strategy_ids or [],
        )
        self._active_collectors: dict[str, StrategyAuditCollector] = {}
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

    def begin_strategy_collector(self, strategy_id: str) -> StrategyAuditCollector:
        collector = StrategyAuditCollector(
            strategy_id=strategy_id,
            detail_level=self.audit_detail_level,
        )
        self._active_collectors[strategy_id] = collector
        return collector

    def finish_strategy_collector(self, collector: StrategyAuditCollector | None) -> None:
        if collector is None:
            return
        self.strategy_evaluations[collector.strategy_id] = collector.to_dict()
        self._active_collectors.pop(collector.strategy_id, None)

    def record_icici_api_call(
        self,
        api: str,
        request: dict[str, Any],
        response: dict[str, Any] | None,
        *,
        rationale: str | None = None,
        error: str | None = None,
        context: dict[str, Any] | None = None,
        latency_ms: float | None = None,
    ) -> None:
        """Record one breeze-connect SDK method invocation (e.g. get_option_chain_quotes)."""
        ok = (response or {}).get("Status") == 200
        self._bump_api_stat(api, success=ok)
        if latency_ms is not None:
            if api == "get_option_chain_quotes":
                self.telemetry.record_quote_latency(latency_ms)
            elif api == "margin_calculator":
                self.telemetry.record_margin_latency(latency_ms)
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
        strategy_id: str | None = None,
    ) -> None:
        if self.audit_detail_level == "summary" and strategy_id:
            slim_outputs = {
                k: v
                for k, v in outputs.items()
                if k
                not in (
                    "candidates_evaluated",
                    "rejection_samples",
                    "samples",
                )
            }
            slim_outputs["see_strategy_evaluations"] = strategy_id
            outputs = slim_outputs
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

    def finalize(
        self,
        summary: dict[str, Any],
        user_explainability: dict[str, Any] | None = None,
    ) -> str:
        finished = datetime.now(timezone.utc)
        duration_ms = int((finished - self._started_mono).total_seconds() * 1000)
        quote_calls = self._api_call_stats.get("get_option_chain_quotes", {}).get("total", 0)
        margin_calls = self._api_call_stats.get("margin_calculator", {}).get("total", 0)
        document = {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "request_id": self.request_id,
            "source": "strategy_builder_new",
            "audit_schema_version": AUDIT_SCHEMA_VERSION,
            "audit_detail_level": self.audit_detail_level,
            "started_at": self.started_at,
            "finished_at": finished.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "duration_ms": duration_ms,
            "audit_file": self.file_path,
            "request": self.request,
            "configuration_snapshot": self._configuration_snapshot,
            "telemetry": self.telemetry.to_dict(
                quote_calls=quote_calls,
                margin_calls=margin_calls,
                total_execution_ms=duration_ms,
            ),
            "strategy_evaluations": self.strategy_evaluations,
            "icici_api_calls": self.icici_api_call_stats,
            "temp_liquid_cache": self._temp_liquid_cache,
            "event_count": len(self.events),
            "events": self.events,
            "summary": summary,
        }
        if user_explainability is not None:
            document["user_explainability"] = user_explainability
        try:
            enforce_audit_retention(self.user_id)
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


def _read_audit_doc(path: str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _audit_files_for_user(user_id: str) -> list[tuple[str, float]]:
    """Return (path, mtime) for audit JSON files owned by user_id."""
    if not user_id:
        return []
    root = audit_log_dir()
    if not os.path.isdir(root):
        return []
    out: list[tuple[str, float]] = []
    for fname in os.listdir(root):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(root, fname)
        if not os.path.isfile(path):
            continue
        doc = _read_audit_doc(path)
        if doc and doc.get("user_id") == user_id:
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            out.append((path, mtime))
    return out


def list_audit_files_for_user(user_id: str) -> list[str]:
    """Return absolute paths for user's audit logs, newest first."""
    files = _audit_files_for_user(user_id)
    files.sort(key=lambda item: item[1], reverse=True)
    return [path for path, _mtime in files]


def _audit_index_row(doc: dict[str, Any], path: str) -> dict[str, Any]:
    request = doc.get("request") or {}
    return {
        "session_id": doc.get("session_id"),
        "started_at": doc.get("started_at"),
        "finished_at": doc.get("finished_at"),
        "stock_code": request.get("stock_code"),
        "expiry_date": request.get("expiry_date"),
        "min_pop_pct": request.get("min_pop_pct"),
        "margin_lacs": request.get("margin_lacs"),
        "max_loss_lacs": request.get("max_loss_lacs"),
        "provision_elm": request.get("provision_elm"),
        "risk_reward_profile": request.get("risk_reward_profile"),
        "strategy_category": request.get("strategy_category"),
        "event_count": doc.get("event_count"),
        "filename": os.path.basename(path),
    }


def list_audit_log_index_for_user(user_id: str) -> list[dict[str, Any]]:
    """Metadata for each retained audit log, newest first."""
    entries: list[tuple[float, dict[str, Any]]] = []
    for path, mtime in _audit_files_for_user(user_id):
        doc = _read_audit_doc(path)
        if not doc:
            continue
        entries.append((mtime, _audit_index_row(doc, path)))
    entries.sort(key=lambda item: item[0], reverse=True)
    return [meta for _mtime, meta in entries]


def enforce_audit_retention(
    user_id: str, *, max_logs: int = _MAX_AUDIT_LOGS_PER_USER
) -> None:
    """Delete oldest audit files so at most max_logs - 1 remain before a new write."""
    if not user_id or max_logs < 1:
        return
    files = _audit_files_for_user(user_id)
    files.sort(key=lambda item: item[1])
    while len(files) >= max_logs:
        path, _mtime = files.pop(0)
        try:
            os.remove(path)
            _logger.info("Removed old strategy builder audit: %s", path)
        except OSError as exc:
            _logger.warning("Failed to remove old strategy builder audit %s: %s", path, exc)


def build_audit_zip_for_user(user_id: str) -> tuple[bytes, str]:
    """Zip all retained audit JSON files for user_id."""
    paths = list_audit_files_for_user(user_id)
    if not paths:
        raise FileNotFoundError(f"No strategy builder audit logs for user {user_id}")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in paths:
            zf.write(path, arcname=os.path.basename(path))
    token = _safe_token(user_id, 16)
    return buf.getvalue(), f"strategy-builder-audits-{token}.zip"


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
