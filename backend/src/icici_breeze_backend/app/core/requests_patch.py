"""Patch requests so GET with data= sends body (breeze_connect uses GET+body).
Also records each HTTP request to a Breeze REST endpoint as one API call (per ICICI definition).
"""
import requests as _requests

_orig_get = _requests.get
_orig_request = _requests.request


def _is_breeze_url(url):
    return url and ("breezeapi" in str(url) or "icicidirect" in str(url))


class _SafeBreezeResponse:
    """Wraps Breeze API response so .json() never raises on 5xx/HTML; SDK gets Status/Error dict instead."""

    def __init__(self, raw):
        self._raw = raw

    @property
    def status_code(self):
        return self._raw.status_code

    @property
    def text(self):
        return getattr(self._raw, "text", None) or ""

    def json(self):
        if self._raw.status_code != 200:
            return {
                "Status": self._raw.status_code,
                "Error": (self.text[:500] if self.text else "Request failed"),
            }
        try:
            data = self._raw.json()
        except Exception:
            return {"Status": 502, "Error": "Invalid response"}
        # ICICI occasionally returns HTTP 200 with body `null`; breeze_connect then does response.get(...)
        if data is None:
            return {"Status": 502, "Error": "Empty API response"}
        return data


def _record_breeze_call(url):
    """Count one API call per HTTP request to Breeze (lazy import to avoid circular deps)."""
    try:
        from icici_breeze_backend.app.services.api_usage import record_breeze_call_if_in_request
        record_breeze_call_if_in_request(str(url) if url else "")
    except Exception:
        pass


def _patched_get(url, **kwargs):
    data = kwargs.pop("data", None)
    has_data = data is not None and isinstance(data, (str, bytes))
    if has_data:
        from requests import PreparedRequest, Session
        from requests.structures import CaseInsensitiveDict
        body = data if isinstance(data, bytes) else data.encode("utf-8")
        prep = PreparedRequest()
        prep.prepare_method("GET")
        prep.prepare_url(url, None)
        hdrs = kwargs.get("headers") or {}
        prep.headers = CaseInsensitiveDict(hdrs) if hdrs else CaseInsensitiveDict()
        prep.body = body
        prep.headers["Content-Length"] = str(len(body))
        if "Content-Type" not in prep.headers and isinstance(data, str):
            prep.headers["Content-Type"] = "application/json"
        out = Session().send(prep, timeout=kwargs.get("timeout"))
        _record_breeze_call(url)
        return _SafeBreezeResponse(out) if _is_breeze_url(url) else out
    if data is not None:
        kwargs["data"] = data
    # Don't count here: _orig_get delegates to requests.request, which we also patch, so we count once in _patched_request only.
    return _orig_get(url, **kwargs)


def _patched_request(method, url, **kwargs):
    if method.upper() == "GET" and kwargs.get("data") is not None and isinstance(kwargs["data"], (str, bytes)):
        from requests import PreparedRequest, Session
        d = kwargs.pop("data")
        body = d if isinstance(d, bytes) else d.encode("utf-8")
        prep = PreparedRequest()
        prep.prepare_method("GET")
        prep.prepare_url(url, None)
        prep.headers = dict(kwargs.get("headers") or {})
        prep.body = body
        prep.headers["Content-Length"] = str(len(body))
        out = Session().send(prep, timeout=kwargs.get("timeout"))
        _record_breeze_call(url)
        return _SafeBreezeResponse(out) if _is_breeze_url(url) else out
    out = _orig_request(method, url, **kwargs)
    _record_breeze_call(url)
    return _SafeBreezeResponse(out) if _is_breeze_url(url) else out


def apply_requests_patch() -> None:
    """Apply the GET+body patch to requests. Call before importing breeze_connect."""
    _requests.get = _patched_get
    _requests.request = _patched_request
