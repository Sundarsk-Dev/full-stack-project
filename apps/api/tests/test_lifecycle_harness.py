"""Offline scenario/harness checks. These are explicitly not MySQL lock acceptance."""
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.config import ROOT
from app.deletion import request_deletion
from app.models import AICall, Base, Document, DocumentDeletion, Job
from app.snapshots import SnapshotStore
from tests.mysql_fixture import LifecycleFixture, TEXT, purge_fixture, require_idle
from tests.test_lifecycle_mysql import RecoveryChecks


class LocalLifecycleTests(RecoveryChecks, LifecycleFixture):
    def setUp(self):
        root = (ROOT / "var/tests").resolve()
        assert root.is_relative_to(ROOT.resolve())
        root.mkdir(parents=True, exist_ok=True)
        temporary = TemporaryDirectory(prefix="lifecycle-", dir=root)
        self.addCleanup(temporary.cleanup)
        self.engine = create_engine("sqlite:///" + (Path(temporary.name) / "fixture.sqlite").as_posix())
        self.addCleanup(self.engine.dispose)
        event.listen(self.engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, expire_on_commit=False)
        self.snapshots = SnapshotStore(Path(temporary.name) / "snapshots")
        self.initialize()

    def test_fixture_cleanup_preserves_other_owner_and_refuses_pending_work(self):
        from app.reports import report_data
        survivor_id = self.finish(self.new_job(owner=self.bob))
        with self.factory() as db: survivor = report_data(db, self.bob, survivor_id)
        queued = self.new_job()
        with self.factory() as db:
            with self.assertRaisesRegex(AssertionError, "test jobs"): require_idle(db)
        first = self.finish(queued)
        document_id = self.report(first)["document_id"]
        self.finish(self.refresh(document_id, TEXT + "\nChanged."))
        with self.factory() as db:
            request_deletion(db, self.alice, document_id)
            with self.assertRaisesRegex(AssertionError, "test deletions"): require_idle(db)
        purge_fixture(self.factory, [self.alice])
        with self.factory() as db:
            self.assertEqual(report_data(db, self.bob, survivor_id), survivor)
            self.assertIsNone(db.get(Document, document_id))
            self.assertIsNone(db.get(DocumentDeletion, document_id))
            require_idle(db)

    def test_child_harness_cleans_and_publishes_with_real_worker_logic_offline(self):
        from tests.mysql_worker_child import main
        old = self.finish(self.new_job())
        document_id = self.report(old)["document_id"]
        with self.factory() as db: request_deletion(db, self.alice, document_id)
        job_id = self.new_job(url=True, hybrid=True)
        lock_engine = Mock()
        lock_engine.connect.return_value.__enter__ = Mock(return_value=lock_engine)
        lock_engine.connect.return_value.__exit__ = Mock(return_value=False)
        lock_engine.execute.return_value.scalar.return_value = 1
        environment = {"TERMSLENS_RUN_MYSQL_TESTS": "1", "TERMSLENS_ACCEPTANCE_OWNER": self.alice,
            "TERMSLENS_TEST_DATABASE_URL": "test-only", "TERMSLENS_DATABASE_URL": "must-not-use-production",
            "TERMSLENS_GROQ_ENABLED": "true", "GROQ_API_KEY": "fixture-key-never-send", "TERMSLENS_GROQ_MODEL": "fixture-model"}
        with patch.dict(os.environ, environment, clear=True), patch("app.config.load_dotenv"), \
             patch("app.db.make_database", side_effect=[(self.engine, self.factory), (lock_engine, self.factory)]) as database, \
             patch("app.snapshots.SnapshotStore", return_value=self.snapshots):
            main()
        self.assertEqual(database.call_count, 2)
        for call in database.call_args_list: self.assertEqual((call.args, call.kwargs), (("test-only",), {"test": True}))
        with self.factory() as db:
            self.assertEqual(db.get(DocumentDeletion, document_id).state, "complete")
            job = db.get(Job, job_id)
            self.assertEqual(job.state, "partial")
            self.assertEqual(self.report(job.result_id)["degradation_reason"], "unverified_quota")
            self.assertEqual(db.scalar(select(func.count()).select_from(AICall)), 0)

    def test_mysql_discovery_skips_before_loading_configuration_or_connecting(self):
        script = """
import io, os, unittest
from unittest.mock import patch
os.environ['TERMSLENS_RUN_MYSQL_TESTS'] = '0'
with patch('app.config.Settings.from_env', side_effect=AssertionError('No private configuration')), \
     patch('app.db.make_database', side_effect=AssertionError('No database')):
    suite = unittest.defaultTestLoader.loadTestsFromName('tests.test_lifecycle_mysql')
    result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
    assert result.testsRun == 8 and len(result.skipped) == 8 and result.wasSuccessful()
"""
        result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 0, "MySQL discovery must skip all eight checks without private configuration/connections.")
