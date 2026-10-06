"""Deterministic message composer grounded in the 4-context framework.

Every per-kind composer reads the dataset's real payload keys and falls back to
merchant context (performance, review_themes, signals) when a trigger payload
is a placeholder. Offers are only called "live" when they are in merchant.offers;
catalog items are always labelled as suggestions.

Optional LLM polish: set VERA_LLM_PROVIDER + VERA_LLM_API_KEY. It never runs
inside compose(); bot.py calls polish() concurrently under a tick-wide budget.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib import request as urlrequest

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass


Composed = Tuple[str, str, str]  # (body, cta, rationale)

MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _humanize(value: Any) -> str:
    return str(value or "").replace("_", " ").strip()


def _pct(value: float, digits: int = 0) -> str:
    return f"{value * 100:.{digits}f}%"


def _signed_pct(value: float) -> str:
    return f"{value * 100:+.0f}%"


def _delta_words(value: float) -> str:
    return f"{'up' if value >= 0 else 'down'} {abs(value) * 100:.0f}%"


def _possessive(name: str) -> str:
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def _ctr(value: float) -> str:
    return f"{value * 100:.1f}%"


def _inr(value: Any) -> str:
    try:
        return f"₹{int(value):,}"
    except (TypeError, ValueError):
        return f"₹{value}"


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _fmt_date(value: Any) -> str:
    d = _parse_dt(value)
    return f"{d.day} {d:%b}" if d else str(value or "")


def _fmt_time(d: datetime) -> str:
    hour = d.hour % 12 or 12
    minute = f":{d.minute:02d}" if d.minute else ""
    return f"{hour}{minute}{'am' if d.hour < 12 else 'pm'}"


def _fmt_datetime(value: Any) -> str:
    d = _parse_dt(value)
    return f"{d:%a} {d.day} {d:%b}, {_fmt_time(d)}" if d else str(value or "")


def _first_sentence(text: str) -> str:
    text = (text or "").strip()
    m = re.match(r"(.+?(?<!Dr)(?<!Mr)(?<!Ms)(?<!\s[A-Z])[.!?])(\s|$)", text)
    return (m.group(1) if m else text).strip()


def _now(now: Optional[datetime]) -> datetime:
    return now or datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------

def _owner(merchant: Dict[str, Any], category: Optional[Dict[str, Any]] = None) -> str:
    """'Dr. X' only for dentists (or clinics named Dr.); pharmacists are not doctors."""
    identity = merchant.get("identity") or {}
    first = identity.get("owner_first_name") or ""
    name = identity.get("name") or ""
    if not first:
        return f"{name} team" if name else "there"
    slug = ((category or {}).get("slug") or merchant.get("category_slug") or "").lower()
    if (slug == "dentists" or name.lower().startswith("dr")) and not first.lower().startswith("dr"):
        return f"Dr. {first}"
    return first


def _biz_name(merchant: Dict[str, Any]) -> str:
    return (merchant.get("identity") or {}).get("name") or "your business"


def _locality(merchant: Dict[str, Any]) -> str:
    identity = merchant.get("identity") or {}
    parts = [p for p in (identity.get("locality"), identity.get("city")) if p]
    return ", ".join(parts) or "your area"


def _city(merchant: Dict[str, Any]) -> str:
    return (merchant.get("identity") or {}).get("city") or "your city"


def _wants_hinglish(
    merchant: Dict[str, Any],
    category: Optional[Dict[str, Any]] = None,
    customer: Optional[Dict[str, Any]] = None,
) -> bool:
    """Customer: explicit language_pref wins. Merchant: Hindi listed + category voice is
    Hindi-English code-mix + non-clinical tone (dentists/gyms stay English-primary)."""
    if customer:
        pref = ((customer.get("identity") or {}).get("language_pref") or "").lower()
        return pref == "hi" or pref.startswith("hi-") or "hindi" in pref
    identity = merchant.get("identity") or {}
    pref = (identity.get("language_pref") or "").lower()
    if pref:
        return "hi" in pref
    langs = [str(x).lower() for x in identity.get("languages") or []]
    voice = (category or {}).get("voice") or {}
    code_mix = str(voice.get("code_mix") or "").lower()
    clinical = "clinical" in str(voice.get("tone") or "").lower()
    return "hi" in langs and code_mix.startswith("hindi") and not clinical


def _t(hinglish: bool, en: str, hi: str) -> str:
    return hi if hinglish else en


def _live_offers(merchant: Dict[str, Any]) -> List[str]:
    return [o["title"] for o in merchant.get("offers") or [] if o.get("status") == "active" and o.get("title")]


def _pick(titles: List[str], keywords: Tuple[str, ...]) -> Optional[str]:
    for kw in keywords:
        for t in titles:
            if kw in t.lower():
                return t
    return None


def _catalog_offer(category: Dict[str, Any], keywords: Tuple[str, ...] = ()) -> Optional[str]:
    """Service+price catalog item; never a generic '% off' unless nothing else exists."""
    titles = [o.get("title") for o in category.get("offer_catalog") or [] if o.get("title")]
    hit = _pick(titles, keywords) if keywords else None
    if hit:
        return hit
    priced = [t for t in titles if "₹" in t and "%" not in t]
    return (priced or titles or [None])[0]


def _offer_hook(merchant: Dict[str, Any], category: Dict[str, Any], keywords: Tuple[str, ...] = ()) -> Tuple[Optional[str], bool]:
    """Returns (offer_title, is_live). Live merchant offers first, else a catalog suggestion."""
    live = _live_offers(merchant)
    if live:
        return (_pick(live, keywords) if keywords else None) or live[0], True
    return _catalog_offer(category, keywords), False


def _offer_phrase(offer: Optional[str], is_live: bool) -> str:
    if not offer:
        return ""
    if is_live:
        return f"your live {offer}"
    return f"a {offer} offer (common in your category — you have nothing live right now)"


def _peer_ctr(category: Dict[str, Any]) -> Optional[float]:
    return (category.get("peer_stats") or {}).get("avg_ctr")


def _digest_by_id(category: Dict[str, Any], item_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not item_id:
        return None
    for item in category.get("digest") or []:
        if item.get("id") == item_id:
            return item
    return None


def _digest_by_kind(category: Dict[str, Any], kinds: Tuple[str, ...]) -> Optional[Dict[str, Any]]:
    """Newest digest item of the given kinds (newest = last; mid-test injections append)."""
    for item in reversed(category.get("digest") or []):
        if item.get("kind") in kinds:
            return item
    return None


def _month_in_range(month_range: str, month: int) -> bool:
    parts = [p.strip()[:3].lower() for p in str(month_range or "").split("-") if p.strip()]
    idx = [MONTHS.index(p) + 1 for p in parts if p in MONTHS]
    if not idx:
        return False
    start, end = idx[0], idx[-1]
    return start <= month <= end if start <= end else (month >= start or month <= end)


def _season_beat(category: Dict[str, Any], month: int) -> Optional[str]:
    for beat in category.get("seasonal_beats") or []:
        if _month_in_range(beat.get("month_range"), month):
            return beat.get("note")
    return None


def _review_theme(merchant: Dict[str, Any], sentiment: str) -> Optional[Dict[str, Any]]:
    themes = [r for r in merchant.get("review_themes") or [] if r.get("sentiment") == sentiment]
    return max(themes, key=lambda r: r.get("occurrences_30d") or 0) if themes else None


def _signal_days(merchant: Dict[str, Any], prefix: str) -> Optional[int]:
    for s in merchant.get("signals") or []:
        m = re.match(rf"{prefix}\D*(\d+)d", str(s))
        if m:
            return int(m.group(1))
    return None


def _customer_name(customer: Optional[Dict[str, Any]]) -> Tuple[str, Optional[str]]:
    """('Sumitra', 'Karthik') for 'Karthik (parent: Sumitra)'; ('there', None) for walk-ins."""
    raw = ((customer or {}).get("identity") or {}).get("name") or ""
    m = re.match(r"\s*(.+?)\s*\(parent:\s*(.+?)\)", raw)
    if m:
        return m.group(2), m.group(1)
    if not raw or raw.startswith("("):
        return "there", None
    return raw, None


def _greet_customer(name: str, hinglish: bool) -> str:
    if name == "there":
        return _t(hinglish, "Hi", "Namaste")
    if hinglish and name.lower().startswith("mr. "):
        return f"Namaste {name[4:]} ji"
    return f"Hi {name}"


def _metric_delta(merchant: Dict[str, Any], negative: bool) -> Tuple[Optional[str], Optional[float]]:
    deltas = (merchant.get("performance") or {}).get("delta_7d") or {}
    pairs = [(k.replace("_pct", ""), v) for k, v in deltas.items() if isinstance(v, (int, float)) and k != "ctr_pct"]
    if not pairs:
        return None, None
    metric, value = (min if negative else max)(pairs, key=lambda kv: kv[1])
    if (negative and value >= 0) or (not negative and value <= 0):
        return None, None
    return metric, value


# ---------------------------------------------------------------------------
# Per-kind composers: fn(category, merchant, trigger, customer, now) -> Composed
# ---------------------------------------------------------------------------

def _compose_research_digest(category, merchant, trigger, customer, now) -> Composed:
    payload = trigger.get("payload") or {}
    item = _digest_by_id(category, payload.get("top_item_id")) or _digest_by_kind(category, ("research",))
    owner = _owner(merchant, category)
    if not item:
        return (
            f"{owner}, this week's {category.get('display_name') or category.get('slug')} digest is out. "
            f"Want me to send you the one item most relevant to {_biz_name(merchant)}?",
            "open_ended",
            "digest trigger with no item in context; offered to pull rather than invent one",
        )
    pct = re.search(r"(\d+%\s+(?:lower|higher|better|fewer|more)[^.,;]*)", item.get("summary") or "")
    trial = f"{item['trial_n']:,}-patient trial" if item.get("trial_n") else ""
    finding = ", ".join(p for p in (trial, pct.group(1) if pct else "") if p)
    agg = merchant.get("customer_aggregate") or {}
    cohort = ""
    if item.get("patient_segment") == "high_risk_adults" and agg.get("high_risk_adult_count"):
        cohort = f" Directly relevant to your {agg['high_risk_adult_count']} high-risk adult patients."
    body = (
        f"{owner}, new in {item.get('source')}: {item.get('title')}"
        f"{f' — {finding}' if finding else ''}.{cohort} "
        f"Worth a 2-min read — want me to pull the abstract + draft a patient-ed WhatsApp you can share?"
    )
    return body, "open_ended", "research digest: cited source + trial size/effect + merchant cohort; reciprocity CTA"


def _compose_regulation_change(category, merchant, trigger, customer, now) -> Composed:
    payload = trigger.get("payload") or {}
    item = _digest_by_id(category, payload.get("top_item_id")) or _digest_by_kind(category, ("compliance",))
    owner = _owner(merchant, category)
    deadline = payload.get("deadline_iso") or (item or {}).get("date")
    if not item:
        return (
            f"{owner}, a compliance update for your category is due{f' by {_fmt_date(deadline)}' if deadline else ''}. "
            f"Want me to summarise what changes for {_biz_name(merchant)}?",
            "binary_yes_no",
            "regulation trigger without digest item; no invented details",
        )
    detail = _first_sentence(item.get("summary") or "")
    actionable = (item.get("actionable") or "").rstrip(".")
    body = (
        f"{owner}, heads-up from {item.get('source')}: {detail}"
        f"{f' Deadline: {_fmt_date(deadline)}.' if deadline else ''}"
        f"{f' Action: {actionable}.' if actionable else ''} "
        f"Want me to draft a 1-page compliance checklist for your clinic SOPs?"
    )
    return body, "binary_yes_no", "regulation: cited circular + concrete change + deadline; effort-externalized checklist"


def _compose_cde(category, merchant, trigger, customer, now) -> Composed:
    payload = trigger.get("payload") or {}
    item = _digest_by_id(category, payload.get("digest_item_id") or payload.get("top_item_id")) or _digest_by_kind(category, ("cde",))
    owner = _owner(merchant, category)
    if not item:
        return _compose_adaptive(category, merchant, trigger, customer, now)
    credits = payload.get("credits") or item.get("credits")
    when = _fmt_datetime(item.get("date")) if item.get("date") else ""
    fee = (item.get("actionable") or _humanize(payload.get("fee"))).rstrip(".")
    speaker = _first_sentence(item.get("summary") or "")
    bits = [b for b in (when, f"{credits} CDE credits" if credits else "", fee) if b]
    body = (
        f"{owner}, {item.get('title')} ({item.get('source')}). {'; '.join(bits)}. {speaker} "
        f"Want me to block it on your calendar + send the registration details?"
    )
    return body, "binary_yes_no", "CDE event from the referenced digest item: date + credits + fee; low-effort RSVP CTA"


def _compose_recall_due(category, merchant, trigger, customer, now) -> Composed:
    payload = trigger.get("payload") or {}
    hinglish = _wants_hinglish(merchant, category, customer)
    name, _child = _customer_name(customer)
    greet = _greet_customer(name, hinglish)
    biz = _biz_name(merchant)
    rel = (customer or {}).get("relationship") or {}
    default_service = {"dentists": "check-up", "gyms": "next session", "salons": "next appointment",
                       "pharmacies": "refill", "restaurants": "next visit"}
    service = _humanize(payload.get("service_due")) or default_service.get(category.get("slug"), "next visit")
    last = payload.get("last_service_date") or rel.get("last_visit")
    last_bit = f" (last visit {_fmt_date(last)})" if last else ""
    live = _live_offers(merchant)
    offer = _pick(live, tuple(service.split()[-1:]) + ("clean", "check-up", "checkup"))
    offer_bit = f" {offer}." if offer else ""
    slots = [s.get("label") for s in payload.get("available_slots") or [] if s.get("label")]
    if len(slots) >= 2:
        body = _t(
            hinglish,
            f"{greet}, {biz} here. Your {service} is due{last_bit}. Two slots open: {slots[0]} or {slots[1]}.{offer_bit} "
            f"Reply 1 or 2, or tell us a time that suits you.",
            f"{greet}, {biz} se. Aapka {service} due hai{last_bit}. 2 slots ready hain: {slots[0]} ya {slots[1]}.{offer_bit} "
            f"Reply 1 ya 2, ya apna time batayein.",
        )
    else:
        body = _t(
            hinglish,
            f"{greet}, {biz} here. Your {service} is due{last_bit}.{offer_bit} Reply YES and we'll book a slot that suits you.",
            f"{greet}, {biz} se. Aapka {service} due hai{last_bit}.{offer_bit} YES reply karein, hum aapke time pe slot book kar denge.",
        )
    return body, "binary_yes_no", "customer recall: real due date + real slots + merchant's live offer only + language pref"


def _compose_customer_lapsed(category, merchant, trigger, customer, now) -> Composed:
    payload = trigger.get("payload") or {}
    hinglish = _wants_hinglish(merchant, category, customer)
    name, _ = _customer_name(customer)
    greet = _greet_customer(name, hinglish)
    biz = _biz_name(merchant)
    rel = (customer or {}).get("relationship") or {}
    days = payload.get("days_since_last_visit")
    if days is None and rel.get("last_visit"):
        last = _parse_dt(rel["last_visit"] + "T00:00:00+00:00")
        days = (_now(now) - last).days if last else None
    focus = _humanize(payload.get("previous_focus"))
    months = payload.get("previous_membership_months")
    history = ""
    if focus and months:
        history = _t(hinglish, f" You put in {months} solid months on your {focus} plan.",
                     f" Aapne {months} mahine {focus} plan pe kaafi achha kaam kiya tha.")
    elif rel.get("visits_total"):
        history = _t(hinglish, f" Thanks for your {rel['visits_total']} visits so far.",
                     f" Ab tak ke {rel['visits_total']} visits ke liye shukriya.")
    days_bit = _t(hinglish, f" It's been {days} days since your last visit.", f" Aapki last visit ko {days} din ho gaye.") if days else ""
    body = _t(
        hinglish,
        f"{greet}, {biz} here.{days_bit}{history} Want to pick up where you left off this week? Reply YES and we'll hold a slot.",
        f"{greet}, {biz} se.{days_bit}{history} Is hafte phir shuru karein? YES reply karein, hum slot hold kar lenge.",
    )
    return body, "binary_yes_no", "customer winback: real gap + real history; no invented discount"


def _compose_trial_followup(category, merchant, trigger, customer, now) -> Composed:
    payload = trigger.get("payload") or {}
    hinglish = _wants_hinglish(merchant, category, customer)
    name, child = _customer_name(customer)
    biz = _biz_name(merchant)
    who = child or "you"
    trial = payload.get("trial_date")
    trial_bit = f" on {_fmt_date(trial)}" if trial else ""
    slots = [s.get("label") for s in payload.get("next_session_options") or [] if s.get("label")]
    live = _live_offers(merchant)
    offer = _pick(live, ("first month", "month", "trial")) or (live[0] if live else None)
    enjoyed = f"hope {child} enjoyed the trial class{trial_bit}!" if child else f"hope you enjoyed your trial{trial_bit}!"
    slot_bit = f" Next session: {slots[0]}." if slots else ""
    offer_bit = f" If {'he/she' if child else 'you'} would like to continue, {offer} is on." if offer else ""
    body = _t(
        hinglish,
        f"Hi {name}, {biz} here — {enjoyed}{slot_bit}{offer_bit} Reply YES to book {'the spot' if who != 'you' else 'your spot'}.",
        f"Hi {name}, {biz} se — {enjoyed}{slot_bit}{offer_bit} Spot book karne ke liye YES reply karein.",
    )
    return body, "binary_yes_no", "trial follow-up: addresses parent for minors, real next slot + live offer"


def _compose_appointment_tomorrow(category, merchant, trigger, customer, now) -> Composed:
    payload = trigger.get("payload") or {}
    hinglish = _wants_hinglish(merchant, category, customer)
    name, _ = _customer_name(customer)
    greet = _greet_customer(name, hinglish)
    biz = _biz_name(merchant)
    when = payload.get("appointment_label") or payload.get("slot_label") or (
        _fmt_datetime(payload["appointment_iso"]) if payload.get("appointment_iso") else _t(hinglish, "tomorrow", "kal")
    )
    service = _humanize(payload.get("service")) or _t(hinglish, "your appointment", "aapka appointment")
    locality = _locality(merchant)
    body = _t(
        hinglish,
        f"{greet}, reminder from {biz} ({locality}): {service} is booked for {when}. Reply 1 to confirm, 2 to reschedule.",
        f"{greet}, {biz} ({locality}) se reminder: {service} {when} ke liye booked hai. Confirm ke liye 1, reschedule ke liye 2 reply karein.",
    )
    return body, "binary_yes_no", "appointment reminder: confirm/reschedule (booking flow, two options allowed)"


def _compose_chronic_refill(category, merchant, trigger, customer, now) -> Composed:
    payload = trigger.get("payload") or {}
    hinglish = _wants_hinglish(merchant, category, customer)
    name, _ = _customer_name(customer)
    greet = _greet_customer(name, hinglish)
    biz = _biz_name(merchant)
    meds = payload.get("molecule_list") or []
    meds_txt = (", ".join(meds[:-1]) + _t(hinglish, " and ", " aur ") + meds[-1]) if len(meds) > 1 else (meds[0] if meds else "")
    runs_out = _fmt_date(payload["stock_runs_out_iso"]) if payload.get("stock_runs_out_iso") else ""
    if not meds and (category.get("slug") or "") != "pharmacies":
        cta = _t(hinglish, "Reply YES and we'll book a time that suits you.", "YES reply karein, hum aapke time pe slot book kar denge.")
        body = f"{greet}, {biz} {_t(hinglish, 'here', 'se')}. {_t(hinglish, 'Your follow-up visit is due.', 'Aapka follow-up visit due hai.')} {cta}"
        return body, "binary_yes_no", "follow-up reminder: no medicines in context, so none are named"
    what = meds_txt or _t(hinglish, "regular medicines", "regular dawaiyan")
    when = (_t(hinglish, f" will run out by {runs_out}", f" {runs_out} tak khatam ho jayengi") if runs_out
            else _t(hinglish, " are due for a refill", " refill ke liye due hain"))
    extras = []
    senior = ((customer or {}).get("identity") or {}).get("senior_citizen")
    senior_offer = _pick(_live_offers(merchant), ("senior",))
    if senior and senior_offer:
        extras.append(_t(hinglish, f"{senior_offer} applies.", f"{senior_offer} bhi lagega."))
    # Cross-check against a recall alert in the category digest (adaptive + safety).
    alert = _digest_by_kind(category, ("alert",))
    recalled = next((m for m in meds if alert and m.lower() in (alert.get("title") or "").lower()), None)
    if recalled:
        extras.append(_t(hinglish, f"We'll also check your {recalled} isn't from the recalled batches.",
                         f"Hum check kar lenge ki aapki {recalled} recalled batch ki nahi hai."))
    delivery = payload.get("delivery_address_saved")
    cta = _t(
        hinglish,
        "Reply YES and we'll deliver to your saved address." if delivery else "Reply YES and we'll keep it ready.",
        "YES reply karein, saved address pe delivery kar denge." if delivery else "YES reply karein, hum ready rakhenge.",
    )
    body = f"{greet}, {biz} {_t(hinglish, 'here', 'se')}. {_t(hinglish, 'Your', 'Aapki')} {what}{when}. {' '.join(extras)} {cta}"
    return re.sub(r"\s{2,}", " ", body), "binary_yes_no", "refill: exact molecules + run-out date + live senior offer + recall cross-check"


def _compose_bridal(category, merchant, trigger, customer, now) -> Composed:
    payload = trigger.get("payload") or {}
    hinglish = _wants_hinglish(merchant, category, customer)
    name, _ = _customer_name(customer)
    biz = _biz_name(merchant)
    wedding = _fmt_date(payload.get("wedding_date")) if payload.get("wedding_date") else None
    trial = _fmt_date(payload.get("trial_completed")) if payload.get("trial_completed") else None
    step = _humanize(payload.get("next_step_window_open")).replace("30day", "30-day") or "pre-bridal prep"
    offer = _pick(_live_offers(merchant), ("bridal", "facial", "skin", "spa"))
    body = (
        f"Hi {name}, {biz} here 💐 "
        f"{f'Since your bridal trial on {trial}, ' if trial else ''}"
        f"{f'with the wedding on {wedding}, ' if wedding else ''}"
        f"the next step is the {step}.{f' {offer} is available.' if offer else ''} "
        f"Want us to hold a Saturday slot to plan it? Reply YES."
    )
    return body, "binary_yes_no", "bridal follow-up: trial + wedding date + next program; only bridal-relevant live offers"


def _compose_perf_dip(category, merchant, trigger, customer, now) -> Composed:
    payload = trigger.get("payload") or {}
    owner = _owner(merchant, category)
    hinglish = _wants_hinglish(merchant, category)
    metric, delta = payload.get("metric"), payload.get("delta_pct")
    if not isinstance(delta, (int, float)):
        metric, delta = _metric_delta(merchant, negative=True)
    window = payload.get("window") or "7d"
    perf = merchant.get("performance") or {}
    peer = _peer_ctr(category)
    if payload.get("is_expected_seasonal"):
        beat = _season_beat(category, _now(now).month) or _humanize(payload.get("season_note"))
        members = (merchant.get("customer_aggregate") or {}).get("total_active_members")
        target = f"your {members} active members" if members else "your existing customers"
        body = (
            f"{owner}, {metric} {_delta_words(delta)} over {window} — but this is the expected seasonal pattern"
            f"{f' ({beat})' if beat else ''}, not something you did. "
            f"Best use of this window is retention: want me to draft a check-in WhatsApp for {target}?"
        )
        return body, "binary_yes_no", "seasonal dip reframed with category beat; retention play instead of panic discounting"
    stats = category.get("peer_stats") or {}
    gap = next(((m, perf[m], stats[f"avg_{m}_30d"]) for m in ("calls", "views", "directions")
                if isinstance(perf.get(m), (int, float)) and isinstance(stats.get(f"avg_{m}_30d"), (int, float))
                and perf[m] < stats[f"avg_{m}_30d"]), None)
    if metric:
        head = f"{metric} {_delta_words(delta)} over the last {window}"
    elif gap:
        # No week-on-week dip in the data: anchor on the real gap to peers instead of claiming one.
        head = f"{gap[1]} {gap[0]} in the last 30 days vs {gap[2]:g} for peers in your category"
    else:
        head = "your numbers have gone flat this month"
    baseline = f" (baseline {payload['vs_baseline']})" if payload.get("vs_baseline") else ""
    ctr_bit = ""
    if isinstance(perf.get("ctr"), (int, float)) and peer and perf["ctr"] < peer:
        ctr_bit = f" Your CTR is {_ctr(perf['ctr'])} vs {_ctr(peer)} peer average."
    sub = merchant.get("subscription") or {}
    if sub.get("status") == "expired" and sub.get("days_since_expiry"):
        ctr_bit += f" Your plan also lapsed {sub['days_since_expiry']} days ago, so profile upkeep has stopped."
    offer, live = _offer_hook(merchant, category)
    unverified = "unverified_gbp" in (merchant.get("signals") or [])
    fix = (
        " Your Google profile is also still unverified, which caps visibility." if unverified else ""
    )
    around = f" around {_offer_phrase(offer, live)}" if offer else " + one service+price offer"
    ask = _t(hinglish, f"Want me to draft a recovery post{around}?", f"Recovery post{around} draft kar doon?")
    body = f"{owner}, quick signal: {head}{baseline}.{ctr_bit}{fix} {ask}"
    return body, "binary_yes_no", "perf dip: exact delta + peer CTR + honest offer status (live vs suggestion)"


def _compose_perf_spike(category, merchant, trigger, customer, now) -> Composed:
    payload = trigger.get("payload") or {}
    owner = _owner(merchant, category)
    metric, delta = payload.get("metric"), payload.get("delta_pct")
    if not isinstance(delta, (int, float)):
        metric, delta = _metric_delta(merchant, negative=False)
    if not metric:
        return _compose_adaptive(category, merchant, trigger, customer, now)
    driver = _humanize(payload.get("likely_driver"))
    driver_bit = f", most likely from your {driver}" if driver else ""
    offer, live = _offer_hook(merchant, category)
    ask = (f"Want 2 more posts in the same style this week, anchored on {_offer_phrase(offer, live)}?" if offer
           else "Want 2 more posts in the same style this week?")
    body = f"{owner}, nice — {metric} {_delta_words(delta)} over {payload.get('window') or '7d'}{driver_bit}. Momentum like this fades if posts go quiet. {ask}"
    return body, "binary_yes_no", "perf spike: signed delta + likely driver; repeat-what-worked CTA"


def _compose_curious_ask(category, merchant, trigger, customer, now) -> Composed:
    """Lever #7 'ask the merchant' — the family production Vera under-uses."""
    owner = _owner(merchant, category)
    hinglish = _wants_hinglish(merchant, category)
    cities = {"delhi", "mumbai", "bangalore", "hyderabad", "pune", "chennai", "jaipur", "lucknow", "chandigarh", "ahmedabad"}
    own_city = _city(merchant).lower()
    trends = [t for t in category.get("trend_signals") or [] if isinstance(t.get("delta_yoy"), (int, float))
              and not (set(t.get("query", "").lower().split()) & (cities - {own_city}))]  # skip other cities' queries
    # Prefer a trend that matches something this merchant actually sells (e.g. "weekday lunch thali" for a thali cafe).
    offer_words = {w for o in _live_offers(merchant) for w in re.findall(r"[a-z]{4,}", o.lower())}
    trends.sort(key=lambda t: (-len(offer_words & set(re.findall(r"[a-z]{4,}", t.get("query", "").lower()))),
                               -t["delta_yoy"]))
    hook = ""
    if trends:
        t = trends[0]
        hook = f" Asking because \"{t['query']}\" searches are up {_pct(t['delta_yoy'])} YoY across metros."
    theme = _review_theme(merchant, "pos")
    if not hook and theme and theme.get("occurrences_30d"):
        hook = f" Asking because {theme['occurrences_30d']} reviews this month praise your {_humanize(theme['theme'])}."
    dish = (category.get("slug") or "") == "restaurants"
    q = _t(
        hinglish,
        f"{owner}, quick one — what's the most-asked {'dish' if dish else 'service'} at {_biz_name(merchant)} this week?",
        f"{owner}, ek quick sawaal — is hafte {_biz_name(merchant)} pe sabse zyada {'kaunsi dish order ho rahi hai' if dish else 'kis service ki demand hai'}?",
    )
    tail = _t(hinglish, " Tell me and I'll turn it into a Google post + a WhatsApp reply you can reuse.",
              " Bata dijiye, main uska Google post + reusable WhatsApp reply bana dungi.")
    return f"{q}{hook}{tail}", "open_ended", "curious-ask (lever #7) + verifiable trend hook + effort externalization"


