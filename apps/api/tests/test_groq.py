"""No network: real HTTP parsing over MockTransport, local config and subprocess deadlines."""
from copy import deepcopy
from contextlib import redirect_stdout
from dataclasses import asdict, replace
from datetime import timedelta
import json
from io import BytesIO, StringIO
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Timer
from time import monotonic
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx
from sqlalchemy import func, select

from app.ai_contract import ESTIMATOR_REVISION, prepare_review
from app.ai_review import ProviderReply
from app.analysis import analyze
from app.config import ROOT, Settings
from app.db import timestamp, utcnow
from app.diagnostics import runtime_report
from app.groq_config import groq_readiness
from app.groq_transport import ENDPOINT, MAX_ENVELOPE_BYTES, GroqTransport, main as transport_main, request_once
from app.main import create_app
from app.manage import main as manage_main
from app.models import AICall, Job
from app.reports import report_data
from app.worker import main as worker_main, process_claim
from tests.test_ai_ledger import AILedgerFixture
from tests.test_ai_review import supported_reply

KEY = "test-only-private-key"
MODEL = "fixture-model"


def readiness_record():
    return {"schema_version": 1, "account_id": "a" * 64, "model": MODEL,
        "verified_at": timestamp(utcnow() - timedelta(minutes=1)),
        "valid_until": timestamp(utcnow() + timedelta(hours=1)),
        "free_access_verified": True, "exclusive_account_usage_verified": True,
        "limits_complete_verified": True, "estimator_verified": True,
        "estimator_revision": ESTIMATOR_REVISION, "verification_reference": "synthetic-test-only",
        "limits": [{"name": "model_requests_minute", "resource": "requests", "period_seconds": 60, "capacity": 10},
                   {"name": "model_tokens_minute", "resource": "total_tokens", "period_seconds": 60, "capacity": 20000}]}


