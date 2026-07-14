#!/usr/bin/env bash
# =============================================================================
# SubIO Main Server (foreign VPS) — one-click installer
# Supported: Ubuntu 22.04 / 24.04
#
# Usage:
#   cd main-server
#   sudo ./install.sh
#
# Optional flags:
#   --with-monitor     start Telethon scanner profile
#   --no-monitor       disable monitoring profile
#   --with-caddy=DOMAIN  install Caddy + automatic TLS
#   --skip-build       up/migrate only (no rebuild)
#   --yes              skip pause for .env editing (when already configured)
# =============================================================================
set -Eeuo pipefail

if [[ -t 1 ]] && command -v tput >/dev/null 2>&1; then
  INFO="$(tput setaf 6)"
  OK="$(tput setaf 2)"
  WARN="$(tput setaf 3)"
  ERROR="$(tput bold; tput setaf 1)"
  NC="$(tput sgr0)"
else
  INFO=$'\033[36m'
  OK=$'\033[32m'
  WARN=$'\033[33m'
  ERROR=$'\033[1;31m'
  NC=$'\033[0m'
fi

_ts() { date '+%H:%M:%S'; }
info()  { printf '%b[%s] [INFO]%b %s\n' "$INFO" "$(_ts)" "$NC" "$*"; }
ok()    { printf '%b[%s] [OK]%b %s\n' "$OK" "$(_ts)" "$NC" "$*"; }
warn()  { printf '%b[%s] [WARN]%b %s\n' "$WARN" "$(_ts)" "$NC" "$*"; }
fail()  { printf '%b[%s] [ERROR]%b %s\n' "$ERROR" "$(_ts)" "$NC" "$*" >&2; exit 1; }

WITH_MONITOR=-1
WITH_CADDY=""
SKIP_BUILD=0
ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --with-monitor) WITH_MONITOR=1 ;;
    --no-monitor) WITH_MONITOR=0 ;;
    --with-caddy=*) WITH_CADDY="${arg#*=}" ;;
    --with-caddy) fail "Use --with-caddy=api.example.com" ;;
    --skip-build) SKIP_BUILD=1 ;;
    --yes|-y) ASSUME_YES=1 ;;
    -h|--help)
      sed -n '2,20p' "$0"
      exit 0
      ;;
    *) fail "Unknown argument: $arg" ;;
  esac
done

[[ ${EUID} -eq 0 ]] || fail "Run as root: sudo ./install.sh"
source /etc/os-release
[[ ${ID:-} == ubuntu && ${VERSION_ID:-} =~ ^(22\.04|24\.04)$ ]] || fail "Only Ubuntu 22.04 / 24.04 is supported"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
export DEBIAN_FRONTEND=noninteractive

# ---------------------------------------------------------------------------
# 1) Base system
# ---------------------------------------------------------------------------
info "Installing base packages..."
apt-get update -qq
apt-get install -y --no-install-recommends \
  ca-certificates curl gnupg openssl ufw jq python3

TOTAL_MEM=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
(( TOTAL_MEM >= 1800 )) || warn "Recommended RAM >= 2 GB (current: ${TOTAL_MEM} MB)"
DISK_FREE=$(df -Pm . | awk 'NR==2 {print $4}')
(( DISK_FREE >= 5000 )) || warn "Recommended free disk >= 5 GB (current: ${DISK_FREE} MB)"

# ---------------------------------------------------------------------------
# 2) Docker
# ---------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  info "Installing Docker..."
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  ARCH="$(dpkg --print-architecture)"
  echo "deb [arch=${ARCH} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
  ok "Docker installed"
else
  ok "Docker already present ($(docker --version))"
fi
command -v docker >/dev/null || fail "docker not found"
docker compose version >/dev/null || fail "docker compose plugin is required"

# ---------------------------------------------------------------------------
# 3) .env
# ---------------------------------------------------------------------------
gen_secret() { openssl rand -base64 32 | tr -d '/+=' | head -c 48; }

gen_b64_key() {
  if command -v python3 >/dev/null 2>&1; then
    python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
  else
    openssl rand -base64 32 | tr '+/' '-_'
  fi
}

