# Deploy without your Mac (free, no credit card)

## Why this path

Fly / Hugging Face / AWS need a card or Pro.  
**Back4app Containers** free tier: no credit card, runs in the cloud from GitHub.

## Step 1 — Put code on GitHub

In **Terminal.app**:

```bash
cd ~/Projects/vera-ai-challenge

# Fix GitHub login (browser will open)
gh auth login -h github.com -p https -w

# Create private repo + push
git add -A
git status   # confirm .env is NOT listed
git commit -m "Initial Vera bot for magicpin AI challenge"
gh repo create vera-ai-challenge --private --source=. --remote=origin --push
```

## Step 2 — Deploy on Back4app (cloud)

1. Sign up free: https://www.back4app.com/  
2. Open **Containers** / Web Deployment  
3. Connect **GitHub** and select `vera-ai-challenge`  
4. Create app:
   - Dockerfile path: `./Dockerfile`
   - Branch: `main`
5. Add **environment variables** (same as your `.env`, do not commit secrets):

| Key | Value |
|-----|--------|
| `VERA_TEAM_NAME` | `Lovepreet` |
| `VERA_TEAM_MEMBERS` | `Lovepreet Singh` |
| `VERA_CONTACT_EMAIL` | `lovepreetsingh40888@gmail.com` |
| `VERA_LLM_PROVIDER` | `groq` |
| `VERA_LLM_MODEL` | `llama-3.3-70b-versatile` |
| `VERA_LLM_API_KEY` | *(your Groq key)* |
| `PORT` | `8080` |

6. Deploy → copy the public URL Back4app gives you  
7. Test: `https://YOUR-APP.../v1/healthz`  
8. Submit **that base URL** on Magicpin  

Then you can shut your Mac — the bot runs on Back4app.

## Limits (free tier)

- ~256 MB RAM / shared CPU (enough for this bot)
- Fine for the challenge; not a huge production cluster
- Keep an eye on their free-plan transfer/hour limits

## After deploy

Paste the Back4app URL here and I’ll verify `/v1/healthz` + `/v1/metadata` for you.
