"""Scoped lifecycle acceptance fixtures; no connections at import time."""
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import unittest

from sqlalchemy import delete, event, select, text, update
from sqlalchemy.exc import SQLAlchemyError

from app.config import ROOT, Settings
from app.db import SCHEMA_REVISION, make_database, utcnow
from app.models import (AICall, Analysis, CategoryScore, Clause, ClauseFinding, Comparison,
    Document, DocumentDeletion, DocumentVersion, FetchHost, Job, LoginSession, User)
from app.snapshots import SnapshotStore
from app.worker import process_claim

LOCK_NAME = "termslens_gate1_worker_test"
TEXT = "A \U0001f4dc policy.\nWe sell your personal data to advertisers.\nWe retain your personal data indefinitely."


def require_idle(db):
    if db.scalar(select(Job.id).where(Job.state.in_(["queued", "running"])).limit(1)):
        raise AssertionError("Finish existing test jobs before acceptance; they are preserved.")
    if db.scalar(select(DocumentDeletion.document_id).where(DocumentDeletion.state == "pending").limit(1)):
        raise AssertionError("Finish existing test deletions before acceptance; they are preserved.")


def purge_fixture(factory, owners, hosts=()):
    """Only UUID owners and synthetic hosts created by this fixture; never reset schema/quotas."""
    with factory.begin() as db:
        analyses = select(Analysis.id).where(Analysis.owner_id.in_(owners))
        clauses = select(Clause.id).where(Clause.analysis_id.in_(analyses))
        documents = select(Document.id).where(Document.owner_id.in_(owners))
        jobs = select(Job.id).where(Job.owner_id.in_(owners))
        db.execute(delete(Comparison).where(Comparison.owner_id.in_(owners)))
        db.execute(delete(ClauseFinding).where(ClauseFinding.clause_id.in_(clauses)))
        db.execute(delete(Clause).where(Clause.analysis_id.in_(analyses)))
        db.execute(delete(CategoryScore).where(CategoryScore.analysis_id.in_(analyses)))
        db.execute(update(AICall).where(AICall.job_id.in_(jobs)).values(retry_of=None))
        db.execute(delete(AICall).where(AICall.job_id.in_(jobs)))
        db.execute(delete(Job).where(Job.owner_id.in_(owners)))
        db.execute(delete(Analysis).where(Analysis.owner_id.in_(owners)))
        db.execute(update(DocumentVersion).where(DocumentVersion.document_id.in_(documents)).values(parent_id=None))
        db.execute(delete(DocumentVersion).where(DocumentVersion.document_id.in_(documents)))
        db.execute(delete(Document).where(Document.owner_id.in_(owners)))
        db.execute(delete(DocumentDeletion).where(DocumentDeletion.owner_id.in_(owners)))
        db.execute(delete(LoginSession).where(LoginSession.user_id.in_(owners)))
        db.execute(delete(User).where(User.id.in_(owners)))
        db.execute(delete(FetchHost).where(FetchHost.host.in_(hosts)))


