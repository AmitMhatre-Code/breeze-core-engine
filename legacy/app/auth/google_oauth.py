"""Google OAuth client for registration, login, correction, and delete flows."""
import logging
from typing import Optional

from authlib.integrations.starlette_client import OAuth

import app.core.config as cfg

logger = logging.getLogger(__name__)

_oauth: Optional[OAuth] = None


def get_oauth() -> OAuth:
    """Lazy-init OAuth client. Requires SessionMiddleware for state storage."""
    global _oauth
    if _oauth is None:
        _oauth = OAuth()
        _oauth.register(
            name="google",
            client_id=cfg.GOOGLE_CLIENT_ID or "dummy",
            client_secret=cfg.GOOGLE_CLIENT_SECRET or "dummy",
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
    return _oauth


def is_google_oauth_configured() -> bool:
    """True if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are set."""
    return bool(
        (cfg.GOOGLE_CLIENT_ID or "").strip()
        and (cfg.GOOGLE_CLIENT_SECRET or "").strip()
    )
