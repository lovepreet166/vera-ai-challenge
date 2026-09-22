# Vera AI Challenge — Submission Brief

**Candidate:** Lovepreet Singh  
**Email:** lovepreetsingh40888@gmail.com  
**Date:** 19 September 2026  
**Challenge:** magicpin Vera AI Challenge  

---

## 1. Public bot URL (for evaluation)

**https://vera.65.20.84.111.sslip.io**

| Check | Endpoint |
|--------|----------|
| Liveness | `GET /v1/healthz` |
| Team / approach | `GET /v1/metadata` |
| Interactive docs | `GET /docs` |

The service is deployed on a cloud VPS and will remain online for the evaluation window.

---

## 2. What I built

A **stateful HTTP bot** that implements Magicpin’s Vera message engine:

- Ingests the **4-context framework** (Category, Merchant, Trigger, Customer)
- Composes grounded WhatsApp-style messages with a clear CTA and rationale
- Handles multi-turn merchant replies (`send` / `wait` / `end`)
- Exposes the full judge contract: `/v1/context`, `/v1/tick`, `/v1/reply`, `/v1/healthz`, `/v1/metadata`

---

## 3. Approach (how it thinks)

### Message composition
- **Trigger-kind dispatch** — different framing for research digest, recall, perf dip, festival, competitor, IPL, etc.
- **Grounded in context only** — numbers, offers, digest titles, slots, and names come from pushed JSON (no invented competitors or fake offers)
- **Category + merchant fit** — peer CTR, active offers, owner first name, Hinglish when `hi` is in languages
- **Restraint** — suppression keys, expiry checks, one proactive action per merchant per tick; empty `actions: []` when nothing is worth sending

### Multi-turn conversation quality
| Scenario | Behaviour |
|----------|-----------|
| WhatsApp Business auto-reply | Detect canned patterns → exit quickly (don’t burn turns) |
| Affirmative intent (“ok let’s do it”, “haan”) | Switch qualifying → **action**; deliver concrete next step |
| Hostile / “stop” / not interested | Graceful `end` |
| Off-topic (e.g. GST) | Stay on-mission politely |

### Stack
- **Python + FastAPI + Uvicorn**
- Deterministic composer (reliable under 30s timeouts)
- Optional LLM polish via Groq (can be enabled without changing the API contract)
- Docker-ready; currently running on a persistent VPS (`sslip.io`)

---

## 4. Design principles aligned with Vera’s pain points

From the challenge brief, production Vera struggles with auto-reply pollution, intent handoff failures, and generic copy. This submission targets those directly:

1. **Faster auto-reply exit** — don’t waste 2–3 turns  
2. **Immediate action on clear intent** — no re-qualification after “let’s do it”  
3. **Service+price specificity** — e.g. Cleaning @ ₹299, not vague “10% off”  
4. **Curiosity / reciprocity levers** where the trigger supports them (research digests, curious-ask)

---

## 5. Example of output quality

**Weak (generic):**  
“Hi Doctor, want to run a discount campaign today?”

**This bot (grounded):**  
Uses merchant CTR vs peer median, live catalog offers, digest source/page, customer slots and language preference — then asks one low-friction next step.

---

## 6. Code & reproducibility

- Private GitHub repository with full source, Dockerfile, and documentation  
- Repo: `https://github.com/lovepreet166/vera-ai-challenge` (collaborator access available on request)  
- Local self-test via Magicpin’s `judge_simulator.py` contract  
- Expanded dataset generated from the official seed pack

---

## 7. Operational readiness

| Item | Status |
|------|--------|
| Public HTTPS URL | Live |
| Health checks | Passing |
| Team metadata | Lovepreet / Lovepreet Singh |
| Timeout-safe responses | Designed for ≤30s |
| Judging window uptime | VPS kept running |

---

## 8. Closing

I’m excited about Vera’s merchant-growth problem space and happy to walk through architecture, trade-offs, or a live trace of `/v1/tick` + `/v1/reply` during review.

**Thank you for the opportunity.**  
— Lovepreet Singh  
lovepreetsingh40888@gmail.com
