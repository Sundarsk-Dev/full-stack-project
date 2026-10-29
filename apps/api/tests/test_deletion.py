from datetime import timedelta
from pathlib import Path
from threading import Event, Thread
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError

from app.comparisons import comparison_data, create_comparison
from app.db import utcnow
from app.deletion import process_deletion, request_deletion
from app.main import Submission
from app.models import AIBudgetWindow, AICall, Analysis, Clause, ClauseFinding, Comparison, Document, DocumentDeletion, DocumentVersion, Job
from app.reports import report_data
from app.snapshots import SnapshotError, SnapshotStore
from app.worker import claim, process_claim
from tests.test_ai_ledger import AILedgerFixture
from tests.test_ai_review import FakeTransport
from tests.test_url_jobs import URL, URLJobFixture, captured
from tests.test_versions import VersionFixture
from tests.test_worker import TEXT


class DeletionTests(VersionFixture):
    def setUp(self):
        super().setUp()
        self.snapshots = SnapshotStore(Path(self.temporary.name) / "snapshots")

    def request_delete(self, owner=None):
        with self.factory() as db: return request_deletion(db, owner or self.alice, self.document_id)

    def retry(self):
        with self.factory.begin() as db: db.get(DocumentDeletion, self.document_id).retry_at = utcnow() - timedelta(seconds=1)
        return process_deletion(self.factory, self.snapshots)

    def test_rejects_active_jobs_and_other_owners_then_hides_tombstone(self):
        with self.assertRaises(HTTPException) as error: self.request_delete(self.bob)
        self.assertEqual(error.exception.status_code, 404)
        self.enqueue_refresh(TEXT + "\nNew.")
        with self.assertRaises(HTTPException) as error: self.request_delete()
        self.assertEqual(error.exception.status_code, 409)
        self.assertIsNotNone(process_claim(self.factory, claim(self.factory)))
        result = self.request_delete()
        self.assertEqual(result["state"], "pending")
        self.assertEqual(self.request_delete(), result)
        with self.factory() as db:
            with self.assertRaises(HTTPException): report_data(db, self.alice, self.result_id)
        with self.assertRaises(HTTPException): self.enqueue_refresh()

    def test_deletion_removes_all_versions_and_comparisons_but_preserves_other_document(self):
        self.enqueue_refresh(TEXT + "\nChanged.")
        changed = process_claim(self.factory, claim(self.factory))
        with self.factory() as db:
            create_comparison(db, self.alice, self.result_id, changed, "versions", "balanced")
        with self.factory.begin() as db:
            other = Document(owner_id=self.bob, title="Preserve", document_type="privacy")
            db.add(other); db.flush(); other_id = other.id
        self.request_delete()
        self.assertTrue(process_deletion(self.factory, self.snapshots))
        with self.factory() as db:
            for model in (Job, Analysis, DocumentVersion, Clause, ClauseFinding, Comparison):
                self.assertEqual(db.scalar(select(func.count()).select_from(model)), 0)
            self.assertIsNotNone(db.get(Document, other_id))
            self.assertEqual(db.get(DocumentDeletion, self.document_id).state, "complete")
        self.assertEqual(self.request_delete()["state"], "complete")

    def test_filesystem_failure_remains_pending_and_resumes_without_open_db_transaction(self):
        self.request_delete()
        with patch.object(self.snapshots, "delete_job", side_effect=OSError("private path must not escape")):
            self.assertFalse(process_deletion(self.factory, self.snapshots))
        with self.factory() as db:
            receipt = db.get(DocumentDeletion, self.document_id)
            self.assertEqual((receipt.state, receipt.error_code), ("pending", "cleanup_pending"))
            self.assertIsNotNone(db.get(Analysis, self.result_id))
        delete_job = self.snapshots.delete_job
        def outside_transaction(*args):
            self.assertEqual(self.engine.pool.checkedout(), 0)
            return delete_job(*args)
        with patch.object(self.snapshots, "delete_job", side_effect=outside_transaction): self.assertTrue(self.retry())

    def test_cross_policy_comparison_is_removed_without_changing_surviving_report(self):
        with self.factory() as db:
            self.submit(Submission(text=TEXT, document_type="privacy"), self.request(), "survivor", self.owner, db)
        survivor = process_claim(self.factory, claim(self.factory))
        with self.factory() as db:
            before = report_data(db, self.alice, survivor)
            saved = create_comparison(db, self.alice, self.result_id, survivor, "policies", "balanced")
        self.request_delete()
        self.assertTrue(process_deletion(self.factory, self.snapshots))
        with self.factory() as db:
            self.assertEqual(report_data(db, self.alice, survivor), before)
            self.assertIsNone(db.get(Comparison, saved["comparison_id"]))

    def test_database_failure_after_file_cleanup_is_recoverable(self):
        self.request_delete()
        def fail(connection, cursor, statement, parameters, context, many):
            if statement.startswith("DELETE FROM analyses"):
                raise OperationalError("redacted", {}, Exception("simulated crash"))
        event.listen(self.engine, "before_cursor_execute", fail)
        try: self.assertFalse(process_deletion(self.factory, self.snapshots))
        finally: event.remove(self.engine, "before_cursor_execute", fail)
        with self.factory() as db:
            self.assertIsNotNone(db.get(Analysis, self.result_id))
            self.assertEqual(db.get(DocumentDeletion, self.document_id).state, "pending")
        self.assertTrue(self.retry())

    def test_cleanup_continues_in_bounded_batches(self):
        with self.factory.begin() as db:
            for i in range(51):
                db.add(Job(owner_id=self.alice, document_id=self.document_id, version_id=self.version_id, state="done",
                    idempotency_key=str(i), request_hash="b" * 64, input_text="", title="Old", document_type="privacy"))
        self.request_delete()
        self.assertTrue(process_deletion(self.factory, self.snapshots))
        with self.factory() as db:
            receipt = db.get(DocumentDeletion, self.document_id)
            self.assertEqual(receipt.state, "pending")
            self.assertIsNotNone(receipt.cursor_job_id)
        self.assertTrue(process_deletion(self.factory, self.snapshots))
        with self.factory() as db: self.assertEqual(db.get(DocumentDeletion, self.document_id).state, "complete")

    def test_foreign_snapshot_reference_cannot_trigger_cleanup(self):
        with self.factory.begin() as db: db.get(DocumentVersion, self.version_id).snapshot_path = f"{self.bob}/outside.json"
        self.request_delete()
        with patch.object(self.snapshots, "delete_job") as delete_job:
            self.assertFalse(process_deletion(self.factory, self.snapshots))
            delete_job.assert_not_called()