class LifecycleFixture(unittest.TestCase):
    def initialize(self):
        # Lazy imports keep discovery/skip checks independent of private configuration.
        from fastapi import Request
        from app.main import create_app
        self.owners, self.hosts, self.identities = [], [], []
        self.addCleanup(self.cleanup_fixture)
        with self.factory.begin() as db:
            for _ in range(2):
                user = User(username="lifecycle_" + uuid4().hex[:20], password_hash="fixture-only")
                db.add(user); db.flush(); self.owners.append(user.id)
        self.alice, self.bob = self.owners
        self.owner = SimpleNamespace(id=self.alice)
        app = create_app(Settings(test_mode=True), self.factory)
        self.refresh_route = next(r.endpoint for r in app.routes if r.path.endswith("/{document_id}/refresh"))
        self.request = Request({"type": "http", "method": "POST", "path": "/api/analyses", "headers": [],
                                "client": ("127.0.0.1", 8000)})
        self.request.state.request_id = str(uuid4())

    def cleanup_fixture(self):
        purge_fixture(self.factory, self.owners, self.hosts)
        for identity in self.identities:
            self.snapshots.delete_job(*identity)
            # All fixture threads/children have joined. Remove only their exact markers.
            for branch in (".deleted", ".locks"):
                self.snapshots.safe_path(self.snapshots.root / branch / identity[0] / identity[1]).unlink(missing_ok=True)

    def new_job(self, *, source=TEXT, owner=None, hybrid=False, url=False):
        with self.factory.begin() as db:
            job = Job(owner_id=owner or self.alice, idempotency_key=str(uuid4()), request_hash=uuid4().hex * 2,
                title="Lifecycle fixture", document_type="privacy", input_text="" if url else source,
                input_url="https://example.com/privacy" if url else None, requested_mode="hybrid" if hybrid else "rules_only",
                consent=hybrid, consent_provider="groq" if hybrid else None, consent_at=utcnow() if hybrid else None)
            db.add(job); db.flush(); self.track(job)
            return job.id

    def track(self, job):
        identity = (job.owner_id, job.id, job.request_hash)
        if identity not in self.identities: self.identities.append(identity)

    def lease(self, job_id):
        with self.factory.begin() as db:
            job = db.get(Job, job_id)
            job.state, job.attempts, job.lease_token = "running", job.attempts + 1, str(uuid4())
            job.lease_expires_at = utcnow() + timedelta(minutes=2)
            return job.id, job.lease_token

    def finish(self, job_id, *, fetcher=None):
        result = process_claim(self.factory, self.lease(job_id), snapshots=self.snapshots, fetcher=fetcher)
        self.assertIsNotNone(result, "Fixture report must publish.")
        return result

    def refresh(self, document_id, source=TEXT):
        from app.main import Refresh
        with self.factory() as db:
            result = self.refresh_route(document_id, Refresh(text=source), self.request, str(uuid4()), self.owner, db)
            self.track(db.get(Job, result["job_id"]))
            return result["job_id"]

    def report(self, report_id):
        from app.reports import report_data
        with self.factory() as db: return report_data(db, self.alice, report_id)


class MySQLFixture(LifecycleFixture):
    def setUp(self):
        try:
            self.settings = Settings.from_env(test=True)
            self.assertFalse(self.settings.groq_enabled or self.settings.groq_api_key or self.settings.groq_model)
            self.engine, self.factory = make_database(self.settings.database_url, test=True)
            self.addCleanup(self.engine.dispose)
            # Bound lock waits independently of the driver read timeout.
            def limits(connection, _):
                with connection.cursor() as cursor: cursor.execute("SET SESSION innodb_lock_wait_timeout = 3")
            event.listen(self.engine, "connect", limits)
            self.lock = self.engine.connect()
            self.addCleanup(self.lock.close)
            self.assertEqual(self.lock.execute(text("SELECT GET_LOCK(:name, 0)"), {"name": LOCK_NAME}).scalar(), 1,
                             "Stop the test worker and other acceptance runs first.")
            self.lock.commit()
            self.addCleanup(self.release_worker_lock)
            with self.factory() as db:
                self.assertEqual(db.execute(text("SELECT version_num FROM alembic_version")).scalar(), SCHEMA_REVISION)
                require_idle(db)
            self.snapshots = SnapshotStore(ROOT / "var/tests/snapshots")
            self.initialize()
        except SQLAlchemyError:
            raise AssertionError("MySQL fixture unavailable; verify test login/migrations privately.") from None

    def release_worker_lock(self):
        self.lock.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": LOCK_NAME}); self.lock.commit()

    def reacquire_worker_lock(self):
        self.assertEqual(self.lock.execute(text("SELECT GET_LOCK(:name, 0)"), {"name": LOCK_NAME}).scalar(), 1,
                         "Test worker lock was not released.")
        self.lock.commit()
