"""FastAPI dependencies for deployment license enforcement."""
from fastapi import HTTPException

from icici_breeze_backend.app.services.deployment_license_status import (
    REVOKED_TRADING_MESSAGE,
    trading_mutations_allowed,
)


def require_trading_not_revoked() -> None:
    """Block trading mutations when deployment license is revoked."""
    if not trading_mutations_allowed():
        raise HTTPException(status_code=403, detail=REVOKED_TRADING_MESSAGE)
