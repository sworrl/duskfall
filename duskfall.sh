#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# Duskfall — Install / Update / Uninstall
#
# Usage:
#   ./duskfall.sh --install     Full setup: clone, DB, deps, services, Caddy
#   ./duskfall.sh --update      Pull, rebuild, restart
#   ./duskfall.sh --uninstall   Remove services, DB, Caddy block
#   ./duskfall.sh --status      Show current status
#
# One-liner from GitHub:
#   git clone https://github.com/sworrl/duskfall /tmp/duskfall-install && bash /tmp/duskfall-install/duskfall.sh --install && rm -rf /tmp/duskfall-install
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

# ── Config ──────────────────────────────────────────────────────────
DEPLOY_DIR="$HOME/.duskfall"
BACKEND_DIR="$DEPLOY_DIR/backend"
WEBCLIENT_DIR="$DEPLOY_DIR/web-client"
RELAY_DIR="$DEPLOY_DIR/relay"
VENV_DIR="$BACKEND_DIR/venv"
RELAY_VENV_DIR="$RELAY_DIR/venv"
REPO_URL="https://github.com/sworrl/duskfall.git"

BACKEND_PORT=8500
SERVICE_NAME="duskfall"
RELAY_SERVICE_NAME="duskfall-relay"
SYSTEMD_DIR="$HOME/.config/systemd/user"
CADDYFILE="/opt/caddy/Caddyfile"

# PostgreSQL — reuse existing PostGIS container
PG_CONTAINER="ots-db"
PG_USER="ots"
PG_PASS="password"
DB_NAME="duskfall"
DB_URL="postgresql+psycopg://${PG_USER}:${PG_PASS}@localhost:5432/${DB_NAME}"

# ── Colors ──────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
DIM='\033[2m'
BOLD='\033[1m'
NC='\033[0m'

log()  { echo -e "${GREEN}[duskfall]${NC} $*"; }
warn() { echo -e "${YELLOW}[duskfall]${NC} $*"; }
err()  { echo -e "${RED}[duskfall]${NC} $*" >&2; }
dim()  { echo -e "${DIM}           $*${NC}"; }

