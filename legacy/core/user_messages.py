"""User-friendly error messages for UI display.

Maps technical error patterns to clear, actionable messages for end users.
Internal details are logged; users see sanitized messages.
"""
import re
import logging

_logger = logging.getLogger(__name__)

# Patterns (regex or substring) -> user message
USER_MESSAGE_MAP = [
    (r"no broker token|cookie missing|session token", "Your session has expired. Please log out and log back in."),
    (r"fetch_credentials failed|no active credentials|No credentials found", "Broker credentials not found. Please register your API credentials."),
    (r"reconstruct.*secret|JWT_SECRET empty|credential reconstruction", "Unable to verify credentials. Please log out and log back in with your secret."),
    (r"get_session_breeze|Could not create ICICI session", "Unable to connect to broker. Please log out and log back in."),
    (r"get_customer_details|Customer details", "Unable to load account details. Please try again or re-login."),
    (r"get_margin_situation|margin", "Unable to load margin information. Please try again or re-login."),
    (r"get_positions|positions", "Unable to load portfolio. Please try again or re-login."),
    (r"get_orders|orders", "Unable to load orders. Please try again or re-login."),
    (r"ICICI.*API|Breeze API|Invalid Checksum", "Broker service temporarily unavailable. Please try again in a few moments."),
    (r"timeout|timed out", "Request timed out. Please try again."),
    (r"rate limit|Too many requests", "Too many requests. Please wait a moment and try again."),
    (r"messages file|messages\.json", "Unable to load order messages. Please try again."),
    (r"stock_codes|fetch_stock_codes", "Unable to load stock list. Please try again."),
    (r"get_quote|quote", "Unable to fetch quote. Please check the symbol and try again."),
]


def to_user_message(error_text: str, default: str = "Something went wrong. Please try again.") -> str:
    """Convert technical error text to a user-friendly message.

    Args:
        error_text: Raw error string (may be technical)
        default: Fallback when no pattern matches

    Returns:
        User-friendly message suitable for UI display
    """
    if not error_text or not str(error_text).strip():
        return default
    text_lower = str(error_text).lower()
    for pattern, message in USER_MESSAGE_MAP:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return message
    # If error looks like a generic exception, use default
    if any(x in text_lower for x in ["exception", "error calling", "traceback", "none"]):
        return default
    # For known API error strings, pass through if short enough
    if len(error_text) < 120 and " " in error_text:
        return error_text
    return default


def sanitize_errors_for_ui(errors: list[dict]) -> list[dict]:
    """Add user-friendly message to each error dict for UI display.

    Each error dict may have 'location', 'contents'. Adds 'user_message' with
    a sanitized message for display.
    """
    result = []
    for err in errors or []:
        copy_err = dict(err)
        raw = err.get("contents") or err.get("Error") or err.get("location") or ""
        copy_err["user_message"] = to_user_message(str(raw))
        result.append(copy_err)
    return result
