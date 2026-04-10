"""Gemini ListModels API: pagination and catalog shaping (no FastAPI)."""
from __future__ import annotations

import httpx
from fastapi import HTTPException

GEMINI_LIST_MODELS_PAGE_SIZE = 1000
_GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"


def dedupe_model_ids(models: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for model in models:
        m = str(model or "").strip()
        if not m or m in seen:
            continue
        seen.add(m)
        out.append(m)
    return out


def models_list_for_user(catalog_models: list[str], tracked: list[str] | None) -> list[str]:
    if not tracked:
        return catalog_models
    cat = set(catalog_models)
    return [m for m in tracked if m in cat]


def fetch_gemini_model_catalog(api_key: str) -> tuple[list[str], dict[str, str]]:
    ordered_ids: list[str] = []
    seen: set[str] = set()
    display: dict[str, str] = {}
    page_token: str | None = None
    with httpx.Client(timeout=60.0) as client:
        while True:
            params: dict[str, str] = {"pageSize": str(GEMINI_LIST_MODELS_PAGE_SIZE)}
            if page_token:
                params["pageToken"] = page_token
            res = client.get(_GEMINI_MODELS_URL, headers={"x-goog-api-key": api_key}, params=params)
            if res.status_code in (401, 403):
                raise HTTPException(status_code=400, detail="Invalid API key or unauthorized provider access")
            if res.status_code >= 400:
                raise HTTPException(status_code=400, detail=f"Could not fetch Gemini model catalog ({res.status_code})")
            payload = res.json() or {}
            for item in payload.get("models", []):
                methods = item.get("supportedGenerationMethods") or []
                if "generateContent" not in methods:
                    continue
                raw_name = str(item.get("name") or "").strip()
                name = raw_name.split("/", 1)[1] if raw_name.startswith("models/") else raw_name
                if not name or name in seen:
                    continue
                seen.add(name)
                ordered_ids.append(name)
                dn = item.get("displayName")
                if dn is not None and str(dn).strip():
                    display[name] = str(dn).strip()
            page_token = (payload.get("nextPageToken") or "").strip() or None
            if not page_token:
                break
    return ordered_ids, display
