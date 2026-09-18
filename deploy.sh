#!/bin/zsh
# One-time cloud deploy so your Mac can sleep.
# Run in Terminal.app:  ./deploy.sh

set -euo pipefail
cd "$(dirname "$0")"

if ! command -v flyctl >/dev/null 2>&1; then
  echo "Installing flyctl..."
  brew install flyctl
fi

echo "==> Checking Fly login..."
if ! flyctl auth whoami >/dev/null 2>&1; then
  echo "==> Logging into Fly.io (browser will open)..."
  flyctl auth login
fi
flyctl auth whoami

APP="vera-lovepreet"

echo "==> Ensuring app exists: $APP"
if ! flyctl status --app "$APP" >/dev/null 2>&1; then
  # Don't force --org personal (slug varies by account)
  if ! flyctl apps create "$APP"; then
    echo "ERROR: could not create app '$APP'."
    echo "Pick another name if taken, then: flyctl apps create <name> && edit fly.toml app=..."
    exit 1
  fi
fi

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [[ -z "${VERA_LLM_API_KEY:-}" ]]; then
  echo "ERROR: VERA_LLM_API_KEY missing from .env"
  exit 1
fi

echo "==> Setting secrets..."
flyctl secrets set \
  VERA_LLM_API_KEY="$VERA_LLM_API_KEY" \
  VERA_TEAM_NAME="${VERA_TEAM_NAME:-Lovepreet}" \
  VERA_TEAM_MEMBERS="${VERA_TEAM_MEMBERS:-Lovepreet Singh}" \
  VERA_CONTACT_EMAIL="${VERA_CONTACT_EMAIL:-lovepreetsingh40888@gmail.com}" \
  VERA_LLM_PROVIDER="${VERA_LLM_PROVIDER:-groq}" \
  VERA_LLM_MODEL="${VERA_LLM_MODEL:-llama-3.3-70b-versatile}" \
  --app "$APP"

echo "==> Deploying (this takes a couple minutes)..."
flyctl deploy --app "$APP" --ha=false

URL="https://${APP}.fly.dev"
echo
echo "============================================"
echo " LIVE URL (use this for Magicpin submit):"
echo " $URL"
echo "============================================"
echo
sleep 3
curl -sS "$URL/v1/healthz" || true
echo
curl -sS "$URL/v1/metadata" || true
echo
echo "You can now quit the local bot + Cloudflare tunnel."
echo "Submit: $URL"