def response_body(content='{"findings": []}'):
    return {"model": MODEL, "choices": [{"index": 0, "finish_reason": "stop",
        "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 600, "completion_tokens": 120, "total_tokens": 720}}


def http_response(body=None, *, status=200, headers=None, stream=None):
    # A streaming response exercises the adapter's byte cap without a real socket.
    stream = stream or httpx.ByteStream(json.dumps(response_body() if body is None else body).encode())
    return httpx.Response(status, headers={"content-type": "application/json", **(headers or {})}, stream=stream)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        root = ROOT / "var/tests"
        root.mkdir(parents=True, exist_ok=True)
        self.directory = TemporaryDirectory(dir=root)
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "readiness.json"
        self.record = readiness_record()
        self.settings = Settings(groq_enabled=True, groq_api_key=KEY, groq_model=MODEL, groq_readiness_file=str(self.path))
        self.write()

    def write(self):
        self.path.write_text(json.dumps(self.record), encoding="utf-8")

    def test_default_and_missing_config_never_enable_calls(self):
        self.assertEqual(groq_readiness(Settings()).reason, "disabled")
        for changes, reason in [({"groq_api_key": ""}, "key_missing_or_invalid"),
                                ({"groq_api_key": "bad\nkey"}, "key_missing_or_invalid"),
                                ({"groq_model": ""}, "model_missing"),
                                ({"groq_readiness_file": str(self.path) + ".missing"}, "readiness_missing_or_invalid")]:
            with self.subTest(reason=reason):
                self.assertEqual(groq_readiness(replace(self.settings, **changes)).reason, reason)

    def test_matching_unexpired_complete_attestations_create_policy(self):
        readiness = groq_readiness(self.settings)
        self.assertEqual(readiness.public_status(), {"available": True, "provider": "groq", "reason": "configured"})
        self.assertEqual(readiness.policy.model, MODEL)
        self.assertEqual(readiness.policy.limits[1].capacity, 20000)
        self.assertIsNone(readiness.policy.valid_until.tzinfo)  # Same UTC convention as the ledger.

    def test_unverified_or_incompatible_records_stay_unavailable(self):
        original = deepcopy(self.record)
        for change in [{name: False} for name in ("free_access_verified", "exclusive_account_usage_verified",
                        "limits_complete_verified", "estimator_verified")] + [
                        {"model": "another-model"}, {"estimator_revision": "unknown"},
                        {"account_id": "not-an-account-hash"}, {"verification_reference": " "},
                        {"free_access_verified": "true"}, {"surplus": "private"},
                        {"limits": []}, {"limits": self.record["limits"][:1]}]:
            with self.subTest(change=change):
                self.record = {**original, **change}
                self.write()
                self.assertIsNone(groq_readiness(self.settings).policy)

    def test_time_window_requires_utc_aware_dates_and_at_most_24_hours(self):
        now = utcnow()
        for change in [{"valid_until": timestamp(now - timedelta(seconds=1))},
                       {"verified_at": timestamp(now + timedelta(minutes=1))},
                       {"valid_until": timestamp(now + timedelta(days=2))},
                       {"verified_at": now.isoformat()}]:
            self.record = {**readiness_record(), **change}
            self.write()
            self.assertEqual(groq_readiness(self.settings).reason, "verification_expired_or_invalid")

    def test_invalid_quota_values_and_duplicate_names_are_rejected(self):
        for change in [{"capacity": 0}, {"capacity": True}, {"period_seconds": 0},
                       {"resource": "unknown"}, {"name": self.record["limits"][0]["name"]}]:
            self.record = readiness_record()
            self.record["limits"][1].update(change)
            self.write()
            self.assertIsNone(groq_readiness(self.settings).policy)

    def test_malformed_oversized_and_example_records_fail_closed(self):
        for raw in [b'{"key":"' + KEY.encode(), b" " * 16385,
                    (ROOT / "groq-readiness.example.json").read_bytes()]:
            self.path.write_bytes(raw)
            self.assertEqual(groq_readiness(self.settings).reason, "readiness_missing_or_invalid")

    def test_environment_loading_and_diagnostics_do_not_expose_secrets_or_connect(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": KEY, "TERMSLENS_GROQ_ENABLED": "true",
                "TERMSLENS_GROQ_MODEL": MODEL, "TERMSLENS_GROQ_READINESS_FILE": str(self.path)}, clear=True), \
                patch("app.config.load_dotenv") as dotenv, patch("httpx.Client", side_effect=AssertionError("no calls")):
            settings = Settings.from_env()
            dotenv.assert_called_once_with(ROOT / ".env", override=False)
            result = runtime_report(settings)
        self.assertEqual(settings.groq_api_key, KEY)
        self.assertTrue(result["external_review"]["available"])
        self.assertFalse(result["network_or_provider_calls"])
        self.assertNotIn(KEY, repr(settings) + json.dumps(result))
        self.assertNotIn(str(self.path), json.dumps(result))

    def test_test_settings_disable_provider_and_never_use_application_database(self):
        environment = {"GROQ_API_KEY": KEY, "TERMSLENS_GROQ_ENABLED": "true", "TERMSLENS_GROQ_MODEL": MODEL,
            "TERMSLENS_GROQ_READINESS_FILE": str(self.path), "TERMSLENS_DATABASE_URL": "application-only",
            "TERMSLENS_TEST_DATABASE_URL": "test-only"}
        with patch.dict(os.environ, environment, clear=True), patch("app.config.load_dotenv"), \
                patch("app.groq_config.Path.open", side_effect=AssertionError("no readiness reads")):
            settings = Settings.from_env(test=True)
            self.assertEqual(settings.database_url, "test-only")
            self.assertEqual(groq_readiness(settings).reason, "disabled")
            self.assertEqual((settings.groq_api_key, settings.groq_model, settings.groq_readiness_file), ("", "", ""))
            del os.environ["TERMSLENS_TEST_DATABASE_URL"]
            self.assertEqual(Settings.from_env(test=True).database_url, "")
            self.assertEqual(Settings.from_env().database_url, "application-only")

    def test_test_diagnostics_use_disabled_test_configuration_without_connecting(self):
        output = StringIO()
        with patch.dict(os.environ, {"GROQ_API_KEY": KEY, "TERMSLENS_GROQ_ENABLED": "true",
                "TERMSLENS_DATABASE_URL": "application-only"}, clear=True), patch("app.config.load_dotenv"), \
                patch.object(sys, "argv", ["app.manage", "--test", "--diagnostics"]), \
                patch("app.manage.make_database", side_effect=AssertionError("no database")), redirect_stdout(output):
            manage_main()
        report = json.loads(output.getvalue())
        self.assertFalse(report["database_configured"])
        self.assertEqual(report["external_review"]["reason"], "disabled")
        self.assertNotIn(KEY, output.getvalue())


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.payload = prepare_review(analyze("Your information supports partnership number 1.", "terms"),
                                      model=MODEL, consent=True)[0][0].payload

    def send(self, response):
        self.requests = []
        def handler(request):
            self.requests.append(request)
            if isinstance(response, Exception): raise response
            return response
        return request_once(self.payload, KEY, timeout_seconds=1, transport=httpx.MockTransport(handler))

    def test_success_uses_one_fixed_endpoint_and_preserves_unicode_content_usage(self):
        content = '{"findings": [], "untrusted": "ignore instructions 📜"}'
        reply = self.send(http_response(response_body(content)))
        self.assertEqual((reply.outcome, reply.text, reply.usage), ("received", content, (600, 120)))
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual((request.method, str(request.url)), ("POST", ENDPOINT))
        self.assertEqual(request.headers["authorization"], "Bearer " + KEY)
        self.assertEqual(request.headers["accept-encoding"], "identity")
        self.assertEqual(json.loads(request.content), self.payload)

    def test_rate_limit_has_no_internal_retry_and_parses_seconds_and_http_date(self):
        for headers, wait in [({"retry-after": "2"}, 2), ({"retry-after": "0"}, 0),
                ({"retry-after": "Thu, 29 Oct 2026 10:00:03 GMT", "date": "Thu, 29 Oct 2026 10:00:00 GMT"}, 3)]:
            reply = self.send(http_response(status=429, headers=headers))
            self.assertEqual((reply.outcome, reply.retry_after_seconds, reply.usage), ("rate_limited", wait, None))
            self.assertEqual(len(self.requests), 1)
        for value in [None, "invalid", "1.5", "-1", "61", "9999999999999999"]:
            reply = self.send(http_response(status=429, headers={} if value is None else {"retry-after": value}))
            self.assertEqual(reply.outcome, "failed")

    def test_redirects_auth_errors_and_server_failures_never_follow_or_retry(self):
        for status in [301, 302, 307, 400, 401, 403, 404, 408, 422, 500, 503]:
            reply = self.send(http_response({"error": KEY}, status=status, headers={"location": "https://example.invalid"}))
            self.assertEqual(reply.outcome, "unknown" if status == 408 or status >= 500 else "failed")
            self.assertEqual(len(self.requests), 1)
            self.assertIsNone(reply.text)
            self.assertIsNone(reply.usage)

    def test_network_timeout_and_truncated_body_are_unknown(self):
        class BrokenStream(httpx.SyncByteStream):
            def __iter__(self):
                yield b'{"model":'
                raise httpx.ReadTimeout(KEY)
        for response in [httpx.ConnectError(KEY), httpx.ReadTimeout(KEY), http_response(stream=BrokenStream()),
                         http_response(stream=httpx.ByteStream(b'{"model":'))]:
            reply = self.send(response)
            self.assertEqual(reply.outcome, "unknown")
            self.assertNotIn(KEY, repr(reply))

    def test_stream_body_and_content_caps_reject_without_reading_unbounded_data(self):
        class LargeStream(httpx.SyncByteStream):
            def __iter__(self):
                yield b" " * MAX_ENVELOPE_BYTES
                yield b"x"
                raise AssertionError("adapter must stop consuming")
        for response in [http_response(headers={"content-length": str(MAX_ENVELOPE_BYTES + 1)}),
                         http_response(stream=LargeStream()), http_response(response_body("x" * 65537))]:
            self.assertEqual(self.send(response).outcome, "failed")

    def test_wrong_model_tool_calls_truncation_and_invalid_envelopes_are_rejected(self):
        wrong_model = response_body(); wrong_model["model"] = "another-model"
        tool_call = response_body(); tool_call["choices"][0]["message"]["tool_calls"] = [{"id": "tool"}]
        truncated = response_body(); truncated["choices"][0]["finish_reason"] = "length"
        for body in [wrong_model, tool_call, truncated, {"choices": []}, []]:
            self.assertEqual(self.send(http_response(body)).outcome, "failed")
        for headers in [{"content-encoding": "gzip"}, {"content-type": "text/html"}]:
            self.assertEqual(self.send(http_response(headers=headers)).outcome, "failed")

    def test_missing_or_inconsistent_usage_keeps_full_reservation(self):
        for usage in [None, {}, {"prompt_tokens": True, "completion_tokens": 10, "total_tokens": 11},
                      {"prompt_tokens": 500, "completion_tokens": 20, "total_tokens": 900}]:
            body = response_body(); body["usage"] = usage
            reply = self.send(http_response(body))
            self.assertEqual(reply.outcome, "received")
            self.assertIsNone(reply.usage)

    def test_child_receives_secret_only_on_stdin_and_uses_no_proxy_or_redirects(self):
        def run(command, **options):
            self.assertNotIn(KEY, repr(command))
            data = json.loads(options["input"])
            self.assertEqual(data["api_key"], KEY)
            self.assertEqual(data["payload"], self.payload)
            self.assertGreater(options["timeout"], 0)
            self.assertLessEqual(options["timeout"], 2)
            self.assertEqual(options["stderr"], subprocess.DEVNULL)
            return subprocess.CompletedProcess(command, 0, json.dumps({"outcome": "received", "text": "{}", "usage": [600, 120]}).encode())
        with patch("app.groq_transport.subprocess.run", side_effect=run) as runner:
            reply = GroqTransport(KEY, MODEL).send(self.payload, timeout_seconds=2)
        self.assertEqual(reply.usage, (600, 120))
        runner.assert_called_once()
        original = httpx.Client
        with patch("app.groq_transport.httpx.Client", wraps=original) as client:
            self.send(http_response())
        self.assertFalse(client.call_args.kwargs["trust_env"])
        self.assertFalse(client.call_args.kwargs["follow_redirects"])

    def test_contract_and_timeout_limits_reject_before_launching(self):
        transport = GroqTransport(KEY, MODEL)
        with patch("app.groq_transport.subprocess.run", side_effect=AssertionError("no process")) as runner:
            for timeout in [0, -1, 11, float("nan"), float("inf")]:
                self.assertEqual(transport.send(self.payload, timeout_seconds=timeout).outcome, "failed")
            for change in [{"model": "different"}, {"tools": []}, {"max_tokens": 1001}, {"messages": []}]:
                self.assertEqual(transport.send({**self.payload, **change}, timeout_seconds=1).outcome, "failed")
        runner.assert_not_called()

    def test_total_deadline_kills_and_reaps_a_stalled_child(self):
        real_run = subprocess.run
        children = []
        real_popen = subprocess.Popen
        def popen(*args, **options):
            child = real_popen(*args, **options)
            children.append(child)
            return child
        def stalled_child(command, **options):
            return real_run([sys.executable, "-c", "import time; time.sleep(5)"], **options)
        started = monotonic()
        with patch("app.groq_transport.subprocess.run", side_effect=stalled_child), \
                patch("subprocess.Popen", side_effect=popen):
            reply = GroqTransport(KEY, MODEL).send(self.payload, timeout_seconds=0.2)
        self.assertEqual(reply.outcome, "unknown")
        self.assertLess(monotonic() - started, 3)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())

    def test_late_invalid_and_failed_child_replies_are_unknown(self):
        for result in [subprocess.CompletedProcess([], 1, b""), subprocess.CompletedProcess([], 0, b"not-json")]:
            with patch("app.groq_transport.subprocess.run", return_value=result):
                self.assertEqual(GroqTransport(KEY, MODEL).send(self.payload, timeout_seconds=1).outcome, "unknown")
        result = subprocess.CompletedProcess([], 0, b'{"outcome":"received","text":"{}"}')
        with patch("app.groq_transport.subprocess.run", return_value=result), \
                patch("app.groq_transport.monotonic", side_effect=[0, 0, 2]):
            self.assertEqual(GroqTransport(KEY, MODEL).send(self.payload, timeout_seconds=1).outcome, "unknown")

    def test_launch_failure_is_unknown_without_exposing_exception(self):
        with patch("app.groq_transport.subprocess.run", side_effect=OSError(KEY)) as runner:
            reply = GroqTransport(KEY, MODEL).send(self.payload, timeout_seconds=1)
        self.assertEqual(reply.outcome, "unknown")
        self.assertNotIn(KEY, repr(reply))
        runner.assert_called_once()

    def test_communication_failure_and_keyboard_interrupt_kill_and_reap_child(self):
        real_popen = subprocess.Popen
        for error in [OSError(KEY), KeyboardInterrupt()]:
            children = []
            def popen(command, **options):
                child = real_popen([sys.executable, "-c", "import time; time.sleep(5)"], **options)
                children.append(child)
                return child
            with self.subTest(error=type(error).__name__), patch("subprocess.Popen", side_effect=popen), \
                    patch.object(real_popen, "communicate", side_effect=error):
                try:
                    if isinstance(error, KeyboardInterrupt):
                        with self.assertRaises(KeyboardInterrupt):
                            GroqTransport(KEY, MODEL).send(self.payload, timeout_seconds=1)
                    else:
                        self.assertEqual(GroqTransport(KEY, MODEL).send(self.payload, timeout_seconds=1).outcome, "unknown")
                    self.assertEqual(len(children), 1)
                    self.assertIsNotNone(children[0].poll())
                finally:
                    for child in children:
                        if child.poll() is None: child.kill()
                        child.wait(timeout=3)

    def test_child_watchdog_exits_stalled_send_without_parent_enforcement(self):
        # Mark readiness after imports, so the assertion measures the send rather than Python startup.
        script = ("import time; from app import groq_transport as t; print(time.monotonic(), flush=True); "
                  "t.request_once = lambda **kw: time.sleep(30); t.main()")
        result = subprocess.run([sys.executable, "-c", script],
            input=json.dumps({"payload": self.payload, "api_key": KEY, "timeout_seconds": 0.2}).encode(),
            capture_output=True, timeout=10, cwd=ROOT / "apps/api",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 1)
        self.assertLess(monotonic() - float(result.stdout), 3)
        self.assertEqual(result.stderr, b"")

    def test_child_cancels_watchdog_after_serializing_success_or_failure(self):
        for response, outcome in [(ProviderReply("received", "{}", (600, 120)), "received"), (OSError(KEY), "unknown")]:
            timers = []
            def timer(*args, **options):
                instance = Timer(*args, **options)
                timers.append(instance)
                return instance
            raw = json.dumps({"payload": self.payload, "api_key": KEY, "timeout_seconds": 0.5}).encode()
            output = BytesIO()
            with self.subTest(outcome=outcome), \
                    patch("app.groq_transport.sys.stdin", SimpleNamespace(buffer=BytesIO(raw))), \
                    patch("app.groq_transport.sys.stdout", SimpleNamespace(buffer=output)), \
                    patch("app.groq_transport.request_once", side_effect=[response]), \
                    patch("app.groq_transport.Timer", side_effect=timer), patch("app.groq_transport.os._exit") as terminate:
                transport_main()
                self.assertEqual(len(timers), 1)
                self.assertFalse(timers[0].is_alive())
                terminate.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())["outcome"], outcome)
            self.assertNotIn(KEY.encode(), output.getvalue())

    def test_child_rejects_invalid_deadlines_before_http(self):
        for timeout in [0, -1, 11, True, "1", float("inf"), float("nan")]:
            raw = json.dumps({"payload": self.payload, "api_key": KEY, "timeout_seconds": timeout}).encode()
            output = BytesIO()
            with self.subTest(timeout=timeout), \
                    patch("app.groq_transport.sys.stdin", SimpleNamespace(buffer=BytesIO(raw))), \
                    patch("app.groq_transport.sys.stdout", SimpleNamespace(buffer=output)), \
                    patch("app.groq_transport.request_once") as http, patch("app.groq_transport.Timer") as timer:
                transport_main()
            self.assertEqual(json.loads(output.getvalue())["outcome"], "unknown")
            http.assert_not_called()
            timer.assert_not_called()


