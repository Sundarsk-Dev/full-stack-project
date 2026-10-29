"""Version/revision contracts using isolated SQLite and fake URL/provider calls."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import func, select

from app.analysis import analyze
from app.config import Settings
from app.db import utcnow
from app.main import Refresh, Submission, create_app
from app.models import AICall, Analysis, Document, DocumentVersion, Job
from app.reports import report_data
from app.snapshots import SnapshotStore
from app.versions import revision_history, version_history
from app.worker import LeaseLost, capture, claim, process_claim, reuse_report
from tests.test_ai_ledger import AILedgerFixture
from tests.test_ai_review import FakeTransport
from tests.test_url_jobs import URL, captured
from tests.test_worker import TEXT, WorkerFixture


class VersionFixture(WorkerFixture):
    def setUp(self):
        super().setUp()
        self.result_id = process_claim(self.factory, claim(self.factory))
        with self.factory() as db:
            self.report = report_data(db, self.alice, self.result_id)
        self.document_id, self.version_id = self.report["document_id"], self.report["version_id"]
        app = create_app(Settings(), self.factory)
        self.refresh = next(r.endpoint for r in app.routes if r.path.endswith("/{document_id}/refresh"))
        self.submit = next(r.endpoint for r in app.routes if r.path == "/api/analyses" and "POST" in r.methods)
        self.owner = SimpleNamespace(id=self.alice)

    def request(self):
        request = Request({"type": "http", "method": "POST", "path": "/api/analyses", "headers": [], "client": ("127.0.0.1", 8000)})
        request.state.request_id = str(uuid4())
        return request

    def enqueue_refresh(self, text=TEXT, key=None, owner=None, **settings):
        with self.factory() as db:
            return self.refresh(self.document_id, Refresh(text=text, **settings), self.request(), key or str(uuid4()), owner or self.owner, db)["job_id"]


class VersionTests(VersionFixture):
    def test_identical_refresh_reuses_report_and_records_check_without_calls(self):
        job_id = self.enqueue_refresh(key="same")
        with patch("app.worker.groq_readiness", side_effect=AssertionError("no readiness needed")), \
             patch("app.worker.review_job", side_effect=AssertionError("no provider needed")):
            self.assertEqual(process_claim(self.factory, claim(self.factory)), self.result_id)
        self.assertEqual(self.enqueue_refresh(key="same"), job_id)
        with self.assertRaises(HTTPException) as error: self.enqueue_refresh(TEXT + "Changed.", key="same")
        self.assertEqual(error.exception.status_code, 409)
        with self.factory() as db:
            job = db.get(Job, job_id)
            self.assertEqual((job.state, job.capture_changed, job.result_reused), ("done", False, True))
            self.assertIsNotNone(job.checked_at)
            self.assertEqual(db.scalar(select(func.count()).select_from(Analysis)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(DocumentVersion)), 1)
            self.assertEqual(report_data(db, self.alice, self.result_id), self.report)

    def test_changed_then_reverted_text_creates_three_versions_and_rechecks_context(self):
        changed = TEXT + "\nHowever, the preceding terms do not apply to visitors."
        results = [self.result_id]
        for value in [changed, TEXT]:
            self.enqueue_refresh(value)
            results.append(process_claim(self.factory, claim(self.factory)))
        with self.factory() as db:
            versions = db.scalars(select(DocumentVersion).order_by(DocumentVersion.version_number)).all()
            self.assertEqual([v.text for v in versions], [TEXT, changed, TEXT])
            self.assertEqual([v.version_number for v in versions], [1, 2, 3])
            self.assertEqual([v.parent_id for v in versions], [None, versions[0].id, versions[1].id])
            self.assertEqual(len(set(results)), 3)
            self.assertEqual(report_data(db, self.alice, results[1])["score"]["coverage"], 0)
            self.assertEqual(report_data(db, self.alice, results[2])["score"], self.report["score"])

    def test_explicit_reanalysis_creates_revision_without_new_capture(self):
        payload = Submission(version_id=self.version_id, document_type="privacy")
        with self.factory() as db:
            queued = self.submit(payload, self.request(), "reanalyze", self.owner, db)
        fetcher = Mock(); fetcher.fetch.side_effect = AssertionError("saved capture must not fetch")
        result_id = process_claim(self.factory, claim(self.factory), fetcher=fetcher)
        self.assertNotEqual(result_id, self.result_id)
        with self.factory() as db:
            report = report_data(db, self.alice, result_id)
            self.assertEqual((report["version_id"], report["revision_number"]), (self.version_id, 2))
            self.assertEqual(report_data(db, self.alice, self.result_id), self.report)
            self.assertEqual(db.scalar(select(func.count()).select_from(DocumentVersion)), 1)
            self.assertEqual(db.get(Job, queued["job_id"]).operation, "reanalysis")
            self.assertIsNone(db.get(Job, queued["job_id"]).checked_at)
            history = revision_history(db, self.alice, self.document_id, self.version_id, 1, 1)
            self.assertEqual(history["total"], 2)
            self.assertEqual(history["items"][0]["id"], self.result_id)
            self.assertEqual(version_history(db, self.alice, self.document_id, 20, 0)["items"][0]["analysis_count"], 2)
        self.enqueue_refresh()
        self.assertEqual(process_claim(self.factory, claim(self.factory)), result_id, "refresh must reuse the latest report revision")

    def test_engine_or_profile_change_blocks_reuse_and_new_revision_is_published(self):
        with self.factory.begin() as db:
            db.get(Analysis, self.result_id).profile_revision = "older-profile"
        self.enqueue_refresh()
        result_id = process_claim(self.factory, claim(self.factory))
        self.assertNotEqual(result_id, self.result_id)
        with self.factory.begin() as db:
            a = db.get(Analysis, result_id)
            a.engine = {**a.engine, "rules": "older-rules"}
        self.enqueue_refresh()
        next_id = process_claim(self.factory, claim(self.factory))
        with self.factory() as db:
            self.assertEqual(db.get(Analysis, next_id).revision_number, 3)

    def test_second_queued_refresh_uses_latest_version_at_attachment(self):
        self.enqueue_refresh(TEXT + "\nChanged A.")
        self.enqueue_refresh(TEXT + "\nChanged B.")
        for _ in range(2): self.assertIsNotNone(process_claim(self.factory, claim(self.factory)))
        with self.factory() as db:
            rows = version_history(db, self.alice, self.document_id, 2, 0)
            self.assertEqual(rows["total"], 3)
            self.assertEqual([v["version_number"] for v in rows["items"]], [3, 2])
            self.assertEqual(rows["items"][0]["parent_id"], rows["items"][1]["id"])

    def test_recovery_after_refresh_attachment_does_not_duplicate_version(self):
        job_id = self.enqueue_refresh(TEXT + "\nChanged.")
        old = claim(self.factory)
        capture(self.factory, *old)
        with self.factory.begin() as db: db.get(Job, job_id).lease_expires_at = utcnow() - timedelta(seconds=1)
        current = claim(self.factory)
        with self.assertRaises(LeaseLost): reuse_report(self.factory, *old, analyze(TEXT, "privacy"), None)
        self.assertIsNotNone(process_claim(self.factory, current))
        with self.factory() as db: self.assertEqual(db.scalar(select(func.count()).select_from(DocumentVersion)), 2)

    def test_owner_tombstone_type_and_source_controls(self):
        bob = SimpleNamespace(id=self.bob)
        with self.assertRaises(HTTPException) as error: self.enqueue_refresh(owner=bob)
        self.assertEqual(error.exception.status_code, 404)
        with self.factory() as db:
            for operation in [lambda: version_history(db, self.bob, self.document_id, 20, 0),
                lambda: revision_history(db, self.bob, self.document_id, self.version_id, 20, 0),
                lambda: self.submit(Submission(version_id=self.version_id, document_type="privacy"), self.request(), "bad-owner", bob, db)]:
                with self.assertRaises(HTTPException) as error: operation()
                self.assertEqual(error.exception.status_code, 404)
            with self.assertRaises(HTTPException):
                self.submit(Submission(version_id=self.version_id, document_type="terms"), self.request(), "bad-type", self.owner, db)
        with self.assertRaises(HTTPException): self.enqueue_refresh(text=None)
        queued = self.enqueue_refresh(TEXT + "\nChanged.")
        with self.factory.begin() as db: db.get(Document, self.document_id).tombstoned = True
        self.assertIsNone(process_claim(self.factory, claim(self.factory)))
        with self.factory() as db:
            self.assertEqual(db.get(Job, queued).error_code, "capture_unavailable")
        with self.assertRaises(HTTPException): self.enqueue_refresh()

    def test_refresh_and_reanalysis_require_fresh_strict_consent(self):
        for extra in [{}, {"consent": "true", "provider": "groq"}, {"consent": True}]:
            with self.assertRaises(ValidationError): Refresh(text=TEXT, mode="hybrid", **extra)
            with self.assertRaises(ValidationError): Submission(version_id=self.version_id, document_type="privacy", mode="hybrid", **extra)
        with self.assertRaises(ValidationError): Submission(version_id=self.version_id, text=TEXT, document_type="privacy")
        job_id = self.enqueue_refresh(mode="hybrid", consent=True, provider="groq")
        result_id = process_claim(self.factory, claim(self.factory))
        with self.factory() as db:
            self.assertIsNotNone(db.get(Job, job_id).consent_at)
            self.assertNotEqual(result_id, self.result_id)
            self.assertEqual(db.get(Analysis, result_id).effective_mode, "rules_only")


class URLRefreshTests(VersionFixture):
    def setUp(self):
        super().setUp()
        self.snapshots = SnapshotStore(Path(self.temporary.name) / "snapshots")
        self.fetcher = Mock(); self.fetcher.fetch.return_value = captured()
        with self.factory() as db:
            self.submit(Submission(url=URL, document_type="privacy"), self.request(), "url-first", self.owner, db)
        self.url_result = process_claim(self.factory, claim(self.factory), fetcher=self.fetcher, snapshots=self.snapshots)
        with self.factory() as db:
            a = report_data(db, self.alice, self.url_result)
            self.document_id, self.version_id = a["document_id"], a["version_id"]

    def test_url_refresh_checks_saved_url_and_keeps_capture_time_on_unchanged(self):
        self.fetcher.fetch.return_value = {**captured(), "retrieved_at": "2026-10-29T11:00:00+00:00"}
        job_id = self.enqueue_refresh(text=None)
        self.assertEqual(process_claim(self.factory, claim(self.factory), fetcher=self.fetcher, snapshots=self.snapshots), self.url_result)
        self.assertEqual(self.fetcher.fetch.call_count, 2)
        self.fetcher.fetch.assert_called_with(URL)
        with self.factory() as db:
            job = db.get(Job, job_id)
            self.assertEqual(job.checked_at.hour, 11)
            self.assertEqual(db.get(DocumentVersion, job.version_id).captured_at.hour, 10)
            self.assertEqual(job.check_metadata["source_url"], URL)
        with self.assertRaises(HTTPException): self.enqueue_refresh(text=TEXT)

    def test_heading_context_change_creates_version_even_when_text_is_identical(self):
        self.fetcher.fetch.return_value = {**captured(), "headings": []}
        self.enqueue_refresh(text=None)
        result_id = process_claim(self.factory, claim(self.factory), fetcher=self.fetcher, snapshots=self.snapshots)
        with self.factory() as db:
            report = report_data(db, self.alice, result_id)
            self.assertEqual(report["text"], TEXT)
            self.assertEqual(report["version_number"], 2)
            self.assertTrue(report["clauses"][0]["substantive"])

    def test_tombstone_during_fetch_blocks_attachment(self):
        def fetched(_url):
            with self.factory.begin() as db: db.get(Document, self.document_id).tombstoned = True
            return captured()
        self.fetcher.fetch.side_effect = fetched
        job_id = self.enqueue_refresh(text=None)
        self.assertIsNone(process_claim(self.factory, claim(self.factory), fetcher=self.fetcher, snapshots=self.snapshots))
        with self.factory() as db: self.assertIsNone(db.get(Job, job_id).version_id)


class HybridReuseTests(AILedgerFixture):
    def test_different_model_reanalyzes_without_reusing_hybrid_as_rules_only(self):
        original_id = process_claim(self.factory, self.claimed, policy=self.policy, transport=FakeTransport())
        with self.factory.begin() as db:
            original = db.get(Job, self.job_id)
            job = Job(owner_id=self.alice, document_id=original.document_id, operation="refresh", input_text=self.policy_text,
                title="Refresh", document_type="terms", idempotency_key="different-model", request_hash="d" * 64,
                requested_mode="hybrid", consent=True, consent_provider="groq", consent_at=utcnow())
            db.add(job)
        result_id = process_claim(self.factory, claim(self.factory), settings=Settings(groq_model="different-model"))
        self.assertNotEqual(result_id, original_id)
        with self.factory() as db:
            report = report_data(db, self.alice, result_id)
            self.assertEqual(report["revision_number"], 2)
            self.assertEqual(report["review_status"], "degraded")
            self.assertEqual(report["effective_mode"], "rules_only")

    def test_hybrid_refresh_reuses_latest_revision_without_provider_or_quota_mutation(self):
        transport = FakeTransport()
        original_id = process_claim(self.factory, self.claimed, policy=self.policy, transport=transport)
        with self.factory.begin() as db:
            original = db.get(Job, self.job_id)
            document_id = original.document_id
            job = Job(owner_id=self.alice, document_id=document_id, operation="refresh", input_text=self.policy_text,
                title="Refresh", document_type="terms", idempotency_key="hybrid-refresh", request_hash="c" * 64,
                requested_mode="hybrid", consent=True, consent_provider="groq", consent_at=utcnow())
            db.add(job); db.flush(); job_id = job.id
            before = db.scalar(select(func.count()).select_from(AICall))
        no_send = Mock(); no_send.send.side_effect = AssertionError("compatible refresh cannot send")
        self.assertEqual(process_claim(self.factory, claim(self.factory), policy=self.policy, transport=no_send), original_id)
        with self.factory() as db:
            self.assertTrue(db.get(Job, job_id).result_reused)
            self.assertEqual(db.get(Job, job_id).state, "partial")
            self.assertEqual(db.scalar(select(func.count()).select_from(AICall)), before)
            self.assertEqual(db.scalar(select(func.count()).select_from(Analysis)), 1)
