"""Build redirect URLs to the Next.js origin (same host in Docker) or relative paths."""
from fastapi.responses import JSONResponse, RedirectResponse

import icici_breeze_backend.app.core.config as cfg


def frontend_url(path_with_query: str) -> str:
    """Absolute URL when PUBLIC_FRONTEND_ORIGIN is set; otherwise relative."""
    p = path_with_query if path_with_query.startswith("/") else f"/{path_with_query}"
    base = (cfg.PUBLIC_FRONTEND_ORIGIN or "").strip().rstrip("/")
    return f"{base}{p}" if base else p


def redirect_to_frontend(path_with_query: str, status_code: int = 302) -> RedirectResponse:
    return RedirectResponse(url=frontend_url(path_with_query), status_code=status_code)


def map_legacy_html_path_to_spa(path_with_query: str) -> str:
    """Map old Jinja page paths to Next.js app routes."""
    if not path_with_query:
        return "/"
    if path_with_query == "/place-order" or path_with_query.startswith("/place-order?"):
        return path_with_query
    if path_with_query == "/order" or path_with_query.startswith("/order?"):
        return "/orders" + path_with_query[6:]
    if path_with_query == "/book" or path_with_query.startswith("/book?"):
        return "/orders" + path_with_query[5:]
    return path_with_query


def json_redirect(path_with_query: str) -> JSONResponse:
    """SPA POST handlers return JSON { redirect } with a path the client should navigate to."""
    p = path_with_query if path_with_query.startswith("/") else f"/{path_with_query}"
    return JSONResponse({"redirect": map_legacy_html_path_to_spa(p)})
