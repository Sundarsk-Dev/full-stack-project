"""Synchronous worker/storage checks. SQLite is only an isolated test fixture."""
from datetime import timedelta
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, Mock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.analysis import analyze
from app.config import ROOT, Settings
from app.db import utcnow
from app.models import Analysis, Base, Document, DocumentVersion, Job, User
from app.reports import report_data
from app.worker import LeaseLost, capture, claim, main, process_claim, publish

TEXT = "A \U0001f4dc heading.\nWe sell your personal data to advertisers.\nWe retain your personal data indefinitely.\nWe use advertising cookies without your consent."


class WorkerFixture(unittest.TestCase):
    def setUp(self):
        root = (ROOT / "var" / "tests").resolve()
        assert root.is_relative_to(ROOT.resolve())
        root.mkdir(parents=True, exist_ok=True)
        self.temporary = TemporaryDirectory(dir=root)
        self.url = "sqlite:///" + (Path(self.temporary.name) / "worker.sqlite").as_posix()
        self.engine = create_engine(self.url)
        event.listen(self.engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, expire_on_commit=False)
        with self.factory.begin() as db:
            a, b = User(username="alice", password_hash="test-only"), User(username="bob", password_hash="test-only")
            db.add_all([a, b]); db.flush()
            self.alice, self.bob = a.id, b.id
            job = Job(owner_id=a.id, idempotency_key="worker-test", request_hash="a" * 64, input_text=TEXT, title="Test", document_type="privacy")
            db.add(job); db.flush(); self.job_id = job.id

    def tearDown(self):
        self.engine.dispose()
        self.temporary.cleanup()


class WorkerTests(WorkerFixture):
    def test_publish_reopen_and_owner_isolation(self):
        report_id = process_claim(self.factory, claim(self.factory))
        self.assertIsNotNone(report_id)
        with self.factory() as db:
            report = report_data(db, self.alice, report_id)
            self.assertEqual(report["text"], TEXT)
            self.assertEqual(report["score"]["coverage"], .75)
            self.assertEqual(db.get(Job, self.job_id).state, "done")
            with self.assertRaises(HTTPException) as failure: report_data(db, self.bob, report_id)
            self.assertEqual(failure.exception.status_code, 404)
        self.engine.dispose()
        new_engine = create_engine(self.url)
        try:
            with sessionmaker(new_engine)() as db:
                self.assertEqual(report_data(db, self.alice, report_id), report)
        finally:
            new_engine.dispose()
        self.assertIsNone(claim(self.factory))

    def test_recovery_preserves_capture_and_rejects_old_lease(self):
        old = claim(self.factory)
        capture(self.factory, *old)
        with self.factory.begin() as db: db.get(Job, self.job_id).lease_expires_at = utcnow() - timedelta(seconds=1)
        current = claim(self.factory)
        with self.assertRaises(LeaseLost): publish(self.factory, *old, analyze(TEXT, "privacy"))
        self.assertIsNotNone(process_claim(self.factory, current))
        with self.factory() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(DocumentVersion)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(Analysis)), 1)
            self.assertEqual(db.get(Job, self.job_id).attempts, 2)

    def test_invalid_evidence_rolls_back_entire_report(self):
        leased = claim(self.factory)
        capture(self.factory, *leased)
        result = analyze(TEXT, "privacy")
        result["clauses"][1]["findings"][0]["quote"] = "Fabricated quote"
        with self.assertRaises(ValueError): publish(self.factory, *leased, result)
        with self.factory() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Analysis)), 0)
            self.assertIsNone(db.get(Job, self.job_id).result_id)

    def test_deleted_or_wrong_owner_document_cannot_attach(self):
        leased = claim(self.factory)
        capture(self.factory, *leased)
        with self.factory.begin() as db:
            version = db.get(DocumentVersion, db.get(Job, self.job_id).version_id)
            db.get(Document, version.document_id).tombstoned = True
        with self.assertRaises(ValueError): publish(self.factory, *leased, analyze(TEXT, "privacy"))

    def test_attempt_budget_persists_across_recovery(self):
        for expected in range(1, 4):
            self.assertIsNotNone(claim(self.factory))
            with self.factory.begin() as db:
                job = db.get(Job, self.job_id)
                self.assertEqual(job.attempts, expected)
                job.lease_expires_at = utcnow() - timedelta(seconds=1)
        self.assertIsNone(claim(self.factory))
        with self.factory() as db: self.assertEqual(db.get(Job, self.job_id).state, "failed")

    def test_only_one_active_job_is_claimed(self):
        self.assertIsNotNone(claim(self.factory))
        with self.factory.begin() as db:
            db.add(Job(owner_id=self.bob, idempotency_key="bob-job", request_hash="b" * 64, input_text=TEXT, title="Other", document_type="privacy"))
        self.assertIsNone(claim(self.factory))


class WorkerCLITests(unittest.TestCase):
    def setUp(self):
        self.engine = MagicMock()
        self.lock = self.engine.connect.return_value.__enter__.return_value
        self.lock.execute.return_value.scalar.return_value = 1
        self.factory = Mock()
        patches = {
            "Settings.from_env": Mock(return_value=Settings()),
            "make_database": Mock(return_value=(self.engine, self.factory)),
            "SnapshotStore": Mock(), "process_deletion": Mock(),
            "claim": Mock(return_value=None), "process_claim": Mock(),
            "heartbeat": Mock(side_effect=lambda *args: nullcontext()),
            "time.sleep": Mock(side_effect=KeyboardInterrupt),
            "logging.basicConfig": Mock(), "configure_logging": Mock(),
        }
        for name, mock in patches.items():
            self.enterContext(patch("app.worker." + name, mock))
        self.mocks = patches
        self.enterContext(patch("sys.argv", ["app.worker"]))

    def assert_released(self):
        self.assertIn("RELEASE_LOCK", str(self.lock.execute.call_args.args[0]))
        self.engine.dispose.assert_called_once()

    def test_idle_worker_announces_start_and_stops_cleanly_on_ctrl_c(self):
        with self.assertLogs("termslens.worker", level="INFO") as logs:
            main()
        self.assertIn("Waiting for jobs", logs.output[0])
        self.assertIn("Worker stopped", logs.output[-1])
        self.mocks["process_claim"].assert_not_called()
        self.assert_released()

    def test_interrupt_during_job_does_not_retry_and_releases_worker_lock(self):
        self.mocks["claim"].return_value = ("job-id", "lease-token")
        self.mocks["process_claim"].side_effect = KeyboardInterrupt
        with self.assertLogs("termslens.worker", level="INFO"):
            main()
        self.mocks["process_claim"].assert_called_once()
        self.mocks["time.sleep"].assert_not_called()
        self.assert_released()

    def test_duplicate_worker_never_announces_start_or_claims_jobs(self):
        self.lock.execute.return_value.scalar.return_value = 0
        with self.assertLogs("termslens.worker", level="INFO") as logs, self.assertRaises(SystemExit) as error:
            main()
        self.assertEqual(error.exception.code, 1)
        self.assertFalse(any("Worker started" in line for line in logs.output))
        self.mocks["claim"].assert_not_called()
        self.lock.execute.assert_called_once()
        self.engine.dispose.assert_called_once()

    def test_once_exits_without_sleep_and_releases_worker_lock(self):
        with patch("sys.argv", ["app.worker", "--once"]), self.assertLogs("termslens.worker", level="INFO") as logs:
            main()
        self.assertIn("--once", logs.output[0])
        self.mocks["time.sleep"].assert_not_called()
        self.assert_released()


if __name__ == "__main__": unittest.main()
