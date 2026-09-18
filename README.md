---
title: Vera — magicpin AI Challenge
emoji: 🏪
colorFrom: yellow
colorTo: green
sdk: docker
app_port: 8080
pinned: false
license: mit
---

# Vera — magicpin AI Challenge

Stateful WhatsApp merchant-growth bot for the [magicpin AI Challenge](https://magicpin.com/vera/ai-challenge).

**Team:** Lovepreet / Lovepreet Singh

## Endpoints

| Method | Path |
|--------|------|
| GET | `/v1/healthz` |
| GET | `/v1/metadata` |
| POST | `/v1/context` |
| POST | `/v1/tick` |
| POST | `/v1/reply` |

## Local run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn bot:app --host 0.0.0.0 --port 8080
```

## Free cloud deploy (no Mac, no credit card)

See **[DEPLOY.md](./DEPLOY.md)** — Back4app Containers via GitHub.

Fly.io / Hugging Face Docker currently require a card or Pro subscription.
