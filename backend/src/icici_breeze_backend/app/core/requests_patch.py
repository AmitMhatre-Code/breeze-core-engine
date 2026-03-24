"""Patch requests so GET with data= sends body (breeze_connect uses GET+body).
Also records each HTTP request to a Breeze REST endpoint as one API call (per ICICI definition).

breeze_connect uses ``import requests`` then ``requests.get`` / ``requests.post`` etc. Those
functions live in ``requests.api`` and call ``requests.api.request`` by name. Patching only
``requests.request`` on the top-level package leaves ``requests.api.request`` unchanged, so
almost no SDK traffic was counted. We patch ``requests.api.request`` (and align
``requests.request`` / ``requests.get``).
"""
import logging
import requests as _requests

_orig_request = _requests.request
_logger = logging.getLogger(__name__)


def _is_breeze_url(url):
    return url and ("breezeapi" in str(url) or "icicidirect" in str(url))


def _preview_for_log(text: str, max_len: int = 320) -> str:
    """Single-line preview for logs (avoid multi-line log spam)."""
    if not text:
        return ""
    one = " ".join(str(text).split())
    return one if len(one) <= max_len else one[: max_len - 3] + "..."


def _looks_like_html_body(text: str) -> bool:
    if not text or len(text) < 15:
        return False
    low = text.lstrip()[:200].lower()
    return low.startswith("<!doctype") or low.startswith("<html")


def _log_breeze_parse_failure(
    *,
    reason: str,
    method: str,
    url: str,
    http_status: int,
    content_type: str | None,
    body_len: int,
    body_preview: str,
) -> None:
    _logger.warning(
        "breeze_http_response_unusable reason=%s method=%s url=%s http_status=%s content_type=%s body_len=%s html_like=%s preview=%r",
        reason,
        method,
        url,
        http_status,
        content_type or "",
        body_len,
        _looks_like_html_body(body_preview),
        _preview_for_log(body_preview),
    )


class _SafeBreezeResponse:
    """Wraps Breeze API response so .json() never raises on 5xx/HTML; SDK gets Status/Error dict instead."""

    def __init__(self, raw, method: str = "?", url: str = ""):
        self._raw = raw
        self._method = method
        self._url = url or ""

    @property
    def status_code(self):
        return self._raw.status_code

    @property
    def text(self):
        return getattr(self._raw, "text", None) or ""

    def raise_for_status(self):
        return self._raw.raise_for_status()

    def iter_content(self, chunk_size=1024, decode_unicode=False):
        return self._raw.iter_content(chunk_size=chunk_size, decode_unicode=decode_unicode)

    def json(self):
        text = self.text
        ct = (self._raw.headers.get("Content-Type") or "").split(";")[0].strip() or None
        if self._raw.status_code != 200:
            _log_breeze_parse_failure(
                reason="non_200_http",
                method=self._method,
                url=self._url,
                http_status=int(self._raw.status_code),
                content_type=ct,
                body_len=len(text),
                body_preview=text[:800] if text else "",
            )
            return {
                "Status": self._raw.status_code,
                "Error": (text[:500] if text else "Request failed"),
            }
        try:
            data = self._raw.json()
        except Exception as ex:
            _log_breeze_parse_failure(
                reason="json_decode_error",
                method=self._method,
                url=self._url,
                http_status=int(self._raw.status_code),
                content_type=ct,
                body_len=len(text),
                body_preview=text[:800] if text else "",
            )
            _logger.debug("breeze_json_decode_error detail: %s", ex, exc_info=True)
            return {"Status": 502, "Error": "Invalid response"}
        # ICICI occasionally returns HTTP 200 with body `null`; breeze_connect then does response.get(...)
        if data is None:
            _log_breeze_parse_failure(
                reason="json_body_null",
                method=self._method,
                url=self._url,
                http_status=int(self._raw.status_code),
                content_type=ct,
                body_len=len(text),
                body_preview=text[:800] if text else "",
            )
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
    m = method.upper()
    u = str(url) if url else ""
    if m == "GET" and kwargs.get("data") is not None and isinstance(kwargs["data"], (str, bytes)):
        from requests import PreparedRequest, Session

        d = kwargs.pop("data")
        body = d if isinstance(d, bytes) else d.encode("utf-8")
        prep = PreparedRequest()
        prep.prepare_method("GET")
        prep.prepare_url(url, None)
        prep.headers = dict(kwargs.get("headers") or {})
        prep.body = body
        prep.headers["Content-Length"] = str(len(body))
        try:
            out = Session().send(prep, timeout=kwargs.get("timeout"))
        except Exception as e:
            if _is_breeze_url(u):
                _logger.warning(
                    "breeze_http_transport_error method=%s url=%s err=%s",
                    m,
                    u,
                    e,
                    exc_info=True,
                )
            raise
        _record_breeze_call(url)
        return _SafeBreezeResponse(out, method=m, url=u) if _is_breeze_url(u) else out
    try:
        out = _orig_request(method, url, **kwargs)
    except Exception as e:
        if _is_breeze_url(u):
            _logger.warning(
                "breeze_http_transport_error method=%s url=%s err=%s",
                m,
                u,
                e,
                exc_info=True,
            )
        raise
    _record_breeze_call(url)
    return _SafeBreezeResponse(out, method=m, url=u) if _is_breeze_url(u) else out


def apply_requests_patch() -> None:
    """Apply the GET+body patch and API counting. Call before importing breeze_connect."""
    import requests.api as _reqapi

    _reqapi.request = _patched_request
    _requests.request = _patched_request
    # Delegate to standard get (it calls ``request`` → patched ``requests.api.request``).
    _requests.get = _reqapi.get
