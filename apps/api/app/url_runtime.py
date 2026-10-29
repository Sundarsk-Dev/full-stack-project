"""The URL service's process boundary. Database credentials travel on stdin only."""
from base64 import b64decode
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from threading import Timer
from time import monotonic

from sqlalchemy.exc import SQLAlchemyError

from .db import make_database
from .url_content import EXTRACTOR
from .url_fetch import DEADLINE_SECONDS, MAX_REDIRECTS, MAX_REQUESTS, FetchOperation
from .url_guard import ERROR_CODES, MAX_BYTES, FetchError, validate_url
from .url_throttle import HostThrottle

MAX_REQUEST_BYTES = 16384
MAX_RESULT_BYTES = 8 * 1024 * 1024


def validate_result(result, target, operation):
    if (result["operation"] != operation or result["source_url"] != target.url
            or type(result["requests"]) is not int or not 1 <= result["requests"] <= MAX_REQUESTS
            or type(result["redirects"]) is not int or not 0 <= result["redirects"] <= MAX_REDIRECTS):
        raise ValueError()
    final = validate_url(result["final_url"])
    if operation == "discovery":
        links = result["candidates"]
        if final.origin != target.origin or not isinstance(links, list) or len(links) > 4: raise ValueError()
        urls = set()
        for link in links:
            linked = validate_url(link["url"])
            if (linked.origin != final.origin or linked.url in urls or len(link["title"]) > 160
                    or link["document_type"] not in {"terms", "privacy", "cookies", "refund"}): raise ValueError()
            urls.add(linked.url)
    else:
        raw, text = b64decode(result["raw_base64"], validate=True), result["text"]
        if (len(raw) > MAX_BYTES or not isinstance(text, str) or not text.strip() or len(text) > 200000
                or len(text.split()) > 25000 or result["extractor"] != EXTRACTOR
                or result["raw_sha256"] != sha256(raw).hexdigest()
                or result["text_sha256"] != sha256(text.encode("utf-8")).hexdigest()): raise ValueError()
        if len(result["headings"]) > 2000: raise ValueError()
        for heading in result["headings"]:
            start, end = heading["start"], heading["end"]
            if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text)
                    or type(heading["level"]) is not int or heading["level"] not in range(1, 7)
                    or text[start:end] != heading["text"]): raise ValueError()
    return result


class URLFetcher:
    def __init__(self, database_url, *, test=False):
        self._database_url, self._test = database_url, test

    def fetch(self, url, *, operation="capture", timeout_seconds=DEADLINE_SECONDS):
        target = validate_url(url)
        if operation not in {"capture", "discovery"}: raise FetchError("invalid_operation")
        if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= DEADLINE_SECONDS:
            raise FetchError("invalid_deadline")
        envelope = json.dumps({"url": target.url, "operation": operation, "database_url": self._database_url,
                               "test": self._test, "timeout_seconds": timeout_seconds}).encode("utf-8")
        if len(envelope) > MAX_REQUEST_BYTES: raise FetchError("invalid_child_request")
        environment = {key: value for key, value in os.environ.items()
                       if key not in {"GROQ_API_KEY", "TERMSLENS_DATABASE_URL", "TERMSLENS_TEST_DATABASE_URL"}}
        started = monotonic()
        try:
            child = subprocess.run([sys.executable, "-m", "app.url_runtime"], input=envelope, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, timeout=timeout_seconds, cwd=Path(__file__).resolve().parents[1], env=environment,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if monotonic() - started >= timeout_seconds: raise FetchError("fetch_deadline")
            if child.returncode or len(child.stdout) > MAX_RESULT_BYTES: raise FetchError("invalid_child_result")
            reply = json.loads(child.stdout)
            if reply.get("status") == "failed" and reply.get("reason") in ERROR_CODES:
                raise FetchError(reply["reason"])
            if reply.get("status") != "ok": raise FetchError("invalid_child_result")
            return validate_result(reply["result"], target, operation)
        except subprocess.TimeoutExpired:
            raise FetchError("fetch_deadline") from None
        except FetchError:
            raise
        except Exception:
            raise FetchError("invalid_child_result") from None


def perform(request):
    try:
        engine, factory = make_database(request["database_url"], test=request["test"])
    except (ValueError, ImportError):
        raise FetchError("storage_unavailable") from None
    try:
        return FetchOperation(HostThrottle(factory), timeout=request["timeout_seconds"]).run(request["url"], request["operation"])
    finally:
        engine.dispose()


def main():
    reply, watchdog = {"status": "failed", "reason": "invalid_child_request"}, None
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES: raise ValueError()
        request = json.loads(raw)
        if (set(request) != {"url", "operation", "database_url", "test", "timeout_seconds"}
                or type(request["test"]) is not bool or not isinstance(request["database_url"], str)
                or request["operation"] not in {"capture", "discovery"}): raise ValueError()
        validate_url(request["url"])
        timeout = request["timeout_seconds"]
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= DEADLINE_SECONDS: raise ValueError()
        watchdog = Timer(timeout, os._exit, args=(1,))
        watchdog.daemon = True
        watchdog.start()
        reply = {"status": "ok", "result": perform(request)}
    except FetchError as error:
        reply = {"status": "failed", "reason": str(error)}
    except SQLAlchemyError:
        reply = {"status": "failed", "reason": "storage_unavailable"}
    except Exception:
        pass
    try:
        data = json.dumps(reply, ensure_ascii=False).encode("utf-8")
        if len(data) > MAX_RESULT_BYTES: data = b'{"status":"failed","reason":"invalid_child_result"}'
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
    finally:
        if watchdog is not None:
            watchdog.cancel()
            watchdog.join()


if __name__ == "__main__": main()
