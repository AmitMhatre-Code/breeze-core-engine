"""Auth domain schemas."""
from typing import List, Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Request body for /auth/login."""
    icici_token: str = Field(
        ...,
        description="Token from ICICI broker authentication",
    )
    credential_challenge_response: str = Field(
        ...,
        description="User-provided secret fragment for credential reconstruction. Use empty string for migrated credentials.",
    )
    user_id: Optional[str] = Field(
        None,
        description="Optional user identifier (e.g. from ICICI redirect).",
    )


class LogoutRequest(BaseModel):
    """Request body for /auth/logout."""
    reason: Optional[str] = Field(None, description="Optional reason (ignored)")


class UserInfo(BaseModel):
    """User information in login response."""
    user_id: str = Field(..., description="Unique user identifier")
    username: str
    email: str
    roles: List[str] = Field(default_factory=lambda: ["trader"])


class LoginResponse(BaseModel):
    """Response body for /auth/login."""
    access_token: str = Field(..., description="JWT access token")
    token_type: str = Field("Bearer", description="Token type")
    expires_in: int = Field(..., description="Expiry in seconds")
    user: UserInfo = Field(..., description="User information")


class AdminRotateRequest(BaseModel):
    """Request for admin credential rotation."""
    target_user_id: str = Field(..., description="User whose credentials to rotate")
    new_secret_fragment: str = Field(..., description="New secret fragment")


class AdminRevokeRequest(BaseModel):
    """Request for admin credential revoke."""
    target_user_id: str = Field(..., description="User whose credentials to revoke")


class LegacyLoginFormRequest(BaseModel):
    """Legacy form-based login (ICICI redirect → challenge → cookies)."""
    user_id: Optional[str] = None
    secret_user: Optional[str] = None
    action: Optional[str] = None  # LOGIN | SUBMIT


class IciciSessionRequest(BaseModel):
    """JSON body for POST /auth/icici-session (React challenge step)."""
    user_id: Optional[str] = None
    apisession: Optional[str] = None
    secret_user: Optional[str] = ""
    action: Optional[str] = None  # LOGIN | SUBMIT


class ChallengeContextResponse(BaseModel):
    """Non-secret context for the challenge page (user id from pre-ICICI cookie)."""
    user_id: Optional[str] = None
