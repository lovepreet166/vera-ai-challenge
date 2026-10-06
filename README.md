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

# Vera — magicpin AI Challenge (Lovepreet Singh)

**Approach.** One composer per trigger kind (28 kinds + keyword routing + a grounded fallback). Each reads the dataset's real payload keys and all four contexts: category voice/digest/peer stats/seasonal beats, merchant offers/performance/reviews/signals, trigger payload, customer language and history. When a payload is a placeholder, the composer anchors on the merchant's own numbers instead of inventing facts. Offers are called "live" only if they are in `merchant.offers`; catalog items are labelled as suggestions. Output is deterministic and takes about 1 ms per message.

**LLM use (optional).** A temperature-0 polish pass runs concurrently for all actions in a tick under a 20 s budget. A rewrite is discarded if it adds any number not already in the grounded draft, uses a taboo word, or grows the message by more than 30%.

**Multi-turn.** Rule-based router: opt-out → abuse (one apology, then exit) → WhatsApp auto-reply (one owner check, then exit) → customer slot booking → join/action intent (acts immediately — Pattern D fixed) → `wait` for "busy/later" → off-topic redirect → honest answers to questions (never invents prices) → YES delivers a real draft → CONFIRM executes → thanks closes. It mirrors Hindi/Hinglish per turn, never repeats a body, and caps at 6 bot turns.

**Tradeoffs.** Rules over LLM for replies: they are predictable, fast and can't hallucinate, at the cost of less natural phrasing on unusual replies. Restraint over volume: a far-off festival (188 days) gets a planning nudge, not a blast, and identical text is never sent twice to the same person.

**What would have helped.** Real available slots and plan prices per merchant, a per-merchant language preference (not just `languages`), and real payloads for the generated triggers.

Run: `uvicorn bot:app --port 8080` · Test: `python -m unittest discover -s tests` · Submission: `python make_submission.py expanded`
