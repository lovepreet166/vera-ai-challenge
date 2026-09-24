# Deploy on Render (recommended)

## One-click Blueprint

1. Open: **https://dashboard.render.com/blueprint/new?repo=https://github.com/lovepreet166/vera-ai-challenge**
2. Sign in with GitHub (authorize `lovepreet166/vera-ai-challenge` if asked).
3. Blueprint loads `render.yaml` → service name `vera-ai-challenge`.
4. When prompted for **`VERA_LLM_API_KEY`**, paste a fresh Groq key from https://console.groq.com/keys  
   (old keys that return 403 will not polish messages).
5. Click **Apply** / **Create resources**. Wait for the first deploy (green).
6. Copy the service URL, e.g. `https://vera-ai-challenge.onrender.com`
7. Verify:

```bash
curl -s https://YOUR-URL.onrender.com/v1/healthz
curl -s https://YOUR-URL.onrender.com/v1/metadata
```

Metadata should show `"version": "1.2.0"` and `"model": "llama-3.3-70b-versatile"`.

8. Submit that **base URL** to Magicpin (or ask HR to re-judge).

## Manual Web Service (same result)

Dashboard → **New** → **Web Service** → connect repo `lovepreet166/vera-ai-challenge`

| Setting | Value |
|--------|--------|
| Runtime | Python 3 |
| Build | `pip install -r requirements.txt` |
| Start | `uvicorn bot:app --host 0.0.0.0 --port $PORT` |
| Health check | `/v1/healthz` |
| Plan | Free (or Starter if you can pay — see below) |

Env vars: same as in `render.yaml` (`VERA_*` + Groq key).

## Critical: free tier sleeps

Free web services **spin down after ~15 min with no traffic**. Cold start ~1 min. Mid-judge sleep = bad score / timeouts.

**During judging**, keep it warm:

- Best: upgrade the service to **Starter** (always on), or
- Free workaround: ping every 5–10 min from [UptimeRobot](https://uptimerobot.com) (or any cron) to `https://YOUR-URL/v1/healthz`

Also: free instances wipe in-memory context on sleep/restart. The judge re-pushes contexts each run, so that is OK if the service is awake for the whole window.

## After deploy

Paste the `onrender.com` URL in chat and I’ll verify healthz + metadata.
