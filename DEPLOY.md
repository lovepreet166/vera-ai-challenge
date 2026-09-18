# Deploy without your Mac (free, no credit card)

## Status (automated)

- [x] Project committed (secrets excluded)
- [x] GitHub login
- [x] Private repo created + pushed: https://github.com/lovepreet166/vera-ai-challenge
- [ ] Back4app container deploy (needs your free account — 5 min in browser)

## Step 2 — Back4app (do this once in browser)

1. Open https://www.back4app.com/signup and create a **free** account (Google/GitHub login is fine).
2. Go to **Containers as a Service** / **Web Deployment**.
3. **Connect GitHub** → authorize → select repo **`lovepreet166/vera-ai-challenge`**.
4. Create app:
   - **Name:** `vera-ai-challenge`
   - **Branch:** `main`
   - **Root directory:** `/` (Dockerfile is in repo root)
   - **Dockerfile:** `./Dockerfile`
5. Add environment variables:

| Key | Value |
|-----|--------|
| `VERA_TEAM_NAME` | `Lovepreet` |
| `VERA_TEAM_MEMBERS` | `Lovepreet Singh` |
| `VERA_CONTACT_EMAIL` | `lovepreetsingh40888@gmail.com` |
| `VERA_LLM_PROVIDER` | `groq` |
| `VERA_LLM_MODEL` | `llama-3.3-70b-versatile` |
| `VERA_LLM_API_KEY` | *(paste from your local `.env`)* |
| `PORT` | `8080` |

6. Click **Create / Deploy**. Wait for build to go green.
7. Copy the public app URL from the sidebar **Actions** / Domains.
8. Test: `https://YOUR-URL/v1/healthz`
9. Submit that base URL on Magicpin.

Then shut your Mac — the bot runs on Back4app.

## After you have the URL

Paste it in chat and I’ll verify healthz + metadata.
