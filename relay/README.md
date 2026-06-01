# Duskfall Feeder-Relay

Encrypted relay node for distributing Duskfall data to remote field nodes.

## Architecture

```
Local Duskfall ──VPN──> Feeder-Relay (your webserver) ──TLS──> Remote Nodes
  (port 8500)            (port 8501)                            (field teams)
       │                      │
       │  push feeds/TAK      ├── /relay/feed    (data)
       │  send chat           ├── /relay/chat    (E2E encrypted)
       │  upload video        ├── /relay/video   (E2E encrypted)
       └──────────────────────├── /relay/tak     (CoT events)
                              ├── /relay/ws      (real-time WebSocket)
                              └── /admin/keys    (SHA-512 key management)
```

## Security

- **Authentication:** SHA-512 hashed API keys. Raw keys are never stored — only their SHA-512 digests
- **Encryption:** Chat and video are E2E encrypted with AES-256-GCM. The relay stores opaque ciphertext — it cannot read message content
- **Key derivation:** Encryption keys are derived from the shared API key via HKDF-SHA256
- **Transport:** TLS via reverse proxy (Caddy/nginx) or direct TLS certs
- **Permissions:** Keys are scoped (data, chat, video, tak, admin)

## Deployment

```bash
# On your webserver
cd relay
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Start the relay
uvicorn relay.main:app --host 0.0.0.0 --port 8501

# First run prints a bootstrap admin key — SAVE IT
```

### With Caddy (recommended for auto-TLS)

```
# Caddyfile
relay.yourdomain.com {
    reverse_proxy localhost:8501
}
```

## Configuration

Set via environment variables or `.env` file:

```bash
RELAY_UPSTREAM_URL=http://YOUR_DUSKFALL_HOST:8500    # Local Duskfall over VPN
RELAY_UPSTREAM_API_KEY=your-federation-key   # Federation key from local instance
RELAY_RELAY_SECRET=your-secret-here          # Master secret for key derivation
RELAY_DATA_RETENTION_HOURS=72                # How long to cache data
```

## Key Management

```bash
# Create a key for a remote node (requires admin key)
curl -X POST "https://relay.yourdomain.com/admin/keys?name=field-team-alpha&permissions=data,chat,tak&node_name=alpha" \
  -H "X-Relay-Key: YOUR_ADMIN_KEY"

# The response contains the raw key — distribute it securely to the field team
```

## Remote Node Usage

```bash
# Pull feed data
curl "https://relay.yourdomain.com/relay/feed" \
  -H "X-Relay-Key: TEAM_KEY"

# Send encrypted chat
curl -X POST "https://relay.yourdomain.com/relay/chat/send" \
  -H "X-Relay-Key: TEAM_KEY" \
  -H "Content-Type: application/json" \
  -d '{"channel": "ops", "encrypted_payload": {"nonce": "...", "ciphertext": "..."}}'

# WebSocket (real-time)
wscat -c "wss://relay.yourdomain.com/relay/ws?key=TEAM_KEY"
```

## Local Duskfall Integration

On your local Duskfall instance, set in `.env`:

```bash
RELAY_URL=https://relay.yourdomain.com
RELAY_API_KEY=your-admin-or-data-key
```

The local instance will push feeds, chat, and TAK events through its `/api/relay/` endpoints.
