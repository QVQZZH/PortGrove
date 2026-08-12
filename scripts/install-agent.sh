#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${PORT_GROVE_INSTALL_DIR:-/opt/port-grove}"
ENV_FILE="${PORT_GROVE_ENV_FILE:-/etc/port-grove-agent.env}"
UNIT_FILE="${PORT_GROVE_UNIT_FILE:-/etc/systemd/system/port-grove-agent.service}"
SKIP_SYSTEMD="${PORT_GROVE_SKIP_SYSTEMD:-0}"
SCRIPT_PATH=${BASH_SOURCE[0]:-$0}
SOURCE_ROOT=$(cd "$(dirname "$SCRIPT_PATH")/.." && pwd)

SERVER_URL="${PORT_GROVE_SERVER:-}"
AGENT_SECRET="${AGENT_TOKEN:-}"
PUBLIC_ADDRESS="${PUBLIC_IP:-}"
DISPLAY_NAME="${SERVER_NAME:-$(hostname -s)}"
PROVIDER="${SERVER_PROVIDER:-Self-hosted}"
INTERVAL="${PROBE_INTERVAL:-30}"
ALLOW_INSECURE=0

usage() {
  cat <<'EOF'
Install and register the Port Grove Agent.

Usage:
  sudo ./scripts/install-agent.sh --server URL [options]

Options:
  --server URL         Central Port Grove URL (required)
  --token TOKEN        Enrollment token; omit to enter it securely
  --public-ip IP       Address used to build verified Web links
  --name NAME          Server display name (default: hostname)
  --provider NAME      Provider or location label
  --interval SECONDS   Report interval, minimum 10 (default: 30)
  --insecure           Allow remote plain HTTP for temporary testing
  -h, --help           Show this help
EOF
}

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

while (($#)); do
  case "$1" in
    --server) SERVER_URL=${2:-}; shift 2 ;;
    --token) AGENT_SECRET=${2:-}; shift 2 ;;
    --public-ip) PUBLIC_ADDRESS=${2:-}; shift 2 ;;
    --name) DISPLAY_NAME=${2:-}; shift 2 ;;
    --provider) PROVIDER=${2:-}; shift 2 ;;
    --interval) INTERVAL=${2:-}; shift 2 ;;
    --insecure) ALLOW_INSECURE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown option: $1" ;;
  esac
done

[[ $EUID -eq 0 ]] || fail "run this installer with sudo"
[[ -n "$SERVER_URL" ]] || fail "--server is required"
[[ "$INTERVAL" =~ ^[0-9]+$ ]] && ((INTERVAL >= 10)) || fail "--interval must be at least 10 seconds"

