"""Duskfall Feeder-Relay Node.

Standalone service deployable on a public webserver. Receives data from
the local Duskfall instance over VPN and serves remote nodes via
SHA-512 authenticated API keys. All sensitive payloads (chat, video)
are end-to-end encrypted with AES-256-GCM.

Architecture:
  Local Duskfall ──VPN──> Feeder-Relay (public) ──TLS──> Remote Nodes
                               │
                               ├── /relay/feed    (data push/pull)
                               ├── /relay/chat    (encrypted messaging)
                               ├── /relay/video   (encrypted video relay)
                               ├── /relay/tak     (CoT forwarding)
                               └── /admin/        (key management)

Run:
  cd relay
  uvicorn relay.main:app --host 0.0.0.0 --port 8501
"""
import asyncio
import logging
import time
import uuid

from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect,
    Depends, Header, HTTPException, Query, UploadFile, File,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from relay.config import relay_settings
from relay.auth import key_store, RelayKey
from relay.crypto import derive_key, encrypt_payload, decrypt_payload
from relay.store import relay_store, ChatMessage, VideoChunk
from relay.upstream import UpstreamSync

from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [relay] %(levelname)s: %(message)s",
)
logger = logging.getLogger("relay")

VIDEO_DIR = Path(__file__).parent / "data" / "video"
VIDEO_DIR.mkdir(parents=True, exist_ok=True)


# --- Auth dependency ---

def require_key(
    x_relay_key: str = Header(default=""),
) -> RelayKey:
    """Verify relay API key from header."""
    rk = key_store.verify(x_relay_key)
    if not rk:
        raise HTTPException(status_code=403, detail="Invalid or expired relay key")
    return rk


def require_permission(permission: str):
    """Create a dependency that checks for a specific permission."""
    def check(rk: RelayKey = Depends(require_key)):
        if not key_store.has_permission(rk, permission):
            raise HTTPException(
                status_code=403, detail=f"Missing permission: {permission}"
            )
        return rk
    return check


# --- App ---

upstream_sync = UpstreamSync()

