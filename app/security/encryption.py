from __future__ import annotations

import os
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken

ENCRYPTED_PREFIX = "enc:v1:"
DEFAULT_ENV_KEY_NAME = "MT5_PORTAL_SECRET_KEY"


def generate_secret_key() -> str:
    """Generate a Fernet key for encrypting stored MT5 credentials.

    Store this outside the database, for example in an environment variable
    named MT5_PORTAL_SECRET_KEY. If the database is copied without this key,
    the encrypted credentials cannot be read.
    """

    return Fernet.generate_key().decode("ascii")


def is_encrypted(value: str) -> bool:
    return value.startswith(ENCRYPTED_PREFIX)


@dataclass(frozen=True)
class SecretCipher:
    """Authenticated symmetric encryption for secrets stored in SQLite.

    This uses Fernet from the cryptography package. Fernet provides encrypted
    and authenticated tokens, so modified ciphertext will not decrypt.
    """

    key: str

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("Encryption key is required")
        # Validate the key immediately so configuration errors fail early.
        Fernet(self.key.encode("ascii"))

    @classmethod
    def from_env(cls, env_key_name: str = DEFAULT_ENV_KEY_NAME) -> "SecretCipher":
        key = os.environ.get(env_key_name)
        if not key:
            raise ValueError(f"Missing required environment variable: {env_key_name}")
        return cls(key)

    def encrypt(self, plaintext: str) -> str:
        if plaintext == "":
            raise ValueError("Cannot encrypt an empty secret")
        if is_encrypted(plaintext):
            return plaintext
        token = Fernet(self.key.encode("ascii")).encrypt(plaintext.encode("utf-8"))
        return ENCRYPTED_PREFIX + token.decode("ascii")

    def decrypt(self, stored_value: str) -> str:
        if stored_value == "":
            raise ValueError("Cannot decrypt an empty secret")
        if not is_encrypted(stored_value):
            return stored_value
        token = stored_value.removeprefix(ENCRYPTED_PREFIX).encode("ascii")
        try:
            return Fernet(self.key.encode("ascii")).decrypt(token).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Could not decrypt secret with the configured key") from exc


def require_cipher_for_encrypted_value(value: str, cipher: SecretCipher | None) -> str:
    """Decrypt when needed and reject encrypted values without a cipher."""

    if is_encrypted(value) and cipher is None:
        raise ValueError("Encrypted secret requires a SecretCipher to be loaded")
    return cipher.decrypt(value) if cipher is not None else value
