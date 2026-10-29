"""Opt-in real MySQL quota-lock and interruption test; uses fixture rows only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
from threading import Barrier
import unittest
from uuid import uuid4

from sqlalchemy import delete, func, select, text

from app.ai_contract import prepare_review
from app.ai_ledger import LedgerBlocked, QuotaLimit, QuotaPolicy, begin_dispatch, digest, recover, reserve
from app.analysis import analyze
from app.config import Settings
from app.db import SCHEMA_REVISION, make_database, utcnow
from app.models import AIBudgetWindow, AICall, Document, DocumentVersion, Job, User
from app.worker import capture


@unittest.skipUnless(os.getenv("TERMSLENS_RUN_MYSQL_TESTS") == "1", "Requires opt-in and migrated termslens_test")
class MySQLLedgerTests(unittest.TestCase):
    def test_competing_jobs_share_capacity_and_interruption_never_resends(self):
        settings = Settings.from_env(test=True)
        engine, factory = make_database(settings.database_url, test=True)
        users, jobs, tokens = [], [], []
        policy = QuotaPolicy(digest(str(uuid4())), "fixture-model", utcnow() + timedelta(hours=1), (
            QuotaLimit("model_requests_minute", "requests", 60, 1),
            QuotaLimit("model_tokens_minute", "total_tokens", 60, 100000),
        ))
        budget_ids = [digest([policy.provider, policy.account_id, q.name]) for q in policy.limits]
        policy_text = "Your information supports our commercial partnerships."
        analysis = analyze(policy_text, "privacy")
        batch = prepare_review(analysis, model=policy.model, consent=True)[0][0]
        lock_name = "termslens_gate1_worker_test"
        with engine.connect() as lock:
            acquired = lock.execute(text("SELECT GET_LOCK(:name, 0)"), {"name": lock_name}).scalar() == 1
            lock.commit()
            self.assertTrue(acquired, "Stop the test worker before running ledger acceptance.")
            try:
                with factory.begin() as db:
                    self.assertEqual(db.execute(text("SELECT version_num FROM alembic_version")).scalar(), SCHEMA_REVISION)
                    self.assertIsNone(db.scalar(select(Job.id).where(Job.state.in_(["queued", "running"])).limit(1)),
                                      "Existing test work is preserved; finish it before this check.")
                    for _ in range(2):
                        user = User(username="ledger_" + uuid4().hex[:20], password_hash="fixture-only")
                        db.add(user); db.flush(); users.append(user.id)
                        token = str(uuid4())
                        job = Job(owner_id=user.id, idempotency_key=str(uuid4()), request_hash="a" * 64,
                            input_text=policy_text, title="Ledger test", document_type="privacy", requested_mode="hybrid",
                            consent=True, consent_provider="groq", consent_at=utcnow(), state="running", stage="analyzing", attempts=1, lease_token=token,
                            lease_expires_at=utcnow() + timedelta(minutes=5))
                        db.add(job); db.flush(); jobs.append(job.id); tokens.append(token)
                for job_id, token in zip(jobs, tokens): capture(factory, job_id, token)
                barrier = Barrier(2)

                def compete(index):
                    barrier.wait(timeout=10)
                    try:
                        return reserve(factory, jobs[index], tokens[index], analysis, batch, policy=policy)
                    except LedgerBlocked as error:
                        return str(error)

                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(compete, i) for i in range(2)]
                    outcomes = [f.result(timeout=20) for f in futures]
                self.assertEqual(outcomes.count("quota_exhausted"), 1)
                winner = 1 if outcomes[0] == "quota_exhausted" else 0
                call_id = outcomes[winner]
                with factory() as db:
                    self.assertEqual(db.scalar(select(func.count()).select_from(AICall).where(AICall.job_id.in_(jobs))), 1)
                    self.assertEqual(db.get(AIBudgetWindow, budget_ids[0]).used, 1)
                begin_dispatch(factory, jobs[winner], tokens[winner], call_id)
                # Simulate reconnect/reclaim after a crash at the dispatch boundary.
                engine.dispose()
                new_token = str(uuid4())
                with factory.begin() as db:
                    job = db.get(Job, jobs[winner])
                    job.lease_token, job.attempts = new_token, 2
                self.assertEqual(recover(factory, jobs[winner], new_token)[0]["state"], "unknown")
                with self.assertRaisesRegex(LedgerBlocked, "previous_attempt_unresolved"):
                    reserve(factory, jobs[winner], new_token, analysis, batch, policy=policy)
                with self.assertRaisesRegex(LedgerBlocked, "dispatch_not_allowed"):
                    begin_dispatch(factory, jobs[winner], new_token, call_id)
                with factory() as db: self.assertEqual(db.get(AIBudgetWindow, budget_ids[0]).used, 1)
            finally:
                if users:
                    with factory.begin() as db:
                        document_ids = select(Document.id).where(Document.owner_id.in_(users))
                        db.execute(delete(AICall).where(AICall.job_id.in_(jobs)))
                        db.execute(delete(Job).where(Job.id.in_(jobs)))
                        db.execute(delete(DocumentVersion).where(DocumentVersion.document_id.in_(document_ids)))
                        db.execute(delete(Document).where(Document.owner_id.in_(users)))
                        db.execute(delete(User).where(User.id.in_(users)))
                        db.execute(delete(AIBudgetWindow).where(AIBudgetWindow.id.in_(budget_ids)))
                lock.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": lock_name})
                lock.commit()
        engine.dispose()
