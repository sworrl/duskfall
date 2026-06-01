"""End-to-end encryption for relay payloads.

Uses AES-256-GCM for symmetric encryption of data payloads.
Each message gets a unique nonce. Keys are derived from shared secrets
using HKDF-SHA256.

Chat messages and video chunks are encrypted before transit.
Feed data uses TLS transport encryption (not E2E) for performance.
"""
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
import base64


def derive_key(shared_secret: str, context: str = "duskfall-relay") -> bytes:
    """Derive a 256-bit AES key from a shared secret using HKDF."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"duskfall-relay-v1",
        info=context.encode(),
    )
    return hkdf.derive(shared_secret.encode())


def encrypt_payload(plaintext: bytes, key: bytes) -> dict:
    """Encrypt a payload with AES-256-GCM. Returns {nonce, ciphertext} as base64."""
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)

    return {
        "nonce": base64.b64encode(nonce).decode(),
        "ciphertext": base64.b64encode(ciphertext).decode(),
    }


def decrypt_payload(encrypted: dict, key: bytes) -> bytes:
    """Decrypt an AES-256-GCM encrypted payload."""
    nonce = base64.b64decode(encrypted["nonce"])
    ciphertext = base64.b64decode(encrypted["ciphertext"])
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, None)
