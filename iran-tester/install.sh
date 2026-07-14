#!/usr/bin/env bash
# =============================================================================
# SubIO Iran Tester (Iran VPS) — one-click installer
# Role: xray config testing + /sub/{token} delivery + SOCKS + S3 fallback
# Supported: Ubuntu 22.04 / 24.04
#
# Usage:
#   cd iran-tester
#   sudo ./install.sh
#
# Optional flags:
#   --with-caddy=tester.example.com,sub.example.com
#       (first domain → test/HMAC API, optional second → public user sub)
#   --main-ip=x.x.x.x     restrict test access to foreign server IP only
#   --skip-build
#   --yes
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

WITH_CADDY=""
MAIN_IP=""
SKIP_BUILD=0
ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --with-caddy=*) WITH_CADDY="${arg#*=}" ;;
    --main-ip=*) MAIN_IP="${arg#*=}" ;;
    --skip-build) SKIP_BUILD=1 ;;
    --yes|-y) ASSUME_YES=1 ;;
    -h|--help)
      sed -n '2,22p' "$0"
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
# 1) Base packages
# ---------------------------------------------------------------------------
info "Installing base packages..."
apt-get update -qq
apt-get install -y --no-install-recommends ca-certificates curl gnupg openssl ufw python3

TOTAL_MEM=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
(( TOTAL_MEM >= 900 )) || warn "Recommended RAM >= 1 GB (current: ${TOTAL_MEM} MB)"

# ---------------------------------------------------------------------------
# 2) Docker
# ---------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  info "Installing Docker..."
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
  ok "Docker installed"
else
  ok "Docker already present"
fi
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
  [[ -f .env.example ]] || fail ".env.example not found"
  cp .env.example .env
  chmod 600 .env
  sed -i "s/CHANGE_ME_MINIMUM_32_CHARACTERS/$(gen_secret)/g" .env
  sed -i "s/CHANGE_ME_URLSAFE_BASE64_32_BYTES/$(gen_b64_key)/" .env
  ensure_env_key SUBSCRIPTION_STORE_DIR /data/subs
  warn "Created .env with generated secrets."
  echo "  IMPORTANT: Set INTERNAL_HMAC_KEY and PAYLOAD_ENCRYPTION_KEY to match main-server exactly."
  echo "  Optionally configure SOCKS_PROXIES and ARVAN_S3_* as needed."
  echo
  echo "  Edit: nano ${SCRIPT_DIR}/.env"
  echo "  Then: sudo ./install.sh --yes"
  exit 2
fi

chmod 600 .env
ensure_env_key SUBSCRIPTION_STORE_DIR /data/subs
ensure_env_key XRAY_BINARY /usr/local/bin/xray
ensure_env_key XRAY_TEST_URL "https://cp.cloudflare.com/generate_204"
ensure_env_key MAX_OPERATION_SECONDS 10
ensure_env_key S3_POLL_SECONDS 3

if grep -q 'CHANGE_ME' .env; then
  if [[ $ASSUME_YES -eq 1 ]]; then
    fail "CHANGE_ME placeholders remain in .env. Edit .env before continuing."
  fi
  warn "Fix CHANGE_ME values in .env, then run: sudo ./install.sh --yes"
  exit 2
fi

PAYLOAD_KEY="$(grep -E '^PAYLOAD_ENCRYPTION_KEY=' .env | head -1 | cut -d= -f2- | tr -d '\r' || true)"
if ! validate_payload_key "$PAYLOAD_KEY"; then
  fail "Invalid PAYLOAD_ENCRYPTION_KEY in .env. It must be URL-safe base64 that decodes to exactly 32 bytes and must match main-server exactly. Generate with: python3 -c \"import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())\""
fi
ok "PAYLOAD_ENCRYPTION_KEY validated (32 bytes)"

# ---------------------------------------------------------------------------
# 4) Build & Up
# ---------------------------------------------------------------------------
info "Validating docker compose configuration..."
docker compose config --quiet

if [[ $SKIP_BUILD -eq 0 ]]; then
  info "Building tester image (includes xray-core download)..."
  docker compose build --pull
else
  warn "Skipping image build (--skip-build)"
fi

info "Starting tester..."
docker compose up -d --remove-orphans

# ---------------------------------------------------------------------------
# 5) systemd + CLI
# ---------------------------------------------------------------------------
info "Installing systemd unit: subio-iran.service"
cat > /etc/systemd/system/subio-iran.service <<EOF
[Unit]
Description=SubIO Iran Tester (Docker Compose)
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${SCRIPT_DIR}
ExecStart=/usr/bin/docker compose up -d --remove-orphans
ExecStop=/usr/bin/docker compose stop
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable subio-iran.service

