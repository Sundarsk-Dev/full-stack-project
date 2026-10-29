import json
from contextlib import redirect_stdout
from io import StringIO
import unittest
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

from app.config import Settings
from app.diagnostics import emit, inspect_job, runtime_report
from app.models import Job
from app.worker import claim, process_claim
from tests.test_worker import WorkerFixture
from sqlalchemy.exc import ArgumentError, OperationalError
from app.manage import database_failure, main as manage_main


class ManageDiagnosticsTests(unittest.TestCase):
    def test_driver_codes_are_actionable_without_printing_private_messages(self):
        private = "secret-password private-db-url SQL-containing-policy"
        for number, expected in [(1045, "mysql_1045"), (1044, "mysql_1044"), (1049, "mysql_1049"),
                                 (2003, "mysql_2003"), (2005, "mysql_2005"), (1146, "schema_missing"),
                                 (9999, "database_check_failed")]:
            with self.subTest(number=number):
                error = OperationalError(private, {}, Exception(number, private))
                code, hint = database_failure(error, "connection")
                self.assertEqual(code, expected)
                self.assertNotIn(private, hint)
        socket_error = OSError("private socket detail")
        socket_error.winerror = 10013
        original = Exception(2003, private)
        original.original_exception = socket_error
        self.assertEqual(database_failure(OperationalError(private, {}, original), "connection")[0], "socket_access_denied")

    def test_cli_reports_login_failure_and_disposes_engine(self):
        engine = Mock()
        engine.connect.side_effect = OperationalError("private SQL", {}, Exception(1045, "private password"))
        output = StringIO()
        with patch("sys.argv", ["app.manage"]), patch("app.manage.Settings.from_env", return_value=Settings()), \
             patch("app.manage.make_database", return_value=(engine, Mock())), redirect_stdout(output):
            with self.assertRaises(SystemExit) as failure: manage_main()
        self.assertEqual(failure.exception.code, 1)
        self.assertIn("NOT READY [mysql_1045]", output.getvalue())
        self.assertNotIn("private password", output.getvalue())
        self.assertNotIn("private SQL", output.getvalue())
        engine.dispose.assert_called_once()

    def test_configuration_and_schema_failures_remain_distinct(self):
        for stage, expected in [("configuration", "database_configuration"), ("server_validation", "server_configuration"),
                                ("schema_validation", "schema_outdated")]:
            self.assertEqual(database_failure(ValueError("private value"), stage)[0], expected)
        self.assertEqual(database_failure(ImportError("private dependency detail"), "configuration")[0], "dependency_missing")
        self.assertEqual(database_failure(ArgumentError("private URL"), "configuration")[0], "database_configuration")
        engine = MagicMock()
        connection = engine.connect.return_value.__enter__.return_value
        connection.execute.return_value.one.return_value = ("8.0.28", "termslens_db", "utf8mb4")
        connection.execute.return_value.scalar.return_value = "old_revision"
        output = StringIO()
        with patch("sys.argv", ["app.manage", "--migrations"]), patch("app.manage.Settings.from_env", return_value=Settings()), \
             patch("app.manage.make_database", return_value=(engine, Mock())), redirect_stdout(output):
            with self.assertRaises(SystemExit): manage_main()
        self.assertIn("NOT READY [schema_outdated]", output.getvalue())
        self.assertNotIn("PASS:", output.getvalue())
        engine.dispose.assert_called_once()


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
