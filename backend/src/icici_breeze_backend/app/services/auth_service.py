"""Authentication business logic."""
import logging

from icici_breeze_backend.app.auth.credentials import CredentialManager
import icici_breeze_backend.app.core.config as cfg

logger = logging.getLogger(__name__)


def rotate_credentials(target_user_id: str, new_secret_fragment: str) -> bool:
    """Admin: rotate credentials for a user."""
    key = (cfg.JWT_SECRET or "").strip()
    if not key:
        raise ValueError("JWT_SECRET not configured")
    mgr = CredentialManager(encryption_key=key)
    return mgr.rotate_credentials(target_user_id, new_secret_fragment)


def revoke_credentials(target_user_id: str) -> bool:
    """Admin: revoke credentials for a user."""
    key = (cfg.JWT_SECRET or "").strip()
    if not key:
        raise ValueError("JWT_SECRET not configured")
    mgr = CredentialManager(encryption_key=key)
    return mgr.revoke_credentials(target_user_id)