ensure_env_key() {
  local key="$1" value="$2"
  if ! grep -qE "^${key}=" .env 2>/dev/null; then
    printf '%s=%s\n' "$key" "$value" >> .env
    info "Added to .env: $key"
  fi
}

validate_payload_key() {
  local key="$1"
  python3 - "$key" <<'PY'
import base64, sys

raw_key = sys.argv[1].strip().strip("\"'")
if not raw_key:
    print("PAYLOAD_ENCRYPTION_KEY is empty.", file=sys.stderr)
    sys.exit(1)
padded = raw_key + "=" * (-len(raw_key) % 4)
try:
    decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
except Exception:
    print(
        "PAYLOAD_ENCRYPTION_KEY is not valid URL-safe base64.",
        file=sys.stderr,
    )
    sys.exit(1)
if len(decoded) != 32:
    print(
        f"PAYLOAD_ENCRYPTION_KEY must decode to exactly 32 bytes (got {len(decoded)}).",
        file=sys.stderr,
    )
    sys.exit(1)
PY
}

if [[ ! -f .env ]]; then
  info "Creating .env from .env.example..."
  [[ -f .env.example ]] || fail ".env.example not found"
  cp .env.example .env
  chmod 600 .env
  PG_PASS="$(gen_secret)"
  RD_PASS="$(gen_secret)"
  sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=${PG_PASS}/" .env
  sed -i "s/^REDIS_PASSWORD=.*/REDIS_PASSWORD=${RD_PASS}/" .env
  sed -i "s|^DATABASE_URL=.*|DATABASE_URL=postgresql+asyncpg://subio:${PG_PASS}@postgres:5432/subio|" .env
  sed -i "s|^REDIS_URL=.*|REDIS_URL=redis://:${RD_PASS}@redis:6379/0|" .env
  sed -i "s/CHANGE_ME_MINIMUM_32_CHARACTERS/$(gen_secret)/g" .env
  sed -i "s/CHANGE_ME_URLSAFE_BASE64_32_BYTES/$(gen_b64_key)/" .env
  warn "Created .env with generated secrets. Configure these before continuing:"
  echo "  - BOT_TOKEN"
  echo "  - ADMIN_TELEGRAM_IDS"
  echo "  - TESTER_BASE_URL   (e.g. https://tester.subio.vip)"
  echo "  - PUBLIC_BASE_URL   (Iran sub domain, e.g. https://subio.vip)"
  echo "  - INTERNAL_HMAC_KEY / PAYLOAD_ENCRYPTION_KEY  (copy same values to iran-tester)"
  echo "  - VERCEL_AI_GATEWAY_API_KEY"
  echo "  - TELETHON_*  (for monitor profile)"
  echo
  echo "Edit:"
  echo "  nano ${SCRIPT_DIR}/.env"
  echo
  echo "Then run again:"
  echo "  sudo ./install.sh --yes"
  exit 2
fi

chmod 600 .env

ensure_env_key AI_ENABLED true
ensure_env_key AI_GATEWAY_BASE_URL "https://ai-gateway.vercel.sh/v1"
ensure_env_key AI_MODEL_SOL "openai/gpt-5.6-sol"
ensure_env_key AI_MODEL_LUNA "openai/gpt-5.6-luna"
ensure_env_key VERCEL_AI_GATEWAY_API_KEY ""
ensure_env_key RETEST_HEALTHY_INTERVAL_SECONDS 10
ensure_env_key RETEST_HEALTHY_BATCH 8
ensure_env_key RETEST_DEAD_INTERVAL_SECONDS 180
ensure_env_key RETEST_DEAD_BATCH 5
ensure_env_key RETEST_DEMOTE_ON_FIRST_FAIL true

if grep -qE 'CHANGE_ME([^_]|$)|CHANGE_ME$' .env || grep -q 'BOT_TOKEN=CHANGE_ME' .env; then
  if [[ $ASSUME_YES -eq 1 ]]; then
    fail "CHANGE_ME placeholders remain in .env. Complete .env before continuing."
  fi
  warn "CHANGE_ME values remain in .env. Edit .env then rerun with --yes."
  echo "  nano ${SCRIPT_DIR}/.env"
  exit 2
