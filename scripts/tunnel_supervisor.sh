#!/usr/bin/env bash
set -eo pipefail

OPS_CORE_DIR="/Users/mirrr/ops-core"
LOGS_DIR="$OPS_CORE_DIR/logs"
URL_FILE="$LOGS_DIR/tunnel_url.txt"
ENV_FILE="$OPS_CORE_DIR/.env"
CLOUDFLARED="/opt/homebrew/bin/cloudflared"

mkdir -p "$LOGS_DIR"

# Source .env if present
if [ -f "$ENV_FILE" ]; then
  # Load environment safely
  set -a
  source "$ENV_FILE" 2>/dev/null || true
  set +a
fi

echo "[$(date -u +%FT%TZ)] [tunnel_supervisor] Starting bridge supervisor..."

# Mode 1: Named Cloudflare Tunnel via Token (Static Production Domain)
if [ -n "$TUNNEL_TOKEN" ]; then
  echo "[$(date -u +%FT%TZ)] [tunnel_supervisor] TUNNEL_TOKEN detected. Starting Named Cloudflare Tunnel..."
  if [ -n "$CUSTOM_DOMAIN" ]; then
    echo "https://$CUSTOM_DOMAIN" > "$URL_FILE"
  fi
  exec "$CLOUDFLARED" tunnel run --token "$TUNNEL_TOKEN"
fi

# Mode 2: Managed Quick Tunnel (trycloudflare.com)
echo "[$(date -u +%FT%TZ)] [tunnel_supervisor] No TUNNEL_TOKEN found. Launching managed Quick Tunnel for http://127.0.0.1:8000..."

# Wait up to 15s for ops-core to become healthy before opening tunnel
for i in {1..30}; do
  if curl -s http://127.0.0.1:8000/health >/dev/null 2>&1; then
    echo "[$(date -u +%FT%TZ)] [tunnel_supervisor] ops-core local daemon is healthy on port 8000."
    break
  fi
  sleep 0.5
done

# Run cloudflared and capture output
"$CLOUDFLARED" tunnel --url http://127.0.0.1:8000 --no-autoupdate 2>&1 | while IFS= read -r line; do
  echo "$line"
  if [[ "$line" =~ https://[-a-zA-Z0-9]+\.trycloudflare\.com ]]; then
    NEW_URL="${BASH_REMATCH[0]}"
    OLD_URL=""
    if [ -f "$URL_FILE" ]; then
      OLD_URL=$(cat "$URL_FILE")
    fi
    if [ "$NEW_URL" != "$OLD_URL" ]; then
      echo "[$(date -u +%FT%TZ)] [tunnel_supervisor] Detected new public tunnel URL: $NEW_URL"
      echo "$NEW_URL" > "$URL_FILE"
      
      # Optional: Auto-sync to Vercel if AUTO_SYNC_VERCEL=true
      if [ "$AUTO_SYNC_VERCEL" = "true" ]; then
        echo "[$(date -u +%FT%TZ)] [tunnel_supervisor] Auto-syncing new tunnel URL to Vercel production..."
        (
          cd /Users/mirrr/bookshop-ledger
          printf "%s" "$NEW_URL" | vercel env rm OPS_CORE_BASE_URL production -y 2>/dev/null || true
          printf "%s" "$NEW_URL" | vercel env add OPS_CORE_BASE_URL production 2>/dev/null || true
          printf "%s" "$NEW_URL" | vercel env rm VITE_OPS_CORE_API_URL production -y 2>/dev/null || true
          printf "%s" "$NEW_URL" | vercel env add VITE_OPS_CORE_API_URL production 2>/dev/null || true
          vercel --prod >/dev/null 2>&1 || true
          echo "[$(date -u +%FT%TZ)] [tunnel_supervisor] Vercel auto-sync completed."
        ) &
      fi
    fi
  fi
done
