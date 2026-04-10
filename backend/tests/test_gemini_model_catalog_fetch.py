"""Tests for paginated Gemini ListModels fetch."""

from __future__ import annotations

import pytest

from icici_breeze_backend.app.services import gemini_model_catalog as gmc


def test_fetch_gemini_model_catalog_paginates(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str | None] = []

    class FakeResp:
        status_code = 200

        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def json(self) -> dict:
            return self._payload

    class FakeClient:
        def __init__(self, *a, **kw) -> None:
            pass

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *a) -> None:
            pass

        def get(self, url: str, headers=None, params=None) -> FakeResp:
            p = params or {}
            pt = p.get("pageToken")
            calls.append(pt)
            assert p.get("pageSize") == str(gmc.GEMINI_LIST_MODELS_PAGE_SIZE)
            if not pt:
                return FakeResp(
                    {
                        "models": [
                            {
                                "name": "models/gem-a",
                                "displayName": "Gem A",
                                "supportedGenerationMethods": ["generateContent"],
                            },
                            {
                                "name": "models/embed-b",
                                "supportedGenerationMethods": ["embedContent"],
                            },
                        ],
                        "nextPageToken": "tok1",
                    }
                )
            return FakeResp(
                {
                    "models": [
                        {
                            "name": "models/gem-c",
                            "supportedGenerationMethods": ["generateContent"],
                        },
                    ],
                }
            )

    monkeypatch.setattr(gmc.httpx, "Client", FakeClient)
    ids, display = gmc.fetch_gemini_model_catalog("fake-key")
    assert ids == ["gem-a", "gem-c"]
    assert display == {"gem-a": "Gem A"}
    assert calls == [None, "tok1"]


def test_models_list_for_user_tracked_order() -> None:
    cat = ["z", "y", "x"]
    tracked = ["x", "y"]
    assert gmc.models_list_for_user(cat, tracked) == ["x", "y"]


def test_models_list_for_user_none_tracked_returns_catalog() -> None:
    cat = ["a", "b"]
    assert gmc.models_list_for_user(cat, None) == cat
