"""Authentication business logic."""
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

from app.domain.auth import LoginRequest, LoginResponse, UserInfo
from app.auth.credentials import CredentialManager, encrypt_for_session_cookie
from app.auth.jwt_handler import JWTHandler
from app.auth.icici_auth import verify_icici_token
from app.auth.user_account import get_google_id_by_user_id
import app.core.config as cfg
from audit.logger import AuditLogger, OperationType

logger = logging.getLogger(__name__)


def _seconds_until_midnight_ist() -> int:
    """Seconds until midnight IST (ICICI token expiry)."""
    ist = ZoneInfo("Asia/Kolkata")
    now = datetime.now(ist)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return int((midnight - now).total_seconds())


async def login(
    request: LoginRequest,
    ip_address: Optional[str] = None,
    request_id: Optional[str] = None,
) -> tuple[LoginResponse, str]:
    """
    Authenticate user with ICICI token and credential challenge.

    Returns:
        Tuple of (LoginResponse, full_secret for cookie encryption)
    """
    user_id = (request.user_id or "").strip() or ("user_" + str(uuid.uuid4())[:8])

    if not verify_icici_token(user_id, request.icici_token):
        AuditLogger(None).log_operation(
            user_id, OperationType.LOGIN, "User", user_id,
            action_status="failure", error_details="ICICI token verification failed",
            request_id=request_id, ip_address=ip_address,
        )
        raise ValueError("Invalid ICICI token or expired")

    cred_manager = CredentialManager(encryption_key=cfg.JWT_SECRET)
    full_secret = cred_manager.reconstruct_full_api_secret(
        user_id, request.credential_challenge_response
    )
    if not full_secret:
        AuditLogger(None).log_operation(
            user_id, OperationType.LOGIN, "User", user_id,
            action_status="failure", error_details="Credential reconstruction failed",
            request_id=request_id, ip_address=ip_address,
        )
        raise ValueError("Invalid credential challenge response")

    with sqlite3.connect(cfg.DATA_PATH + "db.sqlite3") as conn:
        google_id = get_google_id_by_user_id(conn, user_id)
    if not google_id:
        raise ValueError("Account not found. Please register with Google.")

    handler = JWTHandler(secret_key=cfg.JWT_SECRET, access_token_expire_minutes=cfg.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = handler.create_access_token(user_id, user_id, google_id=google_id)

    AuditLogger(None).log_login(user_id, request_id=request_id, ip_address=ip_address)

    user_info = UserInfo(
        user_id=user_id,
        username=f"user_{user_id}",
        email=f"{user_id}@icicibreeze.internal",
        roles=["trader"],
    )

    response = LoginResponse(
        access_token=access_token,
        token_type="Bearer",
        expires_in=cfg.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=user_info,
    )
    return response, full_secret


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
