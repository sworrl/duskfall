"""In-memory + disk-backed store for relayed data.

Stores feed event cache, chat messages, and video chunk metadata.
Data expires after DATA_RETENTION_HOURS.
"""
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from pathlib import Path
from threading import Lock

from relay.config import relay_settings

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"
FEEDS_FILE = DATA_DIR / "feeds_cache.json"
CHAT_FILE = DATA_DIR / "chat_log.json"


@dataclass
class ChatMessage:
    msg_id: str
    sender: str
    channel: str
    encrypted_payload: dict  # {nonce, ciphertext}
    timestamp: float
    ttl_hours: int = 24


@dataclass
class VideoChunk:
    chunk_id: str
    sender: str
    channel: str
    filename: str
    size_bytes: int
    encrypted: bool
    timestamp: float
    ttl_hours: int = 6


class RelayStore:
    """Central data store for the relay node."""

    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._feeds: list[dict] = []
        self._chat: deque[ChatMessage] = deque(maxlen=10000)
        self._video_meta: deque[VideoChunk] = deque(maxlen=1000)
        self._tak_events: deque[dict] = deque(maxlen=5000)
        self._load()

    def _load(self):
        """Load cached data from disk."""
        try:
            if FEEDS_FILE.exists():
                self._feeds = json.loads(FEEDS_FILE.read_text())
        except Exception:
            self._feeds = []

        try:
            if CHAT_FILE.exists():
                data = json.loads(CHAT_FILE.read_text())
                for m in data:
                    self._chat.append(ChatMessage(**m))
        except Exception:
            pass

    def _save_feeds(self):
        """Persist feed cache to disk."""
        try:
            FEEDS_FILE.write_text(json.dumps(self._feeds[:5000]))
        except Exception as e:
            logger.error(f"Failed to save feeds: {e}")

    def _save_chat(self):
        """Persist chat to disk."""
        try:
            data = [asdict(m) for m in self._chat]
            CHAT_FILE.write_text(json.dumps(data[-1000:]))
        except Exception as e:
            logger.error(f"Failed to save chat: {e}")

    def _expire(self):
        """Remove expired data."""
        retention = relay_settings.DATA_RETENTION_HOURS * 3600
        cutoff = time.time() - retention
        self._feeds = [f for f in self._feeds if f.get("_relayed_at", 0) > cutoff]

    # --- Feed data ---

    def update_feeds(self, events: list[dict]):
        """Replace/merge feed cache with new events from upstream."""
        with self._lock:
            # Merge by uid
            existing = {e.get("uid"): e for e in self._feeds}
            now = time.time()
            for event in events:
                event["_relayed_at"] = now
                existing[event.get("uid")] = event
            self._feeds = list(existing.values())
            self._expire()
            self._save_feeds()

    def get_feeds(
        self, feed_type: str | None = None, since: float = 0, limit: int = 500
    ) -> list[dict]:
        """Get cached feed events."""
        with self._lock:
            results = self._feeds
            if feed_type:
                results = [f for f in results if f.get("feed_type") == feed_type]
            if since:
                results = [f for f in results if f.get("_relayed_at", 0) > since]
            return results[-limit:]

    # --- Chat messages ---

    def add_chat(self, msg: ChatMessage):
        """Store an encrypted chat message."""
        with self._lock:
            self._chat.append(msg)
            self._save_chat()

    def get_chat(
        self, channel: str = "general", since: float = 0, limit: int = 100
    ) -> list[dict]:
        """Get chat messages for a channel."""
        with self._lock:
            messages = [
                asdict(m)
                for m in self._chat
                if m.channel == channel and m.timestamp > since
            ]
            return messages[-limit:]

    # --- Video chunks ---

    def add_video_meta(self, chunk: VideoChunk):
        """Store video chunk metadata (actual data stored on disk)."""
        with self._lock:
            self._video_meta.append(chunk)

    def get_video_list(self, channel: str = "general") -> list[dict]:
        """List available video chunks."""
        with self._lock:
            cutoff = time.time() - (6 * 3600)  # 6 hour TTL for video
            return [
                asdict(v)
                for v in self._video_meta
                if v.channel == channel and v.timestamp > cutoff
            ]

    # --- TAK events ---

    def add_tak_event(self, event: dict):
        """Store a CoT event for relay."""
        with self._lock:
            event["_relayed_at"] = time.time()
            self._tak_events.append(event)

    def get_tak_events(self, since: float = 0) -> list[dict]:
        """Get TAK events since timestamp."""
        with self._lock:
            return [
                e for e in self._tak_events if e.get("_relayed_at", 0) > since
            ]


# Singleton
relay_store = RelayStore()
