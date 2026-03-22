"""Direct ICICI Breeze API calls (bypasses breeze_connect SDK for checksum issues)."""
import hashlib
import hmac as _hmac
import base64 as _b64
import json
import logging
from datetime import datetime
from typing import Optional

_logger = logging.getLogger(__name__)

_CUSTOMER_DETAILS_URL = "https://api.icicidirect.com/breezeapi/api/v1/customerdetails"


def _record_after_httpx_breeze(url: str, user_id: Optional[str]) -> None:
    try:
        from icici_breeze_backend.app.services.api_usage import record_breeze_call_if_in_request, record_call

        uid = (user_id or "").strip()
        if uid:
            record_call(uid)
        else:
            record_breeze_call_if_in_request(url)
    except Exception:
        pass


def fetch_customerdetails_session_token(
    api_key: str, broker_token: str, user_id: Optional[str] = None
) -> str:
    """Fetch raw session_token from CustomerDetails API. Returns base64 token for X-SessionToken."""
    import httpx

    body = json.dumps({"SessionToken": broker_token, "AppKey": api_key}, separators=(",", ":"))
    r = httpx.request(
        "GET",
        _CUSTOMER_DETAILS_URL,
        content=body.encode("utf-8"),
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    _record_after_httpx_breeze(_CUSTOMER_DETAILS_URL, user_id)
    data = r.json() if r.text else {}
    succ = data.get("Success") or data.get("success") or {}
    if not isinstance(succ, dict):
        succ = {}
    return (succ.get("session_token") or succ.get("sessionToken") or "").strip()


def call_icici_api_direct(
    url: str,
    payload_dict: dict,
    api_key: str,
    secret: str,
    session_token: str,
    user_id: str = None,
    x_session_token: str = None,
) -> dict:
    """Call ICICI Breeze API directly with documented checksum."""
    time_stamp = datetime.utcnow().isoformat()[:19] + ".000Z"
    payload = json.dumps(payload_dict, separators=(",", ":"))
    body_bytes = payload.encode("utf-8")
    if x_session_token:
        x_session = x_session_token
    else:
        x_session = _b64.b64encode((f"{user_id or ''}:{session_token}").encode("ascii")).decode("ascii") if user_id else str(session_token)

    def _do_request(checksum_val: str) -> dict:
        import httpx

        headers = {
            "Content-Type": "application/json",
            "X-Checksum": "token " + checksum_val,
            "X-Timestamp": time_stamp,
            "X-AppKey": api_key,
            "X-SessionToken": x_session,
        }
        r = httpx.request("GET", url, content=body_bytes, headers=headers, timeout=30)
        _record_after_httpx_breeze(url, user_id)
        return r.json() if r.text else {}

    checksum_sha = hashlib.sha256((time_stamp + payload + secret).encode("utf-8")).hexdigest()
    out = _do_request(checksum_sha)
    err = (out.get("Error") or out.get("error") or "").lower()
    if "invalid checksum" in err:
        raw = (time_stamp + "\r\n" + payload).encode("utf-8")
        checksum_hmac = _b64.b64encode(_hmac.new(secret.encode("utf-8"), raw, digestmod=hashlib.sha256).digest()).decode("ascii")
        out = _do_request(checksum_hmac)
    if (out.get("Status") or out.get("status")) not in (200, None):
        _logger.warning(
            "ICICI API direct call failed: endpoint=%s status=%s error=%r",
            url.split("/")[-1], out.get("Status") or out.get("status"),
            out.get("Error") or out.get("error"),
        )
    return out