def _compose_trend_movement(category, merchant, trigger, customer, now) -> Composed:
    payload = trigger.get("payload") or {}
    owner = _owner(merchant, category)
    trends = category.get("trend_signals") or []
    t = next((x for x in trends if x.get("query") == payload.get("query")), trends[0] if trends else None)
    if not t:
        return _compose_adaptive(category, merchant, trigger, customer, now)
    offer, live = _offer_hook(merchant, category, tuple(t["query"].split()[:1]))
    body = (
        f"{owner}, \"{t['query']}\" searches are up {_pct(t.get('delta_yoy') or 0)} YoY "
        f"(mostly {t.get('segment_age', 'adults')}). "
        f"Want me to draft a Google post that catches that demand{f' with {_offer_phrase(offer, live)}' if offer else ''}?"
    )
    return body, "binary_yes_no", "trend movement: exact query + YoY delta + segment; honest offer label"


def _compose_festival(category, merchant, trigger, customer, now) -> Composed:
    payload = trigger.get("payload") or {}
    owner = _owner(merchant, category)
    hinglish = _wants_hinglish(merchant, category)
    festival = payload.get("festival")
    days = payload.get("days_until")
    date = payload.get("date")
    if not festival:
        beat = _season_beat(category, _now(now).month)
        if not beat:
            return _compose_adaptive(category, merchant, trigger, customer, now)
        offer, live = _offer_hook(merchant, category)
        if "retention" in beat.lower():
            ask = "Want me to draft a check-in WhatsApp for your current members instead of a discount push?"
        else:
            ask = f"Want me to draft a timed post{f' around {_offer_phrase(offer, live)}' if offer else ''}?"
        body = f"{owner}, seasonal window for {category.get('display_name') or category.get('slug')}: {beat}. {ask}"
        return body, "binary_yes_no", "festival placeholder: used the category's current seasonal beat"
    when = f"{festival} is {days} days away ({_fmt_date(date)})" if days is not None else f"{festival} is on {_fmt_date(date)}"
    if isinstance(days, int) and days > 45:
        festival_month = (_parse_dt(date) or _now(now)).month
        beat = _season_beat(category, festival_month)
        beat_bit = f" Worth planning early though ({beat})." if beat else ""
        body = (
            f"{owner}, {when} — too early for a campaign.{beat_bit} "
            f"Want me to set a reminder to build your {festival} package 3 weeks out?"
        )
        return body, "binary_yes_no", "far-off festival: restraint + planning nudge with the season's real stat"
    offer, live = _offer_hook(merchant, category)
    around = f" around {_offer_phrase(offer, live)}" if offer else ""
    ask = _t(hinglish, f"Want me to draft the WhatsApp + Google post{around} today?",
             f"Aaj hi WhatsApp + Google post{around} draft kar doon?")
    body = f"{owner}, {when} — the booking window is open now. {ask}"
    return body, "binary_yes_no", "near festival: countdown urgency + merchant offer; single CTA"


