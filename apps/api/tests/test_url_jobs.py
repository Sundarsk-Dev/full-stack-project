"""Offline URL application contracts; real HTTP/MySQL acceptance is separate."""
from base64 import b64encode
from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import func, select

from app.analysis import analyze
from app.config import ROOT, Settings
from app.db import utcnow
from app.main import Discovery, Submission, create_app
from app.models import Analysis, DocumentVersion, Job, LoginSession
from app.reports import report_data
from app.security import COOKIE, csrf_token, token_hash
from app.snapshots import SnapshotError, SnapshotStore
from app.url_guard import FetchError
from app.worker import capture, claim, process_claim, publish
from tests.test_worker import TEXT, WorkerFixture

URL = "https://example.com/privacy"


def captured(text=TEXT):
    raw = ("<main>" + text + "</main>").encode()
    return {"operation": "capture", "source_url": URL, "final_url": URL,
        "retrieved_at": "2026-10-29T10:00:00+00:00", "requests": 2, "redirects": 0, "elapsed_seconds": 1.5,
        "content_type": "text/html; charset=utf-8", "extractor": "static-policy-v1", "text": text,
        "headings": [{"start": 0, "end": text.index("\n"), "text": text.split("\n")[0], "level": 2}],
        "raw_representation": "decompressed_response_body", "raw_base64": b64encode(raw).decode(),
        "raw_sha256": sha256(raw).hexdigest(), "text_sha256": sha256(text.encode()).hexdigest()}


class URLJobFixture(WorkerFixture):
    def setUp(self):
        super().setUp()
        self.snapshots = SnapshotStore(Path(self.temporary.name) / "snapshots")
        self.fetcher = Mock()
        self.fetcher.fetch.return_value = captured()
        self.identity = (self.alice, self.job_id, "a" * 64)
        with self.factory.begin() as db:
            job = db.get(Job, self.job_id)
            job.input_text, job.input_url = "", URL

    def process(self, leased=None):
        return process_claim(self.factory, leased or claim(self.factory), fetcher=self.fetcher, snapshots=self.snapshots)


class SnapshotTests(URLJobFixture):
    def test_complete_capture_is_immutable_and_bound_to_request(self):
        first = self.snapshots.save(*self.identity, URL, captured())
        self.assertEqual(self.snapshots.save(*self.identity, URL, captured(TEXT + "\nChanged.")), first)
        self.assertIsNone(self.snapshots.load(self.bob, self.job_id, "a" * 64, URL))
        path = self.snapshots.path(*self.identity)
        envelope = json.loads(path.read_text(encoding="utf-8"))
        envelope["identity"][0] = self.bob
        path.write_text(json.dumps(envelope), encoding="utf-8")
        with self.assertRaises(SnapshotError): self.snapshots.load(*self.identity, URL)

    def test_incomplete_files_never_count_as_capture_and_hash_corruption_fails(self):
        path = self.snapshots.path(*self.identity)
        path.parent.mkdir(parents=True)
        path.with_suffix(".interrupted.tmp").write_bytes(b"incomplete")
        self.assertIsNone(self.snapshots.load(*self.identity, URL))
        with patch("app.snapshots.commit_file", side_effect=OSError("simulated interruption")):
            with self.assertRaises(OSError): self.snapshots.save(*self.identity, URL, captured())
        self.assertIsNone(self.snapshots.load(*self.identity, URL))
        bad = captured(); bad["text"] += "tampered"
        with self.assertRaises(SnapshotError): self.snapshots.save(*self.identity, URL, bad)
        self.assertFalse(path.exists())
        self.snapshots.save(*self.identity, URL, captured())
        path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(SnapshotError): self.snapshots.load(*self.identity, URL)

    def test_snapshot_path_rejects_traversal(self):
        for identity in [("../owner", self.job_id, "a" * 64), (self.alice, "../job", "a" * 64),
                         (self.alice, self.job_id, "../hash")]:
            with self.assertRaises(ValueError): self.snapshots.path(*identity)