# ═══════════════════════════════════════════════════════════════════
# INSTALL
# ═══════════════════════════════════════════════════════════════════
do_install() {
    log "${BOLD}Installing Duskfall${NC}"
    echo ""

    # ── 0. Clone repo to deploy dir ────────────────────────────────
    if [ -d "$DEPLOY_DIR/.git" ]; then
        dim "Deploy directory exists — pulling latest"
        cd "$DEPLOY_DIR" && git pull --rebase 2>/dev/null || git pull 2>/dev/null || true
        cd - >/dev/null
    elif [ -d "$DEPLOY_DIR" ]; then
        err "$DEPLOY_DIR exists but is not a git repo"
        err "Remove it first or run --uninstall"
        exit 1
    else
        log "Cloning to ${CYAN}$DEPLOY_DIR${NC}..."
        git clone "$REPO_URL" "$DEPLOY_DIR"
    fi

    # ── 1. Database ─────────────────────────────────────────────────
    log "Setting up PostgreSQL database..."
    if podman exec "$PG_CONTAINER" psql -U "$PG_USER" -lqt 2>/dev/null | grep -qw "$DB_NAME"; then
        dim "Database '$DB_NAME' already exists — skipping"
    else
        podman exec "$PG_CONTAINER" psql -U "$PG_USER" -c "CREATE DATABASE $DB_NAME;" 2>/dev/null
        log "Created database: ${CYAN}$DB_NAME${NC}"
    fi

    # Enable PostGIS
    podman exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$DB_NAME" \
        -c "CREATE EXTENSION IF NOT EXISTS postgis;" 2>/dev/null
    dim "PostGIS extension enabled"

    # ── 2. Backend Python environment ───────────────────────────────
    log "Setting up backend Python environment..."
    if [ ! -d "$VENV_DIR" ]; then
        python3 -m venv "$VENV_DIR"
        log "Created venv at ${CYAN}$VENV_DIR${NC}"
    else
        dim "Venv already exists — skipping creation"
    fi

    uv pip install --quiet -r "$BACKEND_DIR/requirements.txt" -p "$VENV_DIR/bin/python"
    log "Backend dependencies installed"

    # ── 3. Backend .env ─────────────────────────────────────────────
    ENV_FILE="$BACKEND_DIR/.env"
    if [ ! -f "$ENV_FILE" ]; then
        cat > "$ENV_FILE" << EOF
DATABASE_URL=$DB_URL
OTS_HOST=localhost
OTS_COT_PORT=8088
OTS_API_URL=http://localhost:8081
OSRM_URL=https://router.project-osrm.org
OLLAMA_URL=http://localhost:11434
SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
CORS_ORIGINS=["https://duskfall.localhost"]
EOF
        log "Created ${CYAN}.env${NC} with generated secret key"
    else
        dim ".env already exists — skipping"
    fi

    # ── 4. Frontend ─────────────────────────────────────────────────
    log "Building frontend..."
    cd "$WEBCLIENT_DIR"
    npm install --silent 2>/dev/null
    npm run build 2>/dev/null
    log "Frontend built to ${CYAN}$WEBCLIENT_DIR/dist/${NC}"
    cd "$DEPLOY_DIR"

    # ── 5. Initialize database tables ───────────────────────────────
    log "Initializing database tables..."
    cd "$BACKEND_DIR"
    "$VENV_DIR/bin/python" -c "
from app.core.database import init_db
init_db()
print('Tables created')
" 2>&1 | while read -r line; do dim "$line"; done
    cd "$DEPLOY_DIR"

    # ── 6. Systemd service ──────────────────────────────────────────
    log "Installing systemd service..."
    mkdir -p "$SYSTEMD_DIR"

    cat > "$SYSTEMD_DIR/${SERVICE_NAME}.service" << EOF
[Unit]
Description=Duskfall — OSINT Situational Awareness Platform
Documentation=https://github.com/sworrl/duskfall
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=HOME=%h
WorkingDirectory=$BACKEND_DIR
ExecStartPre=/bin/sleep 3
ExecStart=$VENV_DIR/bin/uvicorn app.main:app --host 127.0.0.1 --port $BACKEND_PORT
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOF

    systemctl --user daemon-reload
    systemctl --user enable --now "${SERVICE_NAME}.service"
    log "Service ${CYAN}${SERVICE_NAME}.service${NC} started on port ${CYAN}$BACKEND_PORT${NC}"

    # ── 7. Caddy reverse proxy ──────────────────────────────────────
    log "Configuring Caddy..."
    if grep -q "duskfall.localhost" "$CADDYFILE" 2>/dev/null; then
        dim "Caddy block already exists — skipping"
    else
        cat >> "$CADDYFILE" << EOF

# Duskfall — OSINT Situational Awareness Platform
duskfall.localhost {
	handle /api/* {
		reverse_proxy 127.0.0.1:$BACKEND_PORT
	}

	handle {
		root * $WEBCLIENT_DIR/dist
		try_files {path} /index.html
		file_server
	}
}
EOF
        log "Added ${CYAN}duskfall.localhost${NC} to Caddyfile"
    fi

    # Reload Caddy
    if systemctl is-active caddy &>/dev/null; then
        sudo systemctl reload caddy 2>/dev/null || true
    fi

    # ── 8. Done ─────────────────────────────────────────────────────
    echo ""
    log "${BOLD}${GREEN}Duskfall installed successfully${NC}"
    echo ""
    echo -e "  ${CYAN}https://duskfall.localhost${NC}    — Web UI"
    echo -e "  ${CYAN}http://localhost:$BACKEND_PORT/docs${NC}  — API docs"
    echo ""
    echo -e "  ${DIM}Deployed to: $DEPLOY_DIR${NC}"
    echo -e "  ${DIM}Service:     systemctl --user status duskfall${NC}"
    echo -e "  ${DIM}Logs:        journalctl --user -u duskfall -f${NC}"
    echo -e "  ${DIM}Update:      ~/.duskfall/duskfall.sh --update${NC}"
    echo -e "  ${DIM}Remove:      ~/.duskfall/duskfall.sh --uninstall${NC}"
    echo ""

    # Open the web UI
    sleep 3
    if command -v xdg-open &>/dev/null; then
        xdg-open "https://duskfall.localhost" 2>/dev/null &
    fi
}

# ═══════════════════════════════════════════════════════════════════
# UPDATE
# ═══════════════════════════════════════════════════════════════════
do_update() {
    log "${BOLD}Updating Duskfall${NC}"
    echo ""

    if [ ! -d "$DEPLOY_DIR/.git" ]; then
        err "Duskfall not installed. Run --install first."
        exit 1
    fi

    # Pull latest
    cd "$DEPLOY_DIR"
    BEFORE=$(git rev-parse HEAD)
    git pull --rebase 2>/dev/null || git pull 2>/dev/null || true
    AFTER=$(git rev-parse HEAD)
    if [ "$BEFORE" = "$AFTER" ]; then
        dim "Already up to date"
    else
        log "Updated: ${DIM}$(git log --oneline "$BEFORE..$AFTER" | wc -l) new commits${NC}"
    fi

    # Update backend deps
    log "Updating backend dependencies..."
    uv pip install --quiet -r "$BACKEND_DIR/requirements.txt" -p "$VENV_DIR/bin/python"

    # Rebuild frontend
    log "Rebuilding frontend..."
    cd "$WEBCLIENT_DIR"
    npm install --silent 2>/dev/null
    npm run build 2>/dev/null
    cd "$DEPLOY_DIR"

    # Run DB migrations (re-init is safe — CREATE IF NOT EXISTS)
    log "Updating database schema..."
    cd "$BACKEND_DIR"
    "$VENV_DIR/bin/python" -c "
from app.core.database import init_db
init_db()
" 2>/dev/null
    cd "$DEPLOY_DIR"

    # Restart service
    log "Restarting service..."
    systemctl --user daemon-reload
    systemctl --user restart "${SERVICE_NAME}.service"

    echo ""
    log "${BOLD}${GREEN}Duskfall updated${NC}"
    echo -e "  ${DIM}Status: systemctl --user status duskfall${NC}"
    echo ""
}

# ═══════════════════════════════════════════════════════════════════
# UNINSTALL
# ═══════════════════════════════════════════════════════════════════
do_uninstall() {
    log "${BOLD}${RED}Uninstalling Duskfall${NC}"
    echo ""

    read -rp "This will remove services, database, Caddy config, and $DEPLOY_DIR. Continue? [y/N] " confirm
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        log "Aborted"
        exit 0
    fi

    # ── 1. Stop and remove systemd services ─────────────────────────
    log "Stopping services..."
    systemctl --user stop "${SERVICE_NAME}.service" 2>/dev/null || true
    systemctl --user disable "${SERVICE_NAME}.service" 2>/dev/null || true
    rm -f "$SYSTEMD_DIR/${SERVICE_NAME}.service"
    systemctl --user stop "${RELAY_SERVICE_NAME}.service" 2>/dev/null || true
    systemctl --user disable "${RELAY_SERVICE_NAME}.service" 2>/dev/null || true
    rm -f "$SYSTEMD_DIR/${RELAY_SERVICE_NAME}.service"
    systemctl --user daemon-reload
    log "Services removed"

    # ── 2. Remove Caddy config ──────────────────────────────────────
    log "Removing Caddy config..."
    if grep -q "duskfall.localhost" "$CADDYFILE" 2>/dev/null; then
        sed -i '/^# Duskfall/,/^}/d' "$CADDYFILE"
        sed -i '/^duskfall\.localhost/,/^}/d' "$CADDYFILE"
        sed -i '/^$/N;/^\n$/d' "$CADDYFILE"
        if systemctl is-active caddy &>/dev/null; then
            sudo systemctl reload caddy 2>/dev/null || true
        fi
        log "Removed duskfall.localhost from Caddyfile"
    else
        dim "No Caddy config found — skipping"
    fi

    # ── 3. Drop database ────────────────────────────────────────────
    log "Dropping database..."
    if podman exec "$PG_CONTAINER" psql -U "$PG_USER" -lqt 2>/dev/null | grep -qw "$DB_NAME"; then
        podman exec "$PG_CONTAINER" psql -U "$PG_USER" -c "DROP DATABASE $DB_NAME;" 2>/dev/null
        log "Dropped database: ${CYAN}$DB_NAME${NC}"
    else
        dim "Database not found — skipping"
    fi

    # ── 4. Remove deploy directory ──────────────────────────────────
    log "Removing $DEPLOY_DIR..."
    rm -rf "$DEPLOY_DIR"

    echo ""
    log "${BOLD}${GREEN}Duskfall uninstalled${NC}"
    echo ""
}

# ═══════════════════════════════════════════════════════════════════
# RELAY
# ═══════════════════════════════════════════════════════════════════
do_install_relay() {
    log "${BOLD}Installing Duskfall Relay${NC}"
    echo ""

    if [ ! -d "$RELAY_VENV_DIR" ]; then
        python3 -m venv "$RELAY_VENV_DIR"
    fi
    uv pip install --quiet -r "$RELAY_DIR/requirements.txt" -p "$RELAY_VENV_DIR/bin/python"
    log "Relay dependencies installed"

    mkdir -p "$SYSTEMD_DIR"
    cat > "$SYSTEMD_DIR/${RELAY_SERVICE_NAME}.service" << EOF
[Unit]
Description=Duskfall Relay — Encrypted Feeder Relay Node
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=HOME=%h
WorkingDirectory=$RELAY_DIR
ExecStart=$RELAY_VENV_DIR/bin/uvicorn relay.main:app --host 0.0.0.0 --port 8501
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOF

    systemctl --user daemon-reload
    systemctl --user enable --now "${RELAY_SERVICE_NAME}.service"
    log "Relay service started on port ${CYAN}8501${NC}"
    dim "Check bootstrap key: journalctl --user -u duskfall-relay | grep 'Bootstrap'"
    echo ""
}

# ═══════════════════════════════════════════════════════════════════
# STATUS
# ═══════════════════════════════════════════════════════════════════
do_status() {
    echo -e "${BOLD}Duskfall Status${NC}"
    echo ""

    if systemctl --user is-active "${SERVICE_NAME}.service" &>/dev/null; then
        echo -e "  Backend:     ${GREEN}running${NC} (port $BACKEND_PORT)"
    else
        echo -e "  Backend:     ${RED}stopped${NC}"
    fi

    if systemctl --user is-active "${RELAY_SERVICE_NAME}.service" &>/dev/null; then
        echo -e "  Relay:       ${GREEN}running${NC} (port 8501)"
    else
        echo -e "  Relay:       ${DIM}not installed${NC}"
    fi

    if podman exec "$PG_CONTAINER" psql -U "$PG_USER" -lqt 2>/dev/null | grep -qw "$DB_NAME"; then
        TABLES=$(podman exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$DB_NAME" -Atc \
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'" 2>/dev/null)
        echo -e "  Database:    ${GREEN}$DB_NAME${NC} ($TABLES tables)"
    else
        echo -e "  Database:    ${RED}not found${NC}"
    fi

    if grep -q "duskfall.localhost" "$CADDYFILE" 2>/dev/null; then
        echo -e "  Caddy:       ${GREEN}configured${NC}"
    else
        echo -e "  Caddy:       ${RED}not configured${NC}"
    fi

    if [ -d "$WEBCLIENT_DIR/dist" ]; then
        echo -e "  Frontend:    ${GREEN}built${NC}"
    else
        echo -e "  Frontend:    ${RED}not built${NC}"
    fi

    if curl -s http://localhost:11434/api/tags &>/dev/null; then
        echo -e "  Ollama:      ${GREEN}available${NC}"
    else
        echo -e "  Ollama:      ${DIM}not running${NC}"
    fi

    HEALTH=$(curl -s --max-time 3 http://localhost:$BACKEND_PORT/api/health 2>/dev/null)
    if [ -n "$HEALTH" ]; then
        echo -e "  Health:      ${GREEN}operational${NC}"
    else
        echo -e "  Health:      ${DIM}unreachable${NC}"
    fi

    echo -e "  Deploy dir:  ${DIM}$DEPLOY_DIR${NC}"
    echo ""
}

# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════
usage() {
    echo "Usage: $0 [OPTION]"
    echo ""
    echo "Options:"
    echo "  --install         Full install: clone, DB, deps, services, Caddy"
    echo "  --update          Pull latest, rebuild, restart"
    echo "  --uninstall       Remove services, DB, Caddy, deploy dir"
    echo "  --install-relay   Install the feeder-relay service"
    echo "  --status          Show current status"
    echo "  --help            Show this help"
    echo ""
}

case "${1:-}" in
    --install)       do_install ;;
    --update)        do_update ;;
    --uninstall)     do_uninstall ;;
    --install-relay) do_install_relay ;;
    --status)        do_status ;;
    --help|-h)       usage ;;
    *)
        err "Unknown option: ${1:-}"
        usage
        exit 1
        ;;
esac
