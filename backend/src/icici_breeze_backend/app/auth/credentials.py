"""Per-user credential storage and retrieval."""
import logging
from dataclasses import dataclass
from typing import Optional
from cryptography.fernet import Fernet
from base64 import urlsafe_b64encode
import hashlib

from icici_breeze_backend.app.core.config import JWT_SECRET, DATA_PATH, USERS_DB
from icici_breeze_backend.app.core.timezone import SQLITE_NOW_IST, ist_timestamp

logger = logging.getLogger(__name__)


def _cookie_cipher(encryption_key: str):
    """Fernet cipher for session cookie (distinct key from credential storage)."""
    key_material = hashlib.sha256(((encryption_key or "") + "credential_session_cookie").encode()).digest()
    return Fernet(urlsafe_b64encode(key_material[:32]))


def encrypt_for_session_cookie(secret: str, encryption_key: str) -> str:
    """Encrypt full API secret for HttpOnly session cookie. Returns base64 string."""
    if not secret or not encryption_key:
        return ""
    try:
        return _cookie_cipher(encryption_key).encrypt(secret.encode()).decode("ascii")
    except Exception:
        return ""


def decrypt_from_session_cookie(encrypted: str, encryption_key: str) -> Optional[str]:
    """Decrypt full API secret from session cookie. Returns secret or None."""
    if not encrypted or not encryption_key:
        return None
    try:
        return _cookie_cipher(encryption_key).decrypt(encrypted.encode()).decode()
    except Exception:
        return None


def _broker_session_store_cipher(encryption_key: str):
    """Fernet for the persisted broker session token store (distinct key from the
    session cookie cipher, even though both protect the same underlying ICICI
    session token) -- see app/repositories/broker_session.py."""
    key_material = hashlib.sha256(((encryption_key or "") + "broker_session_token_store_v1").encode()).digest()
    return Fernet(urlsafe_b64encode(key_material[:32]))


def encrypt_broker_session_token(token: str, encryption_key: str) -> str:
    if not token or not encryption_key:
        return ""
    try:
        return _broker_session_store_cipher(encryption_key).encrypt(token.encode()).decode("ascii")
    except Exception:
        return ""


def decrypt_broker_session_token(encrypted: str, encryption_key: str) -> Optional[str]:
    if not encrypted or not encryption_key:
        return None
    try:
        return _broker_session_store_cipher(encryption_key).decrypt(encrypted.encode()).decode()
    except Exception:
        return None


def _direct_icici_cipher(encryption_key: str):
    """Fernet for short-lived direct-login → ICICI redirect cookie (user_id only)."""
    key_material = hashlib.sha256(((encryption_key or "") + "direct_icici_prelogin").encode()).digest()
    return Fernet(urlsafe_b64encode(key_material[:32]))


def encrypt_direct_icici_cookie(user_id: str, encryption_key: str) -> str:
    if not encryption_key or not user_id:
        return ""
    try:
        return _direct_icici_cipher(encryption_key).encrypt(user_id.encode()).decode("ascii")
    except Exception:
        return ""


def decrypt_direct_icici_cookie(encrypted: str, encryption_key: str) -> Optional[str]:
    if not encrypted or not encryption_key:
        return None
    try:
        return _direct_icici_cipher(encryption_key).decrypt(encrypted.encode()).decode()
    except Exception:
        return None


def _broker_recovery_bootstrap_cipher(encryption_key: str):
    key_material = hashlib.sha256(
        ((encryption_key or "") + "broker_recovery_bootstrap_v1").encode()
    ).digest()
    return Fernet(urlsafe_b64encode(key_material[:32]))


def encrypt_broker_recovery_bootstrap_cookie(user_id: str, encryption_key: str) -> str:
    if not encryption_key or not user_id:
        return ""
    try:
        return _broker_recovery_bootstrap_cipher(encryption_key).encrypt(user_id.encode()).decode("ascii")
    except Exception:
        return ""


def decrypt_broker_recovery_bootstrap_cookie(encrypted: str, encryption_key: str) -> Optional[str]:
    if not encrypted or not encryption_key:
        return None
    try:
        return _broker_recovery_bootstrap_cipher(encryption_key).decrypt(encrypted.encode()).decode()
    except Exception:
        return None


def _broker_recovery_pending_cipher(encryption_key: str):
    key_material = hashlib.sha256(
        ((encryption_key or "") + "broker_recovery_pending_v1").encode()
    ).digest()
    return Fernet(urlsafe_b64encode(key_material[:32]))