class URLWorkerTests(URLJobFixture):
    def test_fetch_has_no_open_transaction_and_report_preserves_source(self):
        def fetch(url):
            self.assertEqual(self.engine.pool.checkedout(), 0)
            self.assertEqual(url, URL)
            return captured()
        self.fetcher.fetch.side_effect = fetch
        result_id = self.process()
        self.assertIsNotNone(result_id)
        with self.factory() as db:
            report = report_data(db, self.alice, result_id)
            self.assertEqual(report["source_kind"], "url")
            self.assertEqual(report["capture"]["source_url"], URL)
            self.assertEqual(report["engine"]["extractor"], "static-policy-v1")
            self.assertEqual(report["captured_at"], "2026-10-29T10:00:00.000Z")
            self.assertFalse(report["clauses"][0]["substantive"])
            self.assertEqual(report["text"], TEXT)
            for private in ["snapshot_path", "raw_base64"]: self.assertNotIn(private, json.dumps(report))
            with self.assertRaises(HTTPException): report_data(db, self.bob, result_id)

    def test_restart_after_file_commit_before_database_attachment_does_not_refetch(self):
        save = self.snapshots.save
        def crash(*args):
            save(*args)
            raise RuntimeError("simulated crash after completion")
        with patch.object(self.snapshots, "save", side_effect=crash): self.assertIsNone(self.process())
        with self.factory() as db: self.assertIsNone(db.get(Job, self.job_id).version_id)
        self.engine.dispose()
        self.assertIsNotNone(self.process())
        self.fetcher.fetch.assert_called_once()
        with self.factory() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(DocumentVersion)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(Analysis)), 1)

    def test_expired_worker_cannot_attach_but_next_lease_reuses_completed_capture(self):
        old = claim(self.factory)
        def expire(_url):
            with self.factory.begin() as db:
                db.get(Job, self.job_id).lease_expires_at = utcnow() - timedelta(seconds=1)
            return captured()
        self.fetcher.fetch.side_effect = expire
        self.assertIsNone(self.process(old))
        with self.factory() as db: self.assertIsNone(db.get(Job, self.job_id).version_id)
        self.assertIsNotNone(self.process())
        self.fetcher.fetch.assert_called_once()

    def test_capture_attachment_recovery_and_wrong_extractor_publication(self):
        leased = claim(self.factory)
        capture(self.factory, *leased, fetcher=self.fetcher, snapshots=self.snapshots)
        with self.assertRaises(ValueError): publish(self.factory, *leased, analyze(TEXT, "privacy"))
        self.assertIsNotNone(self.process(leased))
        self.fetcher.fetch.assert_called_once()

    def test_hybrid_fallback_uses_url_extractor_and_heading_context(self):
        self.fetcher.fetch.return_value = captured(TEXT + "\nWe may use unfamiliar practices for service operation.")
        with self.factory.begin() as db:
            job = db.get(Job, self.job_id)
            job.requested_mode, job.consent, job.consent_provider, job.consent_at = "hybrid", True, "groq", utcnow()
        result_id = self.process()
        with self.factory() as db:
            report = report_data(db, self.alice, result_id)
            self.assertEqual(report["review_status"], "degraded")
            self.assertEqual(report["engine"], report["rules_baseline"]["engine"])
            self.assertEqual(report["engine"]["extractor"], "static-policy-v1")
            self.assertFalse(report["rules_baseline"]["clauses"][0]["substantive"])

    def test_failed_fetch_is_terminal_with_fixed_code_and_paste_fallback(self):
        self.fetcher.fetch.side_effect = FetchError("robots_denied")
        self.assertIsNone(self.process())
        with self.factory() as db:
            job = db.get(Job, self.job_id)
            self.assertEqual((job.state, job.error_code), ("failed", "robots_denied"))
            self.assertIn("Paste", job.error)
            self.assertIsNone(job.version_id)
        self.assertIsNone(claim(self.factory))

    def test_test_worker_passes_isolation_to_fetcher_and_snapshots(self):
        settings = Settings(database_url="isolated-fixture", test_mode=True)
        with patch("app.worker.URLFetcher", return_value=self.fetcher) as constructor, \
             patch("app.worker.SnapshotStore", return_value=self.snapshots) as store:
            self.assertIsNotNone(process_claim(self.factory, claim(self.factory), settings=settings))
        constructor.assert_called_once_with("isolated-fixture", test=True)
        store.assert_called_once_with(ROOT / "var" / "tests/snapshots")


