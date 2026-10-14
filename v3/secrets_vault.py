"""Encrypts and decrypts stored secrets; the master key comes from the environment or a local key file."""

import json
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

from config import MASTER_KEY_PATH, SECRETS_ENCRYPTION_KEY


# Returns the master key as bytes: from SECRETS_ENCRYPTION_KEY, else from the key file (created on first use).
# Takes nothing; the key file is written owner-readable only.
def _master_key() -> bytes:
    if SECRETS_ENCRYPTION_KEY:
        return SECRETS_ENCRYPTION_KEY.encode()
    path = Path(MASTER_KEY_PATH)
    if path.exists():
        return path.read_bytes().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    # Create the file with owner-only permissions before writing the key into it
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key


# Encrypts a JSON-able dict of secret values.
# Takes the dict and returns the ciphertext bytes to store.
def encrypt(payload: dict[str, Any]) -> bytes:
    return Fernet(_master_key()).encrypt(json.dumps(payload).encode())


# Decrypts ciphertext back into the dict of secret values.
# Takes the stored bytes; raises cryptography.fernet.InvalidToken if the master key is wrong.
def decrypt(ciphertext: bytes) -> dict[str, Any]:
    return json.loads(Fernet(_master_key()).decrypt(ciphertext))


# Returns the last four characters of a secret, for showing "••••abcd" in the UI.
# Takes the secret text and returns those characters ("" if the secret is shorter than 8).
def last4(secret: str) -> str:
    return secret[-4:] if len(secret) >= 8 else ""
