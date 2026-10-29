"""Synchronous API-contract/F6 checks; not HTTP or real MySQL acceptance."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import func, select

from app.ai_review import review_job
from app.analysis import compute_scores
from app.config import Settings
from app.main import Submission, create_app
from app.models import AICall, Analysis, Job
from app.reports import report_data
from app.worker import claim, process_claim, publish
from tests.test_ai_ledger import AILedgerFixture, TEXT
from tests.test_ai_review import FakeTransport
from tests.test_worker import WorkerFixture


class ConsentContractTests(WorkerFixture):
    def setUp(self):
        super().setUp()
        app = create_app(Settings(), self.factory)
        self.submit = next(r.endpoint for r in app.routes if r.path == "/api/analyses" and "POST" in r.methods)
        self.payload = {"text": TEXT, "document_type": "terms"}

    def request(self):
        request = Request({"type": "http", "method": "POST", "path": "/api/analyses", "headers": [], "client": ("127.0.0.1", 8000)})
        request.state.request_id = str(uuid4())
        return request

    def test_hybrid_requires_boolean_consent_and_named_provider(self):
        for extra in [{}, {"consent": True}, {"provider": "groq"}, {"consent": "true", "provider": "groq"},
                      {"consent": 1, "provider": "groq"}, {"consent": True, "provider": "other"}]:
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                Submission(**self.payload, mode="hybrid", **extra)
        for extra in [{"consent": True}, {"provider": "groq"}]:
            with self.assertRaises(ValidationError): Submission(**self.payload, **extra)

    def test_legacy_rules_request_hash_is_unchanged(self):
        expected = {"title": "Pasted policy", **self.payload, "mode": "rules_only"}
        digest = sha256(json.dumps(expected, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(Submission(**self.payload).request_hash(), digest)
        with self.factory.begin() as db:
            db.add(Job(owner_id=self.alice, idempotency_key="legacy", request_hash=digest, input_text=TEXT,
                       title="Pasted policy", document_type="terms"))
        with self.factory() as db:
            result = self.submit(Submission(**self.payload), self.request(), "legacy", SimpleNamespace(id=self.alice), db)
            self.assertEqual(db.get(Job, result["job_id"]).idempotency_key, "legacy")

    def test_consent_is_server_timestamped_and_idempotent(self):
        payload = Submission(**self.payload, mode="hybrid", consent=True, provider="groq")
        with self.factory() as db:
            first = self.submit(payload, self.request(), "consented", SimpleNamespace(id=self.alice), db)
            job = db.get(Job, first["job_id"])
            original_time = job.consent_at
            self.assertIsNotNone(original_time)
            self.assertEqual(job.consent_provider, "groq")
            again = self.submit(payload, self.request(), "consented", SimpleNamespace(id=self.alice), db)
            self.assertEqual(first, again)
            self.assertEqual(db.get(Job, first["job_id"]).consent_at, original_time)
            with self.assertRaises(HTTPException) as error:
                self.submit(Submission(**self.payload), self.request(), "consented", SimpleNamespace(id=self.alice), db)
            self.assertEqual(error.exception.status_code, 409)
        with self.assertRaises(ValidationError): Submission(**payload.model_dump(), consent_at="2026-01-01")


class ReviewPublicationTests(AILedgerFixture):
    policy_text = "We sell your personal data to advertisers.\n\n" + TEXT

    def test_hybrid_report_persists_baseline_consent_and_each_finding_origin(self):
        result_id = process_claim(self.factory, self.claimed, policy=self.policy, transport=FakeTransport())
        self.assertIsNotNone(result_id)
        self.engine.dispose()
        with self.factory() as db:
            report = report_data(db, self.alice, result_id)
            self.assertEqual((report["requested_mode"], report["effective_mode"]), ("hybrid", "hybrid"))
            self.assertEqual(report["consent_provider"], "groq")
            self.assertTrue(report["consent_at"].endswith("Z"))
            self.assertEqual(report["accepted_ai_findings"], 12)
            self.assertEqual(report["rules_baseline"]["score"], self.analysis["score"])
            self.assertEqual(report["rules_baseline"]["clauses"], self.analysis["clauses"])
            self.assertEqual(db.get(Job, self.job_id).state, "partial")  # Clause budget leaves some work unrated.
            call_ids = {p["id"] for p in report["ai_provenance"]}
            for clause in report["clauses"]:
                for finding in clause["findings"]:
                    if finding["source"] == "ai": self.assertIn(finding["ai_call_id"], call_ids)
                    else: self.assertIsNone(finding["ai_call_id"])
            for private in ["request_payload", "response_text", "budget_charges", "lease_token"]:
                self.assertNotIn(private, json.dumps(report))
            with self.assertRaises(HTTPException) as error: report_data(db, self.bob, result_id)
            self.assertEqual(error.exception.status_code, 404)

    def test_unconfigured_worker_publishes_explicit_rules_fallback(self):
        result_id = process_claim(self.factory, self.claimed)
        with self.factory() as db:
            report = report_data(db, self.alice, result_id)
            self.assertEqual(report["requested_mode"], "hybrid")
            self.assertEqual(report["effective_mode"], "rules_only")
            self.assertEqual(report["review_status"], "degraded")
            self.assertEqual(report["degradation_reason"], "unverified_quota")
            self.assertTrue(report["degradation_message"])
            self.assertEqual(report["score"], report["rules_baseline"]["score"])
            self.assertEqual(report["accepted_ai_findings"], 0)
            self.assertEqual(db.get(Job, self.job_id).state, "partial")
            self.assertEqual(db.scalar(select(func.count()).select_from(AICall)), 0)

    def test_bare_consent_without_provider_attestation_cannot_send(self):
        with self.factory.begin() as db: db.get(Job, self.job_id).consent_provider = None
        transport = FakeTransport()
        result_id = process_claim(self.factory, self.claimed, policy=self.policy, transport=transport)
        self.assertFalse(transport.calls)
        with self.factory() as db:
            self.assertEqual(report_data(db, self.alice, result_id)["degradation_reason"], "consent_required")

    def test_forged_ai_finding_rolls_back_entire_publication(self):
        outcome = review_job(self.factory, *self.claimed, policy=self.policy, transport=FakeTransport())
        changed = deepcopy(outcome.result)
        changed["clauses"][1]["findings"][0].update(rating="invasive", severity=5)
        changed["categories"], changed["score"] = compute_scores(changed["clauses"], "terms")
        with self.assertRaises(ValueError):
            publish(self.factory, *self.claimed, changed, review=replace(outcome, result=changed))
        with self.factory() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Analysis)), 0)
            self.assertIsNone(db.get(Job, self.job_id).result_id)

    def test_another_jobs_response_cannot_be_attached(self):
        outcome = review_job(self.factory, *self.claimed, policy=self.policy, transport=FakeTransport())
        with self.factory.begin() as db:
            other = Job(owner_id=self.bob, idempotency_key="other", request_hash="b" * 64, input_text=TEXT, title="Other", document_type="terms")
            db.add(other); db.flush()
            db.get(AICall, outcome.provenance[0]["id"]).job_id = other.id
        with self.assertRaises(ValueError): publish(self.factory, *self.claimed, outcome.result, review=outcome)
        with self.factory() as db: self.assertIsNone(db.get(Job, self.job_id).result_id)

    def test_private_finding_filter_accepts_ai_and_preserves_owner_check(self):
        result_id = process_claim(self.factory, self.claimed, policy=self.policy, transport=FakeTransport())
        app = create_app(Settings(), self.factory)
        endpoint = next(r.endpoint for r in app.routes if r.path == "/api/analyses/{analysis_id}/findings")
        with self.factory() as db:
            found = endpoint(result_id, category=None, rating=None, source="ai", limit=100, offset=0, user=SimpleNamespace(id=self.alice), db=db)
            self.assertEqual(found["total"], 12)
            self.assertTrue(all(f["source"] == "ai" and f["ai_call_id"] for f in found["items"]))
            with self.assertRaises(HTTPException) as error:
                endpoint(result_id, category=None, rating=None, source="ai", limit=100, offset=0, user=SimpleNamespace(id=self.bob), db=db)
            self.assertEqual(error.exception.status_code, 404)


class LegacyReportTests(WorkerFixture):
    def test_report_without_new_metadata_remains_readable(self):
        result_id = process_claim(self.factory, claim(self.factory))
        with self.factory.begin() as db:
            analysis = db.get(Analysis, result_id)
            for name in ["rules_baseline", "ai_provenance", "review_status", "unresolved_count", "accepted_ai_findings"]:
                setattr(analysis, name, None)
        with self.factory() as db:
            report = report_data(db, self.alice, result_id)
            self.assertEqual(report["effective_mode"], "rules_only")
            self.assertIsNone(report["rules_baseline"])
            self.assertEqual(report["ai_provenance"], [])
            self.assertEqual(report["unresolved_count"], sum(c["substantive"] and not c["findings"] for c in report["clauses"]))