def _compose_category_seasonal(category, merchant, trigger, customer, now) -> Composed:
    payload = trigger.get("payload") or {}
    owner = _owner(merchant, category)
    hinglish = _wants_hinglish(merchant, category)
    parts = []
    for raw in payload.get("trends") or []:
        m = re.match(r"(.+?)_demand_([+-]\d+)", str(raw))
        if m:
            parts.append(f"{_humanize(m.group(1)).replace('cold cough', 'cold/cough')} {m.group(2)}%")
    item = _digest_by_kind(category, ("seasonal",))
    source = f" ({item['source']})" if item and item.get("source") else ""
    action = (item or {}).get("actionable") or "rearrange shelves for the season"
    if not parts:
        beat = _season_beat(category, _now(now).month)
        parts = [beat] if beat else []
    if not parts:
        return _compose_adaptive(category, merchant, trigger, customer, now)
    body = (
        f"{owner}, {_humanize(payload.get('season')) or 'season'} demand shift{source}: {', '.join(parts)}. "
        f"Suggested move: {action[0].lower() + action[1:].rstrip('.')}. "
        f"{_t(hinglish, 'Want me to draft a shelf checklist + a WhatsApp for your repeat customers?', 'Shelf checklist + repeat customers ke liye WhatsApp draft kar doon?')}"
    )
    return body, "binary_yes_no", "category seasonal: exact demand deltas + cited source + concrete shelf action"


