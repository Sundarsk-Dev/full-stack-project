"""One bounded HTTP request in a killable child, with no automatic retries.

The child receives credentials/policy on stdin, never command-line arguments.
The parent deadline bounds DNS, TLS and slow streaming as well as HTTP timeouts.
"""
from dataclasses import asdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from threading import Timer
from time import monotonic

import httpx

from .ai_contract import INPUT_PER_REQUEST, OUTPUT_PER_REQUEST, SYSTEM_PROMPT, estimate_input_tokens
from .ai_review import CALL_TIMEOUT_SECONDS, ProviderReply

ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
MAX_ENVELOPE_BYTES = 131072
MAX_CONTENT_BYTES = 65536


def _usage(body):
    usage = body.get("usage")
    if not isinstance(usage, dict): return None
    values = tuple(usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens"))
    if (all(type(n) is int and 0 <= n <= 1_000_000 for n in values)
            and values[0] > 0 and values[2] == values[0] + values[1]):
        return values[:2]
    return None


def _retry_after(headers):
    value = headers.get("retry-after", "").strip()
    try:
        if value.isascii() and value.isdigit(): seconds = int(value)
        else:
            retry = parsedate_to_datetime(value)
            now = parsedate_to_datetime(headers["date"]) if "date" in headers else datetime.now(timezone.utc)
            if retry.tzinfo is None or now.tzinfo is None: return None
            seconds = max(0, math.ceil((retry - now).total_seconds()))
        return seconds if 0 <= seconds <= 60 else None
    except (ValueError, TypeError, OverflowError):
        return None


def request_once(payload, api_key, *, timeout_seconds, transport=None):
    """HTTP mapping; tests inject MockTransport. Called only in the bounded child live."""
    try:
        with httpx.Client(transport=transport or httpx.HTTPTransport(retries=0), trust_env=False,
                          follow_redirects=False, timeout=timeout_seconds) as client:
            with client.stream("POST", ENDPOINT, json=payload,
                               headers={"Authorization": f"Bearer {api_key}", "Accept-Encoding": "identity"}) as response:
                if response.status_code == 429:
                    wait = _retry_after(response.headers)
                    return ProviderReply("failed") if wait is None else ProviderReply("rate_limited", retry_after_seconds=wait)
                if response.status_code >= 500 or response.status_code == 408:
                    return ProviderReply("unknown")
                if response.status_code != 200: return ProviderReply("failed")
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    return ProviderReply("failed")
                media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if media_type != "application/json": return ProviderReply("failed")
                if "content-length" in response.headers:
                    size = int(response.headers["content-length"])
                    if not 0 <= size <= MAX_ENVELOPE_BYTES: return ProviderReply("failed")
                raw = bytearray()
                for chunk in response.iter_raw():
                    if len(raw) + len(chunk) > MAX_ENVELOPE_BYTES: return ProviderReply("failed")
                    raw.extend(chunk)
                body = json.loads(raw)
                if not isinstance(body, dict) or body.get("model") != payload["model"]:
                    return ProviderReply("failed")
                choices = body.get("choices")
                if not isinstance(choices, list) or len(choices) != 1: return ProviderReply("failed")
                choice = choices[0]
                message = choice.get("message", {})
                content = message.get("content")
                if (choice.get("finish_reason") != "stop" or choice.get("index") != 0
                        or message.get("role") != "assistant" or message.get("tool_calls") or message.get("function_call")
                        or not isinstance(content, str) or len(content.encode("utf-8")) > MAX_CONTENT_BYTES):
                    return ProviderReply("failed")
                return ProviderReply("received", content, _usage(body))
    except Exception:
        # Truncation, network/TLS failures or unreadable replies may follow a send.
        # Never echo raw exceptions, headers or error bodies, and never resend here.
        return ProviderReply("unknown")


class GroqTransport:
    def __init__(self, api_key, model):
        self._api_key, self.model = api_key, model

    def send(self, payload, *, timeout_seconds):
        started = monotonic()
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= CALL_TIMEOUT_SECONDS:
            return ProviderReply("failed")
        # Accept only the coordinator's bounded classify-batch contract.
        if (set(payload) != {"model", "temperature", "max_tokens", "response_format", "messages"}
                or payload["model"] != self.model or payload["temperature"] != 0
                or payload["max_tokens"] != OUTPUT_PER_REQUEST
                or payload["response_format"] != {"type": "json_object"}
                or estimate_input_tokens(payload) > INPUT_PER_REQUEST
                or len(payload["messages"]) != 2
                or payload["messages"][0] != {"role": "system", "content": SYSTEM_PROMPT}
                or set(payload["messages"][1]) != {"role", "content"}
                or payload["messages"][1]["role"] != "user"
                or not isinstance(payload["messages"][1]["content"], str)):
            return ProviderReply("failed")
        envelope = json.dumps({"payload": payload, "api_key": self._api_key, "timeout_seconds": timeout_seconds},
                              ensure_ascii=False).encode("utf-8")
        try:
            remaining = timeout_seconds - (monotonic() - started)
            if remaining <= 0: return ProviderReply("unknown")
            result = subprocess.run([sys.executable, "-m", "app.groq_transport"], input=envelope,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=Path(__file__).resolve().parents[1],
                timeout=remaining, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode or monotonic() - started >= timeout_seconds or len(result.stdout) > 6 * MAX_CONTENT_BYTES + 1024:
                return ProviderReply("unknown")
            reply = json.loads(result.stdout)
            if reply.get("usage") is not None: reply["usage"] = tuple(reply["usage"])
            return ProviderReply(**reply)
        except Exception:
            # subprocess.run kills/reaps on timeout or communication failure.
            return ProviderReply("unknown")


def main():
    reply = ProviderReply("unknown")
    watchdog = None
    try:
        raw = sys.stdin.buffer.read(16385)
        if len(raw) <= 16384:
            request = json.loads(raw)
            timeout = request["timeout_seconds"]
            if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= CALL_TIMEOUT_SECONDS:
                raise ValueError("Invalid deadline")
            # Bound the send even if the supervising worker is forcibly stopped.
            watchdog = Timer(timeout, os._exit, args=(1,))
            watchdog.daemon = True
            watchdog.start()
            reply = request_once(**request)
    except Exception:
        pass
    try:
        sys.stdout.buffer.write(json.dumps(asdict(reply), ensure_ascii=False).encode("utf-8"))
        sys.stdout.buffer.flush()
    finally:
        if watchdog is not None:
            watchdog.cancel()
            watchdog.join()


if __name__ == "__main__": main()
