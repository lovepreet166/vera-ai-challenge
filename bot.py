"""
Vera bot — magicpin AI Challenge

Run:
  uvicorn bot:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor, wait as wait_futures
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from composer import _parse_dt, compose, llm_enabled, polish, trigger_expired
from handlers import handle_reply
from state import store

load_dotenv()

VERSION = "1.3.0"
SUBMITTED_AT = os.getenv("VERA_SUBMITTED_AT", "2026-10-06T00:00:00Z")
TICK_BUDGET_S = float(os.getenv("VERA_TICK_BUDGET", "20"))  # judge timeout is 30s
LLM_POOL = ThreadPoolExecutor(max_workers=20)  # own pool: slow LLM calls never hold up a response

app = FastAPI(title="Vera — magicpin AI Challenge", version=VERSION)

TEAM_NAME = os.getenv("VERA_TEAM_NAME", "Lovepreet")
TEAM_MEMBERS = [m.strip() for m in os.getenv("VERA_TEAM_MEMBERS", "Lovepreet Singh").split(",") if m.strip()]
CONTACT_EMAIL = os.getenv("VERA_CONTACT_EMAIL", "lovepreetsingh40888@gmail.com")


def _model_label() -> str:
    if os.getenv("VERA_LLM_API_KEY") and os.getenv("VERA_LLM_PROVIDER"):
        return os.getenv("VERA_LLM_MODEL") or os.getenv("VERA_LLM_PROVIDER") or "llm"
    return "deterministic-composer+optional-llm"


MODEL_NAME = _model_label()
APPROACH = (
    "Deterministic per-trigger-kind composer grounded in all 4 contexts (real payload keys, "
    "live-vs-catalog offer honesty, language pref, placeholder fallbacks to merchant data); "
    "optional temperature-0 LLM polish run concurrently under a 20s tick budget and rejected "
    "if it adds any number; rule-based multi-turn (auto-reply, join intent, wait, questions, "
    "abuse, off-topic, real drafts, customer slot booking)."
)

@app.get("/")
async def root():
    """Friendly landing page — judge uses /v1/*, not /."""
    return {
        "status": "ok",
        "message": "Vera bot is online (magicpin AI Challenge).",
        "team_name": TEAM_NAME,
        "endpoints": {
            "healthz": "/v1/healthz",
            "metadata": "/v1/metadata",
            "context": "POST /v1/context",
            "tick": "POST /v1/tick",
            "reply": "POST /v1/reply",
            "docs": "/docs",
        },
    }


class ContextBody(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: Dict[str, Any]
    delivered_at: str


class TickBody(BaseModel):
    now: str
    available_triggers: List[str] = Field(default_factory=list)


class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str
    message: str
    received_at: str
    turn_number: int


@app.get("/v1/healthz")
async def healthz():
    return {
        "status": "ok",
        "uptime_seconds": store.uptime_seconds(),
        "contexts_loaded": store.context_counts(),
    }


@app.get("/v1/metadata")
async def metadata():
    return {
        "team_name": TEAM_NAME,
        "team_members": TEAM_MEMBERS,
        "model": MODEL_NAME,
        "approach": APPROACH,
        "contact_email": CONTACT_EMAIL,
        "version": VERSION,
        "submitted_at": SUBMITTED_AT,
    }


@app.post("/v1/context")
async def push_context(body: ContextBody):
    if body.scope not in {"category", "merchant", "customer", "trigger"}:
        return JSONResponse(
            status_code=400,
            content={"accepted": False, "reason": "invalid_scope", "details": body.scope},
        )
    result = store.put_context(
        scope=body.scope,
        context_id=body.context_id,
        version=body.version,
        payload=body.payload,
        delivered_at=body.delivered_at,
    )
    if not result.get("accepted"):
        return JSONResponse(status_code=409, content=result)
    return result


@app.post("/v1/tick")
async def tick(body: TickBody):
    now = _parse_dt(body.now)
    triggers = store.list_triggers(body.available_triggers)
    triggers.sort(key=lambda kv: -(kv[1].get("urgency") or 0))  # most urgent first

    planned: List[Dict[str, Any]] = []
    used = set()  # one action per (merchant, customer) audience per tick
    for trg_id, trigger in triggers:
        if len(planned) >= 20:
            break
        if trigger_expired(trigger, body.now):
            continue
        suppression_key = trigger.get("suppression_key") or ""
        if suppression_key and store.is_suppressed(suppression_key):
            continue
        merchant_id = trigger.get("merchant_id")
        customer_id = trigger.get("customer_id")
        if not merchant_id or (merchant_id, customer_id) in used:
            continue
        merchant = store.get("merchant", merchant_id)
        category = store.get_category_for_merchant(merchant) if merchant else None
        if not (merchant and category):
            continue
        customer = store.get("customer", customer_id) if customer_id else None
        if customer_id and not customer:
            continue  # customer-scoped trigger without its customer context: don't guess
        composed = compose(category, merchant, trigger, customer, now=now)
        if store.already_sent(merchant_id, customer_id, composed["body"]):
            continue  # same text to the same person twice = spam; restraint is rewarded
        used.add((merchant_id, customer_id))
        planned.append({
            "trg_id": trg_id, "trigger": trigger, "merchant": merchant, "category": category,
            "customer": customer, "composed": composed,
        })

    if llm_enabled() and planned:
        # Polish concurrently in threads; anything not done within budget keeps its grounded draft.
        futures = [LLM_POOL.submit(polish, p["composed"], p["category"], p["merchant"]) for p in planned]
        done, _pending = await asyncio.to_thread(wait_futures, futures, TICK_BUDGET_S)
        for p, fut in zip(planned, futures):
            if fut in done and not fut.exception():
                p["composed"] = fut.result()

    actions: List[Dict[str, Any]] = []
    for p in planned:
        composed, trigger = p["composed"], p["trigger"]
        merchant_id, customer_id = trigger.get("merchant_id"), trigger.get("customer_id")
        conversation_id = store.new_conversation_id(merchant_id, p["trg_id"])
        conv = store.get_or_create_conversation(
            conversation_id, merchant_id=merchant_id, customer_id=customer_id,
            trigger_id=p["trg_id"], trigger_kind=trigger.get("kind"),
        )
        conv.turns.append({"from": "vera", "msg": composed["body"]})
        conv.bot_bodies.append(composed["body"])
        conv.last_bot_body = composed["body"]
        if composed["suppression_key"]:
            store.mark_suppressed(composed["suppression_key"])
        store.record_sent(merchant_id, customer_id, composed["body"])
        actions.append({
            "conversation_id": conversation_id,
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "send_as": composed["send_as"],
            "trigger_id": p["trg_id"],
            "template_name": composed["template_name"],
            "template_params": composed["template_params"],
            "body": composed["body"],
            "cta": composed["cta"],
            "suppression_key": composed["suppression_key"],
            "rationale": composed["rationale"],
        })
    return {"actions": actions}


@app.post("/v1/reply")
async def reply(body: ReplyBody):
    conv = store.get_or_create_conversation(
        body.conversation_id, merchant_id=body.merchant_id, customer_id=body.customer_id,
    )
    conv.turns.append({"from": body.from_role, "msg": body.message, "turn": body.turn_number})
    merchant = store.get("merchant", body.merchant_id or conv.merchant_id) or {}
    category = store.get_category_for_merchant(merchant) if merchant else None
    trigger = store.get("trigger", conv.trigger_id) or {}
    customer = store.get("customer", body.customer_id or conv.customer_id)
    result = handle_reply(conv, body.message, merchant, category, trigger, customer, from_role=body.from_role)
    if result.get("action") == "send":
        conv.turns.append({"from": "vera", "msg": result["body"]})
    return result


@app.post("/v1/teardown")
async def teardown():
    store.clear()
    return {"ok": True}
