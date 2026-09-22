"""Deterministic message composer grounded in the 4-context framework.

Works without an LLM so you can develop and smoke-test offline.
Optional LLM upgrade: set VERA_LLM_PROVIDER + VERA_LLM_API_KEY.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib import request as urlrequest

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass


def _owner(merchant: Dict[str, Any]) -> str:
    identity = merchant.get("identity") or {}
    return identity.get("owner_first_name") or (identity.get("name") or "there").split()[0]


def _biz_name(merchant: Dict[str, Any]) -> str:
    return (merchant.get("identity") or {}).get("name") or "your business"


def _locality(merchant: Dict[str, Any]) -> str:
    identity = merchant.get("identity") or {}
    loc = identity.get("locality") or ""
    city = identity.get("city") or ""
    if loc and city:
        return f"{loc}, {city}"
    return loc or city or "your area"


def _languages(merchant: Dict[str, Any]) -> List[str]:
    return list((merchant.get("identity") or {}).get("languages") or ["en"])


def _wants_hinglish(merchant: Dict[str, Any], customer: Optional[Dict[str, Any]] = None) -> bool:
    if customer:
        pref = ((customer.get("identity") or {}).get("language_pref") or "").lower()
        if "hi" in pref:
            return True
    langs = _languages(merchant)
    return "hi" in langs


def _active_offers(merchant: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [o for o in (merchant.get("offers") or []) if o.get("status") == "active"]


def _first_active_offer_title(merchant: Dict[str, Any], category: Dict[str, Any]) -> Optional[str]:
    offers = _active_offers(merchant)
    if offers:
        return offers[0].get("title")
    catalog = category.get("offer_catalog") or []
    if catalog:
        return catalog[0].get("title")
    return None


def _digest_item(category: Dict[str, Any], item_id: Optional[str]) -> Optional[Dict[str, Any]]:
    digest = category.get("digest") or []
    if not digest:
        return None
    if item_id:
        for item in digest:
            if item.get("id") == item_id:
                return item
    # Prefer newest digest entry (adaptation to mid-test injections often append)
    return digest[-1]


def _newest_digest(category: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    digest = category.get("digest") or []
    return digest[-1] if digest else None


def _flatten_facts(obj: Any, prefix: str = "", out: Optional[List[str]] = None, depth: int = 0) -> List[str]:
    """Pull short verifiable facts from arbitrary injected payloads."""
    if out is None:
        out = []
    if depth > 3 or len(out) >= 6:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in {"category", "summary", "body", "description"} and isinstance(v, str) and len(v) > 120:
                out.append(f"{k}: {v[:110]}…")
            else:
                _flatten_facts(v, f"{prefix}{k}.", out, depth + 1)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:3]):
            _flatten_facts(v, f"{prefix}{i}.", out, depth + 1)
    elif isinstance(obj, (str, int, float)) and prefix:
        key = prefix.rstrip(".")
        if key.split(".")[-1] in {"id", "place_id", "phone", "phone_redacted"}:
            return out
        out.append(f"{key.split('.')[-1]}={obj}")
    return out


def _compose_adaptive(category, merchant, trigger, customer):
    """High-compulsion fallback for unknown / newly injected trigger kinds."""
    owner = _owner(merchant)
    kind = (trigger.get("kind") or "update").replace("_", " ")
    payload = trigger.get("payload") or {}
    offer = _first_active_offer_title(merchant, category)
    locality = _locality(merchant)
    perf = merchant.get("performance") or {}
    peer = _peer_ctr(category)
    digest = _newest_digest(category)
    trends = category.get("trend_signals") or []
    signals = merchant.get("signals") or []

    # Prefer demand-style if trends exist
    if trends and offer:
        t = trends[0]
        query = t.get("query") or "your top service"
        delta = t.get("delta_yoy")
        delta_bit = f" (+{_pct(delta)} YoY)" if isinstance(delta, (int, float)) else ""
        body = (
            f"{owner}, local demand check for {kind}: searches for \"{query}\" in "
            f"{locality}{delta_bit}. Should I push your live {offer} to those leads?"
        )
        return body, "binary_yes_no", "adaptive demand+offer CTA for unknown kind"

    facts = _flatten_facts(payload)[:3]
    fact_bit = ""
    if facts:
        fact_bit = " " + "; ".join(facts[:3]) + "."
    elif digest:
        fact_bit = f" Fresh context: {(digest.get('title') or digest.get('source') or 'new digest item')}."
    elif isinstance(perf.get("ctr"), (int, float)) and peer is not None:
        fact_bit = f" Your CTR is {_pct(perf['ctr'])} vs peer {_pct(peer)}."
    elif signals:
        fact_bit = f" Signal: {signals[0]}."

    offer_bit = f" Lead with {offer}." if offer else " I can draft a service+price line from your catalog."
    body = (
        f"{owner}, quick why-now on {kind}.{fact_bit}{offer_bit} "
        f"Want me to draft the WhatsApp (one CTA) now?"
    )
    return body, "binary_yes_no", f"adaptive grounded compose for kind={kind}"


def _peer_ctr(category: Dict[str, Any]) -> Optional[float]:
    stats = category.get("peer_stats") or {}
    return stats.get("avg_ctr")


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%" if abs(value) < 2 else f"{value:.0f}%"


def _cta_for_kind(kind: str) -> str:
    binary = {
        "regulation_change",
        "perf_dip",
        "festival_upcoming",
        "competitor_opened",
        "renewal_due",
        "supply_alert",
        "gbp_unverified",
        "recall_due",
        "chronic_refill_due",
        "appointment_tomorrow",
        "wedding_package_followup",
        "customer_lapsed_hard",
        "trial_followup",
        "winback_eligible",
        "curious_ask_due",
        "category_trend_movement",
    }
    if kind in binary:
        return "binary_yes_no"
    if kind in {"research_digest", "dormant_with_vera", "cde_opportunity", "active_planning_intent"}:
        return "open_ended"
    return "binary_yes_no"


def _resolve_send_as(trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> str:
    if customer or trigger.get("scope") == "customer" or trigger.get("customer_id"):
        return "merchant_on_behalf"
    return "vera"


# ---------------------------------------------------------------------------
# Per-kind composers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Per-kind composers
# ---------------------------------------------------------------------------

def _compose_research_digest(category, merchant, trigger, customer):
    payload = trigger.get("payload") or {}
    item = _digest_item(category, payload.get("top_item_id"))
    owner = _owner(merchant)
    agg = merchant.get("customer_aggregate") or {}
    high_risk = agg.get("high_risk_adult_count")

    if not item:
        body = (
            f"{owner}, a new research digest dropped for {category.get('display_name', 'your category')}. "
            f"Want me to pull the top item + draft a short patient WhatsApp you can share?"
        )
        return body, "open_ended", "research digest available; offer to pull + draft"

    title = item.get("title") or "new research item"
    source = item.get("source") or "source on file"
    trial_n = item.get("trial_n")
    summary = item.get("summary") or ""
    # Pull a concrete number from summary if present
    pct_match = re.search(r"(\d+%)", summary)
    number_bit = ""
    if trial_n and pct_match:
        number_bit = f" — {trial_n:,}-patient trial, {pct_match.group(1)} better outcome"
    elif trial_n:
        number_bit = f" — {trial_n:,}-patient trial"
    elif pct_match:
        number_bit = f" — {pct_match.group(1)} effect size"

    cohort = ""
    if high_risk:
        cohort = f" Relevant to your {high_risk} high-risk adult patients."

    body = (
        f"{owner}, {source} landed. One item for you: {title}{number_bit}.{cohort} "
        f"Worth a 2-min look — want me to pull the abstract + draft a patient-ed WhatsApp?"
    )
    return body, "open_ended", "research digest + merchant cohort anchor + reciprocity CTA"


def _compose_regulation_change(category, merchant, trigger, customer):
    payload = trigger.get("payload") or {}
    item = _digest_item(category, payload.get("top_item_id"))
    owner = _owner(merchant)
    deadline = payload.get("deadline_iso") or (item or {}).get("date") or "the deadline"
    title = (item or {}).get("title") or "a regulation update"
    source = (item or {}).get("source") or "regulator circular"
    actionable = (item or {}).get("actionable") or "I can draft a 1-page checklist"

    body = (
        f"{owner}, heads-up: {title}. Source: {source}. Deadline {deadline}. "
        f"{actionable}. Want me to draft the compliance checklist for your clinic?"
    )
    return body, "binary_yes_no", "urgency + authority + concrete checklist CTA"


def _compose_recall_due(category, merchant, trigger, customer):
    payload = trigger.get("payload") or {}
    offer = _first_active_offer_title(merchant, category) or "your usual visit"
    slots = payload.get("available_slots") or []
    slot_labels = [s.get("label") for s in slots if s.get("label")]
    cust_name = ((customer or {}).get("identity") or {}).get("name") or "there"
    biz = _biz_name(merchant)
    hinglish = _wants_hinglish(merchant, customer)
    service = payload.get("service_due") or "recall"
    last = payload.get("last_service_date") or "your last visit"

    if slot_labels:
        if hinglish and len(slot_labels) >= 2:
            body = (
                f"Hi {cust_name}, {biz} here. Aapka {service.replace('_', ' ')} due hai "
                f"(last visit {last}). 2 slots ready: {slot_labels[0]} ya {slot_labels[1]}. "
                f"{offer}. Reply 1 for first slot, 2 for second, or suggest a time."
            )
        else:
            body = (
                f"Hi {cust_name}, {biz} here. Your {service.replace('_', ' ')} is due "
                f"(last visit {last}). Open slots: {', '.join(slot_labels[:2])}. "
                f"{offer}. Reply YES for the first slot or tell us a better time."
            )
    else:
        body = (
            f"Hi {cust_name}, {biz} here. Your recall window is open "
            f"(last visit {last}). {offer} — reply YES and we'll book a slot that suits you."
        )
    return body, "binary_yes_no", "customer recall with real offer + slots + language match"


def _compose_perf_dip(category, merchant, trigger, customer):
    owner = _owner(merchant)
    payload = trigger.get("payload") or {}
    metric = payload.get("metric") or "calls"
    delta = payload.get("delta_pct")
    delta_s = _pct(delta) if isinstance(delta, (int, float)) else "down"
    offer = _first_active_offer_title(merchant, category)
    peer = _peer_ctr(category)
    perf = merchant.get("performance") or {}
    ctr = perf.get("ctr")
    seasonal = (category.get("seasonal_beats") or [{}])[0].get("note")

    peer_bit = ""
    if ctr is not None and peer is not None:
        peer_bit = f" Your CTR is {_pct(ctr)} vs peer {_pct(peer)}."

    season_bit = f" Seasonal context: {seasonal}." if seasonal else ""
    offer_bit = f" You already have {offer} live — want me to draft a 160-char recovery message around it?" if offer else (
        " Want me to draft a recovery offer from your category catalog?"
    )

    body = (
        f"{owner}, quick signal: {metric} {delta_s} over the last "
        f"{payload.get('window', '7d')}.{peer_bit}{season_bit}{offer_bit}"
    )
    return body, "binary_yes_no", "perf dip + peer anchor + existing offer CTA"


def _compose_curious_ask(category, merchant, trigger, customer):
    owner = _owner(merchant)
    biz = _biz_name(merchant)
    locality = _locality(merchant)
    offer = _first_active_offer_title(merchant, category)
    trends = category.get("trend_signals") or []

    # High-compulsion pattern from the challenge page:
    # specific local demand signal + real offer + single CTA (no generic "discount campaign")
    if trends:
        t = trends[0]
        query = t.get("query") or "your top service"
        delta = t.get("delta_yoy")
        delta_bit = f" (+{_pct(delta)} YoY)" if isinstance(delta, (int, float)) else ""
        place = locality or "your locality"
        if offer:
            body = (
                f"{owner}, searches for \"{query}\" are rising in {place}{delta_bit}. "
                f"Should I send nearby leads your live offer — {offer}?"
            )
        else:
            body = (
                f"{owner}, searches for \"{query}\" are rising in {place}{delta_bit}. "
                f"Want me to draft a one-line WhatsApp + Google post to catch that demand?"
            )
        return body, "binary_yes_no", "local demand signal + real offer + single CTA"

    body = (
        f"Hi {owner}! Quick check — what service has been most asked-for this week at {biz}? "
        f"I'll turn the answer into a Google post + a 4-line WhatsApp reply you can reuse. Takes 5 min."
    )
    return body, "open_ended", "curious-ask lever + reciprocity + effort externalization"


def _compose_festival(category, merchant, trigger, customer):
    owner = _owner(merchant)
    payload = trigger.get("payload") or {}
    festival = payload.get("festival") or "the festival"
    days = payload.get("days_until")
    date = payload.get("date")
    offer = _first_active_offer_title(merchant, category)
    timing = f" in {days} days ({date})" if days is not None else (f" on {date}" if date else "")
    offer_bit = f" Lead with {offer}" if offer else " I can draft a service+price offer from your catalog"
    body = (
        f"{owner}, {festival}{timing}. Peak booking window is opening now.{offer_bit} "
        f"— want me to draft the WhatsApp blast + Google post today?"
    )
    return body, "binary_yes_no", "festival urgency + existing offer + binary CTA"


def _compose_competitor(category, merchant, trigger, customer):
    owner = _owner(merchant)
    payload = trigger.get("payload") or {}
    distance = payload.get("distance_km")
    locality = _locality(merchant)
    dist_bit = f" ~{distance} km from you" if distance else f" near {locality}"
    offer = _first_active_offer_title(merchant, category)
    offer_bit = f" Counter-move: push {offer} this week" if offer else " Counter-move: refresh your top 3 Google photos + pin a clear offer"
    body = (
        f"{owner}, a new competitor just appeared on Google Maps{dist_bit}. "
        f"I'm not naming them — just flagging proximity.{offer_bit}. Want me to draft it?"
    )
    return body, "binary_yes_no", "loss aversion without inventing competitor name"


def _compose_ipl(category, merchant, trigger, customer):
    owner = _owner(merchant)
    payload = trigger.get("payload") or {}
    match = payload.get("match") or "tonight's match"
    venue = payload.get("venue") or ""
    time_iso = payload.get("match_time_iso") or ""
    is_weeknight = payload.get("is_weeknight")
    offer = _first_active_offer_title(merchant, category) or "your active deal"

    advice = (
        "Weeknight IPL usually lifts delivery — lean into it."
        if is_weeknight
        else "Saturday IPL usually shifts covers home (-12% dine-in pattern). Skip match-night walk-in promo; push delivery instead."
    )
    venue_bit = f" at {venue}" if venue else ""
    body = (
        f"Quick heads-up {owner} — {match}{venue_bit} ({time_iso}). {advice} "
        f"Want me to draft a delivery push around {offer}? Live in 10 min."
    )
    return body, "binary_yes_no", "IPL timing advice + existing offer + fast deliverable"


def _compose_perf_spike(category, merchant, trigger, customer):
    owner = _owner(merchant)
    payload = trigger.get("payload") or {}
    metric = payload.get("metric") or "views"
    delta = payload.get("delta_pct")
    delta_s = _pct(delta) if isinstance(delta, (int, float)) else "up"
    offer = _first_active_offer_title(merchant, category)
    offer_bit = f" Lock it in with a Google post around {offer}?" if offer else " Want me to draft a Google post to lock the momentum?"
    body = (
        f"{owner}, nice — {metric} {delta_s} vs your recent baseline. Momentum is rare; "
        f"don't let it cool.{offer_bit}"
    )
    return body, "binary_yes_no", "momentum framing + lock-it-in CTA"


def _compose_milestone(category, merchant, trigger, customer):
    owner = _owner(merchant)
    payload = trigger.get("payload") or {}
    milestone = payload.get("milestone") or payload.get("label") or "a milestone"
    value = payload.get("value")
    value_bit = f" ({value})" if value is not None else ""
    body = (
        f"{owner}, you just hit {milestone}{value_bit}. Worth a short thank-you Google post "
        f"+ a reply template for new reviews. Want me to draft both?"
    )
    return body, "binary_yes_no", "reciprocity + momentum on real milestone"


def _compose_dormant(category, merchant, trigger, customer):
    owner = _owner(merchant)
    payload = trigger.get("payload") or {}
    days = payload.get("days_silent") or payload.get("days_since_last_message") or 14
    body = (
        f"Hey {owner} — it's been ~{days} days. One useful thing I can do in 5 min: "
        f"audit your Google photos vs peers and flag the weakest 2. Want that?"
    )
    return body, "open_ended", "light dormant re-engagement with low ask"


def _compose_bridal(category, merchant, trigger, customer):
    payload = trigger.get("payload") or {}
    cust_name = ((customer or {}).get("identity") or {}).get("name") or "there"
    owner = _owner(merchant)
    biz = _biz_name(merchant)
    days = payload.get("days_to_wedding")
    wedding = payload.get("wedding_date")
    window = payload.get("next_step_window_open") or "skin_prep"
    offer = _first_active_offer_title(merchant, category) or "bridal prep package"
    days_bit = f"{days} days to your wedding" if days is not None else f"wedding on {wedding}"
    body = (
        f"Hi {cust_name} — {owner} from {biz} here. {days_bit} — perfect window for {window.replace('_', ' ')}. "
        f"{offer}. Want me to block your preferred Saturday slot for the first session?"
    )
    return body, "binary_yes_no", "bridal followup with date specificity + binary CTA"


def _compose_chronic_refill(category, merchant, trigger, customer):
    payload = trigger.get("payload") or {}
    cust_name = ((customer or {}).get("identity") or {}).get("name") or "there"
    biz = _biz_name(merchant)
    meds = payload.get("medicines") or payload.get("skus") or []
    total = payload.get("total_amount") or payload.get("amount")
    savings = payload.get("savings")
    meds_bit = ", ".join(meds[:3]) if isinstance(meds, list) and meds else "your regular medicines"
    money_bit = ""
    if total is not None:
        money_bit = f" Total ₹{total}"
        if savings:
            money_bit += f" (save ₹{savings})"
        money_bit += "."
    body = (
        f"Hi {cust_name}, {biz} here. Your refill for {meds_bit} is due.{money_bit} "
        f"Reply YES to confirm and we'll keep it ready."
    )
    return body, "binary_yes_no", "precision refill + price + CONFIRM CTA"


def _compose_appointment_tomorrow(category, merchant, trigger, customer):
    payload = trigger.get("payload") or {}
    cust_name = ((customer or {}).get("identity") or {}).get("name") or "there"
    biz = _biz_name(merchant)
    when = payload.get("appointment_label") or payload.get("slot_label") or "tomorrow"
    service = payload.get("service") or "your appointment"
    body = (
        f"Hi {cust_name}, reminder from {biz}: {service} is booked for {when}. "
        f"Reply 1 to confirm, 2 to reschedule."
    )
    return body, "binary_yes_no", "appointment reminder with confirm/reschedule"


def _compose_review_theme(category, merchant, trigger, customer):
    owner = _owner(merchant)
    payload = trigger.get("payload") or {}
    theme = (payload.get("theme") or "a review theme").replace("_", " ")
    n = payload.get("occurrences_30d")
    quote = payload.get("common_quote")
    n_bit = f"{n} reviews in 30d" if n is not None else "recent reviews"
    quote_bit = f' Common note: "{quote}".' if quote else ""
    body = (
        f"{owner}, review theme emerging: {theme} ({n_bit}).{quote_bit} "
        f"Want me to draft a reply template + one ops fix note you can brief the team with?"
    )
    return body, "binary_yes_no", "review theme grounded in counts + quote"


def _compose_renewal(category, merchant, trigger, customer):
    owner = _owner(merchant)
    payload = trigger.get("payload") or {}
    days = payload.get("days_remaining")
    plan = payload.get("plan") or (merchant.get("subscription") or {}).get("plan") or "Pro"
    amount = payload.get("renewal_amount")
    amount_bit = f" (₹{amount})" if amount else ""
    body = (
        f"{owner}, your {plan} plan renews in {days} days{amount_bit}. "
        f"Want me to prep a 1-page ROI summary of what Vera drove this quarter before you decide?"
    )
    return body, "binary_yes_no", "renewal urgency + ROI reciprocity"


def _compose_supply_alert(category, merchant, trigger, customer):
    owner = _owner(merchant)
    payload = trigger.get("payload") or {}
    item = payload.get("item") or payload.get("sku") or "a tracked item"
    affected = payload.get("affected_customers") or payload.get("customers_at_risk")
    affected_bit = f" ~{affected} of your customers may be affected." if affected else ""
    body = (
        f"{owner}, supply alert on {item}.{affected_bit} "
        f"Want me to draft a short proactive WhatsApp for the affected set?"
    )
    return body, "binary_yes_no", "supply urgency + bounded affected count"


def _compose_active_planning(category, merchant, trigger, customer):
    owner = _owner(merchant)
    payload = trigger.get("payload") or {}
    topic = payload.get("topic") or payload.get("program") or payload.get("ask_template") or "the plan you mentioned"
    body = (
        f"{owner}, picking up the thread on {str(topic).replace('_', ' ')}. "
        f"I can draft the offer copy + a simple ops checklist. Want the draft now?"
    )
    return body, "open_ended", "continue active planning with concrete deliverable"


def _compose_winback_customer(category, merchant, trigger, customer):
    payload = trigger.get("payload") or {}
    cust_name = ((customer or {}).get("identity") or {}).get("name") or "there"
    biz = _biz_name(merchant)
    offer = _first_active_offer_title(merchant, category) or "a welcome-back session"
    days = payload.get("days_since_last_visit") or payload.get("days_lapsed")
    days_bit = f" It's been {days} days." if days else ""
    body = (
        f"Hi {cust_name}, {biz} here — we miss you.{days_bit} "
        f"{offer} is open this week. Reply YES and we'll hold a slot."
    )
    return body, "binary_yes_no", "customer winback with offer + binary CTA"


def _compose_generic(category, merchant, trigger, customer):
    return _compose_adaptive(category, merchant, trigger, customer)


COMPOSERS = {
    "research_digest": _compose_research_digest,
    "regulation_change": _compose_regulation_change,
    "recall_due": _compose_recall_due,
    "perf_dip": _compose_perf_dip,
    "seasonal_perf_dip": _compose_perf_dip,
    "curious_ask_due": _compose_curious_ask,
    "festival_upcoming": _compose_festival,
    "category_seasonal": _compose_festival,
    "competitor_opened": _compose_competitor,
    "ipl_match_today": _compose_ipl,
    "perf_spike": _compose_perf_spike,
    "milestone_reached": _compose_milestone,
    "dormant_with_vera": _compose_dormant,
    "wedding_package_followup": _compose_bridal,
    "chronic_refill_due": _compose_chronic_refill,
    "appointment_tomorrow": _compose_appointment_tomorrow,
    "review_theme_emerged": _compose_review_theme,
    "renewal_due": _compose_renewal,
    "supply_alert": _compose_supply_alert,
    "active_planning_intent": _compose_active_planning,
    "customer_lapsed_hard": _compose_winback_customer,
    "trial_followup": _compose_winback_customer,
    "winback_eligible": _compose_renewal,
    "cde_opportunity": _compose_research_digest,
    "gbp_unverified": _compose_adaptive,
    "category_trend_movement": _compose_curious_ask,
    "local_news_event": _compose_adaptive,
    "weather_heatwave": _compose_festival,
}


def _resolve_composer(kind: str):
    if kind in COMPOSERS:
        return COMPOSERS[kind]
    k = (kind or "").lower()
    if any(x in k for x in ("recall", "appointment", "refill", "lapsed", "trial", "bridal", "wedding", "chronic")):
        if "bridal" in k or "wedding" in k:
            return _compose_bridal
        if "refill" in k or "chronic" in k:
            return _compose_chronic_refill
        if "appointment" in k:
            return _compose_appointment_tomorrow
        if "lapsed" in k or "trial" in k or "winback" in k:
            return _compose_winback_customer
        return _compose_recall_due
    keyword_map = [
        (("research", "digest", "cde", "webinar", "paper", "journal"), _compose_research_digest),
        (("regul", "compliance", "deadline", "circular"), _compose_regulation_change),
        (("curious", "ask", "demand", "search", "trend", "query"), _compose_curious_ask),
        (("festival", "seasonal", "diwali", "eid", "holi", "weather"), _compose_festival),
        (("competitor", "rival", "nearby"), _compose_competitor),
        (("ipl", "match", "cricket"), _compose_ipl),
        (("spike", "surge", "uplift"), _compose_perf_spike),
        (("dip", "drop", "decline", "down"), _compose_perf_dip),
        (("milestone", "crossed"), _compose_milestone),
        (("dormant", "silent", "inactive"), _compose_dormant),
        (("review",), _compose_review_theme),
        (("renewal", "subscription", "expiry"), _compose_renewal),
        (("supply", "stock", "sku"), _compose_supply_alert),
        (("planning", "program", "draft"), _compose_active_planning),
    ]
    for keys, fn in keyword_map:
        if any(x in k for x in keys):
            return fn
    return _compose_adaptive


def _maybe_llm_compose(
    category: Dict[str, Any],
    merchant: Dict[str, Any],
    trigger: Dict[str, Any],
    customer: Optional[Dict[str, Any]],
    fallback: Dict[str, Any],
) -> Dict[str, Any]:
    provider = (os.getenv("VERA_LLM_PROVIDER") or "").strip().lower()
    api_key = (os.getenv("VERA_LLM_API_KEY") or "").strip()
    if not provider or not api_key:
        return fallback

    system = (
        "You are Vera, magicpin's merchant growth assistant on WhatsApp for Indian merchants.\n"
        "Compose ONE high-compulsion message from the JSON contexts.\n\n"
        "GOLD STANDARD (shape to match — use REAL facts from context only):\n"
        '  "190 people in your locality are searching for Dental Check Up. '
        'Should I send them a discounted check up at ₹299?"\n'
        "That pattern = specific local signal + real offer + single CTA.\n\n"
        "HARD RULES:\n"
        "- Do NOT invent numbers, offers, competitor names, or sources not in context.\n"
        "- Prefer service+price offers over vague 'discount/campaign'.\n"
        "- One primary CTA only (binary preferred for action triggers).\n"
        "- Peer/colleague tone; Hinglish OK if merchant languages include hi.\n"
        "- Clearly communicate why-now from the trigger.\n"
        "- If new digest items exist, prefer the newest relevant one.\n"
        "- Keep it concise (2–4 short sentences).\n\n"
        "Return ONLY JSON: {\"body\": str, \"cta\": \"binary_yes_no\"|\"open_ended\"|\"none\", \"rationale\": str}"
    )
    # Compact context for speed/timeouts — still includes newest digests
    digest = (category.get("digest") or [])[-3:]
    slim = {
        "category": {
            "slug": category.get("slug"),
            "voice": category.get("voice"),
            "peer_stats": category.get("peer_stats"),
            "trend_signals": category.get("trend_signals"),
            "digest_newest": digest,
            "offer_catalog": (category.get("offer_catalog") or [])[:4],
        },
        "merchant": {
            "merchant_id": merchant.get("merchant_id"),
            "identity": merchant.get("identity"),
            "performance": merchant.get("performance"),
            "offers": merchant.get("offers"),
            "signals": merchant.get("signals"),
            "customer_aggregate": merchant.get("customer_aggregate"),
        },
        "trigger": trigger,
        "customer": customer,
        "fallback_example": {
            "body": fallback.get("body"),
            "cta": fallback.get("cta"),
            "rationale": fallback.get("rationale"),
        },
    }
    user = json.dumps(slim, ensure_ascii=False)[:80000]

    try:
        if provider == "openai":
            model = os.getenv("VERA_LLM_MODEL") or "gpt-4o-mini"
            payload = {
                "model": model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": {"type": "json_object"},
            }
            req = urlrequest.Request(
                "https://api.openai.com/v1/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                method="POST",
            )
            with urlrequest.urlopen(req, timeout=18) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = data["choices"][0]["message"]["content"]
        elif provider == "groq":
            model = os.getenv("VERA_LLM_MODEL") or "llama-3.3-70b-versatile"
            payload = {
                "model": model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
            req = urlrequest.Request(
                "https://api.groq.com/openai/v1/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                method="POST",
            )
            with urlrequest.urlopen(req, timeout=18) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = data["choices"][0]["message"]["content"]
        else:
            return fallback

        # Groq sometimes wraps ```json
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

        parsed = json.loads(text)
        body = (parsed.get("body") or "").strip()
        if not body or "http://" in body.lower() or re.search(r"\bwww\.", body.lower()):
            return fallback
        cta = parsed.get("cta") or fallback["cta"]
        if cta not in {"binary_yes_no", "open_ended", "none"}:
            cta = fallback["cta"]
        return {
            **fallback,
            "body": body,
            "cta": cta,
            "rationale": parsed.get("rationale") or fallback["rationale"],
        }
    except Exception:
        return fallback


def compose(
    category: Dict[str, Any],
    merchant: Dict[str, Any],
    trigger: Dict[str, Any],
    customer: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    kind = trigger.get("kind") or "unknown"
    fn = _resolve_composer(kind)
    body, cta, rationale = fn(category, merchant, trigger, customer)
    send_as = _resolve_send_as(trigger, customer)
    suppression_key = trigger.get("suppression_key") or f"{kind}:{merchant.get('merchant_id')}"

    result = {
        "body": body.strip(),
        "cta": cta,
        "send_as": send_as,
        "suppression_key": suppression_key,
        "rationale": rationale,
        "template_name": f"vera_{kind}_v1",
        "template_params": [
            _owner(merchant),
            kind,
            (trigger.get("payload") or {}).get("top_item_id")
            or (trigger.get("payload") or {}).get("festival")
            or kind,
        ],
    }
    return _maybe_llm_compose(category, merchant, trigger, customer, result)


def trigger_expired(trigger: Dict[str, Any], now_iso: str) -> bool:
    expires = trigger.get("expires_at")
    if not expires:
        return False
    try:
        now = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
        exp = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        return now > exp
    except Exception:
        return False
