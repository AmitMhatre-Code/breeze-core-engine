"""HttpOnly auth cookie names and TTLs (ICICI bootstrap, broker recovery)."""

DIRECT_ICICI_COOKIE = "direct_icici_bootstrap"
BROKER_RECOVERY_BOOTSTRAP_COOKIE = "broker_recovery_bootstrap"
BROKER_RECOVERY_PENDING_COOKIE = "broker_recovery_pending"
BROKER_RECOVERY_TOKEN_COOKIE = "broker_recovery_token"

# Short-lived bootstrap cookies (password login → ICICI, or recovery start → ICICI)
COOKIE_MAX_AGE = 300

# After broker + challenge, user may take a few minutes to type new password
RECOVERY_TOKEN_MAX_AGE = 600