def encrypt_broker_recovery_pending_cookie(user_id: str, encryption_key: str) -> str:
    if not encryption_key or not user_id:
        return ""
    try:
        return _broker_recovery_pending_cipher(encryption_key).encrypt(user_id.encode()).decode("ascii")
    except Exception:
        return ""


def decrypt_broker_recovery_pending_cookie(encrypted: str, encryption_key: str) -> Optional[str]:
    if not encrypted or not encryption_key:
        return None
    try:
        return _broker_recovery_pending_cipher(encryption_key).decrypt(encrypted.encode()).decode()
    except Exception:
        return None


def _broker_recovery_token_cipher(encryption_key: str):
    key_material = hashlib.sha256(
        ((encryption_key or "") + "broker_recovery_token_v1").encode()
    ).digest()
    return Fernet(urlsafe_b64encode(key_material[:32]))


def encrypt_broker_recovery_token(user_id: str, encryption_key: str, max_age_seconds: int) -> str:
    """Opaque token: user_id + expiry (unix seconds), Fernet-encrypted."""
    import time

    if not encryption_key or not user_id or max_age_seconds <= 0:
        return ""
    exp = int(time.time()) + int(max_age_seconds)
    payload = f"{user_id}\n{exp}"
    try:
        return _broker_recovery_token_cipher(encryption_key).encrypt(payload.encode()).decode("ascii")
    except Exception:
        return ""


def decrypt_broker_recovery_token(encrypted: str, encryption_key: str) -> Optional[tuple[str, int]]:
    """Returns (user_id, exp_unix) or None if invalid/expired."""
    import time

    if not encrypted or not encryption_key:
        return None
    try:
        raw = _broker_recovery_token_cipher(encryption_key).decrypt(encrypted.encode()).decode()
        parts = raw.split("\n", 1)
        if len(parts) != 2:
            return None
        uid, exp_s = parts[0].strip(), parts[1].strip()
        exp = int(exp_s)
        if exp < int(time.time()):
            return None
        return (uid, exp)
    except Exception:
        return None


@dataclass
class UserCredentials:
    """Represents a user's broker API credentials."""
    user_id: str
    broker_api_key: str
    secret_fragment: bytes
    encryption_salt: bytes
    fragment_position: str
    created_at: str
    rotated_at: Optional[str] = None
    is_active: bool = True