def _compose_competitor(category, merchant, trigger, customer, now) -> Composed:
    payload = trigger.get("payload") or {}
    owner = _owner(merchant, category)
    hinglish = _wants_hinglish(merchant, category)
    name = payload.get("competitor_name")
    dist = payload.get("distance_km")
    their = payload.get("their_offer")
    opened = payload.get("opened_date")
    who = name or "a new competitor"
    where = f" ~{dist} km from you" if dist else f" near {_locality(merchant)}"
    since = f" (opened {_fmt_date(opened)})" if opened else ""
    offer, live = _offer_hook(merchant, category)
    price_bit = ""
    if their:
        price_bit = f" They're advertising {their}" + (f" vs your {offer}." if live and offer else ".")
    praise = _review_theme(merchant, "pos")
    proof = ""
    if praise and praise.get("common_quote"):
        proof = f" Your edge is in your reviews: \"{praise['common_quote']}\"."
    elif praise and praise.get("occurrences_30d"):
        proof = f" Your edge: {praise['occurrences_30d']} reviews this month praise your {_humanize(praise['theme'])}."
    ask = _t(
        hinglish,
        "Don't race them on price — want me to draft a post that leads with that?",
        "Price war mat kijiye — kya main isi pe ek post draft kar doon?",
    )
    body = f"{owner}, {who} opened{where}{since}.{price_bit}{proof} {ask}"
    return body, "binary_yes_no", "competitor: named only from context, price gap, social proof from own reviews; loss aversion"


