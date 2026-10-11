"""Encryption at rest for stored secrets (currently the SMTP password).

The key comes from OBS_SECRET_KEY, a urlsafe-base64 Fernet key:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

If it is not set, a key is generated once into backend/.obs_secret.key so a
development machine works out of the box. Keep that file out of source control
and out of backups that are stored with the database: the database and the key
together reveal the password. Production should always set OBS_SECRET_KEY.
"""

import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

# OBS_SECRET_KEY_FILE moves the generated key next to the database (the Docker
# image keeps both on the /data volume so they survive container rebuilds).
KEY_FILE = Path(os.getenv("OBS_SECRET_KEY_FILE") or Path(__file__).resolve().parent / ".obs_secret.key")
logger = logging.getLogger("obs")
_fernet = None


class SecretUnavailable(Exception):
    """The stored secret cannot be decrypted with the configured key."""


def _load_key() -> bytes:
    configured = os.getenv("OBS_SECRET_KEY", "").strip()
    if configured:
        return configured.encode()
    if KEY_FILE.exists():
        return KEY_FILE.read_bytes().strip()
    key = Fernet.generate_key()
    KEY_FILE.write_bytes(key)
    try:
        os.chmod(KEY_FILE, 0o600)
    except OSError:
        pass
    logger.warning("OBS_SECRET_KEY is not set; generated %s for development use.", KEY_FILE)
    return key


def _box() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_key())
    return _fernet


def reset_cache():
    """Forget the loaded key (used by tests that switch keys)."""
    global _fernet
    _fernet = None


def encrypt(plaintext: str) -> str:
    return _box().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _box().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError) as exc:
        raise SecretUnavailable(
            "The stored SMTP password cannot be decrypted. OBS_SECRET_KEY changed or the "
            "key file was lost. Re-enter the password in SMTP settings."
        ) from exc
