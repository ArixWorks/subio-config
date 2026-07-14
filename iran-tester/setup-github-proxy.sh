#!/usr/bin/env bash
# =============================================================================
# Configure Git (and optional shell env) to reach GitHub via SOCKS5.
# Run on Iran VPS only.
#
# Usage:
#   sudo ./setup-github-proxy.sh 'socks5h://USER:PASS@HOST:PORT'
#   sudo ./setup-github-proxy.sh 'HOST:PORT:USER:PASS'
#
# Examples:
#   sudo ./setup-github-proxy.sh 'socks5h://85v7iyg6:H0gUWsHvKGWN@154.44.94.240:5782'
#   sudo ./setup-github-proxy.sh '154.44.94.240:5782:85v7iyg6:H0gUWsHvKGWN'
#
# Disable later:
#   sudo ./setup-github-proxy.sh --off
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

[[ ${EUID} -eq 0 ]] || fail "Run as root: sudo ./setup-github-proxy.sh ..."

PROFILE_FILE="/etc/profile.d/subio-github-proxy.sh"
GIT_CONFIG_SYSTEM="/etc/gitconfig"

urlencode() {
  # encode user/pass for URI (basic)
  python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"
}

to_socks_uri() {
  local raw="$1"
  if [[ "$raw" == socks5://* || "$raw" == socks5h://* ]]; then
    # prefer socks5h (DNS via proxy) for Iran censorship
    echo "${raw/socks5:\/\//socks5h:\/\/}"
    return
  fi
  # HOST:PORT:USER:PASS
  IFS=':' read -r host port user pass <<<"$raw"
  [[ -n "${host:-}" && -n "${port:-}" && -n "${user:-}" && -n "${pass:-}" ]] \
    || fail "Invalid format. Use socks5h://USER:PASS@HOST:PORT or HOST:PORT:USER:PASS"
  local eu ep
  eu="$(urlencode "$user")"
  ep="$(urlencode "$pass")"
  echo "socks5h://${eu}:${ep}@${host}:${port}"
}

disable_proxy() {
  info "Disabling GitHub SOCKS proxy settings..."
  git config --system --unset-all http.proxy 2>/dev/null || true
  git config --system --unset-all https.proxy 2>/dev/null || true
  git config --system --unset-all http.https://github.com.proxy 2>/dev/null || true
  rm -f "$PROFILE_FILE"
  ok "Proxy disabled. Open a new shell (or reboot) so env vars are cleared."
  exit 0
}

if [[ "${1:-}" == "--off" || "${1:-}" == "off" ]]; then
  disable_proxy
fi

[[ -n "${1:-}" ]] || fail "Missing proxy. Example: sudo ./setup-github-proxy.sh 'HOST:PORT:USER:PASS'"

command -v git >/dev/null || apt-get install -y -qq git
command -v python3 >/dev/null || apt-get install -y -qq python3
command -v curl >/dev/null || apt-get install -y -qq curl

PROXY_URI="$(to_socks_uri "$1")"
# Masked display
MASKED="$(echo "$PROXY_URI" | sed -E 's#(socks5h?://)[^:]+:[^@]+@#\1***:***@#')"

info "Configuring git to use $MASKED"
git config --system http.proxy "$PROXY_URI"
git config --system https.proxy "$PROXY_URI"
# Only force GitHub through proxy (safer)
git config --system http.https://github.com.proxy "$PROXY_URI"
git config --system http.https://api.github.com.proxy "$PROXY_URI"
git config --system http.https://codeload.github.com.proxy "$PROXY_URI"

# Shell env for curl/wget in new sessions
cat > "$PROFILE_FILE" <<EOF
# Managed by SubIO setup-github-proxy.sh — GitHub access via SOCKS5
export ALL_PROXY="${PROXY_URI}"
export HTTPS_PROXY="${PROXY_URI}"
export HTTP_PROXY="${PROXY_URI}"
export all_proxy="${PROXY_URI}"
export https_proxy="${PROXY_URI}"
export http_proxy="${PROXY_URI}"
# Do not proxy local / docker internal
export NO_PROXY="localhost,127.0.0.1,::1"
export no_proxy="localhost,127.0.0.1,::1"
EOF
chmod 644 "$PROFILE_FILE"

info "Testing GitHub reachability via proxy (timeout 25s)..."
export ALL_PROXY="$PROXY_URI" HTTPS_PROXY="$PROXY_URI" HTTP_PROXY="$PROXY_URI"
if curl -fsSIL --connect-timeout 15 --max-time 25 https://github.com >/dev/null; then
  ok "curl -> github.com OK"
else
  warn "curl test failed — check SOCKS credentials / IP allowlist"
fi

if timeout 30 git ls-remote https://github.com/ArixWorks/subio-config.git HEAD >/dev/null 2>&1; then
  ok "git ls-remote OK"
else
  warn "git ls-remote failed — still can retry clone below"
fi

ok "Proxy configured."
cat <<EOF

Next (same shell — load env):
  source /etc/profile.d/subio-github-proxy.sh

If /opt/subio is incomplete:
  rm -rf /opt/subio
  git clone --depth 1 https://github.com/ArixWorks/subio-config.git /opt/subio
  cp /root/iran.env.bak /opt/subio/iran-tester/.env
  sed -i 's/\r\$//' /opt/subio/iran-tester/.env
  cd /opt/subio/iran-tester && docker compose up -d --build

Update later:
  cd /opt/subio/iran-tester && sudo ./update.sh

Disable proxy:
  sudo ./setup-github-proxy.sh --off
EOF
