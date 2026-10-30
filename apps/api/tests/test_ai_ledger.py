"""Durable F5 behavior with a fake provider; SQLite does not prove MySQL locks."""
from dataclasses import replace
from datetime import timedelta
import json

from sqlalchemy import func, select

from app.ai_contract import prepare_review, validate_response
from app.ai_ledger import LedgerBlocked, QuotaLimit, QuotaPolicy, begin_dispatch, digest, finish, recover, reserve, saved_response
from app.analysis import analyze
from app.db import utcnow
from app.diagnostics import inspect_job
from app.models import AIBudgetWindow, AICall, Document, DocumentVersion, Job
from app.worker import capture, claim
from tests.test_worker import WorkerFixture

TEXT = "\n\n".join(f"Your information supports partnership number {i}." for i in range(14))


class AILedgerFixture(WorkerFixture):
    policy_text = TEXT

    def setUp(self):
        super().setUp()
        with self.factory.begin() as db:
            job = db.get(Job, self.job_id)
            job.input_text, job.requested_mode, job.consent = self.policy_text, "hybrid", True
            job.document_type = "terms"
            job.consent_provider, job.consent_at = "groq", utcnow()
        self.claimed = claim(self.factory)
        capture(self.factory, *self.claimed)
        self.analysis = analyze(self.policy_text, "terms")
        self.batches, _ = prepare_review(self.analysis, model="fixture-model", consent=True)
        self.policy = QuotaPolicy(digest("fixture-account"), "fixture-model", utcnow() + timedelta(hours=1), (
            QuotaLimit("model_requests_minute", "requests", 60, 10),
            QuotaLimit("model_requests_day", "requests", 86400, 100),
            QuotaLimit("model_tokens_minute", "total_tokens", 60, 100000),
        ))
        self.sent = []

    def reserve(self, index=0, **changes):
        options = dict(policy=self.policy)
        options.update(changes)
        return reserve(self.factory, *self.claimed, self.analysis, self.batches[index], **options)

    def send(self, call_id, **changes):
        permit = begin_dispatch(self.factory, *self.claimed, call_id)
        self.sent.append(permit.payload)
        options = dict(outcome="received", response_text='{"findings": []}', usage=(100, 20))
        options.update(changes)
        finish(self.factory, *self.claimed, call_id, **options)

    def restart(self):
        self.engine.dispose()
        with self.factory.begin() as db:
            db.get(Job, self.job_id).lease_expires_at = utcnow() - timedelta(seconds=1)
        self.claimed = claim(self.factory)
        capture(self.factory, *self.claimed)
        return recover(self.factory, *self.claimed)

    def assert_blocked(self, code, action):
        with self.assertRaises(LedgerBlocked) as error: action()
        self.assertEqual(str(error.exception), code)


class LargerBatchLedgerTests(AILedgerFixture):
    policy_text = "Renewal information " * 95 + "."

    def test_retry_cannot_exceed_total_even_after_restart(self):
        self.assertGreater(self.batches[0].input_reservation, 5000)
        first = self.reserve()
        self.send(first, outcome="rate_limited", usage=None)
        self.restart()
        self.assert_blocked("analysis_budget_exhausted", lambda: self.reserve(retry_of=first))
        with self.factory() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(AICall)), 1)

    def test_account_limit_still_overrides_request_allowance(self):
        policy = replace(self.policy, limits=self.policy.limits[:2] + (
            QuotaLimit("model_tokens_minute", "total_tokens", 60, 6000),))
        self.assert_blocked("quota_exhausted", lambda: self.reserve(policy=policy))
        self.assertFalse(self.sent)


