"""GenAI market outlook orchestration."""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
import feedparser
import httpx

from icici_breeze_backend.app.auth.ai_provider_keys import AiProviderConfig
from icici_breeze_backend.app.domain.outlook_defaults import (
    DEFAULT_OUTLOOK_FEEDS,
    DEFAULT_OUTLOOK_PROMPT_TEMPLATE,
    DEFAULT_OUTLOOK_SYSTEM_PROMPT,
)
from icici_breeze_backend.app.domain.outlook_api import (
    OUTLOOK_DISCLAIMER,
    OutlookResponse,
    OutlookSource,
)

logger = logging.getLogger(__name__)

_ENGLISH_ASCII_RE = re.compile(r"^[\x00-\x7F\s]+$")
_BAD_ADVICE_RE = re.compile(r"\b(buy now|sell now|guaranteed return|sure-shot)\b", re.IGNORECASE)
_GEMINI_DEFAULT_MODELS = ("gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash-latest")


def _ordered_models(*model_groups: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for group in model_groups:
        for model in group:
            m = str(model or "").strip()
            if not m or m in seen:
                continue
            seen.add(m)
            out.append(m)
    return tuple(out)


@dataclass
class CachedOutlook:
    payload: dict[str, Any]
    created_at: float


class OutlookError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class OutlookService:
    def __init__(self) -> None:
        self._cache: dict[str, CachedOutlook] = {}

    def get_market_outlook(
        self,
        *,
        ai_cfg: AiProviderConfig,
        force_refresh: bool = False,
        max_age_minutes: int = 120,
        feeds: Optional[list[tuple[str, str]]] = None,
        prompt_template: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> dict[str, Any]:
        key = f"market:{datetime.now(timezone.utc).strftime('%Y%m%d%H')}"
        return self._get_outlook(
            cache_key=key,
            ai_cfg=ai_cfg,
            force_refresh=force_refresh,
            max_age_minutes=max_age_minutes,
            feeds=feeds,
            prompt_template=prompt_template,
            system_prompt=system_prompt,
        )

    def _get_outlook(
        self,
        *,
        cache_key: str,
        ai_cfg: AiProviderConfig,
        force_refresh: bool,
        max_age_minutes: int,
        feeds: Optional[list[tuple[str, str]]],
        prompt_template: Optional[str],
        system_prompt: Optional[str],
    ) -> dict[str, Any]:
        max_age_seconds = max(1, max_age_minutes) * 60
        now = time.time()
        if not force_refresh:
            cached = self._cache.get(cache_key)
            if cached and now - cached.created_at <= max_age_seconds:
                return cached.payload
            latest_cached = self.get_cached()
            if latest_cached:
                return latest_cached
            raise OutlookError("no_cached_outlook", "No cached outlook yet. Click refresh to generate.")

        sources = self._collect_sources(limit=8, feeds=feeds)
        prompt = self._build_prompt(sources=sources, prompt_template=prompt_template)
        generated = self._generate_with_provider(ai_cfg=ai_cfg, prompt=prompt, system_prompt=system_prompt)
        validated = self._validate_payload(generated)
        self._cache[cache_key] = CachedOutlook(payload=validated, created_at=now)
        return validated

    def get_cached(self) -> Optional[dict[str, Any]]:
        candidates = [(k, v) for k, v in self._cache.items() if k.startswith("market:")]
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[1].created_at, reverse=True)
        return candidates[0][1].payload

    def _collect_sources(
        self,
        *,
        limit: int,
        feeds: Optional[list[tuple[str, str]]],
    ) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        base_feeds = feeds or list(DEFAULT_OUTLOOK_FEEDS)
        feed_list = list(base_feeds)

        for provider, url in feed_list:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:3]:
                    collected.append(
                        {
                            "title": str(entry.get("title") or "").strip(),
                            "url": str(entry.get("link") or "").strip(),
                            "publisher": provider,
                            "published_at": str(entry.get("published") or entry.get("updated") or "").strip() or None,
                            "summary": str(entry.get("summary") or "").strip(),
                        }
                    )
            except Exception:
                logger.warning("source_fetch_failed provider=%s", provider, exc_info=True)
        return [item for item in collected if item["title"] and item["url"]][:limit]

    def _build_prompt(
        self,
        *,
        sources: list[dict[str, Any]],
        prompt_template: Optional[str],
    ) -> str:
        schema = {
            "outlook_type": "market",
            "summary": ["bullet", "bullet"],
            "inference": {
                "volatility_view": "string",
                "movement_scenarios": ["string"],
                "confidence": "low|medium|high",
                "caveats": ["string"],
            },
            "strategy_ideas": [{"tag": "string", "rationale": "string", "risk_note": "string"}],
            "sources": [{"title": "string", "url": "string", "publisher": "string", "published_at": "string|null"}],
        }
        tpl = (prompt_template or "").strip() or DEFAULT_OUTLOOK_PROMPT_TEMPLATE
        values = {
            "{scope}": "market",
            "{symbol}": "N/A",
            "{disclaimer}": OUTLOOK_DISCLAIMER,
            "{sources_json}": json.dumps(sources),
            "{required_schema_json}": json.dumps(schema),
        }
        for k, v in values.items():
            tpl = tpl.replace(k, v)
        return tpl

    def _generate_with_provider(
        self,
        *,
        ai_cfg: AiProviderConfig,
        prompt: str,
        system_prompt: Optional[str],
    ) -> dict[str, Any]:
        provider = (ai_cfg.provider or "").strip().lower()
        if provider == "openai":
            return self._call_openai(ai_cfg=ai_cfg, prompt=prompt, system_prompt=system_prompt)
        if provider == "gemini":
            return self._call_gemini(ai_cfg=ai_cfg, prompt=prompt, system_prompt=system_prompt)
        raise OutlookError("unsupported_provider", "provider must be gemini or openai")

    def _call_openai(self, *, ai_cfg: AiProviderConfig, prompt: str, system_prompt: Optional[str]) -> dict[str, Any]:
        url = "https://api.openai.com/v1/chat/completions"
        model = (ai_cfg.model or "").strip() or "gpt-4o-mini"
        sys_prompt = (system_prompt or "").strip() or DEFAULT_OUTLOOK_SYSTEM_PROMPT
        payload = {
            "model": model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": prompt},
            ],
        }
        headers = {"Authorization": f"Bearer {ai_cfg.api_key}", "Content-Type": "application/json"}
        return self._parse_provider_response(
            url=url,
            headers=headers,
            payload=payload,
            provider=f"openai:{model}",
        )

    def _call_gemini(self, *, ai_cfg: AiProviderConfig, prompt: str, system_prompt: Optional[str]) -> dict[str, Any]:
        configured = (ai_cfg.model or "").strip()
        models = _ordered_models([configured], ai_cfg.fallback_models, list(_GEMINI_DEFAULT_MODELS))
        sys_prompt = (system_prompt or "").strip() or DEFAULT_OUTLOOK_SYSTEM_PROMPT
        payload = {
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
            "contents": [
                {
                    "parts": [{"text": sys_prompt}, {"text": prompt}],
                }
            ],
        }
        last_error: Optional[OutlookError] = None
        for model in models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            headers = {"Content-Type": "application/json", "x-goog-api-key": ai_cfg.api_key}
            for attempt in range(2):
                try:
                    return self._parse_provider_response(
                        url=url,
                        headers=headers,
                        payload=payload,
                        provider=f"gemini:{model}",
                    )
                except OutlookError as exc:
                    last_error = exc
                    status = exc.status_code or 0
                    is_not_found = exc.code == "provider_error" and status == 404
                    is_transient = exc.code == "provider_error" and status in (500, 502, 503, 504)
                    if is_transient and attempt == 0:
                        logger.warning("gemini_transient_failure model=%s status=%s retrying=true", model, status)
                        time.sleep(0.8)
                        continue
                    if is_not_found or is_transient:
                        logger.warning(
                            "gemini_model_fallback model=%s status=%s trying_next=%s",
                            model,
                            status,
                            model != models[-1],
                        )
                        break
                    raise
        if last_error:
            raise last_error
        raise OutlookError("provider_error", "Gemini generation failed without a response.")

    def _parse_provider_response(
        self, *, url: str, headers: dict[str, str], payload: dict[str, Any], provider: str
    ) -> dict[str, Any]:
        start = time.perf_counter()
        try:
            with httpx.Client(timeout=35.0) as client:
                res = client.post(url, headers=headers, json=payload)
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.info(
                "outlook_provider_call provider=%s status=%s elapsed_ms=%s",
                provider,
                res.status_code,
                elapsed_ms,
            )
            provider_label = provider
            if res.status_code in (401, 403):
                raise OutlookError(
                    "invalid_api_key",
                    f"Invalid or unauthorized API key for {provider_label}.",
                    status_code=res.status_code,
                )
            if res.status_code in (429,):
                body = (res.text or "").lower()
                if "quota" in body:
                    raise OutlookError("quota_exceeded", f"Quota exceeded on {provider_label}.", status_code=429)
                raise OutlookError("rate_limit_exceeded", f"Rate limit reached on {provider_label}.", status_code=429)
            if res.status_code >= 400:
                extra = ""
                if res.status_code in (502, 503, 504):
                    extra = (
                        " The provider may be temporarily overloaded, or the model may be "
                        "unavailable or misnamed—try another model under Settings → AI provider."
                    )
                raise OutlookError(
                    "provider_error",
                    f"Provider error {res.status_code} on {provider_label}.{extra}",
                    status_code=res.status_code,
                )
            data = res.json()
            if provider.startswith("openai:") or provider == "openai":
                content = data["choices"][0]["message"]["content"]
            else:
                content = data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(content)
            return parsed
        except OutlookError:
            raise
        except Exception as exc:
            raise OutlookError("provider_error", f"Failed to generate outlook: {exc}", status_code=503) from exc

    def _validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = dict(payload or {})
        payload["outlook_type"] = "market"
        payload["as_of"] = datetime.now(timezone.utc).isoformat()
        payload["english_only"] = True
        payload["disclaimer"] = OUTLOOK_DISCLAIMER

        model = OutlookResponse.model_validate(payload)
        if not model.sources:
            raise OutlookError("validation_error", "Outlook requires at least one source.")
        for s in model.summary:
            if not _ENGLISH_ASCII_RE.match(s):
                raise OutlookError("validation_error", "Outlook summary must be English-only text.")
        encoded = model.model_dump_json()
        if _BAD_ADVICE_RE.search(encoded):
            raise OutlookError("validation_error", "Outlook included imperative investment advice language.")

        normalized = model.model_dump()
        normalized["sources"] = [OutlookSource.model_validate(src).model_dump() for src in normalized["sources"]]
        return normalized
