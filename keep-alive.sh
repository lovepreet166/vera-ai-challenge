#!/bin/zsh
# Keep the local Vera bot + Cloudflare tunnel awake (fully free, no card).
# Run in Terminal.app and leave it open:
#   ./keep-alive.sh

set -euo pipefail
cd "$(dirname "$0")"

PORT=8080
URL_FILE="$PWD/PUBLIC_URL.txt"

echo "==> Starting bot on :$PORT (if needed)..."
if ! curl -sf "http://127.0.0.1:$PORT/v1/healthz" >/dev/null 2>&1; then
  if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
  fi
  nohup .venv/bin/uvicorn bot:app --host 0.0.0.0 --port "$PORT" > /tmp/vera-bot.log 2>&1 &
  echo $! > /tmp/vera-bot.pid
  sleep 2
fi

echo "==> Starting Cloudflare tunnel (if needed)..."
if ! pgrep -f "cloudflared tunnel --url" >/dev/null 2>&1; then
  nohup cloudflared tunnel --url "http://127.0.0.1:$PORT" > /tmp/vera-tunnel.log 2>&1 &
  echo $! > /tmp/vera-tunnel.pid
  sleep 4
fi

# Extract latest public URL from tunnel log
URL=$(rg -o 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' /tmp/vera-tunnel.log 2>/dev/null | tail -1 || true)
if [[ -z "$URL" && -f "$URL_FILE" ]]; then
  URL=$(cat "$URL_FILE")
fi
if [[ -n "$URL" ]]; then
  echo "$URL" > "$URL_FILE"
fi

echo "==> Preventing Mac sleep (caffeinate)..."
# -d display, -i idle, -m disk, -s system sleep
nohup caffeinate -dims -w $(pgrep -f "uvicorn bot:app" | head -1) > /tmp/vera-caffeine.log 2>&1 &

echo
echo "============================================"
echo " FREE public URL (submit this):"
echo " ${URL:-check /tmp/vera-tunnel.log}"
echo "============================================"
curl -sS "${URL}/v1/healthz" 2>/dev/null || curl -sS "http://127.0.0.1:$PORT/v1/healthz"
echo
curl -sS "${URL}/v1/metadata" 2>/dev/null | head -c 300 || true
echo
echo
echo "Leave this Mac plugged in and this Terminal open."
echo "Lid can be closed ONLY if 'Prevent sleeping when displays are off' is enabled"
echo "in System Settings → Battery → Options (or Energy Saver)."
echo
echo "Press Ctrl+C to stop keep-alive monitoring (bot may keep running)."

# Light keep-alive ping so tunnel stays warm
while true; do
  curl -sf "http://127.0.0.1:$PORT/v1/healthz" >/dev/null || echo "$(date): bot health failed"
  if [[ -n "${URL:-}" ]]; then
    curl -sf "$URL/v1/healthz" >/dev/null || echo "$(date): public URL failed"
  fi
  sleep 60
done
