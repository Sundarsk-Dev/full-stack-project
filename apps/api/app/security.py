from collections import OrderedDict, deque
from hashlib import sha256
import hmac
from threading import Lock
from time import monotonic

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from starlette.responses import JSONResponse

PASSWORDS = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
DUMMY_HASH = PASSWORDS.hash("unused-timing-only-password")
COOKIE = "termslens_session"


def token_hash(token):
    return sha256(token.encode()).hexdigest()


def csrf_token(token):
    return hmac.new(token.encode(), b"termslens-csrf-v1", sha256).hexdigest()


def verify_password(encoded, password):
    try:
        return PASSWORDS.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


class RateLimiter:
    """Bounded in-process limits for the single API process used by Gate 1."""
    def __init__(self):
        self.entries = OrderedDict()
        self.lock = Lock()

    def allow(self, key, limit, window=60):
        now = monotonic()
        with self.lock:
            events = self.entries.setdefault(key, deque())
            while events and events[0] <= now - window:
                events.popleft()
            self.entries.move_to_end(key)
            # Refuse new capacity rather than evict a live bucket and reset its budget.
            if len(self.entries) > 10000:
                for old_key in list(self.entries):
                    if not self.entries[old_key] or self.entries[old_key][-1] <= now - window:
                        self.entries.pop(old_key)
                if len(self.entries) > 10000:
                    self.entries.pop(key, None)
                    return False
                events = self.entries.setdefault(key, deque())
            if len(events) >= limit:
                return False
            events.append(now)
            return True


class BodyLimitMiddleware:
    """Bound chunked bodies before FastAPI parses JSON, even without Content-Length."""
    def __init__(self, app, limit=1024 * 1024):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self.limit:
                return await JSONResponse({"detail": "Request body exceeds 1 MiB."}, status_code=413)(scope, receive, send)
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)
