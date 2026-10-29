import json
from unittest.mock import patch
from uuid import uuid4

from app.config import Settings
from app.diagnostics import emit, inspect_job, runtime_report
from app.models import Job
from app.worker import claim, process_claim
from tests.test_worker import WorkerFixture


class DiagnosticsTests(WorkerFixture):
    def test_job_diagnostics_omit_private_fields(self):
        result_id = process_claim(self.factory, claim(self.factory))
        with self.factory() as db:
            result = inspect_job(db, self.job_id)
        self.assertEqual(result["result_id"], result_id)
        self.assertIn("engine", result["analysis"])
        for forbidden in ["input_text", "text", "quote", "owner_id", "lease_token", "password", "cookie"]:
            self.assertNotIn('"' + forbidden + '"', json.dumps(result))

    def test_unexpected_exception_text_is_not_persisted_or_logged(self):
        secret = "sk-private-secret and private policy body"
        with self.assertLogs("termslens.events", level="INFO") as captured:
            with patch("app.worker.analyze", side_effect=ValueError(secret)):
                process_claim(self.factory, claim(self.factory))
        with self.factory() as db:
            job = db.get(Job, self.job_id)
            self.assertEqual(job.error_code, "invalid_input")
            self.assertEqual(job.error_stage, "analyzing")
            self.assertNotIn(secret, job.error)
        self.assertNotIn(secret, " ".join(captured.output))

    def test_structured_events_have_safe_ids_and_reject_bodies(self):
        request_id = str(uuid4())
        with self.assertLogs("termslens.events", level="INFO") as captured:
            emit("api_response", request_id=request_id, status=503)
        self.assertIn(request_id, captured.output[0])
        with self.assertRaises(TypeError): emit("api_response", body="private")
        with self.assertRaises(ValueError): emit("api_response", error_type="private content with spaces")

    def test_runtime_report_redacts_connection_url(self):
        result = runtime_report(Settings(database_url="mysql+pymysql://private:secret@localhost/termslens_db"))
        rendered = json.dumps(result)
        self.assertTrue(result["database_configured"])
        self.assertNotIn("secret", rendered)
        self.assertNotIn("mysql+pymysql", rendered)
