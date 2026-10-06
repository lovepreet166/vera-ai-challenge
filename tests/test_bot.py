"""Run: python -m unittest discover -s tests   (needs fastapi + httpx)."""
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import bot  # noqa: E402
import composer  # noqa: E402
from state import store  # noqa: E402

DATA = ROOT / "dataset"


def load(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


class VeraTests(unittest.TestCase):
    def setUp(self):
        store.clear()
        self.c = TestClient(bot.app)
        for f in (DATA / "categories").glob("*.json"):
            cat = json.loads(f.read_text(encoding="utf-8"))
            self.push("category", cat["slug"], cat)
        for m in load("merchants_seed.json")["merchants"]:
            self.push("merchant", m["merchant_id"], m)
        for cu in load("customers_seed.json")["customers"]:
            self.push("customer", cu["customer_id"], cu)
        self.triggers = load("triggers_seed.json")["triggers"]
        for t in self.triggers:
            self.push("trigger", t["id"], t)

    def push(self, scope, cid, payload, version=1):
        return self.c.post("/v1/context", json={"scope": scope, "context_id": cid, "version": version,
                                                "payload": payload, "delivered_at": "2026-04-26T10:00:00Z"})

    def tick(self, ids=None):
        return self.c.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z",
                                             "available_triggers": ids or [t["id"] for t in self.triggers]}).json()["actions"]

    def reply(self, action, msg, turn=2, role="merchant"):
        return self.c.post("/v1/reply", json={"conversation_id": action["conversation_id"], "merchant_id": action["merchant_id"],
                                              "customer_id": action["customer_id"], "from_role": role, "message": msg,
                                              "received_at": "2026-04-26T10:05:00Z", "turn_number": turn}).json()

    def action_for(self, trigger_id):
        return self.tick([trigger_id])[0]

    # --- contract -----------------------------------------------------------
    def test_context_idempotent_and_stale(self):
        m = load("merchants_seed.json")["merchants"][0]
        self.assertTrue(self.push("merchant", m["merchant_id"], m).json()["accepted"])
        r = self.push("merchant", m["merchant_id"], m, version=0)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.c.get("/v1/healthz").json()["contexts_loaded"]["trigger"], 25)

    # --- composition --------------------------------------------------------
    def test_no_broken_or_fabricated_text(self):
        merchants = {m["merchant_id"]: m for m in load("merchants_seed.json")["merchants"]}
        actions = self.tick() + self.tick() + self.tick() + self.tick()
        self.assertEqual(len(actions), 25)
        for a in actions:
            body = a["body"]
            self.assertNotIn("None", body, a["trigger_id"])
            live = [o["title"] for o in merchants[a["merchant_id"]]["offers"] if o["status"] == "active"]
            if not live:
                self.assertNotIn("your live", body, f"{a['trigger_id']} claims a live offer the merchant doesn't have")

    def test_pharmacist_not_called_doctor(self):
        a = self.action_for("trg_018_supply_atorvastatin_recall")
        self.assertTrue(a["body"].startswith("Ramesh,"), a["body"])

    def test_competitor_named_from_context_with_price_gap(self):
        body = self.action_for("trg_023_competitor_opened_dentist")["body"]
        self.assertIn("Smile Studio", body)
        self.assertIn("₹199", body)

    def test_customer_language_and_send_as(self):
        a = self.action_for("trg_003_recall_due_priya")
        self.assertEqual(a["send_as"], "merchant_on_behalf")
        self.assertIn("Wed 5 Nov, 6pm", a["body"])
        self.assertIn("hai", a["body"])  # hi-en mix honoured

    def test_placeholder_payload_never_breaks(self):
        cat = json.loads((DATA / "categories" / "salons.json").read_text(encoding="utf-8"))
        m = load("merchants_seed.json")["merchants"][2]
        for kind in composer.COMPOSERS:
            out = composer.compose(cat, m, {"id": "x", "kind": kind, "payload": {"placeholder": True}})
            self.assertTrue(out["body"] and "None" not in out["body"], kind)

    # --- multi-turn ---------------------------------------------------------
    def test_auto_reply_hell_exits(self):
        a = self.action_for("trg_001_research_digest_dentists")
        canned = "Thank you for contacting us! Our team will get back to you shortly."
        self.assertEqual(self.reply(a, canned, 2)["action"], "send")
        self.assertEqual(self.reply(a, canned, 3)["action"], "end")

    def test_join_intent_goes_to_action(self):
        a = self.action_for("trg_005_renewal_due_bharat")
        r = self.reply(a, "Mujhe magicpin judna hai")
        self.assertEqual(r["action"], "send")
        self.assertIn("setup", r["body"].lower())

    def test_hostile_then_off_topic_stays_polite(self):
        a = self.action_for("trg_002_compliance_dci_radiograph")
        self.assertEqual(self.reply(a, "This is useless spam, stop wasting my time", 2)["action"], "send")
        r = self.reply(a, "can you also help me file my GST?", 3)
        self.assertEqual(r["action"], "send")
        self.assertIn("CA", r["body"])

    def test_bare_stop_ends(self):
        a = self.action_for("trg_004_perf_dip_bharat")
        self.assertEqual(self.reply(a, "STOP")["action"], "end")

    def test_busy_waits_and_no_ends(self):
        a = self.action_for("trg_004_perf_dip_bharat")
        self.assertEqual(self.reply(a, "not now, busy")["action"], "wait")
        b = self.action_for("trg_012_milestone_mylari")
        self.assertEqual(self.reply(b, "No")["action"], "end")

    def test_question_is_not_treated_as_yes(self):
        a = self.action_for("trg_023_competitor_opened_dentist")
        r = self.reply(a, "What do you do exactly?")
        self.assertIn("Vera", r["body"])

    def test_business_hours_request_is_not_auto_reply(self):
        a = self.action_for("trg_023_competitor_opened_dentist")
        r = self.reply(a, "Can you update my business hours to 10am-9pm?")
        self.assertEqual(r["action"], "send")
        self.assertIn("10am-9pm", r["body"])

    def test_yes_delivers_real_draft_then_closes(self):
        a = self.action_for("trg_001_research_digest_dentists")
        self.assertIn("draft", self.reply(a, "yes please", 2)["body"].lower())
        self.assertEqual(self.reply(a, "confirm", 3)["action"], "send")
        self.assertEqual(self.reply(a, "thanks", 4)["action"], "end")

    def test_customer_slot_choice(self):
        a = self.action_for("trg_003_recall_due_priya")
        r = self.reply(a, "2", role="customer")
        self.assertIn("Thu 6 Nov, 5pm", r["body"])

    # --- latency ------------------------------------------------------------
    def test_tick_respects_budget_with_slow_llm(self):
        def slow(draft, *_):
            time.sleep(5)
            return draft

        with mock.patch.dict(os.environ, {"VERA_LLM_PROVIDER": "groq", "VERA_LLM_API_KEY": "x"}), \
             mock.patch.object(bot, "polish", slow), mock.patch.object(bot, "TICK_BUDGET_S", 1.0):
            t0 = time.time()
            actions = self.tick()
            self.assertLess(time.time() - t0, 3.0)
            self.assertTrue(actions)


if __name__ == "__main__":
    unittest.main()
