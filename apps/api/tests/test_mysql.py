"""Opt-in Gate 1 proof against real MySQL; preserves unrelated test data."""
import os
from pathlib import Path
import secrets
import subprocess
import sys
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text, update

from app.config import Settings
from app.db import SCHEMA_REVISION, make_database
from app.main import create_app
from app.models import AICall, Analysis, CategoryScore, Clause, ClauseFinding, Document, DocumentVersion, Job, LoginSession, User
from tests.mysql_fixture import require_idle


@unittest.skipUnless(os.getenv("TERMSLENS_RUN_MYSQL_TESTS") == "1", "Opt in with TERMSLENS_RUN_MYSQL_TESTS=1 after test migrations")
class MySQLAcceptance(unittest.TestCase):
    def test_http_two_users_worker_process_and_reopen(self):
        settings = Settings.from_env(test=True)
        engine, factory = make_database(settings.database_url, test=True)
        users = []
        clients = []
        try:
            with engine.connect() as connection:
                self.assertEqual(connection.execute(text("SELECT version_num FROM alembic_version")).scalar(), SCHEMA_REVISION)
                require_idle(connection)
            app = create_app(settings, factory)
            origin = sorted(settings.allowed_origins)[0]
            password = secrets.token_urlsafe(24)
            for _ in range(2):
                client = TestClient(app, base_url="http://127.0.0.1:8000", headers={"Origin": origin})
                clients.append(client)
                response = client.post("/api/auth/register", json={"username": "test_" + uuid4().hex[:20], "password": password})
                self.assertEqual(response.status_code, 201, response.text)
                users.append(response.json()["user"]["id"])
                client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
            alice, bob = clients
            policy = "A \U0001f4dc policy.\nWe sell your personal data to advertisers.\nWe retain your personal data indefinitely.\nWe use advertising cookies without your consent."
            response = alice.post("/api/analyses", headers={"Idempotency-Key": str(uuid4())},
                                  json={"title": "MySQL acceptance", "text": policy, "document_type": "privacy", "mode": "rules_only"})
            self.assertEqual(response.status_code, 202, response.text)
            job_id = response.json()["job_id"]
            self.assertEqual(bob.get(f"/api/jobs/{job_id}").status_code, 404)
            run = subprocess.run([sys.executable, "-m", "app.worker", "--test", "--once"], cwd=Path(__file__).resolve().parents[1],
                                 capture_output=True, text=True, timeout=45, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertEqual(run.returncode, 0, "Worker process failed; check MySQL and the single-worker lock.")
            job = alice.get(f"/api/jobs/{job_id}").json()
            self.assertEqual(job["state"], "done")
            report_id = job["result_id"]
            report = alice.get(f"/api/analyses/{report_id}")
            self.assertEqual(report.status_code, 200, report.text)
            self.assertEqual(bob.get(f"/api/analyses/{report_id}").status_code, 404)
            self.assertEqual(bob.get(f"/api/analyses/{report_id}/findings").status_code, 404)
            for clause in report.json()["clauses"]:
                for finding in clause["findings"]:
                    self.assertEqual(policy[finding["start"]:finding["end"]], finding["quote"])
            cookie = alice.cookies.get("termslens_session")
            for client in clients: client.close()
            engine.dispose()
            with TestClient(create_app(settings, factory), base_url="http://127.0.0.1:8000") as reopened:
                reopened.cookies.set("termslens_session", cookie, path="/api")
                self.assertEqual(reopened.get("/api/me").status_code, 200)
                self.assertEqual(reopened.get(f"/api/analyses/{report_id}").json(), report.json())
        finally:
            for client in clients: client.close()
            if users:
                with factory.begin() as db:
                    analysis_ids = select(Analysis.id).where(Analysis.owner_id.in_(users))
                    clause_ids = select(Clause.id).where(Clause.analysis_id.in_(analysis_ids))
                    document_ids = select(Document.id).where(Document.owner_id.in_(users))
                    db.execute(delete(ClauseFinding).where(ClauseFinding.clause_id.in_(clause_ids)))
                    db.execute(delete(Clause).where(Clause.analysis_id.in_(analysis_ids)))
                    db.execute(delete(CategoryScore).where(CategoryScore.analysis_id.in_(analysis_ids)))
                    job_ids = select(Job.id).where(Job.owner_id.in_(users))
                    db.execute(update(AICall).where(AICall.job_id.in_(job_ids)).values(retry_of=None))
                    db.execute(delete(AICall).where(AICall.job_id.in_(job_ids)))
                    db.execute(delete(Job).where(Job.owner_id.in_(users)))
                    db.execute(delete(Analysis).where(Analysis.owner_id.in_(users)))
                    db.execute(delete(DocumentVersion).where(DocumentVersion.document_id.in_(document_ids)))
                    db.execute(delete(Document).where(Document.owner_id.in_(users)))
                    db.execute(delete(LoginSession).where(LoginSession.user_id.in_(users)))
                    db.execute(delete(User).where(User.id.in_(users)))
            engine.dispose()


if __name__ == "__main__": unittest.main()
