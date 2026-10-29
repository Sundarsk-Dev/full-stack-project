"""Supplementary SQLite logic tests; these do not establish MySQL readiness."""
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.analysis import analyze
from app.config import ROOT, Settings
from app.db import utcnow
from app.main import create_app
from app.models import Analysis, Base, DocumentVersion, Job, LoginSession
from app.security import COOKIE
from app.worker import LeaseLost, capture, claim, process_claim, publish

POLICY = "A \U0001f4dc policy heading.\nWe sell your personal data to advertisers.\nWe retain your personal data indefinitely.\nWe use advertising cookies without your consent."
ORIGIN = "http://127.0.0.1:5173"


class StorageTests(unittest.TestCase):
    def setUp(self):
        folder = (ROOT / "var" / "tests").resolve()
        assert folder.is_relative_to(ROOT.resolve())
        folder.mkdir(parents=True, exist_ok=True)
        self.temporary = TemporaryDirectory(dir=folder)
        self.url = "sqlite:///" + (Path(self.temporary.name) / "logic.sqlite").as_posix()
        self.engine = create_engine(self.url, connect_args={"check_same_thread": False})
        event.listen(self.engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, expire_on_commit=False)
        self.app = create_app(Settings(), self.factory)
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.engine.dispose()
        self.temporary.cleanup()

    def account(self, username):
        client = TestClient(self.app, base_url="http://127.0.0.1:8000", headers={"Origin": ORIGIN})
        self.clients.append(client)
        response = client.post("/api/auth/register", json={"username": username, "password": "a-strong-test-password"})
        self.assertEqual(response.status_code, 201, response.text)
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        return client

    def submit(self, client, text=POLICY, key=None):
        return client.post("/api/analyses", headers={"Idempotency-Key": key or str(uuid4())},
                           json={"title": "Test policy", "text": text, "document_type": "privacy", "mode": "rules_only"})

    def test_round_trip_ownership_and_restart(self):
        alice, bob = self.account("alice"), self.account("bob")
        submission = self.submit(alice)
        self.assertEqual(submission.status_code, 202, submission.text)
        job_id = submission.json()["job_id"]
        self.assertEqual(bob.get(f"/api/jobs/{job_id}").status_code, 404)
        report_id = process_claim(self.factory, claim(self.factory))
        report = alice.get(f"/api/analyses/{report_id}")
        self.assertEqual(report.status_code, 200, report.text)
        self.assertEqual(bob.get(f"/api/analyses/{report_id}").status_code, 404)
        self.assertEqual(bob.get(f"/api/analyses/{report_id}/findings").status_code, 404)
        self.assertEqual(bob.get("/api/analyses").json()["total"], 0)
        for clause in report.json()["clauses"]:
            for finding in clause["findings"]:
                self.assertEqual(POLICY[finding["start"]:finding["end"]], finding["quote"])
        old_cookie = alice.cookies.get(COOKIE)
        self.engine.dispose()
        replacement = create_engine(self.url, connect_args={"check_same_thread": False})
        try:
            with TestClient(create_app(Settings(), sessionmaker(replacement, expire_on_commit=False)), base_url="http://127.0.0.1:8000") as reopened:
                reopened.cookies.set(COOKIE, old_cookie, path="/api")
                self.assertEqual(reopened.get("/api/me").status_code, 200)
                self.assertEqual(reopened.get(f"/api/analyses/{report_id}").json(), report.json())
        finally:
            replacement.dispose()

    def test_idempotency_and_input_conflict(self):
        client = self.account("alice")
        key = str(uuid4())
        first, second = self.submit(client, key=key), self.submit(client, key=key)
        self.assertEqual(first.json()["job_id"], second.json()["job_id"])
        self.assertEqual(self.submit(client, text=POLICY + " Changed.", key=key).status_code, 409)

    def test_hybrid_consent_and_visible_fallback_over_http(self):
        client = self.account("alice")
        payload = {"title": "Hybrid request", "text": POLICY, "document_type": "privacy", "mode": "hybrid"}
        headers = {"Idempotency-Key": str(uuid4())}
        rejected = client.post("/api/analyses", headers=headers, json=payload)
        self.assertEqual(rejected.status_code, 422)
        self.assertTrue(rejected.headers.get("X-Request-ID"))
        accepted = client.post("/api/analyses", headers=headers, json={**payload, "consent": True, "provider": "groq"})
        self.assertEqual(accepted.status_code, 202, accepted.text)
        report_id = process_claim(self.factory, claim(self.factory))
        report = client.get(f"/api/analyses/{report_id}").json()
        self.assertEqual((report["requested_mode"], report["effective_mode"]), ("hybrid", "rules_only"))
        self.assertEqual(report["review_status"], "degraded")
        self.assertTrue(report["rules_baseline"])
        self.assertTrue(report["degradation_message"])
        self.assertFalse(client.get("/api/health").json()["external_review"]["available"])

    def test_origin_csrf_logout_and_expiry(self):
        client = self.account("alice")
        csrf = client.headers.pop("X-CSRF-Token")
        self.assertEqual(self.submit(client).status_code, 403)
        client.headers["X-CSRF-Token"] = csrf
        client.headers["Origin"] = "https://untrusted.example"
        self.assertEqual(self.submit(client).status_code, 403)
        client.headers["Origin"] = ORIGIN
        old = client.cookies.get(COOKIE)
        self.assertEqual(client.post("/api/auth/logout").status_code, 204)
        client.cookies.set(COOKIE, old, path="/api")
        self.assertEqual(client.get("/api/me").status_code, 401)
        other = self.account("bob")
        with self.factory.begin() as db:
            for session in db.scalars(select(LoginSession)):
                session.expires_at = utcnow() - timedelta(seconds=1)
        self.assertEqual(other.get("/api/me").status_code, 401)

    def test_recovery_reuses_capture_and_fences_stale_worker(self):
        self.submit(self.account("alice"))
        stale = claim(self.factory)
        capture(self.factory, *stale)
        with self.factory.begin() as db:
            db.get(Job, stale[0]).lease_expires_at = utcnow() - timedelta(seconds=1)
        current = claim(self.factory)
        with self.assertRaises(LeaseLost):
            publish(self.factory, *stale, analyze(POLICY, "privacy"))
        result = process_claim(self.factory, current)
        self.assertIsNotNone(result)
        with self.factory() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(DocumentVersion)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(Analysis)), 1)
            self.assertEqual(db.get(Job, stale[0]).attempts, 2)
        self.assertIsNone(claim(self.factory))

    def test_publication_rolls_back_invalid_evidence(self):
        client = self.account("alice")
        self.submit(client)
        claimed = claim(self.factory)
        capture(self.factory, *claimed)
        result = analyze(POLICY, "privacy")
        result["clauses"][1]["findings"][0]["quote"] = "This evidence is invented."
        with self.assertRaises(ValueError): publish(self.factory, *claimed, result)
        self.assertEqual(client.get("/api/analyses").json()["total"], 0)
        self.assertIsNone(client.get(f"/api/jobs/{claimed[0]}").json()["result_id"])

    def test_three_attempt_limit(self):
        self.submit(self.account("alice"))
        for expected in range(1, 4):
            claimed = claim(self.factory)
            with self.factory.begin() as db:
                job = db.get(Job, claimed[0])
                self.assertEqual(job.attempts, expected)
                job.lease_expires_at = utcnow() - timedelta(seconds=1)
        self.assertIsNone(claim(self.factory))
        with self.factory() as db:
            job = db.get(Job, claimed[0])
            self.assertEqual(job.state, "failed")

    def test_validation_does_not_echo_password_and_limits_body(self):
        client = TestClient(self.app, base_url="http://127.0.0.1:8000", headers={"Origin": ORIGIN})
        self.clients.append(client)
        response = client.post("/api/auth/register", json={"username": "valid_name", "password": "private"})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("private", response.text)
        self.assertEqual(client.post("/api/auth/register", content=b"a" * (1024 * 1024 + 1)).status_code, 413)


if __name__ == "__main__": unittest.main()
