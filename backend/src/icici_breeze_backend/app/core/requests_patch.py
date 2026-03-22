"""Patch requests so GET with data= sends body (breeze_connect uses GET+body).
Also records each HTTP request to a Breeze REST endpoint as one API call (per ICICI definition).

breeze_connect uses ``import requests`` then ``requests.get`` / ``requests.post`` etc. Those
functions live in ``requests.api`` and call ``requests.api.request`` by name. Patching only
``requests.request`` on the top-level package leaves ``requests.api.request`` unchanged, so
almost no SDK traffic was counted. We patch ``requests.api.request`` (and align
``requests.request`` / ``requests.get``).
"""
import requests as _requests

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
    """Apply the GET+body patch and API counting. Call before importing breeze_connect."""
    import requests.api as _reqapi

    _reqapi.request = _patched_request
    _requests.request = _patched_request
    # Delegate to standard get (it calls ``request`` → patched ``requests.api.request``).
    _requests.get = _reqapi.get
