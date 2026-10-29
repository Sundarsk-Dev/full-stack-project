import copy
import json
import unittest

from app.ai_contract import prepare_review, validate_response
from app.analysis import analyze


class AIContractTests(unittest.TestCase):
    def setUp(self):
        self.analysis = analyze("A \U0001f4dc heading.\n\nYour information supports our commercial partnerships.", "privacy")
        self.batches, _ = prepare_review(self.analysis, model="fixture-model", consent=True)
        self.batch = self.batches[0]
        self.item = {"clause_id": 2, "category": 1, "rating": "concerning", "severity": 3,
            "quote": "Your information supports our commercial partnerships.", "explanation": "The commercial use needs review.",
            "uncertain": False, "concern": "other"}

    def validate(self, **changes):
        item = dict(self.item, **changes)
        return validate_response(json.dumps({"findings": [item]}), self.batch, self.analysis)

    def test_no_consent_means_no_payloads(self):
        for consent in [False, None, "true"]:
            batches, reasons = prepare_review(self.analysis, model="fixture-model", consent=consent)
            self.assertFalse(batches)
            self.assertTrue(reasons)

    def test_budget_and_context_limits(self):
        policy = "\n\n".join(f"Unfamiliar clause number {i}." for i in range(20))
        batches, reasons = prepare_review(analyze(policy, "terms"), model="fixture-model", consent=True)
        self.assertLessEqual(len(batches), 2)
        self.assertLessEqual(sum(len(b.clause_ids) for b in batches), 12)
        self.assertTrue(all(len(b.clause_ids) <= 6 for b in batches))
        self.assertLessEqual(sum(b.input_reservation for b in batches), 10000)
        self.assertLessEqual(sum(b.output_reservation for b in batches), 2000)
        self.assertIn("analysis_budget_excluded", reasons.values())

    def test_oversized_context_is_not_truncated(self):
        result = analyze("Private context " * 1000 + "unless an exception applies.", "terms")
        batches, reasons = prepare_review(result, model="fixture-model", consent=True)
        self.assertFalse(batches)
        self.assertIn("adequate_context_does_not_fit", reasons.values())

    def test_definitions_and_exceptions_are_in_context(self):
        analysis = analyze("Personal data means billing details.\n\nA heading.\n\nAnother heading.\n\nYour information supports our commercial partnerships.\n\nHowever, exceptions apply.", "privacy")
        batches, _ = prepare_review(analysis, model="fixture-model", consent=True)
        for batch in batches:
            user = json.loads(batch.payload["messages"][1]["content"])
            context = " ".join(p["text"] for p in user["context"])
            self.assertIn("Personal data means billing details.", context)
            self.assertIn("However, exceptions apply.", context)

    def test_accepted_quote_uses_unicode_codepoint_offsets(self):
        accepted, _ = self.validate()
        self.assertEqual(len(accepted), 1)
        finding = accepted[0]
        self.assertEqual(self.analysis["text"][finding["start"]:finding["end"]], finding["quote"])
        self.assertEqual(finding["source"], "ai")

    def test_uncertain_invalid_and_injected_responses_abstain(self):
        for changes in [{"uncertain": True}, {"severity": 5}, {"quote": "Fabricated evidence"},
                        {"clause_id": 999}, {"category": True}, {"concern": "mandatory_arbitration"}, {"grade": "A"}]:
            with self.subTest(changes=changes):
                accepted, reasons = self.validate(**changes)
                self.assertFalse(accepted)
                self.assertTrue(reasons)

    def test_conflicting_duplicate_cannot_be_partly_accepted(self):
        items = [self.item, dict(self.item, rating="invasive", severity=5)]
        accepted, reasons = validate_response(json.dumps({"findings": items}), self.batch, self.analysis)
        self.assertFalse(accepted)
        self.assertEqual(reasons[2], "duplicate_or_conflicting_findings")

    def test_changed_context_invalidates_reply(self):
        changed = copy.deepcopy(self.analysis)
        changed["text"] += " Except that this never applies."
        accepted, reasons = validate_response(json.dumps({"findings": [self.item]}), self.batch, changed)
        self.assertFalse(accepted)
        self.assertEqual(set(reasons.values()), {"context_changed"})

    def test_critical_concern_keeps_provenance_for_code_caps(self):
        accepted, _ = self.validate(concern="data_sale", rating="invasive", severity=5)
        self.assertEqual(accepted[0]["rule_id"], "sell-data")
        self.assertEqual(accepted[0]["source"], "ai")