class CredentialManager:
    """Manage per-user credential storage and reconstruction."""

    def __init__(self, encryption_key: str):
        self.encryption_key = encryption_key

    def store_credential_fragment(
        self, user_id: str, broker_api_key: str,
        secret_fragment: str, fragment_position: str = 'first_half'
    ) -> UserCredentials:
        try:
            cipher = Fernet(self.encryption_key.encode() if isinstance(self.encryption_key, str) else self.encryption_key)
        except Exception:
            from cryptography.fernet import Fernet
            from base64 import urlsafe_b64encode
            import hashlib
            key_material = hashlib.sha256(self.encryption_key.encode() if isinstance(self.encryption_key, str) else self.encryption_key).digest()
            key = urlsafe_b64encode(key_material[:32])
            cipher = Fernet(key)
        encrypted_fragment = cipher.encrypt(secret_fragment.encode())
        from datetime import datetime
        return UserCredentials(
            user_id=user_id, broker_api_key=broker_api_key,
            secret_fragment=encrypted_fragment, encryption_salt=b"",
            fragment_position=fragment_position,
            created_at=ist_timestamp(), is_active=True
        )

    def reconstruct_full_api_secret(self, user_id: str, user_provided_fragment: str) -> Optional[str]:
        user_fragment_raw = "" if user_provided_fragment is None else str(user_provided_fragment)
        user_fragment = user_fragment_raw.strip()
        if not (self.encryption_key or (isinstance(self.encryption_key, str) and self.encryption_key.strip())):
            logger.warning("reconstruct_full_api_secret: encryption key empty")
            return None
        try:
            cipher = Fernet(self.encryption_key.encode() if isinstance(self.encryption_key, str) else self.encryption_key)
        except Exception:
            from cryptography.fernet import Fernet
            from base64 import urlsafe_b64encode
            import hashlib
            key_material = hashlib.sha256(self.encryption_key.encode() if isinstance(self.encryption_key, str) else self.encryption_key).digest()
            key = urlsafe_b64encode(key_material[:32])
            cipher = Fernet(key)
        try:
            import sqlite3
            db_path = DATA_PATH + USERS_DB
            with sqlite3.connect(db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT secret_fragment, fragment_position FROM user_credentials WHERE user_id = ? AND is_active = 1 ORDER BY created_at DESC LIMIT 1",
                    (user_id,)
                )
                row = cur.fetchone()
            if not row or len(row) < 2:
                logger.warning("reconstruct_full_api_secret: no credential row for user_id=%s", user_id)
                return None
            secret_blob, fragment_position = row[0], row[1]
            try:
                app_fragment = cipher.decrypt(secret_blob).decode()
            except Exception:
                logger.warning("reconstruct_full_api_secret: decryption failed for user_id=%s", user_id)
                return None
            # Challenge is optional: if user didn't provide a fragment, assume DB holds full API secret.
            if not user_fragment:
                return app_fragment
            if fragment_position == 'first_half':
                full_secret = app_fragment + user_fragment
            else:
                full_secret = user_fragment + app_fragment
            return full_secret
        except Exception as e:
            logger.warning("reconstruct_full_api_secret: error for user_id=%s: %s", user_id, e)
            return None

    def rotate_credentials(self, user_id: str, new_secret_fragment: str) -> bool:
        try:
            import sqlite3
            import uuid
            from icici_breeze_backend.app.core.config import DATA_PATH
            db_path = DATA_PATH + USERS_DB
            with sqlite3.connect(db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE user_credentials SET is_active = 0, rotated_at = ? WHERE user_id = ? AND is_active = 1",
                    (ist_timestamp(), user_id)
                )
                try:
                    cipher = Fernet(self.encryption_key.encode() if isinstance(self.encryption_key, str) else self.encryption_key)
                except Exception:
                    from cryptography.fernet import Fernet
                    from base64 import urlsafe_b64encode
                    import hashlib
                    key_material = hashlib.sha256(self.encryption_key.encode() if isinstance(self.encryption_key, str) else self.encryption_key).digest()
                    key = urlsafe_b64encode(key_material[:32])
                    cipher = Fernet(key)
                encrypted = cipher.encrypt(new_secret_fragment.encode())
                credential_id = str(uuid.uuid4())
                cur.execute(
                    f"INSERT INTO user_credentials (credential_id, user_id, broker_api_key, secret_fragment, encryption_salt, fragment_position, created_at, is_active) VALUES (?, ?, ?, ?, ?, ?, {SQLITE_NOW_IST}, 1)",
                    (credential_id, user_id, '', encrypted, b'', 'first_half'),
                )
                conn.commit()
            return True
        except Exception:
            return False

    def update_credentials(self, user_id: str, broker_api_key: str, secret_fragment: str) -> bool:
        """Deactivate current creds, store new api_key + encrypted fragment (first_half). User provides remainder on challenge page."""
        if not (broker_api_key or secret_fragment):
            return False
        try:
            import sqlite3
            import uuid
            db_path = DATA_PATH + USERS_DB
            with sqlite3.connect(db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE user_credentials SET is_active = 0, rotated_at = ? WHERE user_id = ? AND is_active = 1",
                    (ist_timestamp(), user_id),
                )
                try:
                    cipher = Fernet(self.encryption_key.encode() if isinstance(self.encryption_key, str) else self.encryption_key)
                except Exception:
                    from cryptography.fernet import Fernet
                    from base64 import urlsafe_b64encode
                    import hashlib
                    key_material = hashlib.sha256(self.encryption_key.encode() if isinstance(self.encryption_key, str) else self.encryption_key).digest()
                    key = urlsafe_b64encode(key_material[:32])
                    cipher = Fernet(key)
                encrypted = cipher.encrypt(secret_fragment.encode())
                credential_id = str(uuid.uuid4())
                cur.execute(
                    f"INSERT INTO user_credentials (credential_id, user_id, broker_api_key, secret_fragment, encryption_salt, fragment_position, created_at, is_active) VALUES (?, ?, ?, ?, ?, ?, {SQLITE_NOW_IST}, 1)",
                    (credential_id, user_id, broker_api_key, encrypted, b"", "first_half"),
                )
                conn.commit()
            return True
        except Exception as e:
            logger.warning("update_credentials failed user_id=%s: %s", user_id, e)
            return False

    def revoke_credentials(self, user_id: str) -> bool:
        try:
            import sqlite3
            from icici_breeze_backend.app.core.config import DATA_PATH
            db_path = DATA_PATH + USERS_DB
            with sqlite3.connect(db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE user_credentials SET is_active = 0, rotated_at = ? WHERE user_id = ? AND is_active = 1",
                    (ist_timestamp(), user_id)
                )
                affected = cur.rowcount
                conn.commit()
            return affected > 0
        except Exception:
            return False
