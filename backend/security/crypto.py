import base64
import hashlib
import os
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from config import settings


class EncryptionVault:
    """Encrypts provider keys at rest with a local Fernet key.

    The key comes from ENCRYPTION_KEY when set, otherwise a key file is created
    once inside the data directory so stored credentials survive restarts.
    """

    def __init__(self, key: Optional[str] = None) -> None:
        self.cipher = Fernet(self._resolve_key(key))

    def _resolve_key(self, key: Optional[str]) -> bytes:
        candidate = (key or settings.ENCRYPTION_KEY or "").strip()
        if candidate:
            raw = candidate.encode("utf-8")
            try:
                Fernet(raw)
                return raw
            except Exception:
                digest = hashlib.sha256(raw).digest()
                return base64.urlsafe_b64encode(digest)

        key_path = settings.data_path / ".fernet.key"
        try:
            if key_path.exists():
                stored = key_path.read_bytes().strip()
                if stored:
                    Fernet(stored)
                    return stored
            key_path.parent.mkdir(parents=True, exist_ok=True)
            generated = Fernet.generate_key()
            key_path.write_bytes(generated)
            try:
                os.chmod(key_path, 0o600)
            except OSError:
                pass
            return generated
        except OSError:
            # Read-only filesystem: fall back to a process-local key.
            return Fernet.generate_key()

    def encrypt(self, plain_text: str) -> str:
        return self.cipher.encrypt(plain_text.encode("utf-8")).decode("utf-8")

    def decrypt(self, encrypted_text: str) -> str:
        try:
            return self.cipher.decrypt(encrypted_text.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            return ""


vault = EncryptionVault()