cat > /usr/local/bin/subio-iran <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "${SCRIPT_DIR}"
case "\${1:-}" in
  up)      docker compose up -d ;;
  down)    docker compose down ;;
  restart) docker compose restart ;;
  logs)    shift; docker compose logs -f --tail=200 "\$@" ;;
  ps)      docker compose ps ;;
  pull)    docker compose build --pull && docker compose up -d ;;
  *) echo "Usage: subio-iran {up|down|restart|logs|ps|pull}"; exit 1 ;;
esac
EOF
chmod +x /usr/local/bin/subio-iran
ok "CLI installed: subio-iran {up|down|logs|ps|pull}"

# ---------------------------------------------------------------------------
# 6) Firewall — restrict tester access to foreign server IP
# ---------------------------------------------------------------------------
ufw allow OpenSSH >/dev/null 2>&1 || true
ufw allow 80/tcp >/dev/null 2>&1 || true
ufw allow 443/tcp >/dev/null 2>&1 || true
if [[ -n "$MAIN_IP" ]]; then
  info "Restricting tester access to main server IP=${MAIN_IP}"
  ufw allow from "$MAIN_IP" to any port 8080 proto tcp >/dev/null 2>&1 || true
  ok "UFW rule added for ${MAIN_IP}:8080"
else
  warn "To lock tester to foreign server only: sudo ./install.sh --main-ip=FOREIGN_IP --yes"
fi
if ufw status | grep -q inactive; then
  warn "UFW is inactive. Enable with: ufw --force enable"
fi

# ---------------------------------------------------------------------------
# 7) Optional Caddy
# ---------------------------------------------------------------------------
if [[ -n "$WITH_CADDY" ]]; then
  IFS=',' read -r TESTER_DOMAIN SUB_DOMAIN <<<"$WITH_CADDY"
  [[ "$SUB_DOMAIN" == "$TESTER_DOMAIN" ]] && SUB_DOMAIN=""
  info "Installing Caddy: tester=${TESTER_DOMAIN}${SUB_DOMAIN:+ sub=${SUB_DOMAIN}}"
  if ! command -v caddy >/dev/null; then
    apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
    apt-get update -qq
    apt-get install -y caddy
  fi
  cat > /etc/caddy/Caddyfile <<EOF
${TESTER_DOMAIN} {
  encode gzip
  reverse_proxy 127.0.0.1:8080
}
EOF
  if [[ -n "$SUB_DOMAIN" && "$SUB_DOMAIN" != "$TESTER_DOMAIN" ]]; then
    cat >> /etc/caddy/Caddyfile <<EOF
${SUB_DOMAIN} {
  encode gzip
  reverse_proxy 127.0.0.1:8080
}
EOF
  fi
  systemctl enable --now caddy
  systemctl reload caddy || systemctl restart caddy
  ok "Caddy ready — https://${TESTER_DOMAIN}${SUB_DOMAIN:+ and https://${SUB_DOMAIN}}"
fi

# ---------------------------------------------------------------------------
# 8) Health check
# ---------------------------------------------------------------------------
info "Running readiness check..."
HEALTH_OK=0
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8080/health/ready >/dev/null 2>&1; then
    HEALTH_OK=1
    break
  fi
  sleep 2
done

echo
if [[ $HEALTH_OK -eq 1 ]]; then
  ok "Tester is ready: http://127.0.0.1:8080"
else
  docker compose logs --tail=80 tester || true
  fail "Tester readiness check failed"
fi
docker compose ps

printf '\n'
printf '%b═══════════════════════════════════════════════════════%b\n' "$OK" "$NC"
printf '%b  SubIO Iran Tester installed successfully%b\n' "$OK" "$NC"
printf '%b═══════════════════════════════════════════════════════%b\n' "$OK" "$NC"
printf '\n'
printf 'Endpoints:\n'
printf '  POST /v1/tests              test config from main (HMAC)\n'
printf '  GET  /sub/{token}           deliver subscription to users in Iran\n'
printf '  POST /v1/subscription-sync  sync subscription payloads\n'
printf '\n'
printf 'Commands:\n'
printf '  subio-iran ps\n'
printf '  subio-iran logs tester\n'
printf '\n'
printf 'Note: periodic main-server tests run in cheap mode to minimize Iran download traffic.\n'
printf 'Docs: ../docs/INSTALL.fa.md\n'