class ConfiguredWorkerTests(AILedgerFixture):
    def setUp(self):
        super().setUp()
        self.path = Path(self.temporary.name) / "groq.json"
        self.path.write_text(json.dumps(readiness_record()), encoding="utf-8")
        self.settings = Settings(groq_enabled=True, groq_api_key=KEY, groq_model=MODEL, groq_readiness_file=str(self.path))

    def test_configured_worker_uses_http_adapter_and_saves_validated_provenance(self):
        def run(command, **options):
            data = json.loads(options["input"])
            content = supported_reply(data["payload"]).text
            reply = request_once(**data, transport=httpx.MockTransport(lambda _: http_response(response_body(content))))
            return subprocess.CompletedProcess(command, 0, json.dumps(asdict(reply)).encode())
        with patch("app.groq_transport.subprocess.run", side_effect=run) as runner:
            report_id = process_claim(self.factory, self.claimed, settings=self.settings)
        self.assertEqual(runner.call_count, 2)
        with self.factory() as db:
            report = report_data(db, self.alice, report_id)
            self.assertEqual(report["effective_mode"], "hybrid")
            self.assertEqual(report["accepted_ai_findings"], 12)
            self.assertEqual(len(report["ai_provenance"]), 2)
            calls = list(db.scalars(select(AICall)))
            self.assertTrue(all(c.input_tokens == 600 and c.output_tokens == 120 for c in calls))
            self.assertNotIn(KEY, json.dumps(report))

    def test_health_and_worker_recheck_expiring_file_and_fall_back_without_process(self):
        app = create_app(self.settings, self.factory)
        health = next(route.endpoint for route in app.routes if route.path == "/api/health")
        with self.factory() as db: self.assertTrue(health(db)["external_review"]["available"])
        record = readiness_record(); record["valid_until"] = timestamp(utcnow() - timedelta(seconds=1))
        self.path.write_text(json.dumps(record), encoding="utf-8")
        with patch("app.groq_transport.subprocess.run", side_effect=AssertionError("no calls")) as runner:
            with self.factory() as db: self.assertFalse(health(db)["external_review"]["available"])
            report_id = process_claim(self.factory, self.claimed, settings=self.settings)
        runner.assert_not_called()
        with self.factory() as db:
            report = report_data(db, self.alice, report_id)
            self.assertEqual(report["degradation_reason"], "unverified_quota")
            self.assertEqual(db.scalar(select(func.count()).select_from(AICall)), 0)

    def test_rules_only_never_reads_provider_readiness_or_launches_process(self):
        with self.factory.begin() as db: db.get(Job, self.job_id).requested_mode = "rules_only"
        with patch("app.worker.groq_readiness", side_effect=AssertionError("no provider config")) as readiness, \
                patch("app.groq_transport.subprocess.run", side_effect=AssertionError("no calls")) as runner:
            self.assertIsNotNone(process_claim(self.factory, self.claimed, settings=self.settings))
        readiness.assert_not_called()
        runner.assert_not_called()

    def test_test_worker_cli_disables_enabled_provider_and_publishes_fallback(self):
        environment = {"GROQ_API_KEY": KEY, "TERMSLENS_GROQ_ENABLED": "true", "TERMSLENS_GROQ_MODEL": MODEL,
            "TERMSLENS_GROQ_READINESS_FILE": str(self.path), "TERMSLENS_DATABASE_URL": "application-only",
            "TERMSLENS_TEST_DATABASE_URL": "test-only"}
        engine = Mock()
        engine.connect.return_value.__enter__ = Mock(return_value=engine)
        engine.connect.return_value.__exit__ = Mock(return_value=False)
        engine.execute.return_value.scalar.return_value = 1
        with patch.dict(os.environ, environment, clear=True), patch("app.config.load_dotenv"), \
                patch.object(sys, "argv", ["app.worker", "--test", "--once"]), \
                patch("app.worker.make_database", return_value=(engine, self.factory)) as database, \
                patch("app.worker.claim", return_value=self.claimed), patch("app.worker.configure_logging"), \
                patch("app.worker.logging.basicConfig"), \
                patch("app.groq_config.Path.open", side_effect=AssertionError("no readiness file")), \
                patch("app.groq_transport.subprocess.run", side_effect=AssertionError("no calls")) as runner:
            worker_main()
        database.assert_called_once_with("test-only", test=True)
        runner.assert_not_called()
        with self.factory() as db:
            job = db.get(Job, self.job_id)
            report = report_data(db, self.alice, job.result_id)
            self.assertEqual(job.state, "partial")
            self.assertEqual(report["effective_mode"], "rules_only")
            self.assertEqual(report["degradation_reason"], "unverified_quota")
            self.assertEqual(db.scalar(select(func.count()).select_from(AICall)), 0)
