"""FastAPI dependencies for deployment license enforcement."""
from fastapi import HTTPException

from icici_breeze_backend.app.services.deployment_license_status import (
    TRADING_READ_ONLY_MESSAGE,
    trading_mutations_allowed,
)


def require_trading_not_revoked() -> None:
    """Block trading mutations when deployment has no valid license."""
    if not trading_mutations_allowed():
        raise HTTPException(status_code=403, detail=TRADING_READ_ONLY_MESSAGE)
