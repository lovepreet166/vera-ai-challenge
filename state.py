"""In-memory context + conversation state for the Vera challenge bot."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import threading
import time
import uuid


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class StoredContext:
    scope: str
    context_id: str
    version: int
    payload: Dict[str, Any]
    delivered_at: str
    stored_at: str


@dataclass
class ConversationState:
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    trigger_id: Optional[str] = None
    trigger_kind: Optional[str] = None
    mode: str = "qualifying"  # qualifying | action | ended
    turns: List[Dict[str, Any]] = field(default_factory=list)
    auto_reply_hits: int = 0
    action_sends: int = 0
    last_bot_body: Optional[str] = None
    started_at: str = field(default_factory=utc_now_iso)


class Store:
    """Thread-safe in-memory store. Fine for the challenge harness."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.started_at = time.time()
        # (scope, context_id) -> StoredContext
        self.contexts: Dict[Tuple[str, str], StoredContext] = {}
        self.conversations: Dict[str, ConversationState] = {}
        # suppression_key -> last used iso timestamp
        self.suppressions: Dict[str, str] = {}

    def uptime_seconds(self) -> int:
        return int(time.time() - self.started_at)

    def context_counts(self) -> Dict[str, int]:
        counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
        with self._lock:
            for (scope, _), _ in self.contexts.items():
                if scope in counts:
                    counts[scope] += 1
        return counts

    def put_context(
        self,
        scope: str,
        context_id: str,
        version: int,
        payload: Dict[str, Any],
        delivered_at: str,
    ) -> Dict[str, Any]:
        key = (scope, context_id)
        with self._lock:
            existing = self.contexts.get(key)
            if existing is not None and existing.version > version:
                return {
                    "accepted": False,
                    "reason": "stale_version",
                    "current_version": existing.version,
                }
            if existing is not None and existing.version == version:
                # Idempotent no-op for same version
                return {
                    "accepted": True,
                    "ack_id": f"ack_{context_id}_v{version}",
                    "stored_at": existing.stored_at,
                }

            stored_at = utc_now_iso()
            self.contexts[key] = StoredContext(
                scope=scope,
                context_id=context_id,
                version=version,
                payload=payload,
                delivered_at=delivered_at,
                stored_at=stored_at,
            )
            return {
                "accepted": True,
                "ack_id": f"ack_{context_id}_v{version}",
                "stored_at": stored_at,
            }

    def get(self, scope: str, context_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not context_id:
            return None
        with self._lock:
            stored = self.contexts.get((scope, context_id))
            return None if stored is None else stored.payload

    def get_category_for_merchant(self, merchant: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        slug = merchant.get("category_slug")
        return self.get("category", slug) if slug else None

    def list_triggers(self, trigger_ids: List[str]) -> List[Tuple[str, Dict[str, Any]]]:
        out: List[Tuple[str, Dict[str, Any]]] = []
        with self._lock:
            for tid in trigger_ids:
                stored = self.contexts.get(("trigger", tid))
                if stored is not None:
                    out.append((tid, stored.payload))
        return out

    def is_suppressed(self, key: str) -> bool:
        with self._lock:
            return key in self.suppressions

    def mark_suppressed(self, key: str) -> None:
        if not key:
            return
        with self._lock:
            self.suppressions[key] = utc_now_iso()

    def get_or_create_conversation(
        self,
        conversation_id: str,
        merchant_id: Optional[str] = None,
        customer_id: Optional[str] = None,
        trigger_id: Optional[str] = None,
        trigger_kind: Optional[str] = None,
    ) -> ConversationState:
        with self._lock:
            conv = self.conversations.get(conversation_id)
            if conv is None:
                conv = ConversationState(
                    conversation_id=conversation_id,
                    merchant_id=merchant_id,
                    customer_id=customer_id,
                    trigger_id=trigger_id,
                    trigger_kind=trigger_kind,
                )
                self.conversations[conversation_id] = conv
            else:
                if merchant_id:
                    conv.merchant_id = merchant_id
                if customer_id:
                    conv.customer_id = customer_id
                if trigger_id:
                    conv.trigger_id = trigger_id
                if trigger_kind:
                    conv.trigger_kind = trigger_kind
            return conv

    def new_conversation_id(self, merchant_id: str, trigger_id: str) -> str:
        return f"conv_{merchant_id}_{trigger_id}_{uuid.uuid4().hex[:8]}"

    def clear(self) -> None:
        with self._lock:
            self.contexts.clear()
            self.conversations.clear()
            self.suppressions.clear()


store = Store()