class AILedgerTests(AILedgerFixture):

    def test_unknown_expired_or_incomplete_quotas_make_no_reservation(self):
        for policy in [None, replace(self.policy, valid_until=utcnow()), replace(self.policy, limits=()),
                       replace(self.policy, limits=self.policy.limits[:1])]:
            self.assert_blocked("unverified_quota", lambda: self.reserve(policy=policy))
        with self.factory() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(AICall)), 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(AIBudgetWindow)), 0)
        self.assertFalse(self.sent)

    def test_rules_only_or_missing_consent_makes_no_call(self):
        for mode, consent in [("rules_only", True), ("hybrid", False)]:
            with self.factory.begin() as db:
                job = db.get(Job, self.job_id)
                job.requested_mode, job.consent = mode, consent
            self.assert_blocked("consent_required", self.reserve)
        self.assertFalse(self.sent)

    def test_reservation_response_and_reconciliation_are_durable(self):
        call_id = self.reserve()
        with self.factory() as db:
            call = db.get(AICall, call_id)
            self.assertEqual(call.state, "reserved")
            self.assertEqual(call.request_payload, self.batches[0].payload)
            self.assertEqual(len(call.budget_charges), 3)
        self.send(call_id)
        self.restart()
        batch, raw = saved_response(self.factory, *self.claimed, call_id, self.analysis)
        self.assertEqual(batch, self.batches[0])
        self.assertEqual(validate_response(raw, batch, self.analysis)[0], [])
        with self.factory() as db:
            windows = list(db.scalars(select(AIBudgetWindow)))
            self.assertTrue(all(w.used == (1 if w.resource == "requests" else 120) for w in windows))
        self.assert_blocked("dispatch_not_allowed", lambda: begin_dispatch(self.factory, *self.claimed, call_id))
        self.assertEqual(len(self.sent), 1)

    def test_crash_before_or_after_dispatch_never_resends(self):
        call_id = self.reserve()
        # A persisted dispatch marker cannot prove whether the network send happened.
        begin_dispatch(self.factory, *self.claimed, call_id)
        old_claim = self.claimed
        self.assertEqual(self.restart()[0]["state"], "unknown")
        self.assert_blocked("previous_attempt_unresolved", lambda: self.reserve(1))
        self.assert_blocked("dispatch_not_allowed", lambda: begin_dispatch(self.factory, *self.claimed, call_id))
        self.assert_blocked("job_lease_lost", lambda: finish(self.factory, *old_claim, call_id, outcome="received", response_text="{}"))
        with self.factory() as db:
            call = db.get(AICall, call_id)
            for charge in call.budget_charges:
                self.assertEqual(db.get(AIBudgetWindow, charge["id"]).used, charge["amount"])

    def test_reserved_but_unsent_attempt_is_not_replayed_on_recovery(self):
        self.reserve()
        self.assertEqual(self.restart()[0]["state"], "unknown")
        self.assert_blocked("previous_attempt_unresolved", lambda: self.reserve(1))
        self.assertFalse(self.sent)

    def test_expired_dispatch_deadline_cannot_send(self):
        call_id = self.reserve()
        with self.factory.begin() as db:
            db.get(AICall, call_id).dispatch_before = utcnow() - timedelta(seconds=1)
        self.assert_blocked("dispatch_not_allowed", lambda: begin_dispatch(self.factory, *self.claimed, call_id))
        self.assertEqual(recover(self.factory, *self.claimed)[0]["state"], "unknown")

    def test_unknown_outcome_cannot_refund_reported_zero_usage(self):
        call_id = self.reserve()
        self.send(call_id, outcome="unknown", usage=(0, 0))
        with self.factory() as db:
            call = db.get(AICall, call_id)
            for charge in call.budget_charges:
                self.assertEqual(db.get(AIBudgetWindow, charge["id"]).used, charge["amount"])
        self.assert_blocked("previous_attempt_unresolved", lambda: self.reserve(1))

    def test_retry_shares_two_request_ceiling_across_job_restarts(self):
        first = self.reserve()
        self.send(first, outcome="rate_limited", usage=None)
        self.restart()
        second = self.reserve(retry_of=first)
        self.send(second)
        self.assert_blocked("analysis_budget_exhausted", lambda: self.reserve(1))
        with self.factory() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(AICall)), 2)
        self.assertEqual(len(self.sent), 2)

    def test_retry_requires_known_rejection_and_respects_retry_after(self):
        first = self.reserve()
        self.send(first, outcome="rate_limited", retry_after_seconds=30)
        self.assert_blocked("retry_not_due", lambda: self.reserve(retry_of=first))
        self.assert_blocked("batch_already_attempted", self.reserve)
        self.assert_blocked("retry_not_allowed", lambda: self.reserve(1, retry_of=first))

    def test_every_quota_must_fit_and_failed_reservation_rolls_back(self):
        first = self.reserve()
        self.send(first)
        with self.factory() as db:
            before = {w.id: w.used for w in db.scalars(select(AIBudgetWindow))}
        tight = replace(self.policy, limits=tuple(replace(q, capacity=1) if q.name.endswith("day") else q for q in self.policy.limits))
        self.assert_blocked("quota_exhausted", lambda: self.reserve(1, policy=tight))
        with self.factory() as db:
            self.assertEqual({w.id: w.used for w in db.scalars(select(AIBudgetWindow))}, before)
            self.assertEqual(db.scalar(select(func.count()).select_from(AICall)), 1)

    def test_expired_generation_does_not_refund_new_usage(self):
        first = self.reserve()
        begin_dispatch(self.factory, *self.claimed, first)
        with self.factory.begin() as db:
            for window in db.scalars(select(AIBudgetWindow)):
                window.generation, window.used = "new-generation", 7
        finish(self.factory, *self.claimed, first, outcome="received", response_text="{}", usage=(0, 0))
        with self.factory() as db:
            self.assertTrue(all(w.used == 7 for w in db.scalars(select(AIBudgetWindow))))

    def test_limit_increase_cannot_reset_active_capacity(self):
        low = replace(self.policy, limits=tuple(replace(q, capacity=1) if q.resource == "requests" else q for q in self.policy.limits))
        call_id = self.reserve(policy=low)
        self.send(call_id)
        self.assert_blocked("quota_exhausted", lambda: self.reserve(1))
        with self.factory.begin() as db:
            for window in db.scalars(select(AIBudgetWindow)):
                window.expires_at = utcnow() - timedelta(seconds=1)
        self.assertIsNotNone(self.reserve(1))

    def test_unknown_usage_retains_capacity_and_overrun_blocks_more_calls(self):
        first = self.reserve()
        self.send(first, usage=(-1, 0))
        with self.factory() as db:
            call = db.get(AICall, first)
            self.assertIsNone(call.input_tokens)
            for charge in call.budget_charges:
                self.assertEqual(db.get(AIBudgetWindow, charge["id"]).used, charge["amount"])
        second = self.reserve(1)
        self.send(second, usage=(99999, 10))
        self.assert_blocked("previous_attempt_unresolved", self.reserve)
        with self.factory() as db: self.assertEqual(db.get(AICall, second).error_code, "usage_exceeded")

    def test_changed_context_tampered_batch_and_wrong_owner_abstain(self):
        tampered = replace(self.batches[0], input_reservation=1)
        self.assert_blocked("batch_changed", lambda: reserve(self.factory, *self.claimed, self.analysis, tampered, policy=self.policy))
        first = self.reserve()
        self.send(first)
        changed = dict(self.analysis, engine=dict(self.analysis["engine"], rules="future-rules"))
        batch = prepare_review(changed, model=self.policy.model, consent=True)[0][1]
        self.assert_blocked("context_changed", lambda: reserve(self.factory, *self.claimed, changed, batch, policy=self.policy))
        self.assert_blocked("context_changed", lambda: saved_response(self.factory, *self.claimed, first, changed))
        with self.factory.begin() as db:
            version = db.get(DocumentVersion, db.get(Job, self.job_id).version_id)
            db.get(Document, version.document_id).owner_id = self.bob
        self.assert_blocked("capture_unavailable", lambda: self.reserve(1))

    def test_job_inspection_excludes_private_provider_payloads(self):
        self.reserve()
        with self.factory() as db: result = inspect_job(db, self.job_id)
        rendered = json.dumps(result)
        self.assertEqual(result["ai_attempts"][0]["state"], "reserved")
        for value in ["request_payload", "response_text", "lease_token", TEXT, self.policy.account_id]:
            self.assertNotIn(value, rendered)
