import os
import base64
from cryptography.fernet import Fernet
from ..config import settings

class EncryptionVault:
    def __init__(self, key: str = None):
        if not key:
            # Generate deterministic key or use config
            raw = (settings.ENCRYPTION_KEY or settings.SECRET_KEY).encode("utf-8")
            # Fernet needs 32 url-safe base64-encoded bytes
            key = base64.urlsafe_b64encode(raw[:32].ljust(32, b"0")).decode("utf-8")
        self.cipher = Fernet(key)

    def encrypt(self, plain_text: str) -> str:
        return self.cipher.encrypt(plain_text.encode("utf-8")).decode("utf-8")

    def decrypt(self, encrypted_text: str) -> str:
        return self.cipher.decrypt(encrypted_text.encode("utf-8")).decode("utf-8")

vault = EncryptionVault()