fi

PAYLOAD_KEY="$(grep -E '^PAYLOAD_ENCRYPTION_KEY=' .env | head -1 | cut -d= -f2- | tr -d '\r' || true)"
if ! validate_payload_key "$PAYLOAD_KEY"; then
  fail "Invalid PAYLOAD_ENCRYPTION_KEY in .env. It must be URL-safe base64 that decodes to exactly 32 bytes. Generate with: python3 -c \"import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())\" — copy the same value to iran-tester."
fi
ok "PAYLOAD_ENCRYPTION_KEY validated (32 bytes)"

if [[ $WITH_MONITOR -eq -1 ]]; then
  if grep -qE '^TELETHON_SESSION=.+' .env && ! grep -qE '^TELETHON_SESSION=\s*$' .env; then
    WITH_MONITOR=1
    ok "TELETHON_SESSION detected — monitoring profile auto-enabled"
  else
    WITH_MONITOR=0
  fi
fi

set -a
# shellcheck disable=SC1091
source <(grep -E '^(POSTGRES_|REDIS_PASSWORD|DATABASE_URL|REDIS_URL)=' .env | sed 's/\r$//')
set +a

# ---------------------------------------------------------------------------
# 4) Build and start services
# ---------------------------------------------------------------------------
info "Validating docker-compose configuration..."
docker compose config --quiet

COMPOSE_PROFILES=()
if [[ $WITH_MONITOR -eq 1 ]]; then
  COMPOSE_PROFILES+=(--profile monitoring)
  ok "Monitoring profile (Telethon) enabled"
fi

if [[ $SKIP_BUILD -eq 0 ]]; then
  info "Building images (includes openai / AI libs)..."
  docker compose build --pull
else
  warn "Skipping image build (--skip-build)"
fi

info "Starting database dependencies..."
docker compose up -d postgres redis

# ---------------------------------------------------------------------------
# 5) Wait for healthy Postgres + apply migrations on existing volumes
# ---------------------------------------------------------------------------
info "Waiting for Postgres to become healthy..."
for i in $(seq 1 60); do
  if docker compose exec -T postgres pg_isready -U "${POSTGRES_USER:-subio}" -d "${POSTGRES_DB:-subio}" >/dev/null 2>&1; then
    break
  fi
  sleep 2
  [[ $i -eq 60 ]] && fail "Postgres did not start — check: docker compose logs postgres"
done
ok "Postgres is ready"

info "Applying migrations (safe on existing volumes)..."
apply_sql() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  info "  -> $(basename "$file")"
  docker compose exec -T postgres \
    psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-subio}" -d "${POSTGRES_DB:-subio}" < "$file"
}

apply_sql migrations/002_v21_extensions.sql
apply_sql migrations/003_scanner_settings.sql
apply_sql migrations/004_nullable_report_config.sql
apply_sql migrations/005_premium_bot.sql
ok "Migrations applied"

info "Starting application services..."
docker compose "${COMPOSE_PROFILES[@]}" up -d --remove-orphans

# ---------------------------------------------------------------------------
# 6) systemd unit + management CLI
# ---------------------------------------------------------------------------
info "Installing systemd unit: subio-main.service"
PROFILE_ARGS=""
if [[ $WITH_MONITOR -eq 1 ]]; then
  PROFILE_ARGS="--profile monitoring"
fi
cat > /etc/systemd/system/subio-main.service <<EOF
[Unit]
Description=SubIO Main Stack (Docker Compose)
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${SCRIPT_DIR}
ExecStart=/usr/bin/docker compose ${PROFILE_ARGS} up -d --remove-orphans
ExecStop=/usr/bin/docker compose stop
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable subio-main.service
ok "systemd enabled (auto-starts after reboot)"