class SnapshotDeletionTests(URLJobFixture):
    def test_raw_completed_and_interrupted_job_files_are_removed_and_late_writes_are_fenced(self):
        result_id = self.process()
        with self.factory() as db: document_id = report_data(db, self.alice, result_id)["document_id"]
        path = self.snapshots.path(*self.identity)
        temporary = path.with_suffix("." + uuid4().hex + ".tmp")
        temporary.write_bytes(b"partial raw content")
        with self.factory() as db: request_deletion(db, self.alice, document_id)
        self.assertTrue(process_deletion(self.factory, self.snapshots))
        self.assertFalse(path.parent.exists())
        with self.assertRaises(SnapshotError): self.snapshots.save(*self.identity, URL, captured())
        self.assertFalse(path.parent.exists())

    def test_file_lock_serializes_cleanup_with_a_late_writer(self):
        self.snapshots.save(*self.identity, URL, captured())
        started, finished, errors = Event(), Event(), []
        def remove():
            started.set()
            try: self.snapshots.delete_job(*self.identity)
            except Exception as error: errors.append(error)
            finally: finished.set()
        with self.snapshots.job_lock(self.alice, self.job_id):
            thread = Thread(target=remove)
            thread.start(); self.assertTrue(started.wait(1))
            self.assertFalse(finished.wait(.1))
        thread.join(3)
        self.assertTrue(finished.is_set())
        self.assertFalse(errors)
        with self.assertRaises(SnapshotError): self.snapshots.save(*self.identity, URL, captured())

    def test_unexpected_nested_directory_is_not_recursively_removed(self):
        path = self.snapshots.path(*self.identity)
        nested = path.parent / "keep"
        nested.mkdir(parents=True)
        (nested / "user-work.txt").write_text("preserve", encoding="utf-8")
        with self.assertRaises(SnapshotError): self.snapshots.delete_job(*self.identity)
        self.assertTrue((nested / "user-work.txt").exists())


class QuotaDeletionTests(AILedgerFixture):
    def test_private_provider_payloads_are_deleted_without_refunding_account_usage(self):
        result_id = process_claim(self.factory, self.claimed, policy=self.policy, transport=FakeTransport())
        with self.factory() as db:
            document_id = report_data(db, self.alice, result_id)["document_id"]
            before = [(r.id, r.used, r.generation) for r in db.scalars(select(AIBudgetWindow).order_by(AIBudgetWindow.id))]
            request_deletion(db, self.alice, document_id)
        self.assertTrue(process_deletion(self.factory, SnapshotStore(Path(self.temporary.name) / "snapshots")))
        with self.factory() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(AICall)), 0)
            self.assertEqual([(r.id, r.used, r.generation) for r in db.scalars(select(AIBudgetWindow).order_by(AIBudgetWindow.id))], before)
