"""Self-service log download route."""
from __future__ import annotations

import io
import time
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from icici_breeze_backend.app.api.deps import get_current_user
from icici_breeze_backend.app.api.v1 import route_diagnostics
from icici_breeze_backend.app.auth.context import RequestContext
from icici_breeze_backend.app.services import log_bundle


@pytest.fixture
def sink_dir(tmp_path, monkeypatch):
    directory = tmp_path / "logs"
    directory.mkdir()
    monkeypatch.setattr(log_bundle, "logs_dir", lambda: str(directory))
    return directory


def _app(authenticated: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(route_diagnostics.router, prefix="")
    if authenticated:
        async def _user():
            return RequestContext(
                user_id="trader@example.com",
                username="trader@example.com",
                roles=["trader"],
                is_authenticated=True,
                broker_token="broker-token",
            )

        app.dependency_overrides[get_current_user] = _user
    return app


@pytest.fixture
def client(sink_dir):
    with TestClient(_app()) as test_client:
        yield test_client


class TestDownload:
    def test_returns_a_zip_of_the_deployment_logs(self, client, sink_dir):
        (sink_dir / "backend.jsonl").write_text('{"message":"hi"}\n', encoding="utf-8")
        (sink_dir / "chain-builder.jsonl").write_text('{"m":"w"}\n', encoding="utf-8")
        response = client.get("/diagnostics/logs/download?days=7")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        assert set(archive.namelist()) == {"backend.jsonl", "chain-builder.jsonl"}

    def test_bundle_is_deployment_wide_not_per_user(self, client, sink_dir):
        # Deliberate: the records worth downloading (chain builder, scheduler,
        # heartbeats) carry no user id at all.
        (sink_dir / "backend.jsonl").write_text(
            '{"message":"req","user_id":"someone.else@example.com"}\n'
            '{"message":"chain builder refreshed NIFTY"}\n',
            encoding="utf-8",
        )
        response = client.get("/diagnostics/logs/download")
        body = zipfile.ZipFile(io.BytesIO(response.content)).read("backend.jsonl")
        assert b"someone.else@example.com" in body
        assert b"chain builder" in body

    def test_attachment_filename_is_set(self, client, sink_dir):
        (sink_dir / "backend.jsonl").write_text("{}\n", encoding="utf-8")
        disposition = client.get("/diagnostics/logs/download").headers[
            "content-disposition"
        ]
        assert disposition.startswith("attachment; filename=")
        assert disposition.endswith('.zip"')

    def test_response_is_not_cached(self, client, sink_dir):
        assert client.get("/diagnostics/logs/download").headers["cache-control"] == (
            "no-store"
        )

    def test_empty_directory_still_returns_a_valid_zip(self, client):
        response = client.get("/diagnostics/logs/download")
        assert response.status_code == 200
        assert zipfile.ZipFile(io.BytesIO(response.content)).namelist() == []

    @pytest.mark.parametrize("days", [0, -1, 31, 9999])
    def test_out_of_range_windows_are_rejected(self, client, days):
        assert client.get(f"/diagnostics/logs/download?days={days}").status_code == 422

    def test_requires_authentication(self, sink_dir):
        with TestClient(_app(authenticated=False)) as anon:
            assert anon.get("/diagnostics/logs/download").status_code in (401, 403)


class TestStatus:
    def test_reports_what_is_available(self, client, sink_dir):
        (sink_dir / "backend.jsonl").write_text("x" * 100, encoding="utf-8")
        payload = client.get("/diagnostics/logs/status?days=7").json()
        assert payload["enabled"] is True
        assert payload["total_bytes"] == 100
        assert payload["files"][0]["name"] == "backend.jsonl"
        assert payload["level"] == "INFO"

    def test_excludes_files_outside_the_window(self, client, sink_dir):
        old = sink_dir / "backend.jsonl.3"
        old.write_text("stale", encoding="utf-8")
        import os

        past = time.time() - 40 * 86400
        os.utime(old, (past, past))
        payload = client.get("/diagnostics/logs/status?days=7").json()
        assert payload["files"] == []
        assert payload["total_bytes"] == 0

    def test_requires_authentication(self, sink_dir):
        with TestClient(_app(authenticated=False)) as anon:
            assert anon.get("/diagnostics/logs/status").status_code in (401, 403)
