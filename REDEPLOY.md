# Redeploy v1.2 (required for 75+ attempt)

Magicpin scores the **live URL**, not GitHub. After pushing, SSH into the VPS that hosts
`https://vera.65.20.84.111.sslip.io` and run:

```bash
# Paste your Groq key once
export GROQ_KEY='gsk_...'   # from https://console.groq.com/keys

cd ~/vera-ai-challenge 2>/dev/null || cd /root/vera-ai-challenge 2>/dev/null || cd /home/*/vera-ai-challenge
git fetch origin && git reset --hard origin/main

python3 -m venv .venv 2>/dev/null || true
.venv/bin/pip install -q -r requirements.txt

# Persist env for restarts
cat > .env <<EOF
VERA_TEAM_NAME=Lovepreet
VERA_TEAM_MEMBERS=Lovepreet Singh
VERA_CONTACT_EMAIL=lovepreetsingh40888@gmail.com
VERA_LLM_PROVIDER=groq
VERA_LLM_API_KEY=$GROQ_KEY
VERA_LLM_MODEL=llama-3.3-70b-versatile
VERA_LLM_TIMEOUT=6
EOF

pkill -f 'uvicorn bot:app' || true
nohup .venv/bin/uvicorn bot:app --host 0.0.0.0 --port 8080 > /tmp/vera.log 2>&1 &
sleep 2
curl -s https://vera.65.20.84.111.sslip.io/v1/metadata
```

**Must show:**
- `"version": "1.2.0"`
- `"model": "llama-3.3-70b-versatile"` (not `deterministic-composer...`)

Then email Magicpin HR and ask to **re-run judging** on the same URL.