cat > /usr/local/bin/subio-main <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "${SCRIPT_DIR}"
case "\${1:-}" in
  up)      docker compose ${PROFILE_ARGS} up -d ;;
  down)    docker compose down ;;
  restart) docker compose restart \${2:-} ;;
  logs)    shift; docker compose logs -f --tail=200 "\$@" ;;
  ps)      docker compose ps ;;
  pull)    docker compose pull; docker compose build --pull; docker compose ${PROFILE_ARGS} up -d ;;
  migrate) for f in migrations/002_v21_extensions.sql migrations/003_scanner_settings.sql migrations/004_nullable_report_config.sql migrations/005_premium_bot.sql; do docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U ${POSTGRES_USER:-subio} -d ${POSTGRES_DB:-subio} < "\$f"; done ;;
  *) echo "Usage: subio-main {up|down|restart|logs|ps|pull|migrate}"; exit 1 ;;
esac
EOF
chmod +x /usr/local/bin/subio-main
ok "CLI installed: subio-main {up|down|logs|ps|pull|migrate}"

# ---------------------------------------------------------------------------
# 7) Base firewall
# ---------------------------------------------------------------------------
if command -v ufw >/dev/null; then
  ufw allow OpenSSH >/dev/null 2>&1 || true
  ufw allow 80/tcp >/dev/null 2>&1 || true
  ufw allow 443/tcp >/dev/null 2>&1 || true
  if ufw status | grep -q inactive; then
    warn "UFW is inactive. Enable with: ufw --force enable"
  fi
fi

# ---------------------------------------------------------------------------
# 8) Optional Caddy
# ---------------------------------------------------------------------------
if [[ -n "$WITH_CADDY" ]]; then
  DOMAIN="$WITH_CADDY"
  info "Installing Caddy for ${DOMAIN} -> 127.0.0.1:8000"
  if ! command -v caddy >/dev/null; then
    apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
    apt-get update -qq
    apt-get install -y caddy
  fi
  cat > /etc/caddy/Caddyfile <<EOF
${DOMAIN} {
  encode gzip
  reverse_proxy 127.0.0.1:8000
}
EOF
  systemctl enable --now caddy
  systemctl reload caddy || systemctl restart caddy
  ok "Caddy serving https://${DOMAIN}"
fi

# ---------------------------------------------------------------------------
# 9) Health check
# ---------------------------------------------------------------------------
info "Running API health check..."
sleep 5
API_OK=0
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/health/ready >/dev/null 2>&1; then
    API_OK=1
    break
  fi
  sleep 2
done

echo
if [[ $API_OK -eq 1 ]]; then
  ok "API is live: http://127.0.0.1:8000"
  ok "Admin panel: http://127.0.0.1:8000/admin"
else
  docker compose logs --tail=100 api || true
  fail "Health check failed after 60 seconds"
fi

docker compose ps

MONITOR_LABEL=""
if [[ $WITH_MONITOR -eq 1 ]]; then
  MONITOR_LABEL=" · monitor"
fi

printf '\n'
printf '%b═══════════════════════════════════════════════════════%b\n' "$OK" "$NC"
printf '%b  SubIO Main Server installed successfully%b\n' "$OK" "$NC"
printf '%b═══════════════════════════════════════════════════════%b\n' "$OK" "$NC"
printf '\n'
printf 'Services: postgres · redis · api · bot · worker%s\n' "$MONITOR_LABEL"
printf '\n'
printf 'Useful commands:\n'
printf '  subio-main ps\n'
printf '  subio-main logs api bot worker\n'
printf '  subio-main restart worker\n'
printf '  docker compose --profile monitoring up -d   # enable monitor later\n'
printf '\n'
printf 'Next steps:\n'
printf '  1) Point your API domain to this server (Caddy/Nginx)\n'
printf '  2) On Iran VPS: sudo ./install.sh with the same HMAC/PAYLOAD keys\n'
printf '  3) TESTER_BASE_URL must point to Iran tester over HTTPS\n'
printf '  4) PUBLIC_BASE_URL must be the Iran subscription domain\n'
printf '\n'
printf 'Docs: ../docs/INSTALL.fa.md and ../docs/AI_FEATURES.fa.md\n'