def _compose_ipl(category, merchant, trigger, customer, now) -> Composed:
    payload = trigger.get("payload") or {}
    owner = _owner(merchant, category)
    hinglish = _wants_hinglish(merchant, category)
    match = payload.get("match") or "tonight's match"
    venue = f" at {payload['venue']}" if payload.get("venue") else ""
    start = _parse_dt(payload.get("match_time_iso"))
    when = f", {_fmt_time(start)}" if start else ""
    weeknight = payload.get("is_weeknight")
    item = _digest_by_kind(category, ("seasonal",))
    summary = (item or {}).get("summary") or ""
    sat = re.search(r"down (\d+%)", summary)
    wk = re.search(r"\+(\d+%)", summary)
    live = _live_offers(merchant)
    combo = _catalog_offer(category, ("match",))
    if weeknight:
        stat = f" Weeknight matches drive +{wk.group(1)} covers (magicpin order data)." if wk else ""
        offer = live[0] if live else combo
        ask = f"Want me to push {offer} for tonight?" if offer else "Want me to draft a match-night push?"
        body = f"{owner}, {match}{venue} tonight{when}.{stat} {ask}"
    else:
        stat = f" Saturday IPL nights run ~{sat.group(1)} below a normal Saturday for dine-in (magicpin order data) — people watch at home." if sat else ""
        weekday_only = [o for o in live if re.search(r"tue|wed|thu|weekday", o.lower())]
        note = f" Your {weekday_only[0]} doesn't run today," if weekday_only else ""
        ask = (f"{note} so I'd do a delivery-only {combo} for the match. Want me to set it up?" if combo
               else " I'd lean on delivery tonight. Want me to draft the push?")
        body = f"{owner}, {match}{venue} today{when}.{stat}{ask}"
    body = body.replace(" ,", ",")
    if hinglish:
        body = body.replace("Want me to set it up?", "Set up kar doon?").replace("Want me to draft the push?", "Push draft kar doon?")
    return body, "binary_yes_no", "IPL: cited order-data stat + judgment on offer-day mismatch; one CTA"


