"""Multi-turn reply handling: auto-reply, intent, hostile/exit, action mode."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from state import ConversationState


AUTO_REPLY_PATTERNS = [
    r"thank you for contacting",
    r"thanks for contacting",
    r"our team will (get back|respond|reply)",
    r"we will (get back|respond) (to you )?shortly",
    r"we(?:'re| are) currently unavailable",
    r"business hours",
    r"leave a message",
    r"auto[- ]?reply",
    r"this is an automated",
    r"आपके संदेश के लिए धन्यवाद",
]

AFFIRMATIVE_PATTERNS = [
    r"\byes\b",
    r"\byeah\b",
    r"\byep\b",
    r"\bok\b",
    r"\bokay\b",
    r"lets? do it",
    r"let'?s do it",
    r"go ahead",
    r"start",
    r"confirm",
    r"proceed",
    r"sure",
    r"do it",
    r"kar do",
    r"karo",
    r"haan",
    r"haa?n?\b",
    r"chalte hain",
    r"theek hai",
    r"thik hai",
    r"bilkul",
    r"send (it|me)",
    r"draft",
]

NEGATIVE_PATTERNS = [
    r"\bstop\b",
    r"unsubscribe",
    r"not interested",
    r"no thanks",
    r"don'?t (message|contact|text)",
    r"band karo",
    r"nahi chahiye",
    r"mat bhejo",
    r"leave me alone",
    r"spam",
    r"useless",
    r"shut up",
]

HOSTILE_PATTERNS = [
    r"spam",
    r"useless",
    r"idiot",
    r"stupid",
    r"scam",
    r"fraud",
    r"harass",
]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def is_auto_reply(message: str, conv: ConversationState) -> bool:
    text = _norm(message)
    if not text:
        return False
    for pat in AUTO_REPLY_PATTERNS:
        if re.search(pat, text):
            return True
    # Verbatim repeat of prior merchant messages in this conversation
    merchant_msgs = [t["msg"] for t in conv.turns if t.get("from") == "merchant"]
    if len(merchant_msgs) >= 2 and merchant_msgs[-1] == merchant_msgs[-2]:
        return True
    return False


def is_affirmative(message: str) -> bool:
    text = _norm(message)
    return any(re.search(p, text) for p in AFFIRMATIVE_PATTERNS)


def is_negative_or_hostile(message: str) -> bool:
    text = _norm(message)
    return any(re.search(p, text) for p in NEGATIVE_PATTERNS + HOSTILE_PATTERNS)


def is_off_topic(message: str) -> bool:
    text = _norm(message)
    topics = ["gst", "loan", "hotel", "flight", "income tax", "itr", "salary"]
    return any(t in text for t in topics)


def handle_reply(
    conv: ConversationState,
    message: str,
    merchant: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if conv.mode == "ended":
        return {
            "action": "end",
            "rationale": "Conversation already ended; not re-opening.",
        }

    # Hostile / hard opt-out first
    if is_negative_or_hostile(message):
        conv.mode = "ended"
        return {
            "action": "end",
            "rationale": "Merchant opted out or hostile; graceful exit.",
        }

    # Auto-reply pollution — exit fast (production Vera burns 2–3 turns; we don't)
    if is_auto_reply(message, conv):
        conv.auto_reply_hits += 1
        conv.mode = "ended"
        return {
            "action": "end",
            "rationale": "Detected WhatsApp Business auto-reply; ending instead of burning turns.",
        }

    # Intent → action
    if is_affirmative(message) or conv.mode == "action":
        conv.mode = "action"
        owner = ((merchant or {}).get("identity") or {}).get("owner_first_name") or "there"
        body = (
            f"Done {owner} — drafting now. Next: I'll send (1) the short WhatsApp copy and "
            f"(2) a 1-line Google post. Reply EDIT if you want changes, or CONFIRM to publish."
        )
        if conv.last_bot_body and body.strip() == conv.last_bot_body.strip():
            body = (
                f"Proceeding now — here is the concrete next step: confirm the draft and I'll "
                f"queue the send. Reply CONFIRM."
            )
        conv.last_bot_body = body
        return {
            "action": "send",
            "body": body,
            "cta": "binary_yes_no",
            "rationale": "Affirmative intent detected; switched qualifying→action with concrete next step.",
        }

    # Off-topic redirect
    if is_off_topic(message):
        body = (
            "Got it — that's outside what I can help with here. "
            "I can still help with Google profile, offers, campaigns, or customer recalls. "
            "Want to continue on one of those?"
        )
        conv.last_bot_body = body
        return {
            "action": "send",
            "body": body,
            "cta": "open_ended",
            "rationale": "Off-topic ask; stayed on-mission politely.",
        }

    # Default: light qualifying advance (one clear question)
    owner = ((merchant or {}).get("identity") or {}).get("owner_first_name") or "there"
    body = (
        f"Got it {owner}. To move fast: should I (A) draft the message now, or "
        f"(B) adjust the offer/angle first? Reply A or B."
    )
    if conv.last_bot_body and body == conv.last_bot_body:
        body = f"Quick choice {owner}: draft now (YES) or pause for later (STOP)?"
    conv.last_bot_body = body
    return {
        "action": "send",
        "body": body,
        "cta": "binary_yes_no",
        "rationale": "Continuing qualification with a single low-friction binary choice.",
    }