if [[ "$SERVER_URL" =~ ^http:// ]] && [[ ! "$SERVER_URL" =~ ^http://(127\.0\.0\.1|localhost|\[::1\])([:/]|$) ]] && ((ALLOW_INSECURE == 0)); then
  fail "remote HTTP requires --insecure; use HTTPS for production"
fi
[[ "$SERVER_URL" =~ ^https?:// ]] || fail "--server must start with http:// or https://"

if [[ -z "$AGENT_SECRET" ]]; then
  [[ -r /dev/tty ]] || fail "no token supplied and no interactive terminal is available"
  read -r -s -p "Enrollment token: " AGENT_SECRET </dev/tty
  printf '\n' >/dev/tty
fi
(( ${#AGENT_SECRET} >= 16 )) || fail "agent token must contain at least 16 characters"

for value in "$SERVER_URL" "$AGENT_SECRET" "$PUBLIC_ADDRESS" "$DISPLAY_NAME" "$PROVIDER"; do
  [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]] || fail "configuration values cannot contain newlines"
done

if ! command -v python3 >/dev/null || ! command -v ss >/dev/null || ! command -v curl >/dev/null; then
  if command -v apt-get >/dev/null; then
    apt-get update
    apt-get install -y python3 iproute2 curl
  elif command -v dnf >/dev/null; then
    dnf install -y python3 iproute curl
  elif command -v yum >/dev/null; then
    yum install -y python3 iproute curl
  else
    fail "install Python 3, iproute2, and curl before running this installer"
  fi
fi
command -v systemctl >/dev/null || fail "systemd is required"

TEMP_SOURCE=""
cleanup() {
  if [[ -n "$TEMP_SOURCE" ]]; then rm -rf "$TEMP_SOURCE"; fi
}
trap cleanup EXIT

if [[ ! -f "$SOURCE_ROOT/agent/agent.py" || ! -f "$SOURCE_ROOT/deploy/port-grove-agent.service" ]]; then
  TEMP_SOURCE=$(mktemp -d)
  SOURCE_ROOT="$TEMP_SOURCE"
  install -d -m 0755 "$SOURCE_ROOT/agent" "$SOURCE_ROOT/deploy"
  for filename in agent.py collector.py __init__.py; do
    curl -fsSL "$SERVER_URL/api/agent/files/$filename" -o "$SOURCE_ROOT/agent/$filename"
  done
  curl -fsSL "$SERVER_URL/api/agent/files/port-grove-agent.service" -o "$SOURCE_ROOT/deploy/port-grove-agent.service"
fi

quote_env() {
  local value=$1
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  printf '"%s"' "$value"
}

install -d -m 0755 "$INSTALL_DIR/agent"
install -m 0644 "$SOURCE_ROOT/agent/__init__.py" "$INSTALL_DIR/agent/__init__.py"
install -m 0644 "$SOURCE_ROOT/agent/collector.py" "$INSTALL_DIR/agent/collector.py"
install -m 0644 "$SOURCE_ROOT/agent/agent.py" "$INSTALL_DIR/agent/agent.py"
install -m 0644 "$SOURCE_ROOT/deploy/port-grove-agent.service" "$UNIT_FILE"

umask 077
{
  printf 'PORT_GROVE_SERVER=%s\n' "$(quote_env "$SERVER_URL")"
  printf 'AGENT_TOKEN=%s\n' "$(quote_env "$AGENT_SECRET")"
  printf 'PUBLIC_IP=%s\n' "$(quote_env "$PUBLIC_ADDRESS")"
  printf 'SERVER_NAME=%s\n' "$(quote_env "$DISPLAY_NAME")"
  printf 'SERVER_PROVIDER=%s\n' "$(quote_env "$PROVIDER")"
  printf 'PROBE_INTERVAL=%s\n' "$INTERVAL"
  if ((ALLOW_INSECURE)); then
    printf 'ALLOW_INSECURE_HTTP=1\n'
  fi
} >"$ENV_FILE"
chmod 0600 "$ENV_FILE"

printf 'Testing the first report to %s ...\n' "$SERVER_URL"
export PORT_GROVE_SERVER="$SERVER_URL"
export AGENT_TOKEN="$AGENT_SECRET"
export PUBLIC_IP="$PUBLIC_ADDRESS"
export SERVER_NAME="$DISPLAY_NAME"
export SERVER_PROVIDER="$PROVIDER"
export PROBE_INTERVAL="$INTERVAL"
if ((ALLOW_INSECURE)); then export ALLOW_INSECURE_HTTP=1; fi
python3 "$INSTALL_DIR/agent/agent.py" --once

if [[ "$SKIP_SYSTEMD" == "1" ]]; then
  printf '\nPort Grove Agent download and first report completed.\n'
  exit 0
fi

systemctl daemon-reload
systemctl enable --now port-grove-agent.service
systemctl restart port-grove-agent.service
systemctl is-active --quiet port-grove-agent.service || fail "agent service did not start"

printf '\nPort Grove Agent installed successfully.\n'
printf 'Server: %s\n' "$DISPLAY_NAME"
printf 'Service: systemctl status port-grove-agent\n'
printf 'Logs: journalctl -u port-grove-agent -f\n'
