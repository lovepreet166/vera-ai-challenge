"""Multi-turn reply handling.

Routing order (first match wins):
  ended -> opt-out -> abuse -> auto-reply -> customer booking flow -> join/action intent
  -> soft refusal / wait -> off-topic -> question -> affirmative -> unclear

Every send is checked against all previous bot bodies in the conversation, and the
bot exits after MAX_BOT_TURNS so it never loops.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from composer import _biz_name, _customer_name, _digest_by_id, _digest_by_kind, _live_offers, _owner
from state import ConversationState

MAX_BOT_TURNS = 6


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _has(text: str, phrases) -> bool:
    """Whole-word / whole-phrase match (no more 'do' matching 'what do you do')."""
    return any(re.search(rf"(?<![\w']){re.escape(p)}(?![\w'])", text) for p in phrases)


OPT_OUT = [
    "unsubscribe", "stop messaging", "stop sending", "stop texting", "stop messages", "not interested", "no thanks", "don't message", "dont message",
    "do not message", "don't contact", "dont contact", "remove me", "band karo", "nahi chahiye",
    "mat bhejo", "message mat karo", "leave me alone",
]
ABUSE = [
    "idiot", "stupid", "useless", "spam", "scam", "fraud", "bakwas", "shut up", "nonsense",
    "harass", "pagal", "bewakoof", "wasting my time",
]
AUTO_REPLY = [
    r"thank(s| you) for (contacting|reaching|your message|messaging)",
    r"our team will (get back|respond|reply|contact|revert)",
    r"we (will|'ll) (get back|respond|revert|reply)",
    r"currently (unavailable|away|closed)",
    r"(outside|during) (our )?(business|working|office) hours",
    r"our (business|working) hours are",
    r"leave (us )?a message",
    r"auto[- ]?(reply|response|generated)",
    r"(this is an|i am an|i'm an) automated",
    r"main ek automated",
    r"aapki jaankari ke liye",
    r"team tak pahuncha",
    r"आपके संदेश के लिए धन्यवाद",
]
JOIN_INTENT = [
    "join", "judna", "judrna", "jud na", "jodna", "judna hai", "sign up", "signup", "sign me up",
    "register", "onboard", "start karo", "chalu karo", "shuru karo", "i want to start",
]
AFFIRM = [
    "yes", "yeah", "yep", "yup", "ok", "okay", "sure", "go ahead", "let's do it", "lets do it",
    "do it", "proceed", "confirm", "confirmed", "haan", "han", "ha", "ji", "ji haan", "bilkul",
    "theek hai", "thik hai", "kar do", "karo", "chalo", "send it", "send me", "please do",
    "sounds good", "perfect", "done", "approved", "publish", "no problem", "👍",
]
SOFT_NO = ["no", "nope", "nahi", "nahin", "na", "not really", "not needed", "zaroorat nahi", "don't want", "dont want", "no need"]
WAIT = [
    "not now", "later", "baad mein", "baad me", "busy", "abhi nahi", "kal", "tomorrow",
    "next week", "call later", "give me time", "sochta", "sochti", "soch ke", "will think",
    "let me think", "in a meeting",
]
OFF_TOPIC = [
    "gst", "income tax", "itr", "tax filing", "loan", "insurance", "flight", "hotel booking",
    "passport", "visa", "salary", "bank account", "credit card", "stock market", "crypto",
]
PRICE_Q = ["cost", "price", "pricing", "charges", "charge", "fee", "fees", "kitna", "kitne", "paise", "rupees", "how much"]
IDENTITY_Q = ["who are you", "what is this", "what do you do", "kaun ho", "kya hai ye", "ye kya hai", "what's this"]
ACTION_REQ = ["update", "change", "add", "set", "upload", "edit", "fix", "post", "remove"]
QUESTION_START = (
    "what", "how", "why", "when", "where", "which", "who", "can", "could", "do ", "does", "is ",
    "are ", "will", "kya", "kaise", "kitna", "kitne", "kab", "kahan", "kaun", "kyun",
)
HINDI_TOKENS = [
    "hai", "hain", "nahi", "kya", "karo", "kar", "mujhe", "aap", "haan", "kitne", "kaise",
    "chahiye", "judna", "theek", "bhi", "mein", "kal", "abhi", "batao", "kijiye",
]


THANKS = ["thanks", "thank you", "thx", "ty", "shukriya", "dhanyavaad", "dhanyawad", "great", "👍", "🙏"]
RECAP = ["what were you saying", "tell me more", "go on", "explain", "batao", "kya keh rahe", "what was it", "say again"]


def _is_opt_out(text: str) -> bool:
    """Bare 'STOP' / 'stop.' or an explicit opt-out phrase; 'stop wasting my time' is abuse, not opt-out."""
    return bool(re.fullmatch(r"stop[.! ]*", text)) or _has(text, OPT_OUT)


def is_auto_reply(message: str, conv: ConversationState) -> bool:
    text = _norm(message)
    if not text:
        return False
    if any(re.search(p, text) for p in AUTO_REPLY):
        return True
    previous = [_norm(t["msg"]) for t in conv.turns[:-1] if t.get("from") in ("merchant", "customer")]
    return len(text) > 25 and text in previous


def _is_question(text: str) -> bool:
    return "?" in text or text.startswith(QUESTION_START)


def _speaks_hindi(text: str) -> bool:
    if re.search(r"[ऀ-ॿ]", text):
        return True
    words = set(re.findall(r"[a-z]+", text))
    return len(words & set(HINDI_TOKENS)) >= 1 and not words & {"the", "is", "are", "you", "what", "how"}


# ---------------------------------------------------------------------------
# Concrete deliverables (action mode actually ships something)
# ---------------------------------------------------------------------------

def _draft(conv: ConversationState, merchant: Dict[str, Any], category: Dict[str, Any], trigger: Dict[str, Any]) -> str:
    kind = (conv.trigger_kind or trigger.get("kind") or "").lower()
    payload = trigger.get("payload") or {}
    biz = _biz_name(merchant)
    locality = (merchant.get("identity") or {}).get("locality") or ""
    live = _live_offers(merchant)
    offer = live[0] if live else None

    if "cde" in kind:
        item = _digest_by_id(category, payload.get("digest_item_id")) or _digest_by_kind(category, ("cde",))
        if item:
            return (
                f"Calendar hold: {item.get('title')} — {item.get('date', '')[:16].replace('T', ' ')} "
                f"({payload.get('credits') or item.get('credits')} CDE credits).\n"
                f"Registration: {item.get('actionable') or item.get('source')}. I'll send the link here once you confirm."
            )
    if "research" in kind:
        item = _digest_by_id(category, payload.get("top_item_id")) or _digest_by_kind(category, ("research",))
        if item:
            return (
                f"Patient WhatsApp draft:\n\"Hi! {biz} here. New research ({item.get('source')}) shows "
                f"{(item.get('title') or '').lower()}. If you've had cavities recently, ask us whether a "
                f"shorter recall suits you. Reply YES to book a check.\""
            )
    if "regulation" in kind or "compliance" in kind:
        item = _digest_by_id(category, payload.get("top_item_id")) or _digest_by_kind(category, ("compliance",))
        if item:
            return (
                f"Checklist draft ({item.get('source')}):\n1. {item.get('actionable') or 'Review the circular'}\n"
                f"2. Note the change: {(item.get('summary') or '').split('. ')[0]}.\n"
                f"3. File the updated SOP with today's date and keep a copy at reception."
            )
    if "review" in kind:
        theme = (payload.get("theme") or "the issue").replace("_", " ")
        return (
            f"Reply template:\n\"Thank you for the feedback — you're right about the {theme}, and we're fixing it this week. "
            f"Please give us another try and ask for the owner directly.\"\n"
            f"Team note: 1) name one owner for {theme} 2) track it daily for 2 weeks 3) review on Monday."
        )
    if "competitor" in kind and offer:
        return f"Google post draft:\n\"{offer} at {biz}{', ' + locality if locality else ''} — what's included, explained up front. Book on WhatsApp or call.\""
    if "planning" in kind:
        topic = (payload.get("intent_topic") or "the program").replace("_", " ")
        return (
            f"Launch copy draft — {topic}:\n\"New at {biz}: {topic}. Limited spots, fixed start date."
            f"{' Starting from ' + offer + '.' if offer else ''} Reply on WhatsApp to reserve.\"\n"
            f"Tell me the start date + price and I'll finalise it."
        )
    if "renewal" in kind or "winback" in kind:
        perf = merchant.get("performance") or {}
        return (
            f"Summary — last {perf.get('window_days', 30)} days for {biz}: {perf.get('views', 0):,} Google views, "
            f"{perf.get('calls', 0)} calls, {perf.get('directions', 0)} direction requests, {perf.get('leads', 0)} leads."
        )
    if "gbp" in kind or "unverified" in kind:
        return (
            "Steps: 1) Open business.google.com → your listing 2) Tap 'Get verified' 3) Choose phone call if offered "
            "(fastest), else postcard 4) Send me the code here and I'll check it's done."
        )
    if "supply" in kind:
        batches = ", ".join(payload.get("affected_batches") or [])
        molecule = payload.get("molecule") or "the recalled medicine"
        return (
            f"Customer WhatsApp draft:\n\"Namaste, {biz} here. A {molecule} batch ({batches}) has been recalled as a precaution. "
            f"If yours is from this batch, bring it in and we'll replace it free.\"\nShelf: pull {batches} today."
        )
    if offer:
        return f"Google post draft:\n\"{offer} at {biz}{', ' + locality if locality else ''}. Message us on WhatsApp to book.\""
    return f"Google post draft:\n\"{biz}{', ' + locality if locality else ''} — message us on WhatsApp to book your slot this week.\""


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def _send(conv: ConversationState, body: str, cta: str, rationale: str) -> Dict[str, Any]:
    if any(body.strip() == b.strip() for b in conv.bot_bodies):
        conv.mode = "ended"
        return {"action": "end", "rationale": "Would repeat an earlier message verbatim; ending instead."}
    conv.bot_bodies.append(body)
    conv.last_bot_body = body
    return {"action": "send", "body": body, "cta": cta, "rationale": rationale}


def _end(conv: ConversationState, rationale: str) -> Dict[str, Any]:
    conv.mode = "ended"
    return {"action": "end", "rationale": rationale}


def handle_reply(
    conv: ConversationState,
    message: str,
    merchant: Optional[Dict[str, Any]] = None,
    category: Optional[Dict[str, Any]] = None,
    trigger: Optional[Dict[str, Any]] = None,
    customer: Optional[Dict[str, Any]] = None,
    from_role: str = "merchant",
) -> Dict[str, Any]:
    merchant, category, trigger = merchant or {}, category or {}, trigger or {}
    text = _norm(message)
    hindi = _speaks_hindi(text)
    hl = lambda en, hi: hi if hindi else en  # noqa: E731 — per-turn language mirroring
    owner = _owner(merchant, category) if merchant else "there"
    name = "" if owner == "there" else f" {owner}"

    if conv.mode == "ended":
        return {"action": "end", "rationale": "Conversation already ended; not re-opening."}
    if len(conv.bot_bodies) >= MAX_BOT_TURNS:
        return _end(conv, f"Reached {MAX_BOT_TURNS} bot turns; closing to avoid fatigue.")

    # 1. Explicit opt-out: honour immediately.
    if _is_opt_out(text):
        return _end(conv, "Merchant opted out (STOP / not interested); no further messages.")

    # 2. Abuse without opt-out: one calm de-escalation, then exit on repeat.
    if _has(text, ABUSE):
        conv.hostile_hits += 1
        if conv.hostile_hits > 1:
            return _end(conv, "Repeated hostility; graceful exit.")
        return _send(conv, hl(
            f"Sorry for the bother{name}. I only message about {_biz_name(merchant)}'s Google listing and customer "
            f"offers. Reply STOP and I won't message again.",
            f"Pareshani ke liye sorry{name}. Main sirf {_biz_name(merchant)} ke Google listing aur offers ke liye message karti hoon. "
            f"STOP likhiye, phir message nahi aayega.",
        ), "binary_yes_no", "Hostile reply: apologised once, stated scope, offered STOP (no arguing).")

    # 3. WhatsApp Business auto-reply: one owner check (Pattern B), then exit.
    if is_auto_reply(message, conv):
        conv.auto_reply_hits += 1
        if conv.auto_reply_hits > 1:
            return _end(conv, "Auto-reply repeated; exiting instead of burning turns.")
        return _send(conv,
            f"Samajh gayi{name} — lagta hai ye auto-reply hai. Owner/manager dekh rahe hon to bas YES likh dijiye, "
            f"2 minute ka kaam hai. Warna main baad mein reconnect kar lungi.",
            "binary_yes_no", "Auto-reply detected: one owner check (Pattern B), exit if it repeats.")

    # Task already delivered: a thank-you closes the loop instead of re-pitching.
    if conv.action_sends >= 2 and (_has(text, THANKS) or _has(text, AFFIRM)):
        return _end(conv, "Task delivered and acknowledged; closing the loop.")

    # 4. Customer-facing booking flow (merchant_on_behalf conversations).
    if from_role == "customer":
        return _customer_reply(conv, text, hindi, merchant, trigger, customer)

    # 5. Explicit join / action intent: act now, no re-qualification (Pattern D fix).
    polite_request = not _is_question(text) or text.startswith(("can you", "could you", "please", "will you", "kya aap"))
    negated = _has(text, ["no", "not", "don't", "dont", "nahi", "mat", "never"])
    wants_action = _has(text, JOIN_INTENT) or (_has(text, ACTION_REQ) and polite_request and not negated)
    if wants_action:
        conv.mode = "action"
        if _has(text, JOIN_INTENT):
            body = hl(
                f"Great{name} — starting your setup now. I'll need just one thing: the best number for the onboarding "
                f"call (or reply SAME for this one). Our team will confirm the slot here.",
                f"Badhiya{name} — setup abhi shuru kar rahi hoon. Bas ek cheez: onboarding call ke liye number bhej dijiye "
                f"(ya SAME likhiye isi number ke liye). Team yahin slot confirm karegi.",
            )
            return _send(conv, body, "open_ended", "Join intent: switched straight to action (one required detail only).")
        request = re.sub(r"^(can|could|will) you (please )?|^please ", "", message.strip().rstrip("?.!"), flags=re.I)
        body = hl(
            f"On it{name}: \"{request}\". I'll make the change and confirm here; Google usually takes 24-48 hours to "
            f"show edits. Anything else to add while I'm in there?",
            f"Ho jayega{name}: \"{request}\". Change karke yahin confirm karungi; Google pe dikhne mein 24-48 ghante "
            f"lagte hain. Aur kuch add karna hai?",
        )
        return _send(conv, body, "open_ended", "Concrete action request: executed + honest timeline (Pattern A).")

    # 6. Wait / soft refusal.
    if _has(text, WAIT):
        return {"action": "wait", "wait_seconds": 86400 if _has(text, ["kal", "tomorrow"]) else 3 * 3600,
                "rationale": "Merchant asked for time; backing off instead of pushing."}
    if _has(text, SOFT_NO) and not _has(text, AFFIRM) and not _is_question(text):
        return _end(conv, "Merchant declined; exiting politely without another pitch.")

    # 7. Off-topic: stay on mission, don't fake expertise.
    if _has(text, OFF_TOPIC):
        conv.off_topic_hits += 1
        if conv.off_topic_hits > 1:
            return _end(conv, "Repeated off-topic asks; closing politely.")
        return _send(conv, hl(
            f"That one's outside what I can help with{name} — a CA or your bank is the right person. "
            f"What I can do today is the {_topic(conv, trigger)}. Want me to continue with that?",
            f"Ye mere scope se bahar hai{name} — iske liye CA ya bank sahi rahega. Main aaj {_topic(conv, trigger)} "
            f"mein madad kar sakti hoon. Continue karein?",
        ), "binary_yes_no", "Off-topic ask: honest redirect to the mission, single CTA.")

    # 8a. "What were you saying?" after a detour: short recap of the opener, then the same single CTA.
    if _has(text, RECAP) and conv.bot_bodies:
        opener = " ".join(re.split(r"(?<=[.!?])\s+", conv.bot_bodies[0])[:2])
        return _send(conv, hl(f"Short version: {opener} Want the {_topic(conv, trigger)}?",
                              f"Short mein: {opener} {_topic(conv, trigger).capitalize()} bhej doon?"),
                     "binary_yes_no", "Recap requested: restated the trigger fact + same single CTA.")

    # 8. Questions get answers, not another qualifying question.
    if _is_question(text):
        return _answer(conv, text, hl, name, merchant, trigger)

    # 9. Affirmative: deliver, then confirm, then close.
    if _has(text, AFFIRM) or (conv.mode == "action" and _has(text, ["confirm", "go", "publish"])):
        conv.action_sends += 1
        conv.mode = "action"
        if conv.action_sends == 1:
            body = f"{hl('Here you go', 'Ye raha draft')}{name}:\n\n{_draft(conv, merchant, category, trigger)}\n\n" + hl(
                "Reply CONFIRM to use it as-is, or send one change.", "CONFIRM likhiye ya ek change bataiye.")
            return _send(conv, body, "binary_yes_no", "Affirmative → delivered the actual draft (no re-qualification).")
        if conv.action_sends == 2:
            if "post" in _topic(conv, trigger):
                body = hl(f"Done{name} — it's queued. I'll share how it performed (views/calls) in 7 days.",
                          f"Ho gaya{name} — queue kar diya. 7 din mein views/calls ka result share karungi.")
            else:
                body = hl(f"Done{name} — I'll confirm here as soon as it's complete.",
                          f"Ho gaya{name} — complete hote hi yahin confirm karungi.")
            return _send(conv, body, "none", "Confirmed → executed and set a follow-up expectation.")
        return _end(conv, "Task delivered and confirmed; closing the loop.")

    # 10. Unclear: one clarifying binary, never twice in a row.
    if conv.clarify_hits >= 1:
        return {"action": "wait", "wait_seconds": 6 * 3600, "rationale": "Still unclear after one clarification; giving space."}
    conv.clarify_hits += 1
    return _send(conv, hl(
        f"Got it{name}. Should I go ahead with the {_topic(conv, trigger)} (YES), or leave it for now (STOP)?",
        f"Samajh gayi{name}. {_topic(conv, trigger)} ke saath aage badhun (YES), ya abhi rehne dein (STOP)?",
    ), "binary_yes_no", "Unclear reply: one binary clarification.")


def _topic(conv: ConversationState, trigger: Dict[str, Any]) -> str:
    kind = (conv.trigger_kind or trigger.get("kind") or "").lower()
    topics = {
        "research": "patient-ed WhatsApp draft", "regulation": "compliance checklist", "review": "review reply template",
        "competitor": "counter-post draft", "renewal": "plan summary", "winback": "restart summary",
        "gbp": "Google verification", "supply": "recall WhatsApp", "planning": "launch copy",
        "milestone": "review-request WhatsApp", "festival": "festival post", "perf": "recovery post",
        "cde": "calendar hold + registration details", "recall": "booking", "refill": "refill order",
    }
    return next((v for k, v in topics.items() if k in kind), "Google post draft")


def _answer(conv, text, hl, name, merchant, trigger) -> Dict[str, Any]:
    payload = trigger.get("payload") or {}
    if _has(text, PRICE_Q):
        sub = merchant.get("subscription") or {}
        amount = payload.get("renewal_amount")
        if amount:
            fact = hl(f"Your {sub.get('plan', '')} plan renewal is ₹{int(amount):,}.", f"Aapke {sub.get('plan', '')} plan ka renewal ₹{int(amount):,} hai.")
        else:
            fact = hl("I don't have the exact plan price in front of me and won't guess — the magicpin team will share it here.",
                      "Exact price abhi mere paas nahi hai, guess nahi karungi — magicpin team yahin share karegi.")
        body = f"{fact} " + hl(f"The {_topic(conv, trigger)} itself costs nothing extra. Want it?",
                               f"{_topic(conv, trigger).capitalize()} ka koi extra charge nahi hai. Bhej doon?")
        return _send(conv, body, "binary_yes_no", "Price question answered honestly (no invented price).")
    if _has(text, IDENTITY_Q):
        body = hl(
            f"I'm Vera, magicpin's assistant for {_biz_name(merchant)} — I watch your Google listing and local demand, "
            f"and draft posts/offers so you don't have to. Right now: a {_topic(conv, trigger)}. Want to see it?",
            f"Main Vera hoon, magicpin ki assistant — {_biz_name(merchant)} ki Google listing aur local demand dekhti hoon, "
            f"aur posts/offers draft karti hoon. Abhi: {_topic(conv, trigger)}. Dekhna chahenge?",
        )
        return _send(conv, body, "binary_yes_no", "Identity question answered + tied back to the trigger.")
    body = hl(
        f"Good question{name} — I don't have that detail and won't guess; I'll check with the team and reply here. "
        f"Meanwhile, want the {_topic(conv, trigger)}?",
        f"Achha sawaal{name} — ye detail abhi mere paas nahi hai, team se confirm karke yahin bataungi. "
        f"Tab tak {_topic(conv, trigger)} bhej doon?",
    )
    return _send(conv, body, "binary_yes_no", "Unanswerable question: honest, no fabrication, kept momentum.")


def _customer_reply(conv, text, hindi, merchant, trigger, customer) -> Dict[str, Any]:
    payload = trigger.get("payload") or {}
    slots = [s.get("label") for s in (payload.get("available_slots") or payload.get("next_session_options") or []) if s.get("label")]
    name, _ = _customer_name(customer)
    if hindi and name.lower().startswith("mr. "):
        name = f"{name[4:]} ji"
    who = "" if name == "there" else f" {name}"
    biz = _biz_name(merchant)
    hl = lambda en, hi: hi if hindi else en  # noqa: E731
    choice = re.fullmatch(r"(1|2|first|second|pehla|doosra|dusra)\b.*", text)
    if choice or _has(text, AFFIRM):
        idx = 1 if choice and choice.group(1) in ("2", "second", "doosra", "dusra") else 0
        if slots:
            slot = slots[min(idx, len(slots) - 1)]
            body = hl(f"Confirmed{who} ✅ {slot} at {biz}. Reply here if you need to change it.",
                      f"Confirm ho gaya{who} ✅ {slot}, {biz}. Change karna ho to yahin reply karein.")
        elif "refill" in (trigger.get("kind") or ""):
            saved = payload.get("delivery_address_saved")
            body = hl(f"Confirmed{who} ✅ {biz} will pack your refill" + (" and deliver it to your saved address." if saved else " and keep it ready for pickup."),
                      f"Confirm ho gaya{who} ✅ {biz} aapka refill pack karke" + (" saved address pe deliver kar dega." if saved else " pickup ke liye ready rakhega."))
        else:
            body = hl(f"Thanks{who}! {biz} will message you shortly to fix a time that suits you.",
                      f"Shukriya{who}! {biz} jaldi aapko time fix karne ke liye message karega.")
        conv.mode = "done"
        return _send(conv, body, "none", "Customer booked: confirmed the exact slot from context.")
    if _has(text, SOFT_NO) or _has(text, WAIT):
        return _end(conv, "Customer declined/deferred; no pressure on behalf of the merchant.")
    body = hl(f"Thanks{who} — {biz}'s team will answer that personally. Should they call you (YES)?",
              f"Shukriya{who} — {biz} ki team aapko personally bataegi. Call karein (YES)?")
    return _send(conv, body, "binary_yes_no", "Customer question routed to merchant (no medical/price claims by bot).")
