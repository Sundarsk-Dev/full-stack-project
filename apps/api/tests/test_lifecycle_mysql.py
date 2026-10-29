"""Opt-in MySQL race/recovery acceptance. Synthetic sources; never public/provider calls."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
from threading import Barrier, Event, local
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, event, func, select
from sqlalchemy.exc import SQLAlchemyError

from app.comparisons import comparison_data, create_comparison
from app.analysis import analyze
from app.db import utcnow
from app.deletion import process_deletion, request_deletion
from app.models import AIBudgetWindow, Analysis, Comparison, Document, DocumentDeletion, DocumentVersion, FetchHost, Job
from app.snapshots import SnapshotError, SnapshotStore
from app.url_guard import FetchError
from app.url_throttle import HostThrottle
from app.worker import LeaseLost, capture, claim, process_claim, publish
from tests.mysql_fixture import MySQLFixture, TEXT


class Interrupted(BaseException):
    """Bypass recoverable-error handlers to model process loss at a durable boundary."""


class RecoveryChecks:
    def test_snapshot_completion_reclaim_and_stale_publication(self):
        from tests.test_url_jobs import captured
        job_id = self.new_job(url=True)
        first = self.lease(job_id)
        save = self.snapshots.save
        def interrupted(*args):
            save(*args)
            raise Interrupted()
        with patch.object(self.snapshots, "save", side_effect=interrupted):
            with self.assertRaises(Interrupted): capture(self.factory, *first, fetcher=Mock(fetch=Mock(return_value=captured(TEXT))), snapshots=self.snapshots)
        with self.factory.begin() as db:
            job = db.get(Job, job_id)
            self.assertIsNone(job.version_id)
            job.lease_expires_at = utcnow() - timedelta(seconds=1)
        self.engine.dispose()
        second = claim(self.factory)
        self.assertEqual(second[0], job_id)
        no_fetch = Mock(fetch=Mock(side_effect=AssertionError("Must recover the saved capture")))
        capture(self.factory, *second, fetcher=no_fetch, snapshots=self.snapshots)
        with self.factory.begin() as db:
            job = db.get(Job, job_id); version_id = job.version_id
            job.lease_expires_at = utcnow() - timedelta(seconds=1)
        third = claim(self.factory)
        with self.assertRaises(LeaseLost): capture(self.factory, *first, snapshots=self.snapshots)
        with self.assertRaises(LeaseLost): publish(self.factory, *second, {})
        result = process_claim(self.factory, third, fetcher=no_fetch, snapshots=self.snapshots)
        self.assertIsNotNone(result)
        with self.factory() as db:
            job = db.get(Job, job_id)
            self.assertEqual((job.version_id, job.attempts, job.result_id), (version_id, 3, result))
            self.assertEqual(db.scalar(select(func.count()).select_from(Analysis).where(Analysis.version_id == version_id)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(DocumentVersion).where(DocumentVersion.document_id == job.document_id)), 1)
        no_fetch.fetch.assert_not_called()

    def test_cleanup_restarts_after_files_and_before_final_commit(self):
        from tests.test_url_jobs import captured
        for boundary in ("after_files", "before_commit"):
            with self.subTest(boundary=boundary):
                fetcher = Mock(fetch=Mock(return_value=captured(TEXT)))
                report_id = self.finish(self.new_job(url=True), fetcher=fetcher)
                survivor_id = self.finish(self.new_job(url=True), fetcher=fetcher)
                report, survivor = self.report(report_id), self.report(survivor_id)
                document_id = report["document_id"]
                budget_id = uuid4().hex * 2
                with self.factory.begin() as db:
                    db.add(AIBudgetWindow(id=budget_id, resource="requests", period_seconds=60, capacity=100,
                        used=7, generation=str(uuid4()), expires_at=utcnow() + timedelta(minutes=1)))
                def remove_budget(identity=budget_id):
                    with self.factory.begin() as db: db.execute(delete(AIBudgetWindow).where(AIBudgetWindow.id == identity))
                self.addCleanup(remove_budget)
                with self.factory() as db:
                    compared = create_comparison(db, self.alice, report_id, survivor_id, "policies", "balanced")
                    request_deletion(db, self.alice, document_id)
                    identity = next(i for i in self.identities if db.get(Job, i[1]) and db.get(Job, i[1]).document_id == document_id)
                remove = self.snapshots.delete_job
                def after_files(*args):
                    remove(*args)
                    raise Interrupted()
                def before_commit(db):
                    if any(isinstance(r, DocumentDeletion) and r.document_id == document_id and r.state == "complete" for r in db.identity_map.values()):
                        raise Interrupted()
                if boundary == "before_commit": event.listen(self.factory, "before_commit", before_commit)
                try:
                    with patch.object(self.snapshots, "delete_job", side_effect=after_files if boundary == "after_files" else remove):
                        with self.assertRaises(Interrupted): process_deletion(self.factory, self.snapshots)
                finally:
                    if boundary == "before_commit": event.remove(self.factory, "before_commit", before_commit)
                self.assertFalse(self.snapshots.path(*identity).exists())
                with self.factory.begin() as db:
                    receipt = db.get(DocumentDeletion, document_id)
                    self.assertEqual(receipt.state, "pending")
                    self.assertIsNotNone(db.get(Analysis, report_id))
                    receipt.retry_at = utcnow() - timedelta(seconds=1)
                self.engine.dispose()
                self.assertTrue(process_deletion(self.factory, SnapshotStore(self.snapshots.root)))
                with self.factory() as db:
                    self.assertEqual(db.get(DocumentDeletion, document_id).state, "complete")
                    self.assertIsNone(db.get(Document, document_id))
                    self.assertIsNone(db.get(Comparison, compared["comparison_id"]))
                    self.assertEqual(db.get(AIBudgetWindow, budget_id).used, 7)
                self.assertEqual(self.report(survivor_id), survivor)
                with self.assertRaises(SnapshotError): self.snapshots.save(*identity, "https://example.com/privacy", captured(TEXT))


@unittest.skipUnless(os.getenv("TERMSLENS_RUN_MYSQL_TESTS") == "1", "Requires opt-in and migrated termslens_test")
class MySQLLifecycleAcceptance(RecoveryChecks, MySQLFixture):
    def ordered(self, first, second, *, table="documents", lock_number=1):
        """First transaction owns the target row lock before the contender reaches lock SQL."""
        state = local()
        acquired, attempted, release = Event(), Event(), Event()
        def is_lock(sql): return f"from {table}" in " ".join(sql.lower().split()) and "for update" in sql.lower()
        def before(conn, cursor, sql, params, context, many):
            if getattr(state, "role", None) == "second" and is_lock(sql):
                state.count += 1
                if state.count == 1: attempted.set()
        def after(conn, cursor, sql, params, context, many):
            if getattr(state, "role", None) == "first" and is_lock(sql):
                state.count += 1
                if state.count == lock_number:
                    acquired.set()
                    if not release.wait(5): raise AssertionError("Lock synchronization timed out.")
        def run(role, operation):
            state.role, state.count = role, 0
            try: return operation()
            except HTTPException as error: return error.status_code
            except SQLAlchemyError: raise AssertionError("Concurrent MySQL operation failed; inspect test DB privately.") from None
        event.listen(self.engine, "before_cursor_execute", before)
        event.listen(self.engine, "after_cursor_execute", after)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                a = pool.submit(run, "first", first)
                try:
                    self.assertTrue(acquired.wait(5), f"First {table} lock was not observed.")
                    b = pool.submit(run, "second", second)
                    self.assertTrue(attempted.wait(5), f"Contending {table} lock was not observed.")
                finally: release.set()
                return a.result(timeout=10), b.result(timeout=10)
        finally:
            event.remove(self.engine, "before_cursor_execute", before)
            event.remove(self.engine, "after_cursor_execute", after)

    def delete_document(self, document_id):
        with self.factory() as db: return request_deletion(db, self.alice, document_id)

    def test_compare_and_delete_in_both_lock_orders(self):
        for deletion_first in (False, True):
            with self.subTest(deletion_first=deletion_first):
                left, right = self.finish(self.new_job()), self.finish(self.new_job())
                document_id = self.report(left)["document_id"]
                survivor = self.report(right)
                def compare():
                    with self.factory() as db: return create_comparison(db, self.alice, left, right, "policies", "balanced")
                remove = lambda: self.delete_document(document_id)
                first, second = self.owner_ordered(remove, compare) if deletion_first else self.owner_ordered(compare, remove)
                if deletion_first: self.assertEqual(second, 404)
                else:
                    self.assertIn("comparison_id", first)
                    with self.factory() as db:
                        with self.assertRaises(HTTPException): comparison_data(db, self.alice, first["comparison_id"])
                self.assertTrue(process_deletion(self.factory, self.snapshots))
                self.assertEqual(self.report(right), survivor)

    def test_enqueue_and_delete_in_both_owner_lock_orders(self):
        # Enqueue and deletion lock the owner before the document. Use that first lock
        # for synchronization so the contender can reach SQL while the owner is held.
        for deletion_first in (False, True):
            with self.subTest(deletion_first=deletion_first):
                report_id = self.finish(self.new_job())
                document_id = self.report(report_id)["document_id"]
                remove = lambda: self.delete_document(document_id)
                refresh = lambda: self.refresh(document_id, TEXT + "\nChanged.")
                first, second = self.owner_ordered(remove, refresh) if deletion_first else self.owner_ordered(refresh, remove)
                if deletion_first:
                    self.assertEqual(second, 404)
                    self.assertTrue(process_deletion(self.factory, self.snapshots))
                else:
                    self.assertEqual(second, 409)
                    self.finish(first)

    def owner_ordered(self, first, second):
        return self.ordered(first, second, table="users")

    def test_publication_and_delete_in_both_owner_lock_orders(self):
        for deletion_first in (False, True):
            with self.subTest(deletion_first=deletion_first):
                original = self.finish(self.new_job())
                document_id = self.report(original)["document_id"]
                leased = self.lease(self.refresh(document_id, TEXT + "\nChanged."))
                source, kind = capture(self.factory, *leased)
                result = analyze(source, kind)
                publish_report = lambda: publish(self.factory, *leased, result)
                remove = lambda: self.delete_document(document_id)
                first, second = self.owner_ordered(remove, publish_report) if deletion_first else self.owner_ordered(publish_report, remove)
                if deletion_first:
                    self.assertEqual(first, 409)
                    self.assertEqual(self.report(second)["version_number"], 2)
                else:
                    self.assertIsInstance(first, str)
                    self.assertEqual(second["state"], "pending")
                    self.assertTrue(process_deletion(self.factory, self.snapshots))

    def test_concurrent_refresh_attachment_reuses_capture_and_revert_is_new(self):
        original = self.finish(self.new_job())
        document_id = self.report(original)["document_id"]
        a, b = [self.lease(self.refresh(document_id, TEXT + "\nChanged.")) for _ in range(2)]
        self.ordered(lambda: capture(self.factory, *a), lambda: capture(self.factory, *b), lock_number=2)
        for leased in (a, b): self.assertIsNotNone(process_claim(self.factory, leased, snapshots=self.snapshots))
        reverted = self.finish(self.refresh(document_id))
        with self.factory() as db:
            versions = db.scalars(select(DocumentVersion).where(DocumentVersion.document_id == document_id).order_by(DocumentVersion.version_number)).all()
            self.assertEqual([v.text for v in versions], [TEXT, TEXT + "\nChanged.", TEXT])
            self.assertEqual([v.version_number for v in versions], [1, 2, 3])
            self.assertEqual([v.parent_id for v in versions], [None, versions[0].id, versions[1].id])
            self.assertEqual(db.get(Job, a[0]).version_id, db.get(Job, b[0]).version_id)
        self.assertEqual(self.report(reverted)["score"], self.report(original)["score"])

    def test_host_contention_expiry_and_stale_release(self):
        host = uuid4().hex + ".example.invalid"; self.hosts.append(host)
        barrier, acquired, rejected, release = Barrier(2), Event(), Event(), Event()
        def compete():
            barrier.wait(timeout=5)
            try:
                with HostThrottle(self.factory)(host, 1, 30):
                    acquired.set()
                    if not release.wait(5): raise AssertionError("Host synchronization timed out.")
                    return "acquired"
            except FetchError as error:
                rejected.set(); return str(error)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(compete) for _ in range(2)]
            try:
                self.assertTrue(acquired.wait(5)); self.assertTrue(rejected.wait(5))
            finally: release.set()
            self.assertCountEqual([f.result(timeout=10) for f in futures], ["acquired", "host_busy"])
        with self.factory.begin() as db: db.get(FetchHost, host).last_finished_at = None
        old = HostThrottle(self.factory)(host, 3, 30); old.__enter__()
        try:
            with self.factory.begin() as db: db.get(FetchHost, host).lease_expires_at = utcnow() - timedelta(seconds=1)
            with HostThrottle(self.factory)(host, 1, 30):
                with self.factory() as db: replacement = db.get(FetchHost, host).lease_token
                old.__exit__(None, None, None); old = None
                with self.factory() as db:
                    row = db.get(FetchHost, host)
                    self.assertEqual((row.lease_token, row.interval_seconds), (replacement, 3))
            with self.assertRaisesRegex(FetchError, "host_throttled"):
                with HostThrottle(self.factory)(host, 1, .5): pass
        finally:
            if old is not None: old.__exit__(None, None, None)

    def test_test_worker_child_cleans_then_publishes_with_production_settings_disabled(self):
        from tests.mysql_worker_child import run_worker
        from tests.test_url_jobs import captured
        old = self.finish(self.new_job(url=True), fetcher=Mock(fetch=Mock(return_value=captured(TEXT))))
        document_id = self.report(old)["document_id"]
        self.delete_document(document_id)
        job_id = self.new_job(url=True, hybrid=True)
        self.release_worker_lock()
        try:
            result = run_worker(self.settings.database_url, self.alice)
            self.assertEqual(result.returncode, 0, "Isolated worker process failed; no private child output is displayed.")
        finally: self.reacquire_worker_lock()
        with self.factory() as db:
            self.assertEqual(db.get(DocumentDeletion, document_id).state, "complete")
            self.assertIsNone(db.get(Document, document_id))
            job = db.get(Job, job_id)
            self.assertEqual(job.state, "partial")
            report_id = job.result_id
            identity = job.owner_id, job.id, job.request_hash
        report = self.report(report_id)
        self.assertEqual(report["effective_mode"], "rules_only")
        self.assertEqual(report["degradation_reason"], "unverified_quota")
        self.assertTrue(self.snapshots.path(*identity).is_file())