app = FastAPI(
    title="Duskfall Relay",
    description="Encrypted feeder-relay for Duskfall mesh network",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Relay serves remote nodes from anywhere
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    """Start upstream sync and create default admin key if needed."""
    if not key_store.list_keys():
        raw = key_store.create_key(
            name="admin-bootstrap",
            permissions=["admin", "data", "chat", "video", "tak"],
            node_name="bootstrap",
        )
        logger.info(f"Bootstrap admin key created: {raw}")
        logger.info("SAVE THIS KEY — it will not be shown again!")

    # Start upstream sync loop
    asyncio.create_task(upstream_sync.sync_loop())


@app.get("/health")
async def health():
    return {
        "status": "operational",
        "relay": relay_settings.RELAY_NAME,
        "upstream": upstream_sync.connected,
        "connected_nodes": len(_ws_clients),
    }


# ============================================================
# FEED DATA RELAY
# ============================================================

@app.get("/relay/feed")
def get_feeds(
    feed_type: str | None = None,
    since: float = 0,
    limit: int = Query(default=500, le=5000),
    rk: RelayKey = Depends(require_permission("data")),
):
    """Pull cached feed events from the relay."""
    events = relay_store.get_feeds(feed_type=feed_type, since=since, limit=limit)
    return {"count": len(events), "events": events, "relay": relay_settings.RELAY_NAME}


@app.post("/relay/feed/push")
def push_feeds(
    payload: dict,
    rk: RelayKey = Depends(require_permission("data")),
):
    """Push feed events to the relay (from upstream Duskfall instance)."""
    events = payload.get("events", [])
    relay_store.update_feeds(events)
    return {"accepted": len(events)}


# ============================================================
# ENCRYPTED CHAT
# ============================================================

@app.post("/relay/chat/send")
def send_chat(
    payload: dict,
    rk: RelayKey = Depends(require_permission("chat")),
):
    """Send an encrypted chat message.

    Payload: {channel, encrypted_payload: {nonce, ciphertext}}
    Messages are E2E encrypted — the relay cannot read them.
    """
    msg = ChatMessage(
        msg_id=str(uuid.uuid4()),
        sender=rk.node_name or rk.name,
        channel=payload.get("channel", "general"),
        encrypted_payload=payload.get("encrypted_payload", {}),
        timestamp=time.time(),
        ttl_hours=payload.get("ttl_hours", 24),
    )
    relay_store.add_chat(msg)

    # Broadcast to connected WebSocket clients
    asyncio.create_task(_broadcast_chat(msg))

    return {"msg_id": msg.msg_id, "status": "relayed"}


@app.get("/relay/chat/history")
def chat_history(
    channel: str = "general",
    since: float = 0,
    limit: int = Query(default=100, le=500),
    rk: RelayKey = Depends(require_permission("chat")),
):
    """Get chat message history for a channel."""
    messages = relay_store.get_chat(channel=channel, since=since, limit=limit)
    return {"channel": channel, "count": len(messages), "messages": messages}


# ============================================================
# ENCRYPTED VIDEO RELAY
# ============================================================

@app.post("/relay/video/upload")
async def upload_video(
    channel: str = "general",
    file: UploadFile = File(...),
    rk: RelayKey = Depends(require_permission("video")),
):
    """Upload an encrypted video chunk.

    The file should already be encrypted client-side with AES-256-GCM.
    The relay stores it opaquely and serves it to authorized nodes.
    """
    chunk_id = str(uuid.uuid4())
    file_path = VIDEO_DIR / f"{chunk_id}.enc"

    content = await file.read()
    if len(content) > relay_settings.MAX_VIDEO_CHUNK:
        raise HTTPException(status_code=413, detail="Video chunk too large")

    file_path.write_bytes(content)

    chunk = VideoChunk(
        chunk_id=chunk_id,
        sender=rk.node_name or rk.name,
        channel=channel,
        filename=file.filename or "unknown",
        size_bytes=len(content),
        encrypted=True,
        timestamp=time.time(),
    )
    relay_store.add_video_meta(chunk)

    return {"chunk_id": chunk_id, "size": len(content), "status": "stored"}


@app.get("/relay/video/list")
def list_video(
    channel: str = "general",
    rk: RelayKey = Depends(require_permission("video")),
):
    """List available video chunks."""
    chunks = relay_store.get_video_list(channel=channel)
    return {"channel": channel, "count": len(chunks), "chunks": chunks}


@app.get("/relay/video/{chunk_id}")
def download_video(
    chunk_id: str,
    rk: RelayKey = Depends(require_permission("video")),
):
    """Download an encrypted video chunk."""
    file_path = VIDEO_DIR / f"{chunk_id}.enc"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Chunk not found")
    return FileResponse(
        file_path,
        media_type="application/octet-stream",
        filename=f"{chunk_id}.enc",
    )


# ============================================================
# TAK CoT RELAY
# ============================================================

@app.post("/relay/tak/push")
def push_tak_event(
    payload: dict,
    rk: RelayKey = Depends(require_permission("tak")),
):
    """Push a CoT event to the relay for distribution."""
    relay_store.add_tak_event(payload)
    asyncio.create_task(_broadcast_tak(payload))
    return {"status": "relayed"}


@app.get("/relay/tak/events")
def get_tak_events(
    since: float = 0,
    rk: RelayKey = Depends(require_permission("tak")),
):
    """Pull TAK events from the relay."""
    events = relay_store.get_tak_events(since=since)
    return {"count": len(events), "events": events}


# ============================================================
# WEBSOCKET — real-time push to connected nodes
# ============================================================

_ws_clients: dict[str, WebSocket] = {}


@app.websocket("/relay/ws")
async def relay_websocket(websocket: WebSocket, key: str = Query(default="")):
    """Real-time WebSocket for chat, TAK, and feed updates.

    Connect with ?key=YOUR_API_KEY for authentication.
    """
    rk = key_store.verify(key)
    if not rk:
        await websocket.close(code=4003)
        return

    await websocket.accept()
    client_id = str(uuid.uuid4())
    _ws_clients[client_id] = websocket
    logger.info(f"WS client connected: {rk.name} ({client_id})")

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "chat" and key_store.has_permission(rk, "chat"):
                msg = ChatMessage(
                    msg_id=str(uuid.uuid4()),
                    sender=rk.node_name or rk.name,
                    channel=data.get("channel", "general"),
                    encrypted_payload=data.get("encrypted_payload", {}),
                    timestamp=time.time(),
                )
                relay_store.add_chat(msg)
                await _broadcast_chat(msg, exclude=client_id)

            elif msg_type == "tak" and key_store.has_permission(rk, "tak"):
                event = data.get("event", {})
                relay_store.add_tak_event(event)
                await _broadcast_tak(event, exclude=client_id)

    except WebSocketDisconnect:
        _ws_clients.pop(client_id, None)
        logger.info(f"WS client disconnected: {client_id}")


async def _broadcast_chat(msg: ChatMessage, exclude: str = ""):
    """Broadcast a chat message to all connected WS clients."""
    payload = {
        "type": "chat",
        "msg_id": msg.msg_id,
        "sender": msg.sender,
        "channel": msg.channel,
        "encrypted_payload": msg.encrypted_payload,
        "timestamp": msg.timestamp,
    }
    await _broadcast(payload, exclude)


async def _broadcast_tak(event: dict, exclude: str = ""):
    """Broadcast a TAK event to all connected WS clients."""
    await _broadcast({"type": "tak", "event": event}, exclude)


async def _broadcast(payload: dict, exclude: str = ""):
    """Send payload to all WS clients except excluded."""
    disconnected = []
    for cid, ws in _ws_clients.items():
        if cid == exclude:
            continue
        try:
            await ws.send_json(payload)
        except Exception:
            disconnected.append(cid)
    for cid in disconnected:
        _ws_clients.pop(cid, None)


# ============================================================
# ADMIN — key management
# ============================================================

@app.post("/admin/keys")
def create_key(
    name: str,
    permissions: str = "data,chat,tak",
    node_name: str = "",
    expires_hours: int = 0,
    rk: RelayKey = Depends(require_permission("admin")),
):
    """Create a new API key for a remote node.

    Returns the raw key — store it securely, it cannot be retrieved later.
    """
    perm_list = [p.strip() for p in permissions.split(",")]
    raw_key = key_store.create_key(
        name=name,
        permissions=perm_list,
        node_name=node_name,
        expires_hours=expires_hours,
    )
    return {
        "raw_key": raw_key,
        "sha512_prefix": f"{__import__('hashlib').sha512(raw_key.encode()).hexdigest()[:16]}...",
        "permissions": perm_list,
        "message": "Store this key securely — it cannot be retrieved again",
    }


@app.get("/admin/keys")
def list_keys(rk: RelayKey = Depends(require_permission("admin"))):
    """List all API keys (hashes only, not raw keys)."""
    return key_store.list_keys()


@app.delete("/admin/keys/{name}")
def revoke_key(
    name: str,
    rk: RelayKey = Depends(require_permission("admin")),
):
    """Revoke an API key by name."""
    if key_store.revoke(name):
        return {"status": "revoked", "name": name}
    raise HTTPException(status_code=404, detail="Key not found")