def _compose_milestone(category, merchant, trigger, customer, now) -> Composed:
    payload = trigger.get("payload") or {}
    owner = _owner(merchant, category)
    now_v, target = payload.get("value_now"), payload.get("milestone_value")
    metric = _humanize(payload.get("metric") or "reviews").replace("review count", "reviews")
    if now_v is not None and target is not None and payload.get("is_imminent") and now_v < target:
        body = (
            f"{owner}, you're at {now_v:,} {metric} — just {target - now_v} away from {target:,}. "
            f"A short ask to this week's happy customers usually closes a gap this small. Want me to draft the review-request WhatsApp?"
        )
        return body, "binary_yes_no", "imminent milestone: exact gap + effort-externalized ask"
    if now_v is not None:
        body = f"{owner}, you've crossed {now_v:,} {metric}. Want me to draft a thank-you Google post + a reply template for new reviews?"
        return body, "binary_yes_no", "milestone reached: real value + reciprocity post"
    perf = merchant.get("performance") or {}
    if perf.get("views"):
        body = (
            f"{owner}, {_biz_name(merchant)} had {perf['views']:,} Google views and {perf.get('calls', 0)} calls in the last "
            f"{perf.get('window_days', 30)} days. Want me to turn that into a thank-you post for your regulars?"
        )
        return body, "binary_yes_no", "milestone placeholder: anchored on real 30d performance instead of inventing a milestone"
    return _compose_adaptive(category, merchant, trigger, customer, now)


def _compose_dormant(category, merchant, trigger, customer, now) -> Composed:
    payload = trigger.get("payload") or {}
    owner = _owner(merchant, category)
    hinglish = _wants_hinglish(merchant, category)
    days = payload.get("days_since_last_merchant_message") or _signal_days(merchant, "dormant_with_vera")
    topic = _humanize(payload.get("last_topic"))
    metric, delta = _metric_delta(merchant, negative=True)
    opener = f"{owner}, it's been {days} days since we last spoke" if days else f"{owner}, it's been a while"
    topic_bit = f" (last time: {topic})" if topic else ""
    hook = f" Since then your {metric} are {_delta_words(delta)} week-on-week." if metric else ""
    ask = _t(
        hinglish,
        "Want a 3-line status of your Google profile vs peers? Takes me 2 minutes.",
        "Aapke Google profile ka peers ke saath 3-line comparison bhej doon? 2 minute ka kaam hai.",
    )
    return f"{opener}{topic_bit}.{hook} {ask}", "binary_yes_no", "dormant: real silence length + last topic + real metric delta; low ask"


def _compose_active_planning(category, merchant, trigger, customer, now) -> Composed:
    """Merchant already asked 'what would it look like' — deliver a v1, don't re-qualify."""
    payload = trigger.get("payload") or {}
    owner = _owner(merchant, category)
    topic = _humanize(payload.get("intent_topic") or payload.get("topic")) or "the program you mentioned"
    words = tuple(w for w in topic.lower().split() if len(w) > 3)
    live = _live_offers(merchant)
    anchor = _pick(live, words) or (live[0] if live else None) or _catalog_offer(category, words)
    agg = merchant.get("customer_aggregate") or {}
    audience = agg.get("total_active_members") or agg.get("total_unique_ytd")
    audience_bit = f"WhatsApp to your {audience:,} {'members' if agg.get('total_active_members') else 'customers'}" if audience else "WhatsApp to your regulars"
    # If Vera already sketched the program in this thread, build on that sketch instead of a generic template.
    sketch = None
    for turn in reversed(merchant.get("conversation_history") or []):
        m = re.search(r"Suggest (.+?)\.", turn.get("body") or "") if turn.get("from") == "vera" else None
        if m:
            sketch = m.group(1)
            break
    if sketch:
        perf_note = ""
        up_metric, up = _metric_delta(merchant, negative=False)
        if up_metric and up and up > 0:
            perf_note = f" Timing's good: {up_metric} are {_delta_words(up)} this week."
        body = (
            f"{owner}, picking up your {topic}: {sketch}, as we sketched.{perf_note} "
            f"Next I'd launch it with a Google post + a {audience_bit}. "
            f"Want me to draft both now for you to approve?"
        )
        return body, "binary_yes_no", "active intent: builds on the program already sketched in conversation history; routes to launch drafts"
    body = (
        f"{owner}, here's a v1 for the {topic}: "
        f"(1) one fixed format with a clear start date and limited spots; "
        f"(2) price anchored on {'your ' if anchor in live else ''}{anchor or 'one service+price line'}; "
        f"(3) launch with a Google post + a {audience_bit}. "
        f"Want me to fill in dates + price and send you the final copy to approve?"
    )
    return body, "binary_yes_no", "active intent: delivered a concrete v1 (Pattern D avoided) anchored on real offer + audience size"


def _compose_review_theme(category, merchant, trigger, customer, now) -> Composed:
    payload = trigger.get("payload") or {}
    owner = _owner(merchant, category)
    theme = payload.get("theme")
    n = payload.get("occurrences_30d")
    quote = payload.get("common_quote")
    trend = payload.get("trend")
    if not theme:
        r = _review_theme(merchant, "neg") or _review_theme(merchant, "pos")
        if not r:
            return _compose_adaptive(category, merchant, trigger, customer, now)
        theme, n, quote = r.get("theme"), r.get("occurrences_30d"), r.get("common_quote")
    count = f"{n} reviews in 30 days" if n else "recent reviews"
    quote_bit = f' Typical line: "{quote}".' if quote else ""
    body = (
        f"{owner}, a review theme is {'rising' if trend == 'rising' else 'showing up'}: {_humanize(theme)} ({count})."
        f"{quote_bit} "
        f"Want me to draft a reply template + a 3-point fix note for your team?"
    )
    return body, "binary_yes_no", "review theme: count + verbatim quote + two concrete deliverables"


