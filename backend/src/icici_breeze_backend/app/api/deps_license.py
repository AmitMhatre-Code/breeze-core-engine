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


def allowed_in_read_only() -> None:
    """Marks a route that stays open in read-only mode, because it can only reduce risk.

    Read-only mode exists to stop an unlicensed deployment *trading*; it must never stop a
    user leaving a trade (B-09). Read-only is also not only a revoked licence: it is any
    stretch with no verified heartbeat, and at that moment the user may hold open positions.
    So cancelling an order, disarming or arming a stop, cancelling a GTT, deleting a parked
    order and switching a bot off are always allowed. Anything that opens or changes a
    position keeps `require_trading_not_revoked`.
    """
    return None
