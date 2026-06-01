"""SHA-512 based API key authentication for relay nodes.

Key lifecycle:
  1. Admin generates a key via POST /admin/keys
  2. Server stores SHA-512(key) in the key store
  3. Client sends raw key in X-Relay-Key header
  4. Server hashes the received key with SHA-512 and compares

Keys are scoped with permissions:
  - data: read feed events, sensor data
  - chat: send/receive encrypted messages
  - video: send/receive encrypted video chunks
  - tak: receive/send CoT events
  - admin: manage keys and relay config
"""
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

KEYS_FILE = Path(__file__).parent / "data" / "keys.json"


@dataclass
class RelayKey:
    key_hash: str  # SHA-512 hex digest
    name: str
    permissions: list[str]
    created_at: float
    last_used: float = 0
    is_active: bool = True
    node_name: str = ""
    expires_at: float = 0  # 0 = never


def hash_key(raw_key: str) -> str:
    """SHA-512 hash a raw API key."""
    return hashlib.sha512(raw_key.encode("utf-8")).hexdigest()


def generate_key() -> str:
    """Generate a cryptographically secure API key (64 hex chars)."""
    return secrets.token_hex(32)


class KeyStore:
    """Manages SHA-512 hashed API keys on disk."""

    def __init__(self):
        self._keys: dict[str, RelayKey] = {}
        self._load()

    def _load(self):
        """Load keys from disk."""
        if KEYS_FILE.exists():
            try:
                data = json.loads(KEYS_FILE.read_text())
                for k in data.get("keys", []):
                    rk = RelayKey(**k)
                    self._keys[rk.key_hash] = rk
            except Exception as e:
                logger.error(f"Failed to load keys: {e}")

    def _save(self):
        """Persist keys to disk."""
        KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {"keys": [asdict(k) for k in self._keys.values()]}
        KEYS_FILE.write_text(json.dumps(data, indent=2))

    def create_key(
        self,
        name: str,
        permissions: list[str],
        node_name: str = "",
        expires_hours: int = 0,
    ) -> str:
        """Create a new API key. Returns the raw key (only shown once)."""
        raw_key = generate_key()
        key_hash = hash_key(raw_key)

        rk = RelayKey(
            key_hash=key_hash,
            name=name,
            permissions=permissions,
            created_at=time.time(),
            node_name=node_name,
            expires_at=time.time() + (expires_hours * 3600)
            if expires_hours
            else 0,
        )
        self._keys[key_hash] = rk
        self._save()

        logger.info(f"Created relay key: {name} ({', '.join(permissions)})")
        return raw_key

    def verify(self, raw_key: str) -> Optional[RelayKey]:
        """Verify a raw key. Returns the RelayKey if valid, None if not."""
        if not raw_key:
            return None

        key_hash = hash_key(raw_key)
        rk = self._keys.get(key_hash)

        if not rk:
            return None
        if not rk.is_active:
            return None
        if rk.expires_at and time.time() > rk.expires_at:
            return None

        # Update last used
        rk.last_used = time.time()
        self._save()

        return rk

    def has_permission(self, rk: RelayKey, permission: str) -> bool:
        """Check if a key has a specific permission."""
        return permission in rk.permissions or "admin" in rk.permissions

    def list_keys(self) -> list[dict]:
        """List all keys (without hashes for security)."""
        return [
            {
                "name": k.name,
                "permissions": k.permissions,
                "node_name": k.node_name,
                "is_active": k.is_active,
                "created_at": k.created_at,
                "last_used": k.last_used,
                "hash_prefix": k.key_hash[:16] + "...",
            }
            for k in self._keys.values()
        ]

    def revoke(self, name: str) -> bool:
        """Revoke a key by name."""
        for rk in self._keys.values():
            if rk.name == name:
                rk.is_active = False
                self._save()
                return True
        return False


# Singleton
key_store = KeyStore()
