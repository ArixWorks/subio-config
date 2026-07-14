#!/usr/bin/env bash
# =============================================================================
# SubIO Main Server — one-command update from GitHub
# Usage (on abroad VPS):
#   cd /opt/subio/main-server   # or ~/subio-config/main-server
#   sudo ./update.sh
# =============================================================================
set -Eeuo pipefail

if [[ -t 1 ]] && command -v tput >/dev/null 2>&1; then
  INFO="$(tput setaf 6)"; OK="$(tput setaf 2)"; WARN="$(tput setaf 3)"
  ERROR="$(tput bold; tput setaf 1)"; NC="$(tput sgr0)"
else
  INFO=$'\033[36m'; OK=$'\033[32m'; WARN=$'\033[33m'; ERROR=$'\033[1;31m'; NC=$'\033[0m'
fi
_ts() { date '+%H:%M:%S'; }
info()  { printf '%b[%s] [INFO]%b %s\n' "$INFO" "$(_ts)" "$NC" "$*"; }
ok()    { printf '%b[%s] [OK]%b %s\n' "$OK" "$(_ts)" "$NC" "$*"; }
warn()  { printf '%b[%s] [WARN]%b %s\n' "$WARN" "$(_ts)" "$NC" "$*"; }
fail()  { printf '%b[%s] [ERROR]%b %s\n' "$ERROR" "$(_ts)" "$NC" "$*" >&2; exit 1; }

[[ ${EUID} -eq 0 ]] || fail "Run as root: sudo ./update.sh"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$SCRIPT_DIR"

# Prefer repo root if this is a git clone of the full project
if [[ -d "$REPO_ROOT/.git" ]]; then
  cd "$REPO_ROOT"
  info "Pulling latest from GitHub (repo root)..."
  git fetch origin
  BRANCH="$(git rev-parse --abbrev-ref HEAD)"
  git pull --ff-only origin "$BRANCH" || fail "git pull failed — resolve conflicts or stash local edits"
  cd "$SCRIPT_DIR"
elif [[ -d "$SCRIPT_DIR/.git" ]]; then
  info "Pulling latest from GitHub (main-server only)..."
  git pull --ff-only || fail "git pull failed"
else
  fail "Not a git checkout. Clone first: git clone https://github.com/ArixWorks/subio-config.git /opt/subio"
fi

[[ -f .env ]] || fail ".env missing — copy from .env.example and fill secrets (never commit .env)"

sed -i 's/\r$//' .env install.sh update.sh 2>/dev/null || true

info "Rebuilding and restarting Main stack..."
MONITOR_ARGS=()
if grep -qE '^TELETHON_SESSION=.+' .env && ! grep -qE '^TELETHON_SESSION=\s*$' .env; then
  MONITOR_ARGS=(--profile monitoring)
fi

docker compose "${MONITOR_ARGS[@]}" build
docker compose "${MONITOR_ARGS[@]}" up -d --remove-orphans

# Apply new SQL migrations safely on existing DB
if [[ -d migrations ]]; then
  info "Applying migrations..."
  set -a
  # shellcheck disable=SC1091
  source <(grep -E '^(POSTGRES_USER|POSTGRES_DB)=' .env | sed 's/\r$//')
  set +a
  for f in migrations/002_v21_extensions.sql migrations/003_scanner_settings.sql migrations/004_nullable_report_config.sql; do
    [[ -f "$f" ]] || continue
    docker compose exec -T postgres \
      psql -v ON_ERROR_STOP=0 -U "${POSTGRES_USER:-subio}" -d "${POSTGRES_DB:-subio}" < "$f" >/dev/null || true
  done
fi

sleep 4
if curl -fsS http://127.0.0.1:8000/health/live >/dev/null; then
  ok "Main API is healthy"
else
  warn "API health check failed — run: docker compose logs --tail=80 api"
fi

docker compose ps
ok "Update complete"
