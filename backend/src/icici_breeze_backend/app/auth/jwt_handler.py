"""JWT token creation and validation logic."""
from datetime import datetime, timedelta, timezone
from typing import Optional
import jwt
from pydantic import BaseModel


class TokenPayload(BaseModel):
    """JWT token payload structure. google_id = identity (PK); user_id = ICICI username for API calls."""
    google_id: Optional[str] = None  # Required for Google-only; legacy tokens lack this
    user_id: str
    username: str
    exp: int
    iat: int
    roles: list[str] = ["trader"]
    jti: Optional[str] = None


class JWTHandler:
    """Handle JWT token operations."""

    def __init__(self, secret_key: str, algorithm: str = "HS256", access_token_expire_minutes: int = 15):
        self.secret_key = secret_key
        self.algorithm = algorithm
        self.access_token_expire_minutes = access_token_expire_minutes

    def create_access_token(
        self,
        user_id: str,
        username: str,
        google_id: Optional[str] = None,
        roles: list[str] = None,
    ) -> str:
        now = datetime.now(timezone.utc)
        expire = now + timedelta(minutes=self.access_token_expire_minutes)
        payload = {
            "user_id": user_id,
            "username": username,
            "roles": roles or ["trader"],
            "exp": int(expire.timestamp()),
            "iat": int(now.timestamp()),
        }
        if google_id:
            payload["google_id"] = google_id
        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)

    def validate_token(self, token: str) -> Optional[TokenPayload]:
        try:
            decoded = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            return TokenPayload(**decoded)
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None
