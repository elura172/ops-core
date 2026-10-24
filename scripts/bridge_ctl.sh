#!/usr/bin/env bash
set -eo pipefail

UID_VAL=$(id -u)
GUI_DOMAIN="gui/$UID_VAL"
PLIST_DIR="$HOME/Library/LaunchAgents"
OPS_PLIST="$PLIST_DIR/ca.seekersbooks.opscore.plist"
TUNNEL_PLIST="$PLIST_DIR/ca.seekersbooks.tunnel.plist"
LOGS_DIR="/Users/mirrr/ops-core/logs"
URL_FILE="$LOGS_DIR/tunnel_url.txt"

function check_service_running() {
  local label="$1"
  launchctl print "$GUI_DOMAIN/$label" >/dev/null 2>&1
}

function cmd_status() {
  echo "========================================================"
  echo "       SEEKERS BOOKS · OPS-CORE & STAGING BRIDGE        "
  echo "========================================================"
  
  # 1. Ops-Core Daemon Status
  echo -n "● ops-core daemon (launchd ca.seekersbooks.opscore): "
  if check_service_running "ca.seekersbooks.opscore"; then
    echo "RUNNING"
  else
    echo "STOPPED / NOT LOADED"
  fi

  # Local HTTP Check
  echo -n "  Local HTTP (http://127.0.0.1:8000/health): "
  LOCAL_HEALTH=$(curl -s -m 2 http://127.0.0.1:8000/health 2>/dev/null || true)
  if [ -n "$LOCAL_HEALTH" ]; then
    echo "HEALTHY ($LOCAL_HEALTH)"
  else
    echo "UNREACHABLE"
  fi

  # 2. Tunnel Status
  echo -n "● Cloudflare Tunnel (launchd ca.seekersbooks.tunnel): "
  if check_service_running "ca.seekersbooks.tunnel"; then
    echo "RUNNING"
  else
    echo "STOPPED / NOT LOADED"
  fi

  # Active Tunnel URL
  if [ -f "$URL_FILE" ]; then
    ACTIVE_URL=$(cat "$URL_FILE" | tr -d '[:space:]')
    echo "  Public Endpoint: $ACTIVE_URL"
    echo -n "  Public Health Check: "
    PUB_HEALTH=$(curl -s -m 4 "$ACTIVE_URL/health" 2>/dev/null || true)
    if [ -n "$PUB_HEALTH" ]; then
      echo "REACHABLE ($PUB_HEALTH)"
    else
      echo "UNREACHABLE"
    fi
  else
    echo "  Public Endpoint: (no tunnel URL recorded yet)"
  fi

  # 3. Vercel Storefront
  echo "● Vercel Storefront: https://bookshop-ledger.vercel.app"
  echo -n "  Storefront Status: "
  STORE_CODE=$(curl -s -o /dev/null -w "%{http_code}" https://bookshop-ledger.vercel.app/ 2>/dev/null || true)
  echo "HTTP $STORE_CODE"
  echo "========================================================"
}

function cmd_start() {
  echo "Starting Seekers Books ops-core and tunnel services via launchd..."
  mkdir -p "$LOGS_DIR"

  if [ -f "$OPS_PLIST" ]; then
    launchctl bootstrap "$GUI_DOMAIN" "$OPS_PLIST" 2>/dev/null || launchctl load "$OPS_PLIST" 2>/dev/null || true
    echo "✓ Loaded ca.seekersbooks.opscore"
  fi

  if [ -f "$TUNNEL_PLIST" ]; then
    launchctl bootstrap "$GUI_DOMAIN" "$TUNNEL_PLIST" 2>/dev/null || launchctl load "$TUNNEL_PLIST" 2>/dev/null || true
    echo "✓ Loaded ca.seekersbooks.tunnel"
  fi

  sleep 2
  cmd_status
}

function cmd_stop() {
  echo "Stopping Seekers Books ops-core and tunnel services..."
  launchctl bootout "$GUI_DOMAIN" "$TUNNEL_PLIST" 2>/dev/null || launchctl unload "$TUNNEL_PLIST" 2>/dev/null || true
  launchctl bootout "$GUI_DOMAIN" "$OPS_PLIST" 2>/dev/null || launchctl unload "$OPS_PLIST" 2>/dev/null || true
  echo "✓ Services stopped."
}

function cmd_restart() {
  cmd_stop
  sleep 1
  cmd_start
}

function cmd_logs() {
  echo "Tailing ops-core and tunnel logs (Ctrl+C to exit)..."
  tail -f "$LOGS_DIR/opscore.log" "$LOGS_DIR/opscore.error.log" "$LOGS_DIR/tunnel.log" "$LOGS_DIR/tunnel.error.log"
}

function cmd_sync() {
  if [ ! -f "$URL_FILE" ]; then
    echo "Error: No tunnel URL found at $URL_FILE. Start the tunnel first."
    exit 1
  fi
  ACTIVE_URL=$(cat "$URL_FILE" | tr -d '[:space:]')
  echo "Syncing active tunnel URL ($ACTIVE_URL) to Vercel production..."
  
  cd /Users/mirrr/bookshop-ledger
  printf "%s" "$ACTIVE_URL" | vercel env rm OPS_CORE_BASE_URL production -y 2>/dev/null || true
  printf "%s" "$ACTIVE_URL" | vercel env add OPS_CORE_BASE_URL production
  printf "%s" "$ACTIVE_URL" | vercel env rm VITE_OPS_CORE_API_URL production -y 2>/dev/null || true
  printf "%s" "$ACTIVE_URL" | vercel env add VITE_OPS_CORE_API_URL production
  echo "Deploying to Vercel production..."
  vercel --prod
  echo "✓ Vercel successfully synced and redeployed with $ACTIVE_URL"
}

case "$1" in
  start)
    cmd_start
    ;;
  stop)
    cmd_stop
    ;;
  restart)
    cmd_restart
    ;;
  status)
    cmd_status
    ;;
  logs)
    cmd_logs
    ;;
  sync)
    cmd_sync
    ;;
  *)
    echo "Usage: $0 {status|start|stop|restart|logs|sync}"
    exit 1
    ;;
esac
