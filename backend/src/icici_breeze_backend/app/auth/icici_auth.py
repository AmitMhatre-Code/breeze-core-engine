"""ICICI Breeze API token verification."""
import logging

logger = logging.getLogger(__name__)


def verify_icici_token(user_id: str, icici_session_token: str) -> bool:
    """Verify ICICI session token. In MVP, non-empty token is accepted."""
    try:
        return bool(icici_session_token and str(icici_session_token).strip())
    except Exception as e:
        logger.warning("ICICI token verification failed: %s", e, exc_info=True)
    return False
