"""Coordinator checks use synthetic responses and durable local fixture storage."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import func, select

from app.ai_ledger import LedgerBlocked
from app.ai_review import ProviderReply, review_job
from app.db import utcnow
from app.models import AICall, Job
from tests.test_ai_ledger import AILedgerFixture, TEXT


def supported_reply(payload):
    clauses = json.loads(payload["messages"][1]["content"])["clauses"]
    findings = [{"clause_id": c["clause_id"], "category": 4, "rating": "concerning", "severity": 3,
                 "quote": c["text"], "explanation": "Synthetic review finding.", "uncertain": False, "concern": "other"}
                for c in clauses]
    return ProviderReply("received", json.dumps({"findings": findings}), (600, 120))


class FakeTransport:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def send(self, payload, *, timeout_seconds):
        self.calls.append((deepcopy(payload), timeout_seconds))
        reply = self.replies.pop(0) if self.replies else supported_reply
        if isinstance(reply, BaseException): raise reply
        return reply(payload) if callable(reply) else reply


class ReviewTests(AILedgerFixture):
    policy_text = "We sell your personal data to advertisers.\n\n" + TEXT

    def review(self, transport, **options):
        return review_job(self.factory, *self.claimed, policy=options.get("policy", self.policy), transport=transport)

    def test_valid_review_merges_scores_and_keeps_rules_baseline_immutable(self):
        transport = FakeTransport()
        outcome = self.review(transport)
        self.assertEqual(len(transport.calls), 2)
        self.assertTrue(all(0 < timeout <= 10 for _, timeout in transport.calls))
        self.assertEqual(outcome.effective_mode, "hybrid")
        self.assertEqual(outcome.status, "limited")  # Two of fourteen candidates do not fit the clause budget.
        self.assertEqual(outcome.accepted_ai_findings, 12)
        self.assertEqual(outcome.unresolved_count, 2)
        self.assertEqual(outcome.baseline, self.analysis)
        self.assertEqual(outcome.result["clauses"][0], self.analysis["clauses"][0])
        self.assertAlmostEqual(outcome.result["score"]["coverage"], 13 / 15)
        self.assertEqual(outcome.result["score"]["best"], 74)
        self.assertEqual(len(outcome.provenance), 2)
        for clause in outcome.result["clauses"]:
            for finding in clause["findings"]:
                self.assertNotIn("clause_id", finding)  # publish supplies the database clause ID.
                self.assertEqual(self.policy_text[finding["start"]:finding["end"]], finding["quote"])
        outcome.result["clauses"][1]["findings"].clear()
        self.assertEqual(outcome.baseline, self.analysis)

    def test_rules_only_skips_provider_and_all_ai_reservations(self):
        with self.factory.begin() as db: db.get(Job, self.job_id).requested_mode = "rules_only"
        transport = FakeTransport()
        with patch("app.ai_ledger.reserve", side_effect=AssertionError("rules-only must not reserve")):
            outcome = self.review(transport)
        self.assertEqual(outcome.status, "not_requested")
        self.assertEqual(outcome.result, self.analysis)
        self.assertFalse(transport.calls)
        with self.factory() as db: self.assertEqual(db.scalar(select(func.count()).select_from(AICall)), 0)

    def test_missing_consent_limits_or_provider_degrades_without_calls(self):
        transport = FakeTransport()
        with self.factory.begin() as db: db.get(Job, self.job_id).consent = False
        self.assertEqual(self.review(transport).degradation_reason, "consent_required")
        with self.factory.begin() as db: db.get(Job, self.job_id).consent = True
        self.assertEqual(self.review(transport, policy=None).degradation_reason, "unverified_quota")
        self.assertEqual(self.review(None).degradation_reason, "provider_unavailable")
        expired = replace(self.policy, valid_until=utcnow() - timedelta(seconds=1))
        self.assertEqual(self.review(transport, policy=expired).degradation_reason, "unverified_quota")
        self.assertFalse(transport.calls)

    def test_second_batch_failure_discards_ai_findings_and_preserves_private_errors(self):
        private = "private policy and provider key"
        transport = FakeTransport(supported_reply, TimeoutError(private))
        with self.assertLogs("termslens.events", level="INFO") as logs:
            outcome = self.review(transport)
        self.assertEqual(outcome.effective_mode, "rules_only")
        self.assertEqual(outcome.status, "degraded")
        self.assertEqual(outcome.degradation_reason, "provider_outcome_unknown")
        self.assertEqual(outcome.accepted_ai_findings, 0)
        self.assertEqual(outcome.result["score"], self.analysis["score"])
        self.assertEqual(outcome.unresolved_count, 14)
        self.assertEqual(len(transport.calls), 2)
        self.assertNotIn(private, " ".join(logs.output))
        self.assertNotIn(private, outcome.degradation_message)
        with self.factory() as db:
            calls = list(db.scalars(select(AICall).order_by(AICall.attempt_number)))
            self.assertEqual([c.state for c in calls], ["received", "unknown"])
            self.assertTrue(calls[0].response_text)
            self.assertIsNone(calls[1].response_text)
            self.assertIn(calls[0].id, " ".join(logs.output))

    def test_completed_responses_recover_without_provider_or_new_budget(self):
        transport = FakeTransport()
        first = self.review(transport)
        self.restart()
        reopened = self.review(None, policy=None)
        self.assertEqual(first, reopened)
        self.assertEqual(len(transport.calls), 2)
        with self.factory() as db: self.assertEqual(db.scalar(select(func.count()).select_from(AICall)), 2)

    def test_process_crash_at_dispatch_is_unknown_and_never_resent(self):
        class PowerLoss(BaseException): pass
        transport = FakeTransport(PowerLoss())
        with self.assertRaises(PowerLoss): self.review(transport)
        self.restart()
        outcome = self.review(transport)
        self.assertEqual(outcome.degradation_reason, "provider_outcome_unknown")
        self.assertEqual(len(transport.calls), 1)

    def test_reserved_but_unsent_attempt_is_not_replayed(self):
        self.reserve()
        self.restart()
        transport = FakeTransport()
        self.assertEqual(self.review(transport).degradation_reason, "provider_outcome_unknown")
        self.assertFalse(transport.calls)

    def test_crash_after_response_commit_reuses_it_before_finishing_next_batch(self):
        transport = FakeTransport()
        with patch("app.ai_review.validate_response", side_effect=RuntimeError("interrupted before interpretation")):
            with self.assertRaises(RuntimeError): self.review(transport)
        with self.factory() as db:
            self.assertEqual(db.scalar(select(AICall.state)), "received")
        self.restart()
        outcome = self.review(transport)
        self.assertEqual(outcome.accepted_ai_findings, 12)
        self.assertEqual(len(transport.calls), 2)

    def test_one_retry_consumes_second_batch_capacity(self):
        transport = FakeTransport(ProviderReply("rate_limited"), supported_reply)
        outcome = self.review(transport)
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(outcome.effective_mode, "hybrid")
        self.assertEqual(outcome.accepted_ai_findings, 6)
        self.assertEqual(outcome.unresolved_count, 8)
        self.assertIn("analysis_budget_excluded", [c["unrated_reason"] for c in outcome.result["clauses"]])
        with self.factory() as db:
            attempts = list(db.scalars(select(AICall).order_by(AICall.attempt_number)))
            self.assertEqual(attempts[1].retry_of, attempts[0].id)

    def test_short_retry_after_is_honored_and_long_wait_degrades(self):
        moment = [utcnow()]
        waits = []

        def advance(seconds):
            waits.append(seconds)
            moment[0] += timedelta(seconds=seconds)

        transport = FakeTransport(ProviderReply("rate_limited", retry_after_seconds=1), ProviderReply("rate_limited", retry_after_seconds=30))
        with patch("app.ai_review.utcnow", side_effect=lambda: moment[0]), patch("app.ai_ledger.utcnow", side_effect=lambda: moment[0]), patch("app.ai_review.sleep", side_effect=advance):
            outcome = self.review(transport)
        self.assertEqual(waits, [1])
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(outcome.degradation_reason, "provider_rate_limited")

    def test_retry_after_beyond_wait_limit_does_not_retry(self):
        transport = FakeTransport(ProviderReply("rate_limited", retry_after_seconds=30))
        with patch("app.ai_review.sleep", side_effect=AssertionError("must not wait this long")):
            outcome = self.review(transport)
        self.assertEqual(outcome.degradation_reason, "provider_rate_limited")
        self.assertEqual(len(transport.calls), 1)

    def test_late_reply_keeps_unknown_outcome_without_accepting_findings(self):
        transport = FakeTransport()
        with patch("app.ai_review.monotonic", side_effect=[0, 11]):
            outcome = self.review(transport)
        self.assertEqual(outcome.degradation_reason, "provider_outcome_unknown")
        self.assertEqual(len(transport.calls), 1)
        with self.factory() as db:
            call = db.scalar(select(AICall))
            self.assertEqual(call.state, "unknown")
            self.assertIsNone(call.response_text)
            self.assertIsNone(call.input_tokens)

    def test_invalid_model_output_abstains_without_repair_requests(self):
        invalid = ProviderReply("received", '{"findings": [], "grade": "A"}')
        transport = FakeTransport(invalid, invalid)
        outcome = self.review(transport)
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(outcome.accepted_ai_findings, 0)
        self.assertEqual(outcome.unresolved_count, 14)
        self.assertEqual(outcome.effective_mode, "hybrid")
        self.assertIsNone(outcome.degradation_reason)
        self.assertIn("invalid_response_schema", [c["unrated_reason"] for c in outcome.result["clauses"]])

    def test_uncertainty_remains_unrated_with_other_valid_findings(self):
        def uncertain(payload):
            reply = supported_reply(payload)
            data = json.loads(reply.text)
            data["findings"][0].update(uncertain=True, rating=None, severity=None)
            return replace(reply, text=json.dumps(data))

        outcome = self.review(FakeTransport(uncertain))
        self.assertEqual(outcome.accepted_ai_findings, 11)
        self.assertEqual(outcome.unresolved_count, 3)
        self.assertEqual(outcome.result["clauses"][1]["unrated_reason"], "model_uncertain")

    def test_quota_exhaustion_after_usable_reply_returns_rules_only(self):
        limited = replace(self.policy, limits=tuple(replace(q, capacity=1) if q.resource == "requests" else q for q in self.policy.limits))
        transport = FakeTransport()
        outcome = self.review(transport, policy=limited)
        self.assertEqual(outcome.degradation_reason, "quota_exhausted")
        self.assertEqual(outcome.accepted_ai_findings, 0)
        self.assertEqual(outcome.result["score"], self.analysis["score"])
        self.assertEqual(len(transport.calls), 1)

    def test_changed_model_cannot_reuse_or_resend_previous_review(self):
        transport = FakeTransport()
        self.review(transport)
        changed = self.review(transport, policy=replace(self.policy, model="different-model"))
        self.assertEqual(changed.degradation_reason, "analysis_changed")
        self.assertEqual(changed.accepted_ai_findings, 0)
        self.assertEqual(len(transport.calls), 2)

    def test_lost_lease_cannot_return_publishable_review(self):
        def replace_lease(payload):
            with self.factory.begin() as db: db.get(Job, self.job_id).lease_token = str(uuid4())
            return supported_reply(payload)

        with self.assertRaisesRegex(LedgerBlocked, "job_lease_lost"):
            self.review(FakeTransport(replace_lease))
        with self.factory() as db: self.assertEqual(db.scalar(select(AICall.state)), "sending")

    def test_reported_usage_overrun_stops_further_review(self):
        def overrun(payload): return replace(supported_reply(payload), usage=(99999, 10))
        transport = FakeTransport(overrun)
        outcome = self.review(transport)
        self.assertEqual(outcome.degradation_reason, "usage_exceeded")
        self.assertEqual(len(transport.calls), 1)


class NoReviewNeededTests(AILedgerFixture):
    policy_text = "We sell your personal data to advertisers."

    def test_fully_rated_text_needs_no_provider(self):
        transport = FakeTransport()
        outcome = review_job(self.factory, *self.claimed, transport=transport)
        self.assertEqual(outcome.status, "not_needed")
        self.assertEqual(outcome.effective_mode, "rules_only")
        self.assertEqual(outcome.unresolved_count, 0)
        self.assertIsNone(outcome.degradation_reason)
        self.assertFalse(transport.calls)
