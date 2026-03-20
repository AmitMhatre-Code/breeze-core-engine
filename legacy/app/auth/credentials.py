"""Per-user credential storage and retrieval."""
import logging
from dataclasses import dataclass
from typing import Optional
from cryptography.fernet import Fernet
from base64 import urlsafe_b64encode
import hashlib

from app.core.config import JWT_SECRET, DATA_PATH

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


def _google_oauth_cipher(encryption_key: str):
    """Fernet cipher for Google OAuth temp cookie (google_id|email)."""
    key_material = hashlib.sha256(((encryption_key or "") + "google_oauth_temp").encode()).digest()
    return Fernet(urlsafe_b64encode(key_material[:32]))


def encrypt_google_oauth_cookie(google_id: str, email: str, encryption_key: str) -> str:
    """Encrypt google_id|email for short-lived OAuth cookie."""
    if not encryption_key or not google_id:
        return ""
    payload = f"{google_id}|{email or ''}"
    try:
        return _google_oauth_cipher(encryption_key).encrypt(payload.encode()).decode("ascii")
    except Exception:
        return ""


def decrypt_google_oauth_cookie(encrypted: str, encryption_key: str) -> Optional[tuple]:
    """Decrypt cookie to (google_id, email). Returns None on failure."""
    if not encrypted or not encryption_key:
        return None
    try:
        payload = _google_oauth_cipher(encryption_key).decrypt(encrypted.encode()).decode()
        parts = payload.split("|", 1)
        return (parts[0], parts[1] if len(parts) > 1 else "")
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
            created_at=datetime.utcnow().isoformat(), is_active=True
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
            db_path = DATA_PATH + "db.sqlite3"
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
            from app.core.config import DATA_PATH
            db_path = DATA_PATH + "db.sqlite3"
            with sqlite3.connect(db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE user_credentials SET is_active = 0, rotated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND is_active = 1",
                    (user_id,)
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
                    "INSERT INTO user_credentials (credential_id, user_id, broker_api_key, secret_fragment, encryption_salt, fragment_position, created_at, is_active) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), 1)",
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
            db_path = DATA_PATH + "db.sqlite3"
            with sqlite3.connect(db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE user_credentials SET is_active = 0, rotated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND is_active = 1",
                    (user_id,),
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
                    "INSERT INTO user_credentials (credential_id, user_id, broker_api_key, secret_fragment, encryption_salt, fragment_position, created_at, is_active) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), 1)",
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
            from app.core.config import DATA_PATH
            db_path = DATA_PATH + "db.sqlite3"
            with sqlite3.connect(db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE user_credentials SET is_active = 0, rotated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND is_active = 1",
                    (user_id,)
                )
                affected = cur.rowcount
                conn.commit()
            return affected > 0
        except Exception:
            return False
