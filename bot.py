"""
Vera bot — magicpin AI Challenge

Run:
  uvicorn bot:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from composer import compose, trigger_expired
from handlers import handle_reply
from state import store

load_dotenv()

app = FastAPI(title="Vera — magicpin AI Challenge", version="1.0.0")

TEAM_NAME = os.getenv("VERA_TEAM_NAME", "Lovepreet")
TEAM_MEMBERS = [m.strip() for m in os.getenv("VERA_TEAM_MEMBERS", "Lovepreet Singh").split(",") if m.strip()]
CONTACT_EMAIL = os.getenv("VERA_CONTACT_EMAIL", "lovepreetsingh40888@gmail.com")
MODEL_NAME = os.getenv("VERA_LLM_MODEL") or (
    "deterministic-composer+optional-llm" if not os.getenv("VERA_LLM_API_KEY") else os.getenv("VERA_LLM_PROVIDER", "llm")
)
APPROACH = (
    "v1.1: trigger-kind + adaptive unknown-kind compose, high-compulsion CTAs, "
    "Pattern-B auto-reply (try once then end), contextual action replies, optional Groq polish."
)
VERSION = "1.1.0"

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
        "submitted_at": datetime.utcnow().isoformat() + "Z",
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
    actions: List[Dict[str, Any]] = []
    used_merchants = set()

    for trg_id, trigger in store.list_triggers(body.available_triggers):
        if len(actions) >= 20:
            break

        if trigger_expired(trigger, body.now):
            continue

        suppression_key = trigger.get("suppression_key") or ""
        if suppression_key and store.is_suppressed(suppression_key):
            continue

        merchant_id = trigger.get("merchant_id")
        if not merchant_id or merchant_id in used_merchants:
            # One proactive action per merchant per tick keeps spam down
            continue

        merchant = store.get("merchant", merchant_id)
        if not merchant:
            continue
        category = store.get_category_for_merchant(merchant)
        if not category:
            continue

        customer_id = trigger.get("customer_id")
        customer = store.get("customer", customer_id) if customer_id else None

        composed = compose(category, merchant, trigger, customer)
        conversation_id = store.new_conversation_id(merchant_id, trg_id)
        conv = store.get_or_create_conversation(
            conversation_id,
            merchant_id=merchant_id,
            customer_id=customer_id,
            trigger_id=trg_id,
            trigger_kind=trigger.get("kind"),
        )
        conv.last_bot_body = composed["body"]
        conv.turns.append({"from": "vera", "msg": composed["body"]})

        if suppression_key:
            store.mark_suppressed(suppression_key)
        used_merchants.add(merchant_id)

        actions.append(
            {
                "conversation_id": conversation_id,
                "merchant_id": merchant_id,
                "customer_id": customer_id,
                "send_as": composed["send_as"],
                "trigger_id": trg_id,
                "template_name": composed["template_name"],
                "template_params": composed["template_params"],
                "body": composed["body"],
                "cta": composed["cta"],
                "suppression_key": composed["suppression_key"],
                "rationale": composed["rationale"],
            }
        )

    return {"actions": actions}


@app.post("/v1/reply")
async def reply(body: ReplyBody):
    conv = store.get_or_create_conversation(
        body.conversation_id,
        merchant_id=body.merchant_id,
        customer_id=body.customer_id,
    )
    conv.turns.append({"from": body.from_role, "msg": body.message, "turn": body.turn_number})

    merchant = store.get("merchant", body.merchant_id or conv.merchant_id)
    result = handle_reply(conv, body.message, merchant)

    if result.get("action") == "send" and result.get("body"):
        conv.turns.append({"from": "vera", "msg": result["body"]})
        conv.last_bot_body = result["body"]
    if result.get("action") == "end":
        conv.mode = "ended"

    return result


@app.post("/v1/teardown")
async def teardown():
    store.clear()
    return {"ok": True}