class URLSubmissionTests(WorkerFixture):
    def setUp(self):
        super().setUp()
        self.fetcher = Mock()
        self.fetcher.fetch.return_value = {"operation": "discovery", "candidates": []}
        app = create_app(Settings(), self.factory, url_fetcher=self.fetcher)
        self.submit = next(r.endpoint for r in app.routes if r.path == "/api/analyses" and "POST" in r.methods)
        route = next(r for r in app.routes if r.path == "/api/discovery")
        self.discover = route.endpoint
        self.authenticate = next(d.call for d in route.dependant.dependencies if d.name == "user")

    def request(self, address="127.0.0.1", headers=()):
        request = Request({"type": "http", "method": "POST", "path": "/api/discovery", "headers": list(headers), "client": (address, 8000)})
        request.state.request_id = str(uuid4())
        return request

    def test_exclusive_input_url_validation_and_paste_hash_compatibility(self):
        for payload in [{}, {"text": TEXT, "url": URL}, {"url": "http://127.0.0.1"}, {"url": "https://name:password@example.com"}]:
            with self.assertRaises(ValidationError): Submission(document_type="privacy", **payload)
        for mode in ["rules_only", "hybrid"]:
            legacy = {"title": "Pasted policy", "text": TEXT, "document_type": "privacy", "mode": mode}
            if mode == "hybrid": legacy.update(consent=True, provider="groq")
            digest = sha256(json.dumps(legacy, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
            self.assertEqual(Submission(**legacy).request_hash(), digest)

    def test_url_is_queued_without_fetch_and_idempotency_is_owned(self):
        payload = Submission(url=URL, document_type="privacy", title="URL policy")
        with self.factory() as db:
            first = self.submit(payload, self.request(), "url-submit", SimpleNamespace(id=self.alice), db)
            job = db.get(Job, first["job_id"])
            self.assertEqual((job.input_url, job.input_text, job.state), (URL, "", "queued"))
            self.assertEqual(first, self.submit(payload, self.request(), "url-submit", SimpleNamespace(id=self.alice), db))
            other = self.submit(payload, self.request(), "url-submit", SimpleNamespace(id=self.bob), db)
            self.assertNotEqual(first["job_id"], other["job_id"])
            with self.assertRaises(HTTPException) as error:
                self.submit(Submission(url=URL + "?different", document_type="privacy"), self.request(), "url-submit", SimpleNamespace(id=self.alice), db)
            self.assertEqual(error.exception.status_code, 409)
        self.fetcher.fetch.assert_not_called()

    def test_discovery_has_no_jobs_transaction_and_owner_rate_limit(self):
        def fetch(*args, **kwargs):
            self.assertEqual(self.engine.pool.checkedout(), 0)
            self.assertEqual(kwargs, {"operation": "discovery"})
            return {"candidates": []}
        self.fetcher.fetch.side_effect = fetch
        with self.factory() as db:
            before = db.scalar(select(func.count()).select_from(Job))
            for i in range(3): self.discover(Discovery(url=URL), self.request(str(i)), SimpleNamespace(id=self.alice), db)
            with self.assertRaises(HTTPException) as error:
                self.discover(Discovery(url=URL), self.request("new-ip"), SimpleNamespace(id=self.alice), db)
            self.assertEqual(error.exception.status_code, 429)
            self.assertEqual(db.scalar(select(func.count()).select_from(Job)), before)

    def test_discovery_ip_limit_spans_users(self):
        with self.factory() as db:
            for _ in range(3): self.discover(Discovery(url=URL), self.request(), SimpleNamespace(id=self.alice), db)
            with self.assertRaises(HTTPException) as error:
                self.discover(Discovery(url=URL), self.request(), SimpleNamespace(id=self.bob), db)
            self.assertEqual(error.exception.status_code, 429)

    def test_discovery_dependency_requires_session_origin_and_csrf(self):
        token = "local-test-session-token-value"
        with self.factory.begin() as db:
            db.add(LoginSession(token_hash=token_hash(token), user_id=self.alice, expires_at=utcnow() + timedelta(hours=1)))
        headers = [(b"cookie", f"{COOKIE}={token}".encode()), (b"origin", b"http://127.0.0.1:5173"), (b"x-csrf-token", csrf_token(token).encode())]
        with self.factory() as db:
            for current, status in [(headers[1:], 401), (headers[:2], 403), (headers[:1] + headers[2:], 403)]:
                with self.assertRaises(HTTPException) as error: self.authenticate(self.request(headers=current), db)
                self.assertEqual(error.exception.status_code, status)
            self.assertEqual(self.authenticate(self.request(headers=headers), db).id, self.alice)