def _compose_renewal(category, merchant, trigger, customer, now) -> Composed:
    payload = trigger.get("payload") or {}
    owner = _owner(merchant, category)
    sub = merchant.get("subscription") or {}
    days = payload.get("days_remaining", sub.get("days_remaining"))
    plan = payload.get("plan") or sub.get("plan") or "magicpin"
    amount = payload.get("renewal_amount")
    perf = merchant.get("performance") or {}
    when = f"renews in {days} days" if days is not None else "is up for renewal"
    proof = ""
    if perf.get("views"):
        proof = f" Last {perf.get('window_days', 30)} days: {perf['views']:,} views, {perf.get('calls', 0)} calls, {perf.get('directions', 0)} direction requests."
    body = (
        f"{owner}, your {plan} plan {when}{f' ({_inr(amount)})' if amount else ''}.{proof} "
        f"Want a 1-page summary of what that drove before you decide?"
    )
    return body, "binary_yes_no", "renewal: exact days/amount + real 30d results as value proof"


def _compose_winback_merchant(category, merchant, trigger, customer, now) -> Composed:
    payload = trigger.get("payload") or {}
    owner = _owner(merchant, category)
    hinglish = _wants_hinglish(merchant, category)
    days = payload.get("days_since_expiry") or (merchant.get("subscription") or {}).get("days_since_expiry")
    dip = payload.get("perf_dip_pct")
    lapsed = payload.get("lapsed_customers_added_since_expiry")
    facts = []
    if isinstance(dip, (int, float)):
        facts.append(f"performance is {_delta_words(dip)}")
    if lapsed:
        facts.append(f"{lapsed} more customers have gone lapsed")
    since = f"In the {days} days since your plan lapsed" if days else "Since your plan lapsed"
    body = (
        f"{owner}, {since[0].lower() + since[1:]}, {' and '.join(facts) or 'your listing has had no active campaigns'}. "
        f"{_t(hinglish, 'Want me to send a 1-page summary of what restarting would recover?', 'Restart karne se kya wapas aa sakta hai, uska 1-page summary bhej doon?')}"
    )
    return body, "binary_yes_no", "merchant winback: loss aversion with real post-expiry deltas; no invented price"


def _compose_supply_alert(category, merchant, trigger, customer, now) -> Composed:
    payload = trigger.get("payload") or {}
    owner = _owner(merchant, category)
    hinglish = _wants_hinglish(merchant, category)
    item = _digest_by_id(category, payload.get("alert_id")) or _digest_by_kind(category, ("alert", "supply"))
    molecule = payload.get("molecule") or payload.get("item") or "a tracked item"
    batches = payload.get("affected_batches") or []
    mfr = payload.get("manufacturer")
    source = (item or {}).get("source")
    reason = re.search(r"flagged for ([^.;]+)", (item or {}).get("summary") or "")
    chronic = (merchant.get("customer_aggregate") or {}).get("chronic_rx_count")
    body = (
        f"{owner}, {source + ': ' if source else ''}voluntary recall on {molecule}"
        f"{' — batches ' + ', '.join(batches) if batches else ''}{f' ({mfr})' if mfr else ''}"
        f"{f', flagged for {reason.group(1)}' if reason else ''}. "
        f"{f'Worth checking against your {chronic} chronic-Rx customers. ' if chronic else ''}"
        f"{_t(hinglish, 'Want me to draft the customer WhatsApp + a shelf-pull checklist?', 'Customer WhatsApp + shelf-pull checklist draft kar doon?')}"
    )
    return body, "binary_yes_no", "supply alert: molecule + batch numbers + cited regulator + affected chronic-Rx base"


def _compose_gbp_unverified(category, merchant, trigger, customer, now) -> Composed:
    payload = trigger.get("payload") or {}
    owner = _owner(merchant, category)
    hinglish = _wants_hinglish(merchant, category)
    path = _humanize(payload.get("verification_path")).replace(" or ", " or a ")
    uplift = payload.get("estimated_uplift_pct")
    perf = merchant.get("performance") or {}
    views = f" You still got {perf['views']:,} views last month without it." if perf.get("views") else ""
    body = (
        f"{owner}, {_possessive(_biz_name(merchant))} Google profile is still unverified, so Google holds back your edits and ranking."
        f"{views}{f' Verifying typically lifts visibility ~{_pct(uplift)}.' if isinstance(uplift, (int, float)) else ''}"
        f"{f' It is done via {path}.' if path else ''} "
        f"{_t(hinglish, 'Want me to walk you through it now? 5 minutes.', 'Abhi 5 minute mein karwa doon?')}"
    )
    return body, "binary_yes_no", "GBP unverified: concrete uplift + path + effort externalization"


def _compose_weather(category, merchant, trigger, customer, now) -> Composed:
    payload = trigger.get("payload") or {}
    owner = _owner(merchant, category)
    temp = payload.get("temp_c") or payload.get("temperature_c")
    city = payload.get("city") or _city(merchant)
    slug = category.get("slug")
    plays = {
        "restaurants": "push delivery + cold beverages for the afternoon",
        "pharmacies": "keep ORS + sunscreen at the counter",
        "gyms": "promote early-morning and evening slots",
        "salons": "push indoor services like hair spa during peak heat",
        "dentists": "move non-urgent appointments to cooler hours",
    }
    head = f"{temp}°C in {city} today" if temp else f"Heatwave alert in {city}"
    body = f"{owner}, {head}. Suggested move: {plays.get(slug, 'adjust timings for the heat')}. Want me to draft a quick post for it?"
    return body, "binary_yes_no", "weather trigger: real temp/city + category-specific play"


def _compose_adaptive(category, merchant, trigger, customer, now) -> Composed:
    """Unknown or placeholder kinds: anchor on the strongest real fact we have."""
    owner = _owner(merchant, category)
    kind = _humanize(trigger.get("kind") or "update")
    perf = merchant.get("performance") or {}
    peer = _peer_ctr(category)
    if customer:
        name, _ = _customer_name(customer)
        hinglish = _wants_hinglish(merchant, category, customer)
        body = _t(
            hinglish,
            f"{_greet_customer(name, hinglish)}, {_biz_name(merchant)} here. Just checking in — reply YES if you'd like us to book your next visit.",
            f"{_greet_customer(name, hinglish)}, {_biz_name(merchant)} se. Agli visit book karni ho to YES reply karein.",
        )
        return body, "binary_yes_no", f"customer-facing fallback for kind={kind}; no invented facts"
    fact = ""
    if isinstance(perf.get("ctr"), (int, float)) and peer:
        fact = f" Your CTR is {_ctr(perf['ctr'])} vs {_ctr(peer)} peer average"
        fact += " — above peers." if perf["ctr"] >= peer else "."
    offer, live = _offer_hook(merchant, category)
    ask = f"Want me to draft one post around {_offer_phrase(offer, live)}?" if offer else "Want me to draft one post for this week?"
    return f"{owner}, quick update on {kind}.{fact} {ask}", "binary_yes_no", f"adaptive grounded compose for kind={kind}"


