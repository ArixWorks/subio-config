#!/usr/bin/env bash
# =============================================================================
# SubIO Iran Tester — one-command update from GitHub
# Usage: cd /opt/subio/iran-tester && sudo ./update.sh
# On VPS, local edits to tracked files are discarded (`.env` is kept).
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

sync_git() {
  local root="$1"
  cd "$root"
  info "Syncing Git to origin/main (keeps .env; discards local tracked edits)..."
  git fetch origin
  local branch
  branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
  if git show-ref --verify --quiet "refs/remotes/origin/${branch}"; then
    git reset --hard "origin/${branch}"
  else
    git reset --hard origin/main
  fi
  ok "Git sync complete ($(git rev-parse --short HEAD))"
}

if [[ -d "$REPO_ROOT/.git" ]]; then
  sync_git "$REPO_ROOT"
  cd "$SCRIPT_DIR"
elif [[ -d "$SCRIPT_DIR/.git" ]]; then
  sync_git "$SCRIPT_DIR"
else
  warn "No git repo here — rebuilding from current files"
fi

[[ -f .env ]] || fail ".env missing — keep your secrets local; never commit .env"
sed -i 's/\r$//' .env install.sh update.sh 2>/dev/null || true

info "Validating PAYLOAD_ENCRYPTION_KEY..."
PAYLOAD_KEY="$(grep -E '^PAYLOAD_ENCRYPTION_KEY=' .env | head -1 | cut -d= -f2- | tr -d '\r' || true)"
python3 - "$PAYLOAD_KEY" <<'PY' || fail "Invalid PAYLOAD_ENCRYPTION_KEY (must be url-safe base64 of 32 bytes)"
import base64, sys
val = sys.argv[1].strip().strip('"\'')
val += "=" * (-len(val) % 4)
key = base64.urlsafe_b64decode(val.encode())
assert len(key) == 32, len(key)
PY

info "Rebuilding and restarting Iran tester..."
docker compose build
docker compose up -d --remove-orphans

sleep 4
if curl -fsS http://127.0.0.1:8080/health/live >/dev/null; then
  ok "Iran tester is healthy"
else
  warn "Health check failed — run: docker compose logs --tail=80 tester"
fi
docker compose ps
ok "Update complete"
