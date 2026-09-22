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
    r"i(?:'m| am) (?:an )?automated",
    r"aapki jaankari ke liye",
    r"आपके संदेश के लिए धन्यवाद",
    r"main ek automated",
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
    r"\bstart\b",
    r"confirm",
    r"proceed",
    r"\bsure\b",
    r"do it",
    r"kar do",
    r"\bkaro\b",
    r"\bhaan\b",
    r"\bhaa?n?\b",
    r"chalte hain",
    r"theek hai",
    r"thik hai",
    r"bilkul",
    r"send (it|me)",
    r"\bdraft\b",
    r"\bgo\b",
    r"\bdo\b",
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
    r"\bspam\b",
    r"\buseless\b",
    r"shut up",
]

HOSTILE_PATTERNS = [
    r"\bspam\b",
    r"\buseless\b",
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
    topics = ["gst", "loan", "hotel", "flight", "income tax", "itr", "salary", "passport"]
    return any(t in text for t in topics)


def _owner(merchant: Optional[Dict[str, Any]]) -> str:
    return ((merchant or {}).get("identity") or {}).get("owner_first_name") or "there"


def _active_offer(merchant: Optional[Dict[str, Any]]) -> Optional[str]:
    for o in (merchant or {}).get("offers") or []:
        if o.get("status") == "active" and o.get("title"):
            return o["title"]
    return None


def _action_body(conv: ConversationState, merchant: Optional[Dict[str, Any]], turn: int) -> str:
    """Concrete next step — varies by trigger kind and turn (not one canned line)."""
    owner = _owner(merchant)
    offer = _active_offer(merchant)
    kind = (conv.trigger_kind or "").lower()
    last = (conv.last_bot_body or "").lower()

    # Second+ action turn: close the loop
    if turn >= 2:
        if offer:
            return (
                f"Sending now, {owner}. Draft locked around {offer}. "
                f"Reply CONFIRM to publish, or EDIT with one change."
            )
        return (
            f"Proceeding now, {owner} — draft is ready. "
            f"Reply CONFIRM to send, or tell me one edit."
        )

    if "research" in kind or "digest" in kind or "abstract" in last or "jida" in last:
        return (
            f"Done {owner}. Pulling the abstract now + drafting a 90-sec patient WhatsApp. "
            f"I'll paste both here in a minute — reply EDIT or CONFIRM."
        )
    if "recall" in kind or "appointment" in kind or "refill" in kind:
        return (
            f"Done {owner}. Booking/reminder flow started. "
            f"I'll confirm the slot with the customer and update you. Reply STOP to cancel."
        )
    if "perf" in kind or "dip" in kind or "spike" in kind:
        hook = f" around {offer}" if offer else ""
        return (
            f"Done {owner}. Drafting the recovery/momentum WhatsApp{hook} + a Google post now. "
            f"Reply EDIT or CONFIRM when you see it."
        )
    if "festival" in kind or "ipl" in kind or "seasonal" in kind:
        hook = f" featuring {offer}" if offer else ""
        return (
            f"Done {owner}. Drafting the timed campaign WhatsApp{hook} + Insta/Google line. "
            f"Live draft in ~2 min — reply CONFIRM to push."
        )
    if "competitor" in kind:
        hook = f" with {offer}" if offer else " with a photo refresh + clear offer pin"
        return (
            f"Done {owner}. Counter-move draft{hook} coming next. "
            f"Reply CONFIRM to use it this week."
        )
    if "curious" in kind or "search" in last or "rising" in last:
        hook = f" — {offer}" if offer else ""
        return (
            f"Done {owner}. Drafting the demand-catch WhatsApp{hook} + matching Google post. "
            f"Reply EDIT or CONFIRM."
        )
    if "review" in kind:
        return (
            f"Done {owner}. Writing (1) a reply template for that review theme and "
            f"(2) a 3-bullet ops fix note for your team. Reply CONFIRM."
        )
    if "renewal" in kind or "winback" in kind:
        return (
            f"Done {owner}. Preparing your 1-page ROI / renewal summary from recent activity. "
            f"I'll send it here — reply if you want a shorter version."
        )
    if offer:
        return (
            f"Done {owner}. Next step: draft ready around {offer}. "
            f"I'll send the WhatsApp copy + one Google post line. Reply EDIT or CONFIRM."
        )
    return (
        f"Done {owner}. Switching to action — I'll deliver the concrete draft next "
        f"(WhatsApp copy + next step). Reply EDIT or CONFIRM."
    )


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

    # Auto-reply: try once (gold Pattern B), then end
    if is_auto_reply(message, conv):
        conv.auto_reply_hits += 1
        if conv.auto_reply_hits == 1:
            owner = _owner(merchant)
            body = (
                f"Samajh gayi{', ' + owner if owner != 'there' else ''}. "
                f"Before this goes to the team — want to see the exact next step yourself? "
                f"2 minutes. Reply YES if you're the owner/manager, or I'll reconnect later."
            )
            conv.last_bot_body = body
            return {
                "action": "send",
                "body": body,
                "cta": "binary_yes_no",
                "rationale": "First auto-reply hit: one owner-check (Pattern B), then stop if it repeats.",
            }
        conv.mode = "ended"
        return {
            "action": "end",
            "rationale": "Repeated auto-reply; ending politely without burning more turns.",
        }

    # Intent → action (contextual deliverable)
    if is_affirmative(message) or conv.mode == "action":
        conv.mode = "action"
        send_n = conv.action_sends + 1
        body = _action_body(conv, merchant, send_n)
        conv.action_sends = send_n
        if conv.last_bot_body and body.strip() == conv.last_bot_body.strip():
            body = (
                f"Here is the concrete next step: I'm queuing the send now. "
                f"Reply CONFIRM to publish or STOP to hold."
            )
        conv.last_bot_body = body
        return {
            "action": "send",
            "body": body,
            "cta": "binary_yes_no",
            "rationale": "Affirmative intent → action mode with trigger-specific next step (no re-qualification).",
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

    # Default: single low-friction binary
    owner = _owner(merchant)
    offer = _active_offer(merchant)
    if offer:
        body = (
            f"Got it {owner}. Fastest path: should I draft around your live {offer} now (YES), "
            f"or tweak the angle first (EDIT)?"
        )
    else:
        body = (
            f"Got it {owner}. To move fast: should I (A) draft the message now, or "
            f"(B) adjust the offer/angle first? Reply A or B."
        )
    if conv.last_bot_body and body == conv.last_bot_body:
        body = f"Quick choice {owner}: draft now (YES) or pause (STOP)?"
    conv.last_bot_body = body
    return {
        "action": "send",
        "body": body,
        "cta": "binary_yes_no",
        "rationale": "Continuing qualification with one low-friction binary choice.",
    }