COMPOSERS = {
    "research_digest": _compose_research_digest,
    "regulation_change": _compose_regulation_change,
    "cde_opportunity": _compose_cde,
    "recall_due": _compose_recall_due,
    "customer_lapsed_soft": _compose_customer_lapsed,
    "customer_lapsed_hard": _compose_customer_lapsed,
    "trial_followup": _compose_trial_followup,
    "appointment_tomorrow": _compose_appointment_tomorrow,
    "chronic_refill_due": _compose_chronic_refill,
    "wedding_package_followup": _compose_bridal,
    "perf_dip": _compose_perf_dip,
    "seasonal_perf_dip": _compose_perf_dip,
    "perf_spike": _compose_perf_spike,
    "curious_ask_due": _compose_curious_ask,
    "category_trend_movement": _compose_trend_movement,
    "festival_upcoming": _compose_festival,
    "category_seasonal": _compose_category_seasonal,
    "competitor_opened": _compose_competitor,
    "ipl_match_today": _compose_ipl,
    "milestone_reached": _compose_milestone,
    "dormant_with_vera": _compose_dormant,
    "active_planning_intent": _compose_active_planning,
    "review_theme_emerged": _compose_review_theme,
    "renewal_due": _compose_renewal,
    "winback_eligible": _compose_winback_merchant,
    "supply_alert": _compose_supply_alert,
    "gbp_unverified": _compose_gbp_unverified,
    "weather_heatwave": _compose_weather,
    "local_news_event": _compose_adaptive,
}

KEYWORD_ROUTES = [
    (("recall",), _compose_recall_due),
    (("refill", "chronic"), _compose_chronic_refill),
    (("appointment",), _compose_appointment_tomorrow),
    (("bridal", "wedding"), _compose_bridal),
    (("lapsed", "winback_customer"), _compose_customer_lapsed),
    (("research", "digest", "journal"), _compose_research_digest),
    (("regul", "compliance", "circular"), _compose_regulation_change),
    (("cde", "webinar", "training"), _compose_cde),
    (("trend",), _compose_trend_movement),
    (("curious", "ask"), _compose_curious_ask),
    (("festival", "diwali", "holi", "eid"), _compose_festival),
    (("seasonal",), _compose_category_seasonal),
    (("weather", "heat", "rain"), _compose_weather),
    (("competitor", "rival"), _compose_competitor),
    (("ipl", "match", "cricket"), _compose_ipl),
    (("spike", "surge"), _compose_perf_spike),
    (("dip", "drop", "decline"), _compose_perf_dip),
    (("milestone",), _compose_milestone),
    (("dormant", "silent", "inactive"), _compose_dormant),
    (("review",), _compose_review_theme),
    (("renewal", "expiry"), _compose_renewal),
    (("supply", "stock", "alert"), _compose_supply_alert),
    (("planning", "intent"), _compose_active_planning),
]


def _resolve_composer(kind: str):
    if kind in COMPOSERS:
        return COMPOSERS[kind]
    k = (kind or "").lower()
    for keys, fn in KEYWORD_ROUTES:
        if any(x in k for x in keys):
            return fn
    return _compose_adaptive


def _resolve_send_as(trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> str:
    if customer or trigger.get("scope") == "customer" or trigger.get("customer_id"):
        return "merchant_on_behalf"
    return "vera"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compose(
    category: Dict[str, Any],
    merchant: Dict[str, Any],
    trigger: Dict[str, Any],
    customer: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Deterministic and fast (no network). Use polish() for the optional LLM pass."""
    kind = trigger.get("kind") or "unknown"
    body, cta, rationale = _resolve_composer(kind)(category, merchant, trigger, customer, now)
    body = re.sub(r"\s{2,}", " ", body).strip()
    send_as = _resolve_send_as(trigger, customer)
    template_params = [_owner(merchant, category) if send_as == "vera" else _customer_name(customer)[0], body]
    return {
        "body": body,
        "cta": cta,
        "send_as": send_as,
        "suppression_key": trigger.get("suppression_key") or f"{kind}:{merchant.get('merchant_id')}",
        "rationale": f"{rationale} [trigger={trigger.get('id')}, kind={kind}]",
        "template_name": f"vera_{kind}_v1",
        "template_params": template_params,
    }


def trigger_expired(trigger: Dict[str, Any], now_iso: str) -> bool:
    exp, now = _parse_dt(trigger.get("expires_at")), _parse_dt(now_iso)
    return bool(exp and now and now > exp)


# ---------------------------------------------------------------------------
# Optional LLM polish (called concurrently by bot.py under a global budget)
# ---------------------------------------------------------------------------

def _num_tokens(text: str) -> set:
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def llm_enabled() -> bool:
    return bool(os.getenv("VERA_LLM_PROVIDER") and os.getenv("VERA_LLM_API_KEY"))


def polish(draft: Dict[str, Any], category: Dict[str, Any], merchant: Dict[str, Any]) -> Dict[str, Any]:
    """Rewrite for flow only. Rejected unless every number in the rewrite already appears
    in the deterministic draft (the draft is fully grounded, so this blocks fabrication)."""
    provider = (os.getenv("VERA_LLM_PROVIDER") or "").strip().lower()
    api_key = (os.getenv("VERA_LLM_API_KEY") or "").strip()
    urls = {"openai": "https://api.openai.com/v1/chat/completions",
            "groq": "https://api.groq.com/openai/v1/chat/completions"}
    if provider not in urls or not api_key:
        return draft
    voice = category.get("voice") or {}
    system = (
        "You polish one WhatsApp message from Vera (magicpin's merchant assistant). "
        "Keep EVERY fact, number, name, date, offer and source exactly as in the draft; add none. "
        "Keep the same single CTA as the last sentence. Keep the same language mix. "
        f"Tone: {voice.get('tone')}. Never use: {', '.join((voice.get('vocab_taboo') or [])[:8])}. "
        "If the draft is already good, return it unchanged. "
        'Return JSON only: {"body": "..."}'
    )
    payload = {
        "model": os.getenv("VERA_LLM_MODEL") or ("gpt-4o-mini" if provider == "openai" else "llama-3.3-70b-versatile"),
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": draft["body"]}],
    }
    try:
        req = urlrequest.Request(
            urls[provider],
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        with urlrequest.urlopen(req, timeout=float(os.getenv("VERA_LLM_TIMEOUT", "8"))) as resp:
            text = json.loads(resp.read().decode("utf-8"))["choices"][0]["message"]["content"]
        body = (json.loads(text).get("body") or "").strip()
    except Exception:
        return draft
    taboo = [str(t).lower() for t in voice.get("vocab_taboo") or []]
    if (
        not body
        or not _num_tokens(body) <= _num_tokens(draft["body"])
        or any(t and t in body.lower() for t in taboo)
        or len(body) > len(draft["body"]) * 1.3
    ):
        return draft
    return {**draft, "body": body, "rationale": draft["rationale"] + " (LLM-polished, numbers verified)"}
