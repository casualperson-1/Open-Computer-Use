"""Fernet encryption for stored secrets, with explicit key handling.

The key is never silently derived from something else: a malformed
``ENCRYPTION_KEY``, or an ``ENCRYPTION_KEY_FILE`` that does not exist, raises at
startup instead of quietly creating a second key that cannot read the existing
credentials. ``startup_check`` verifies that the loaded key decrypts everything
already in the database, so a mismatched backup is caught before the UI shows
empty API keys.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken

from config import settings


class EncryptionConfigError(RuntimeError):
    """The configured encryption key is missing, malformed or unusable."""


class EncryptionKeyMismatch(RuntimeError):
    """The key cannot decrypt secrets that are already stored."""


def _valid_key(raw: bytes) -> bool:
    try:
        Fernet(raw)
        return True
    except Exception:
        return False


class EncryptionVault:
    """Encrypts provider keys and other secrets at rest with Fernet."""

    def __init__(self, key: Optional[str] = None) -> None:
        raw, self.key_source = self._resolve_key(key)
        self.cipher = Fernet(raw)
        self._raw = raw

    # -- key resolution --------------------------------------------------
    def _resolve_key(self, key: Optional[str]) -> Tuple[bytes, str]:
        candidate = (key or settings.ENCRYPTION_KEY or "").strip()
        if candidate:
            raw = candidate.encode("utf-8")
            if not _valid_key(raw):
                raise EncryptionConfigError(
                    "ENCRYPTION_KEY is not a valid Fernet key. Generate one with:\n"
                    "    python -c \"from cryptography.fernet import Fernet; "
                    'print(Fernet.generate_key().decode())"'
                )
            return raw, "environment"

        key_file = (settings.ENCRYPTION_KEY_FILE or "").strip()
        if key_file:
            path = Path(key_file).expanduser()
            if not path.exists():
                raise EncryptionConfigError(
                    f"ENCRYPTION_KEY_FILE points to {path}, which does not exist. "
                    "Mount the key file (mode 600) or set ENCRYPTION_KEY."
                )
            raw = path.read_bytes().strip()
            if not _valid_key(raw):
                raise EncryptionConfigError(
                    f"ENCRYPTION_KEY_FILE ({path}) does not contain a valid Fernet key."
                )
            return raw, f"file:{path}"

        default_path = settings.data_path / ".fernet.key"
        try:
            if default_path.exists():
                stored = default_path.read_bytes().strip()
                if not _valid_key(stored):
                    raise EncryptionConfigError(
                        f"{default_path} does not contain a valid Fernet key. "
                        "Restore the key from a backup or remove the file to start fresh."
                    )
                return stored, f"file:{default_path}"
            default_path.parent.mkdir(parents=True, exist_ok=True)
            generated = Fernet.generate_key()
            default_path.write_bytes(generated)
            try:
                os.chmod(default_path, 0o600)
            except OSError:
                pass
            return generated, f"generated:{default_path}"
        except EncryptionConfigError:
            raise
        except OSError as exc:
            raise EncryptionConfigError(
                f"Could not read or create the encryption key at {default_path}: {exc}"
            ) from exc

    # -- data ------------------------------------------------------------
    @property
    def key_bytes(self) -> bytes:
        """The key material, used by ``backup`` to store both parts together."""
        return self._raw

    def encrypt(self, plain_text: str) -> str:
        return self.cipher.encrypt(plain_text.encode("utf-8")).decode("utf-8")

    def can_decrypt(self, encrypted_text: Optional[str]) -> bool:
        if not encrypted_text:
            return True
        try:
            self.cipher.decrypt(encrypted_text.encode("utf-8"))
            return True
        except (InvalidToken, ValueError):
            return False

    def decrypt(self, encrypted_text: str) -> str:
        try:
            return self.cipher.decrypt(encrypted_text.encode("utf-8")).decode("utf-8")
        except (InvalidToken, ValueError):
            return ""

    def decrypt_or_raise(self, encrypted_text: str) -> str:
        try:
            return self.cipher.decrypt(encrypted_text.encode("utf-8")).decode("utf-8")
        except (InvalidToken, ValueError) as exc:
            raise EncryptionKeyMismatch(
                "The encryption key cannot decrypt a stored secret. The database and "
                "its Fernet key must come from the same backup."
            ) from exc


vault = EncryptionVault()


def affected_secrets(rows: Iterable[Tuple[str, Optional[str]]]) -> List[str]:
    """Names of stored secrets the current key cannot decrypt."""
    return [name for name, value in rows if value and not vault.can_decrypt(value)]


async def startup_check(session_factory: Any) -> None:
    """Fail clearly when stored credentials cannot be decrypted.

    Called from the app lifespan: an operator who restored a database without
    its key gets one actionable error instead of a UI full of empty keys.
    """
    from sqlalchemy import select

    from database.models import ApiKey, McpServer, ProviderSetting

    rows: List[Tuple[str, Optional[str]]] = []
    async with session_factory() as session:
        for row in (await session.execute(select(ApiKey))).scalars().all():
            rows.append((f"api key {row.provider}", row.encrypted_key))
        for row in (await session.execute(select(ProviderSetting))).scalars().all():
            rows.append((f"provider {row.provider}", row.encrypted_key))
        for row in (await session.execute(select(McpServer))).scalars().all():
            rows.append((f"mcp server {row.name} env", row.env))
            rows.append((f"mcp server {row.name} headers", row.headers))

    broken = affected_secrets(rows)
    if not broken:
        return
    raise EncryptionKeyMismatch(
        "The encryption key cannot decrypt saved secrets for: "
        + ", ".join(sorted(broken))
        + ".\nRestore the matching Fernet key (the backup command stores the database "
        "and key together), or remove those credentials and re-enter them."
    )


__all__ = [
    "EncryptionConfigError",
    "EncryptionKeyMismatch",
    "EncryptionVault",
    "affected_secrets",
    "startup_check",
    "vault",
]
