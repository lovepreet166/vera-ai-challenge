# Redeploy v1.1 fixes to your VPS (no Mac dependency for judging)

SSH into the server that hosts https://vera.65.20.84.111.sslip.io and run:

```bash
cd /path/to/vera-ai-challenge   # wherever the repo lives on the VPS
git pull origin main            # after you push these fixes

# Enable Groq (required for better wording scores)
export VERA_LLM_PROVIDER=groq
export VERA_LLM_API_KEY='your_groq_key'
export VERA_LLM_MODEL=llama-3.3-70b-versatile
export VERA_TEAM_NAME=Lovepreet
export VERA_TEAM_MEMBERS='Lovepreet Singh'
export VERA_CONTACT_EMAIL=lovepreetsingh40888@gmail.com

# Restart
pkill -f 'uvicorn bot:app' || true
nohup .venv/bin/uvicorn bot:app --host 0.0.0.0 --port 8080 > /tmp/vera.log 2>&1 &

# Verify
curl -s https://vera.65.20.84.111.sslip.io/v1/metadata
# model should show llama-3.3-70b-versatile (not deterministic-composer...)
```

If the app is managed by systemd/docker-compose, restart that service instead and set the same env vars there.
