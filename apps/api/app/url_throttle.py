"""Shared per-host request leases and quiet intervals; transactions never span HTTP."""
from contextlib import contextmanager
from datetime import timedelta
import math

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .db import utcnow
from .models import FetchHost, new_id
from .url_guard import FetchError


class HostThrottle:
    def __init__(self, factory):
        self.factory = factory

    @contextmanager
    def __call__(self, host, interval, remaining):
        if not math.isfinite(interval) or not 1 <= interval <= 86400 or not 0 < remaining <= 60:
            raise FetchError("invalid_host_budget")
        token = new_id()
        for attempt in range(2):
            try:
                with self.factory.begin() as db:
                    row = db.scalar(select(FetchHost).where(FetchHost.host == host).with_for_update())
                    if row is None:
                        row = FetchHost(host=host, interval_seconds=1)
                        db.add(row)
                        db.flush()
                    now = utcnow()
                    if row.lease_token and row.lease_expires_at > now: raise FetchError("host_busy")
                    interval = max(interval, row.interval_seconds)
                    wait = max(0, (row.last_finished_at + timedelta(seconds=interval) - now).total_seconds()) if row.last_finished_at else 0
                    if wait >= remaining: raise FetchError("host_throttled")
                    row.lease_token, row.lease_expires_at = token, now + timedelta(seconds=remaining)
                    row.interval_seconds = interval
                break
            except IntegrityError:
                if attempt: raise FetchError("host_busy") from None
        try:
            yield wait
        finally:
            with self.factory.begin() as db:
                row = db.scalar(select(FetchHost).where(FetchHost.host == host).with_for_update())
                if row is not None and row.lease_token == token:
                    row.last_finished_at = utcnow()
                    row.lease_token, row.lease_expires_at = None, None
